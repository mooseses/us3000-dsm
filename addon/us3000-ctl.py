#!/bin/python3
"""us3000-ctl: root gatekeeper between the US3000 Companion app and the NUT driver.

The driver's control socket accepts any INSTCMD/SET and its HID write path ignores
the addon's read-only overrides, so it must never be opened to unprivileged users.
This service is the only way in: it accepts a fixed allow-list of commands from
root or the us3000-companion package user, checks them, forwards them to the
driver and reports the driver's result. Everything else is refused and logged.

Request (one JSON line):  {"op": "ping"}
                          {"op": "instcmd", "cmd": "beeper.enable"}
                          {"op": "instcmd", "cmd": "test.battery.start.deep", "confirm": true}
Reply (one JSON line):    {"ok": true|false, ...}
"""

import grp
import json
import os
import pwd
import socket
import struct
import sys
import time
import uuid

VERSION = "1.2.0"
SOCK = "/run/us3000-ctl.sock"
DRIVER_SOCK = "/run/ups_state/usbhid-ups-ups"
UPSD = ("127.0.0.1", 3493)
LOG = "/var/log/us3000-ctl.log"
PKG_USER = "us3000-companion"

# The only commands that may reach the UPS. Everything that cuts power
# (load.off, shutdown.*, driver.killpower) and every SET is deliberately absent.
ALLOWED = {
    "beeper.enable": {},
    "beeper.disable": {},
    "beeper.mute": {},
    "test.battery.start.quick": {"mains_only": True},
    "test.battery.start.deep": {"mains_only": True, "confirm": True},
    "test.battery.stop": {},
}
RESULT = {0: "handled", 1: "unknown error", 2: "invalid command", 3: "failed", 4: "conversion failed"}


def log(msg):
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        os.chmod(LOG, 0o644)
    except OSError:
        pass


def upsd_query(line, end):
    with socket.create_connection(UPSD, timeout=4) as s:
        f = s.makefile("rw", encoding="utf-8", newline="\n")
        f.write(line + "\n")
        f.flush()
        out = []
        while True:
            l = f.readline()
            if not l:
                break
            l = l.rstrip("\n")
            out.append(l)
            if l.startswith(end) or l.startswith("ERR"):
                break
        return out


def ups_status():
    for l in upsd_query("GET VAR ups ups.status", "VAR"):
        if l.startswith("VAR "):
            return l.split('"')[1]
    return ""


def ups_cmds():
    return {l.split()[2] for l in upsd_query("LIST CMD ups", "END LIST") if l.startswith("CMD ")}


def driver_instcmd(cmd):
    """Send INSTCMD with TRACKING and wait for the driver's verdict."""
    tid = uuid.uuid4().hex[:12]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    try:
        s.connect(DRIVER_SOCK)
        s.sendall(("INSTCMD %s TRACKING %s\n" % (cmd, tid)).encode())
        buf = b""
        deadline = time.time() + 15
        while time.time() < deadline:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            for line in buf.decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) == 3 and parts[0] == "TRACKING" and parts[1] == tid:
                    return int(parts[2])
        return None
    finally:
        s.close()


def handle(req):
    op = req.get("op")
    if op == "ping":
        return {"ok": True, "version": VERSION, "allowed": sorted(ALLOWED)}
    if op != "instcmd":
        return {"ok": False, "error": "unknown op"}
    cmd = req.get("cmd")
    rule = ALLOWED.get(cmd)
    if rule is None:
        return {"ok": False, "error": "command not allowed: %s" % cmd}
    if rule.get("confirm") and req.get("confirm") is not True:
        return {"ok": False, "error": "%s needs confirmation" % cmd}
    try:
        if cmd not in ups_cmds():
            return {"ok": False, "error": "the UPS driver doesn't offer %s" % cmd}
        if rule.get("mains_only") and "OB" in ups_status().split():
            return {"ok": False, "error": "refused: the UPS is on battery"}
    except OSError as e:
        return {"ok": False, "error": "upsd unreachable: %s" % e}
    try:
        code = driver_instcmd(cmd)
    except OSError as e:
        return {"ok": False, "error": "driver unreachable: %s" % e}
    if code is None:
        return {"ok": False, "error": "no answer from the driver"}
    return {"ok": code == 0, "code": code, "result": RESULT.get(code, str(code))}


def allowed_uids():
    uids = {0}
    try:
        uids.add(pwd.getpwnam(PKG_USER).pw_uid)
    except KeyError:
        pass
    return uids


def set_socket_access():
    """root:us3000-companion 0660 once the package exists, root-only until then."""
    try:
        gid = grp.getgrnam(PKG_USER).gr_gid
    except KeyError:
        os.chmod(SOCK, 0o600)
        return False
    os.chown(SOCK, 0, gid)
    os.chmod(SOCK, 0o660)
    return True


def serve():
    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    shared = set_socket_access()
    srv.listen(4)
    srv.settimeout(60)
    log("started v%s, socket %s (%s)" % (VERSION, SOCK, "shared with %s" % PKG_USER if shared else "root only"))
    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            if not shared:   # the companion may have been installed since we started
                shared = set_socket_access()
            continue
        conn.settimeout(20)
        try:
            pid, uid, gid = struct.unpack("3i", conn.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            data = b""
            while not data.endswith(b"\n") and len(data) < 4096:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            try:
                req = json.loads(data.decode() or "{}")
                if not isinstance(req, dict):
                    raise ValueError
            except ValueError:
                req = {}
            if uid not in allowed_uids():
                reply = {"ok": False, "error": "caller uid %d not permitted" % uid}
            else:
                reply = handle(req)
            if req.get("op") != "ping":
                log("uid=%d pid=%d %s -> %s" % (uid, pid, json.dumps(req), json.dumps(reply)))
            conn.sendall((json.dumps(reply) + "\n").encode())
        except Exception as e:   # one bad client must not stop the service
            log("error: %s: %s" % (type(e).__name__, e))
        finally:
            conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(VERSION)
        sys.exit(0)
    serve()
