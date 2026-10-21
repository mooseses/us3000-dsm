"""Shared code for the US3000 Companion collector and web API (Python 3.8)."""

import glob
import json
import os
import socket
import sqlite3
import subprocess
import time

PKG = "us3000-companion"
DEFAULT_DB = "/var/packages/%s/var/companion.db" % PKG
UPS_NAME = "ups"                    # DSM always names its UPS "ups"
UPSD = ("127.0.0.1", 3493)
RUNTIME_UNKNOWN = 65535             # what the US3000 reports on mains
RETENTION_DAYS = 30
WRAPPER = "/usr/bin/usbhid-ups"
WRAPPER_MARK = b"rr-addon:us3000 wrapper"
ADDON_LOG = "/var/log/rr-us3000.log"
AUTHENTICATE = "/usr/syno/synoman/webman/modules/authenticate.cgi"

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
  ts INTEGER PRIMARY KEY, status TEXT, charge REAL, runtime REAL,
  vin REAL, vout REAL, load REAL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  kind TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS outages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, start_ts INTEGER NOT NULL, end_ts INTEGER,
  start_charge REAL, min_charge REAL, end_charge REAL, runtime_start REAL,
  lb_ts INTEGER, lb_charge REAL, fsd_ts INTEGER, end_reason TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


# ---------------------------------------------------------------- NUT client

class NutError(Exception):
    pass


def nut_list_vars(ups=UPS_NAME, addr=UPSD, timeout=4.0):
    """Return {var: value} from upsd. Read-only; needs no login."""
    try:
        with socket.create_connection(addr, timeout=timeout) as s:
            f = s.makefile("rw", encoding="utf-8", newline="\n")
            f.write("LIST VAR %s\n" % ups)
            f.flush()
            out = {}
            while True:
                line = f.readline()
                if not line:
                    raise NutError("upsd closed the connection")
                line = line.rstrip("\n")
                if line.startswith("ERR "):
                    raise NutError(line[4:])
                if line.startswith("END LIST"):
                    break
                if line.startswith("VAR "):
                    # VAR ups battery.charge "100"
                    _, _, rest = line.partition(" ")
                    _, _, rest = rest.partition(" ")
                    name, _, val = rest.partition(" ")
                    out[name] = val.strip('"').replace('\\"', '"')
            f.write("LOGOUT\n")
            f.flush()
            return out
    except OSError as e:
        raise NutError(str(e))


def num(vars_, key):
    try:
        return float(vars_[key])
    except (KeyError, ValueError):
        return None


def runtime_of(vars_):
    r = num(vars_, "battery.runtime")
    return None if r is None or r >= RUNTIME_UNKNOWN else r


def flags_of(vars_):
    return set(vars_.get("ups.status", "").split())


# ------------------------------------------------------------------ database

def db_open(path, readonly=False):
    if readonly:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
    else:
        con = sqlite3.connect(path, timeout=10)
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def meta_get(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def meta_set(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                (key, json.dumps(value)))


# -------------------------------------------------------------- addon health

def _usb_ids():
    ids = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(os.path.join(d, "idVendor")) as f:
                v = f.read().strip()
            with open(os.path.join(d, "idProduct")) as f:
                p = f.read().strip()
            ids.append("%s:%s" % (v, p))
        except OSError:
            pass
    return ids


def _driver_cmdline():
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"usbhid-ups"):
            return [a.decode("utf-8", "replace") for a in args if a]
    return None


def health_check(vars_=None, comm_error=None):
    """What we can verify about the addon without root. Returns a dict with
    an overall level ('ok' | 'warning' | 'critical') and per-check rows."""
    checks = []

    def add(name, level, detail):
        checks.append({"name": name, "level": level, "detail": detail})

    ids = _usb_ids()
    ugreen = [i for i in ids if i.startswith("2b89:")]
    add("US3000 on USB", "ok" if ugreen else "critical",
        ", ".join(ugreen) if ugreen else "no 2b89:* device on the USB bus")

    try:
        with open(WRAPPER, "rb") as f:
            head = f.read(256)
        if WRAPPER_MARK in head:
            add("Addon wrapper", "ok", "%s is the us3000 wrapper" % WRAPPER)
        elif head.startswith(b"\x7fELF"):
            add("Addon wrapper", "critical",
                "%s is DSM's stock driver: the addon didn't run at boot "
                "(or a DSM update replaced it)" % WRAPPER)
        else:
            add("Addon wrapper", "warning", "%s is not recognised" % WRAPPER)
    except OSError as e:
        add("Addon wrapper", "warning", "can't read %s: %s" % (WRAPPER, e))

    cmd = _driver_cmdline()
    if cmd is None:
        add("Driver process", "critical", "no usbhid-ups process running")
    elif "us3000/" in cmd[0]:
        add("Driver process", "ok", cmd[0])
    else:
        add("Driver process", "critical",
            "%s is running instead of the NUT 2.8.5 driver" % cmd[0])

    if comm_error:
        add("upsd", "critical", "can't read the UPS from upsd: %s" % comm_error)
    elif vars_ is not None:
        add("upsd", "ok", "reachable on %s:%d" % UPSD)
        ver = vars_.get("driver.version", "?")
        sub = vars_.get("driver.parameter.subdriver", "?")
        add("Driver version", "ok" if ver.startswith("2.8.5") else "warning",
            "NUT %s, subdriver %s" % (ver, sub))
        locked = "driver.parameter.override.output.voltage.nominal" in vars_
        add("Voltage setting lock", "ok" if locked else "warning",
            "output.voltage.nominal is read-only" if locked
            else "output.voltage.nominal is NOT locked by the addon")
        lb = vars_.get("driver.flag.ignorelb") == "enabled"
        add("Low-battery logic", "ok" if lb else "warning",
            "LB derived from charge <= %s%%" % vars_.get("battery.charge.low", "?")
            if lb else "using the UPS's own LB flag (ignorelb is off)")

    log_tail = None
    try:
        with open(ADDON_LOG, "r", errors="replace") as f:
            log_tail = f.readlines()[-15:]
    except OSError:
        pass

    order = {"ok": 0, "warning": 1, "critical": 2}
    overall = max((c["level"] for c in checks), key=order.get, default="ok")
    return {"level": overall, "checks": checks, "addon_log": log_tail,
            "checked_at": int(time.time())}


# ------------------------------------------------------------------- web auth

def dsm_admin_user():
    """Return the logged-in DSM user if they are an administrator, else None.
    authenticate.cgi reads the session from the CGI environment we inherit."""
    try:
        user = subprocess.run([AUTHENTICATE], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=5,
                              check=False).stdout.decode().strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not user:
        return None
    try:
        with open("/etc/group") as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if parts[0] == "administrators" and len(parts) > 3:
                    if user in parts[3].split(","):
                        return user
    except OSError:
        pass
    return None
