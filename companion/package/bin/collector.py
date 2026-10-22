#!/bin/python3
"""US3000 Companion collector: polls DSM's upsd and records samples, events
and outages in SQLite. Runs as the package user; never writes to the UPS."""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import ucore  # noqa: E402

SAMPLE_EVERY = 30        # seconds between stored samples on mains
SAMPLE_EVERY_OB = 10     # ... and on battery
HEALTH_EVERY = 60
PRUNE_EVERY = 3600
RESUME_WINDOW = 120      # an open outage younger than this survives a restart

log = logging.getLogger("collector")


class Collector:
    def __init__(self, con, query=ucore.nut_list_vars, health=ucore.health_check,
                 clock=time.time):
        self.con = con
        self.query = query
        self.health = health
        self.clock = clock
        self.last_sample = 0
        self.last_status = None
        self.last_health = 0
        self.last_prune = 0
        self.comm_ok = None
        self.rb_reported = ucore.meta_get(con, "rb_reported", False)

    # -- helpers
    def event(self, ts, kind, detail=""):
        self.con.execute("INSERT INTO events(ts, kind, detail) VALUES(?,?,?)",
                         (ts, kind, detail))
        log.info("%s %s", kind, detail)

    def open_outage(self):
        return self.con.execute(
            "SELECT * FROM outages WHERE end_ts IS NULL ORDER BY id DESC LIMIT 1").fetchone()

    # -- lifecycle
    def startup(self):
        now = int(self.clock())
        self.event(now, "COLLECTOR_START")
        o = self.open_outage()
        if o is not None:
            last = self.con.execute(
                "SELECT MAX(ts) AS t FROM samples WHERE ts >= ?", (o["start_ts"],)).fetchone()["t"]
            last = last or o["start_ts"]
            if now - last > RESUME_WINDOW:
                # The collector (usually the whole NAS) went down on battery.
                self.con.execute(
                    "UPDATE outages SET end_ts=?, end_reason='nas_down' WHERE id=?",
                    (last, o["id"]))
                self.event(last, "OUTAGE_CLOSED",
                           "NAS went down while on battery (last reading %d min into the outage)"
                           % ((last - o["start_ts"]) // 60))
        self.con.commit()

    def poll(self):
        now = int(self.clock())
        try:
            v = self.query()
            err = None
        except ucore.NutError as e:
            v, err = None, str(e)

        if err:
            if self.comm_ok is not False:
                self.event(now, "COMM_LOST", err)
            self.comm_ok = False
            if now - self.last_sample >= SAMPLE_EVERY:
                self.con.execute("INSERT OR REPLACE INTO samples(ts, status) VALUES(?, 'NOCOMM')", (now,))
                self.last_sample = now
            self._periodic(now, None, err)
            self.con.commit()
            return

        if self.comm_ok is False:
            self.event(now, "COMM_OK")
        self.comm_ok = True

        status = v.get("ups.status", "")
        flags = ucore.flags_of(v)
        charge = ucore.num(v, "battery.charge")
        runtime = ucore.runtime_of(v)
        on_batt = "OB" in flags          # never DISCHRG: the US3000 sets it on mains too

        o = self.open_outage()
        if on_batt and o is None:
            self.con.execute(
                "INSERT INTO outages(start_ts, start_charge, min_charge, runtime_start) VALUES(?,?,?,?)",
                (now, charge, charge, runtime))
            self.event(now, "ON_BATTERY", "charge %s%%" % _fmt(charge))
        elif o is not None:
            if on_batt:
                seen = [x for x in (o["min_charge"], charge) if x is not None]
                upd = {"min_charge": min(seen) if seen else None}
                if o["runtime_start"] is None and runtime is not None:
                    upd["runtime_start"] = runtime
                if "LB" in flags and o["lb_ts"] is None:
                    upd["lb_ts"], upd["lb_charge"] = now, charge
                    self.event(now, "LOW_BATTERY", "charge %s%%, %d min on battery"
                               % (_fmt(charge), (now - o["start_ts"]) // 60))
                if "FSD" in flags and o["fsd_ts"] is None:
                    upd["fsd_ts"] = now
                    self.event(now, "FORCED_SHUTDOWN", "DSM is shutting down")
                sets = ", ".join("%s=?" % k for k in upd)
                self.con.execute("UPDATE outages SET %s WHERE id=?" % sets,
                                 tuple(upd.values()) + (o["id"],))
            else:
                self.con.execute(
                    "UPDATE outages SET end_ts=?, end_charge=?, end_reason='power_restored' WHERE id=?",
                    (now, charge, o["id"]))
                self.event(now, "ON_MAINS", "after %s, charge %s%%"
                           % (_dur(now - o["start_ts"]), _fmt(charge)))

        # Beeper and self-test changes, whoever caused them (the app, the UPS itself).
        for var, kind in (("ups.beeper.status", "BEEPER"), ("ups.test.result", "TEST_RESULT")):
            val = v.get(var)
            prev = ucore.meta_get(self.con, "last:" + var)
            if val is not None and val != prev:
                if prev is not None:
                    self.event(now, kind, "%s → %s" % (prev, val))
                ucore.meta_set(self.con, "last:" + var, val)

        if "RB" in flags and not self.rb_reported:
            self.event(now, "REPLACE_BATTERY", "the UPS reports its battery needs replacing")
            self.rb_reported = True
            ucore.meta_set(self.con, "rb_reported", True)
        elif "RB" not in flags and self.rb_reported:
            self.rb_reported = False
            ucore.meta_set(self.con, "rb_reported", False)

        every = SAMPLE_EVERY_OB if on_batt else SAMPLE_EVERY
        if status != self.last_status or now - self.last_sample >= every:
            self.con.execute(
                "INSERT OR REPLACE INTO samples(ts, status, charge, runtime, vin, vout, load) "
                "VALUES(?,?,?,?,?,?,?)",
                (now, status, charge, runtime, ucore.num(v, "input.voltage"),
                 ucore.num(v, "output.voltage"), ucore.num(v, "ups.load")))
            self.last_sample = now
        self.last_status = status
        ucore.meta_set(self.con, "last_poll", now)
        self._periodic(now, v, None)
        self.con.commit()

    def _periodic(self, now, v, err):
        if now - self.last_health >= HEALTH_EVERY:
            ucore.meta_set(self.con, "health", self.health(v, err))
            self.last_health = now
        if now - self.last_prune >= PRUNE_EVERY:
            cutoff = now - ucore.RETENTION_DAYS * 86400
            self.con.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self.con.execute("DELETE FROM events WHERE ts < ? AND kind IN "
                             "('COLLECTOR_START','COMM_LOST','COMM_OK')", (cutoff,))
            self.last_prune = now


def _fmt(x):
    return "?" if x is None else "%.0f" % x


def _dur(s):
    return "%dm %02ds" % (s // 60, s % 60) if s < 3600 else "%dh %02dm" % (s // 3600, s % 3600 // 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=ucore.DEFAULT_DB)
    ap.add_argument("--log", default=None)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (testing)")
    a = ap.parse_args()

    os.umask(0o022)   # the web API may run as another user and only needs to read
    handler = (logging.handlers.RotatingFileHandler(a.log, maxBytes=512 * 1024, backupCount=2)
               if a.log else logging.StreamHandler())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))

    con = ucore.db_open(a.db)
    c = Collector(con)
    c.startup()
    t_end = time.time() + a.duration if a.duration else None
    while not stop and (t_end is None or time.time() < t_end):
        try:
            c.poll()
        except Exception:
            log.exception("poll failed")
        time.sleep(a.interval)
    con.close()


if __name__ == "__main__":
    main()
