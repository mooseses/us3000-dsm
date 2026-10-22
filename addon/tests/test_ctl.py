"""Offline tests for us3000-ctl: a fake NUT driver socket and a fake upsd stand in
for the real ones, so nothing reaches a UPS. Run: python3 -I addon/tests/test_ctl.py"""

import importlib.util
import json
import os
import pwd
import socket
import socketserver
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
spec = importlib.util.spec_from_file_location("ctl", os.path.join(HERE, "..", "us3000-ctl.py"))
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

tmp = tempfile.mkdtemp()
ctl.SOCK = os.path.join(tmp, "ctl.sock")
ctl.DRIVER_SOCK = os.path.join(tmp, "driver.sock")
ctl.LOG = os.path.join(tmp, "ctl.log")
me = pwd.getpwuid(os.getuid()).pw_name
ctl.PKG_USER = me                       # the test runs as the "package user"

state = {"status": "OL", "cmds": set(ctl.ALLOWED) | {"load.off", "shutdown.return"},
         "result": {}, "driver_got": []}


class FakeUpsd(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode().strip()
        if line == "GET VAR ups ups.status":
            self.wfile.write(('VAR ups ups.status "%s"\n' % state["status"]).encode())
        elif line == "LIST CMD ups":
            out = "BEGIN LIST CMD ups\n" + "".join("CMD ups %s\n" % c for c in sorted(state["cmds"])) + "END LIST CMD ups\n"
            self.wfile.write(out.encode())


class FakeDriver(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline().decode().split()
        state["driver_got"].append(" ".join(line))
        # a real driver also broadcasts state updates; the gatekeeper must skip them
        self.wfile.write(b'SETINFO ups.status "OL"\n')
        if line[0] == "INSTCMD" and line[2] == "TRACKING":
            self.wfile.write(("TRACKING %s %d\n" % (line[3], state["result"].get(line[1], 0))).encode())


class TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


upsd = TCP(("127.0.0.1", 0), FakeUpsd)
ctl.UPSD = upsd.server_address
drv = socketserver.ThreadingUnixStreamServer(ctl.DRIVER_SOCK, FakeDriver)
for s in (upsd, drv):
    threading.Thread(target=s.serve_forever, daemon=True).start()
threading.Thread(target=ctl.serve, daemon=True).start()
for _ in range(50):
    if os.path.exists(ctl.SOCK):
        break
    time.sleep(0.05)


def call(req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(ctl.SOCK)
    s.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data)


fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


r = call({"op": "ping"})
check(r["ok"] and "beeper.enable" in r["allowed"] and "load.off" not in r["allowed"], "ping lists only the allow-list")
# There's no group named after the test user, i.e. the "companion not installed" case.
# (0660 + chown to the package group needs root, so it's checked on the NAS instead.)
check(oct(os.stat(ctl.SOCK).st_mode & 0o777) == "0o600", "socket is root-only (0600) while the package group is missing")

r = call({"op": "instcmd", "cmd": "beeper.enable"})
check(r["ok"] and r["result"] == "handled", "beeper.enable forwarded and handled")
check(state["driver_got"][-1].startswith("INSTCMD beeper.enable TRACKING "), "driver got INSTCMD with TRACKING")

n = len(state["driver_got"])
for bad in ["load.off", "shutdown.return", "driver.killpower", "beeper.enable; load.off", ""]:
    r = call({"op": "instcmd", "cmd": bad})
    check(not r["ok"] and "not allowed" in r["error"], "refused: %r" % bad)
r = call({"op": "set", "var": "output.voltage.nominal", "value": "19"})
check(not r["ok"], "SET of any variable refused")
check(len(state["driver_got"]) == n, "nothing refused ever reached the driver")

r = call({"op": "instcmd", "cmd": "test.battery.start.deep"})
check(not r["ok"] and "confirmation" in r["error"], "deep test needs confirm")
r = call({"op": "instcmd", "cmd": "test.battery.start.deep", "confirm": True})
check(r["ok"], "deep test with confirm forwarded")

state["status"] = "OB DISCHRG"
r = call({"op": "instcmd", "cmd": "test.battery.start.quick"})
check(not r["ok"] and "on battery" in r["error"], "battery test refused while on battery")
r = call({"op": "instcmd", "cmd": "beeper.mute"})
check(r["ok"], "beeper still allowed on battery")
state["status"] = "OL"

state["cmds"].discard("test.battery.stop")
r = call({"op": "instcmd", "cmd": "test.battery.stop"})
check(not r["ok"] and "doesn't offer" in r["error"], "command the UPS doesn't offer is refused")

state["result"]["beeper.disable"] = 3
r = call({"op": "instcmd", "cmd": "beeper.disable"})
check(not r["ok"] and r["result"] == "failed", "driver failure reported as failed")

ctl.PKG_USER = "nobody-such-user"   # now the caller is neither root nor the package user
r = call({"op": "instcmd", "cmd": "beeper.enable"})
check(not r["ok"] and "not permitted" in r["error"], "unknown caller refused")

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(ctl.SOCK)
s.sendall(b"not json\n")
s.recv(4096)
s.close()
ctl.PKG_USER = me
check(call({"op": "ping"})["ok"], "service survives garbage input")
check("load.off" in open(ctl.LOG).read(), "refusals are logged")

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
