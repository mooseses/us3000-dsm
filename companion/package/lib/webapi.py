"""US3000 Companion web API, served as a CGI by DSM's web server. Read-only."""

import json
import os
import sys
import time
import urllib.parse

import ucore

RANGES = {           # range -> (seconds, bucket seconds)
    "1h": (3600, 30),
    "24h": (86400, 300),
    "7d": (7 * 86400, 1800),
    "30d": (30 * 86400, 7200),
}
LIVE_KEYS = ["ups.status", "battery.charge", "battery.charge.low", "battery.runtime",
             "input.voltage", "output.voltage", "ups.load", "ups.beeper.status",
             "device.mfr", "device.model", "device.serial", "driver.version",
             "driver.parameter.subdriver", "ups.delay.shutdown", "ups.delay.start"]


def _since(con, now):
    """When the UPS entered its current power state."""
    row = con.execute("SELECT ts, kind FROM events WHERE kind IN ('ON_BATTERY','ON_MAINS') "
                      "ORDER BY ts DESC LIMIT 1").fetchone()
    if row:
        return row["ts"]
    row = con.execute("SELECT MIN(ts) AS t FROM samples").fetchone()
    return row["t"] if row and row["t"] else now


def status(con, now):
    try:
        v = ucore.nut_list_vars()
        live = {k: v.get(k) for k in LIVE_KEYS}
        live["battery.runtime"] = ucore.runtime_of(v)
        err = None
    except ucore.NutError as e:
        live, err = None, str(e)
    last_poll = ucore.meta_get(con, "last_poll")
    return {
        "now": now,
        "live": live,
        "comm_error": err,
        "since": _since(con, now),
        "health": ucore.meta_get(con, "health"),
        "collector": {"last_poll": last_poll,
                      "running": bool(last_poll and now - last_poll < 30)},
    }


def history(con, now, rng):
    span, bucket = RANGES.get(rng, RANGES["24h"])
    start = now - span
    rows = con.execute(
        "SELECT (ts / ?) * ? AS t, AVG(charge) AS charge, MIN(charge) AS charge_min, "
        "AVG(vin) AS vin, AVG(vout) AS vout, AVG(load) AS load, "
        "MAX(status LIKE '%OB%') AS ob, MAX(status = 'NOCOMM') AS nocomm "
        "FROM samples WHERE ts >= ? GROUP BY t ORDER BY t",
        (bucket, bucket, start)).fetchall()
    bands = con.execute(
        "SELECT start_ts, COALESCE(end_ts, ?) AS end_ts FROM outages "
        "WHERE COALESCE(end_ts, ?) >= ? ORDER BY start_ts", (now, now, start)).fetchall()
    return {"range": rng if rng in RANGES else "24h", "start": start, "end": now,
            "bucket": bucket, "points": [dict(r) for r in rows],
            "outages": [dict(b) for b in bands]}


def outages(con, now):
    out = []
    for r in con.execute("SELECT * FROM outages ORDER BY start_ts DESC LIMIT 500"):
        d = dict(r)
        end = d["end_ts"] or now
        d["duration"] = end - d["start_ts"]
        d["ongoing"] = d["end_ts"] is None
        drop = (d["start_charge"] - d["min_charge"]
                if d["start_charge"] is not None and d["min_charge"] is not None else None)
        # Only outages long enough to move the charge reading say anything about wear.
        d["rate"] = (round(drop / (d["duration"] / 60.0), 3)
                     if drop is not None and drop > 0 and d["duration"] >= 120 else None)
        d["lb_after"] = d["lb_ts"] - d["start_ts"] if d["lb_ts"] else None
        out.append(d)
    return {"outages": out}


def events(con, limit):
    rows = con.execute("SELECT ts, kind, detail FROM events ORDER BY ts DESC, id DESC LIMIT ?",
                       (max(1, min(limit, 500)),)).fetchall()
    return {"events": [dict(r) for r in rows]}


def handle(params, db_path=ucore.DEFAULT_DB, now=None):
    now = int(now or time.time())
    action = params.get("action", "status")
    if not os.path.exists(db_path):
        return 503, {"error": "no data yet: the collector hasn't created its database"}
    con = ucore.db_open(db_path, readonly=True)
    try:
        if action == "status":
            return 200, status(con, now)
        if action == "history":
            return 200, history(con, now, params.get("range", "24h"))
        if action == "outages":
            return 200, outages(con, now)
        if action == "events":
            try:
                limit = int(params.get("limit", "100"))
            except ValueError:
                limit = 100
            return 200, events(con, limit)
        return 400, {"error": "unknown action"}
    finally:
        con.close()


def cgi_main(auth=ucore.dsm_admin_user, db_path=ucore.DEFAULT_DB, out=sys.stdout):
    params = dict(urllib.parse.parse_qsl(os.environ.get("QUERY_STRING", "")))
    user = auth()
    if not user:
        code, body = 403, {"error": "sign in to DSM as an administrator"}
    else:
        try:
            code, body = handle(params, db_path)
        except Exception as e:     # keep the UI informative rather than a bare 500 page
            code, body = 500, {"error": "%s: %s" % (type(e).__name__, e)}
    reason = {200: "OK", 400: "Bad Request", 403: "Forbidden",
              500: "Internal Server Error", 503: "Service Unavailable"}[code]
    out.write("Status: %d %s\r\nContent-Type: application/json; charset=utf-8\r\n"
              "Cache-Control: no-store\r\n\r\n" % (code, reason))
    out.write(json.dumps(body))
