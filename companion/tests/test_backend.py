"""Offline tests: drive the collector with a scripted UPS and a fake clock,
then check what the web API returns. Run: python3 -I tests/test_backend.py"""

import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "package", "lib"))
sys.path.insert(0, os.path.join(HERE, "..", "package", "bin"))
import ucore      # noqa: E402
import webapi     # noqa: E402
import collector  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def ups(status, charge, runtime=65535, vin=12.0):
    return {"ups.status": status, "battery.charge": str(charge), "battery.runtime": str(runtime),
            "input.voltage": str(vin), "output.voltage": "12.0", "ups.load": "1",
            "battery.charge.low": "20", "driver.version": "2.8.5"}


def run(c, clock, script):
    """script: list of (seconds, vars or exception)"""
    for secs, state in script:
        def q(state=state):
            if isinstance(state, Exception):
                raise state
            return state
        c.query = q
        steps = int(secs // 5)
        for _ in range(steps):
            c.poll()
            clock.t += 5


fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "t.db")
clock = Clock(1_800_000_000)
con = ucore.db_open(db)
c = collector.Collector(con, health=lambda v, e: {"level": "ok", "checks": []}, clock=clock)
c.startup()

# 10 min mains, 12 min outage dropping 100 -> 76 with LB at the end, then mains again.
script = [(600, ups("OL DISCHRG", 100))]
ch = 100
for i in range(12):
    ch -= 2
    st = "OB DISCHRG LB" if ch <= 80 else "OB DISCHRG"
    script.append((60, ups(st, ch, runtime=900 - i * 60, vin=0.0)))
script.append((300, ups("OL DISCHRG CHRG", ch)))
script.append((30, ucore.NutError("Connection refused")))
script.append((30, ups("OL DISCHRG CHRG", ch + 1)))
run(c, clock, script)

o = con.execute("SELECT * FROM outages").fetchall()
check(len(o) == 1, "one outage recorded")
o = dict(o[0])
check(o["end_reason"] == "power_restored", "outage closed by power restore")
check(o["start_charge"] == 98 and o["min_charge"] == 76, "start/min charge tracked (%s/%s)" % (o["start_charge"], o["min_charge"]))
check(o["runtime_start"] == 900, "first real runtime estimate kept (%s)" % o["runtime_start"])
check(o["lb_ts"] is not None and o["lb_charge"] == 80, "first LB recorded at 80%")
kinds = [r["kind"] for r in con.execute("SELECT kind FROM events ORDER BY id")]
check(kinds == ["COLLECTOR_START", "ON_BATTERY", "LOW_BATTERY", "ON_MAINS", "COMM_LOST", "COMM_OK"],
      "event sequence %s" % kinds)
n_ob = con.execute("SELECT COUNT(*) FROM samples WHERE status LIKE 'OB%'").fetchone()[0]
check(60 <= n_ob <= 80, "denser sampling on battery (%d samples in 12 min)" % n_ob)

# NAS dies mid-outage: open outage, then collector restarts 10 min later.
run(c, clock, [(120, ups("OB DISCHRG", ch))])
clock.t += 600
c2 = collector.Collector(con, health=c.health, clock=clock)
c2.startup()
last = dict(con.execute("SELECT * FROM outages ORDER BY id DESC LIMIT 1").fetchone())
check(last["end_reason"] == "nas_down" and last["end_ts"] is not None, "stale outage closed as nas_down")

# Restart within the resume window keeps the outage open.
run(c2, clock, [(60, ups("OB DISCHRG", ch))])
clock.t += 30
c3 = collector.Collector(con, health=c.health, clock=clock)
c3.startup()
check(c3.open_outage() is not None, "quick restart during an outage keeps it open")
run(c3, clock, [(30, ups("OL", ch))])
check(c3.open_outage() is None, "and it closes normally afterwards")
con.close()

# ---- web API on top of that data
code, body = webapi.handle({"action": "outages"}, db, now=clock.t)
check(code == 200 and len(body["outages"]) == 3, "API lists 3 outages")
first = body["outages"][-1]
check(first["rate"] is not None and abs(first["rate"] - 22 / 12.0) < 0.2,
      "discharge rate ~1.83 %%/min (%s)" % first["rate"])
check(first["lb_after"] is not None, "LB time-into-outage reported")
for rng in ["1h", "24h", "7d", "30d", "bogus"]:
    code, body = webapi.handle({"action": "history", "range": rng}, db, now=clock.t)
    check(code == 200 and body["points"] and body["outages"] is not None,
          "history %s -> %d points, %d bands" % (rng, len(body["points"]), len(body["outages"])))
code, body = webapi.handle({"action": "events", "limit": "5"}, db, now=clock.t)
check(code == 200 and len(body["events"]) == 5, "events limit")
code, _ = webapi.handle({"action": "nope"}, db, now=clock.t)
check(code == 400, "unknown action -> 400")
code, _ = webapi.handle({"action": "status"}, os.path.join(tmp, "missing.db"))
check(code == 503, "missing DB -> 503")

# CGI wrapper: auth refused / accepted
buf = io.StringIO()
webapi.cgi_main(auth=lambda: None, db_path=db, out=buf)
check(buf.getvalue().startswith("Status: 403"), "CGI refuses non-admins")
os.environ["QUERY_STRING"] = "action=outages&SynoToken=abc"
buf = io.StringIO()
webapi.cgi_main(auth=lambda: "admin", db_path=db, out=buf)
head, _, payload = buf.getvalue().partition("\r\n\r\n")
check(head.startswith("Status: 200") and "outages" in json.loads(payload), "CGI serves admins")

