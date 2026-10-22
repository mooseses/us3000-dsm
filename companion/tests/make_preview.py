"""Builds tests/preview/index.html: the real UI with a fetch() stub serving API
responses computed from 30 days of simulated data. For visual checks only."""

import json
import math
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "package", "lib"))
import ucore   # noqa: E402
import webapi  # noqa: E402

random.seed(4)
db = os.path.join(tempfile.mkdtemp(), "p.db")
con = ucore.db_open(db)
now = 1792612800
start = now - 30 * 86400
outs = [(start + d * 86400 + h * 3600, mins, rate) for d, h, mins, rate in
        [(3, 14, 6, 1.6), (9, 2, 22, 1.65), (16, 19, 3, 1.7), (24, 8, 14, 1.75), (29, 12, 9, 1.8)]]
ev = [(start, "COLLECTOR_START", "")]
t = start
charge = 100.0
while t <= now:
    ob = next((o for o in outs if o[0] <= t < o[0] + o[1] * 60), None)
    if ob:
        charge = max(5, 100 - (t - ob[0]) / 60 * ob[2])
        st, vin = "OB DISCHRG", 0.0
    else:
        charge = min(100, charge + 0.4)
        st, vin = "OL DISCHRG" + (" CHRG" if charge < 100 else ""), 12.0 + random.uniform(-0.05, 0.05)
    con.execute("INSERT INTO samples VALUES(?,?,?,?,?,?,?)",
                (t, st, round(charge), None, vin, 12.0, 1 + (1 if math.sin(t / 7200) > 0.9 else 0)))
    t += 300 if not ob else 30
for o in outs:
    end = o[0] + o[1] * 60
    mn = 100 - o[1] * o[2]
    lb = o[0] + 9 * 60 if o[1] >= 14 else None
    con.execute("INSERT INTO outages(start_ts,end_ts,start_charge,min_charge,end_charge,runtime_start,lb_ts,lb_charge,end_reason) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (o[0], end, 100, mn, mn, 900, lb, 80 if lb else None,
                                              "nas_down" if o[1] == 22 else "power_restored"))
    ev += [(o[0], "ON_BATTERY", "charge 100%")]
    if lb:
        ev.append((lb, "LOW_BATTERY", "charge 80%, 9 min on battery"))
    ev.append((end, "ON_MAINS" if o[1] != 22 else "OUTAGE_CLOSED",
               "after %dm 00s, charge %d%%" % (o[1], mn) if o[1] != 22 else "NAS went down while on battery"))
con.executemany("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", ev)
ucore.meta_set(con, "last_poll", now - 3)
ucore.meta_set(con, "health", {"level": "warning", "checked_at": now - 20, "addon_log": None, "checks": [
    {"name": "US3000 on USB", "level": "ok", "detail": "2b89:ffff"},
    {"name": "Addon at this boot", "level": "ok", "detail": "v1.2.0 ran at this boot (late mode, 2026-10-21 19:58:02)"},
    {"name": "Addon wrapper", "level": "ok", "detail": "/usr/bin/usbhid-ups is the us3000 wrapper"},
    {"name": "Driver process", "level": "ok", "detail": "/usr/lib/us3000/usbhid-ups"},
    {"name": "Driver version", "level": "ok", "detail": "NUT 2.8.5, subdriver MGE"},
    {"name": "Voltage setting lock", "level": "ok", "detail": "output.voltage.nominal is read-only"},
    {"name": "Low-battery logic", "level": "warning", "detail": "using the UPS's own LB flag (ignorelb is off)"},
    {"name": "Control service", "level": "ok", "detail": "us3000-ctl v1.2.0 (beeper, battery test)"}]})
con.commit()

ucore.nut_list_vars = lambda: {"ups.status": "OL DISCHRG", "battery.charge": "100", "battery.charge.low": "20",
                               "battery.runtime": "65535", "input.voltage": "12.0", "output.voltage": "12.0",
                               "ups.load": "1", "ups.beeper.status": "disabled", "device.mfr": "UGREEN",
                               "device.model": "US3000", "device.serial": "SN00000", "driver.version": "2.8.5",
                               "driver.parameter.subdriver": "MGE", "ups.test.result": "Done and passed"}
ucore.ctl_call = lambda req, timeout=20.0, path=None: {
    "ok": True, "version": "1.2.0", "allowed": sorted(webapi.CONTROL_CMDS)}
resp = {"status": webapi.handle({"action": "status"}, db, now)[1],
        "outages": webapi.handle({"action": "outages"}, db, now)[1],
        "events": webapi.handle({"action": "events", "limit": "80"}, db, now)[1]}
for r in webapi.RANGES:
    resp["history:" + r] = webapi.handle({"action": "history", "range": r}, db, now)[1]

app = os.path.join(HERE, "..", "package", "app")
html = open(os.path.join(app, "index.html")).read()
stub = ("<script>window.__R=%s;window.fetch=function(u){var p=new URLSearchParams(u.split('?')[1]);"
        "var k=p.get('action')==='history'?'history:'+p.get('range'):p.get('action');"
        "return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(window.__R[k]);}});};"
        "Date.now=function(){return %d000};</script>" % (json.dumps(resp), now))
html = html.replace('<link rel="stylesheet" href="app.css">',
                    "<style>%s</style>" % open(os.path.join(app, "app.css")).read())
html = html.replace('<script src="app.js"></script>', stub + "<script>%s</script>" % open(os.path.join(app, "app.js")).read())
out = os.path.join(HERE, "preview")
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "index.html"), "w").write(html)
print(os.path.join(out, "index.html"))