# ---- addon boot status (v1.2.0 status file vs the kernel boot ID)
bdir = tempfile.mkdtemp()
ucore.ADDON_STATUS = os.path.join(bdir, "status")
ucore.BOOT_ID = os.path.join(bdir, "boot_id")
open(ucore.BOOT_ID, "w").write("aaaa-1111\n")
lvl, msg = ucore.addon_boot_status()
check(lvl == "warning" and "older than v1.2.0" in msg, "no status file -> warning")
open(ucore.ADDON_STATUS, "w").write("version=1.2.0\nmode=late\nboot_id=aaaa-1111\ntime=t\nresult=ok\nerrors=\n")
check(ucore.addon_boot_status()[0] == "ok", "status from this boot, ok -> ok")
open(ucore.ADDON_STATUS, "w").write("version=1.2.0\nmode=late\nboot_id=bbbb-2222\ntime=t\nresult=ok\nerrors=\n")
lvl, msg = ucore.addon_boot_status()
check(lvl == "critical" and "did not run at this boot" in msg, "status from an earlier boot -> critical")
open(ucore.ADDON_STATUS, "w").write("version=1.2.0\nmode=late\nboot_id=aaaa-1111\ntime=t\nresult=error\nerrors=driver missing\n")
lvl, msg = ucore.addon_boot_status()
check(lvl == "critical" and "driver missing" in msg, "errors at this boot -> critical with the message")

# ---- control endpoint against a fake us3000-ctl
import socketserver, threading  # noqa: E401,E402
ctl_reqs = []


class FakeCtl(socketserver.StreamRequestHandler):
    def handle(self):
        req = json.loads(self.rfile.readline())
        ctl_reqs.append(req)
        if req.get("op") == "ping":
            rep = {"ok": True, "version": "1.2.0", "allowed": sorted(webapi.CONTROL_CMDS)}
        elif req.get("cmd") == "test.battery.start.quick":
            rep = {"ok": False, "error": "refused: the UPS is on battery"}
        else:
            rep = {"ok": True, "code": 0, "result": "handled"}
        self.wfile.write((json.dumps(rep) + "\n").encode())


ucore.CTL_SOCK = os.path.join(bdir, "ctl.sock")
ctl_srv = socketserver.ThreadingUnixStreamServer(ucore.CTL_SOCK, FakeCtl)
threading.Thread(target=ctl_srv.serve_forever, daemon=True).start()

code, _ = webapi.handle({"action": "control"}, db, now=clock.t, method="GET", body='{"cmd":"beeper.enable"}', same_origin=True)
check(code == 405, "control via GET -> 405")
code, _ = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"beeper.enable"}', same_origin=False)
check(code == 403, "control without the X-US3000 header -> 403")
code, _ = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"load.off"}', same_origin=True)
check(code == 400 and not any(r.get("cmd") == "load.off" for r in ctl_reqs), "unknown command never reaches the gatekeeper")
code, _ = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body="not json", same_origin=True)
check(code == 400, "malformed body -> 400")
code, body = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"beeper.enable"}', user="admin", same_origin=True)
check(code == 200 and body["ok"] and ctl_reqs[-1] == {"op": "instcmd", "cmd": "beeper.enable", "confirm": False}, "beeper.enable forwarded")
code, body = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"test.battery.start.deep","confirm":true}', user="admin", same_origin=True)
check(code == 200 and ctl_reqs[-1]["confirm"] is True, "deep test forwards confirm=true")
code, body = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"test.battery.start.quick"}', user="admin", same_origin=True)
check(code == 200 and not body["ok"] and "on battery" in body["error"], "gatekeeper refusal passed through")
code, body = webapi.handle({"action": "events", "limit": "5"}, db, now=clock.t)
ctl_events = [e for e in body["events"] if e["kind"] == "CONTROL"]
check(len(ctl_events) == 3 and "beeper.enable by admin: accepted by the UPS" in [e["detail"] for e in ctl_events],
      "control actions recorded as events with the user")
ucore.nut_list_vars = lambda: {"ups.status": "OL", "ups.beeper.status": "enabled"}
code, body = webapi.handle({"action": "status"}, db, now=clock.t)
check(body["controls"]["available"] and body["controls"]["version"] == "1.2.0", "status reports controls available")
ctl_srv.shutdown(); ctl_srv.server_close(); os.unlink(ucore.CTL_SOCK)
code, body = webapi.handle({"action": "status"}, db, now=clock.t)
check(not body["controls"]["available"] and "v1.2.0" in body["controls"]["error"], "missing gatekeeper -> controls unavailable with reason")
code, body = webapi.handle({"action": "control"}, db, now=clock.t, method="POST", body='{"cmd":"beeper.enable"}', user="admin", same_origin=True)
check(code == 502, "control with no gatekeeper -> 502")

# ---- collector records beeper / test-result changes
con = ucore.db_open(db)
c4 = collector.Collector(con, health=lambda v, e: {"level": "ok", "checks": []}, clock=clock)
for beep, test in [("disabled", "No test initiated"), ("disabled", "No test initiated"), ("enabled", "In progress"), ("enabled", "Done and passed")]:
    st = ups("OL", 100); st["ups.beeper.status"] = beep; st["ups.test.result"] = test
    c4.query = lambda st=st: st
    c4.poll(); clock.t += 5
kinds = [(r["kind"], r["detail"]) for r in con.execute("SELECT kind, detail FROM events WHERE kind IN ('BEEPER','TEST_RESULT') ORDER BY id")]
check(kinds == [("BEEPER", "disabled → enabled"), ("TEST_RESULT", "No test initiated → In progress"), ("TEST_RESULT", "In progress → Done and passed")],
      "beeper/test changes recorded once each: %s" % kinds)
con.close()

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
