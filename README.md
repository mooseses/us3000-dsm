# UGREEN US3000 UPS on Synology DSM

Makes the **UGREEN US3000 DC UPS** work with DSM's built-in UPS support on a NAS
running DSM through the [RR loader](https://github.com/RROrg/rr), and adds a DSM
app that shows what DSM's own UPS page doesn't.

> Unofficial and not affiliated with UGREEN or Synology. A UPS decides when your
> NAS loses power: test it (pull the plug while watching) before you rely on it.

**Status.** Developed on a UGREEN DXP4800S (DSM 7.4.1-90080, RR 26.9.0) with a
US3000 reporting USB ID `2b89:ffff`. Tested so far:

- the addon's `install.sh` applied to a running DSM, and its install, uninstall
  and idempotency checked against a copy of DSM's files;
- DSM's UPS service with the 2.8.5 driver, including a real power cut (on
  battery, then back on mains);
- the companion package installed through Package Center.

**Not yet tested:** installing the addon through the RR menu and booting with it,
and a full low-battery shutdown.

| Part | What it is |
|---|---|
| [`addon/`](addon) | RR addon that gets DSM's UPS service to drive the US3000 |
| [`companion/`](companion) | DSM package: live status, outage history, battery-wear trend, addon health |

![US3000 Companion](docs/companion.png)
*(Screenshot with simulated data.)*

## Why an addon is needed

DSM 7.4 ships NUT 2.8.0. The US3000 is a standard HID power device, but NUT 2.8.0
has no matching subdriver for its vendor ID (`2b89`), and it has no `subdriver`
option to force one. DSM also only starts its UPS service for USB IDs on its own
whitelist. The addon:

1. bundles a static **NUT 2.8.5** `usbhid-ups` built with DSM's paths;
2. wraps DSM's `/usr/bin/usbhid-ups` so the US3000 gets the 2.8.5 driver and any
   other UPS still gets DSM's own driver;
3. adds `2b89:ffff` and `2b89:3245` to DSM's UPS whitelist
   (`/lib/udev/devicetable/usb.nut-hid`) and driver table (`/etc/ups/nutscan-usb.h`).

It reapplies itself on every boot, so it survives DSM updates. Uninstalling it
restores every file it touched.

### Driver settings and why

- **`subdriver=MGE`.** UGREEN's own NAS OS (UGOS) drives the US3000 with NUT plus
  a "TOP HID" subdriver. Its mapping table is identical to NUT's MGE subdriver,
  so MGE exposes everything UGOS sees, including the beeper and battery-test
  commands.
- **`ignorelb` + `battery.charge.low=20`.** UGOS ignores the UPS's own low-battery
  flag too, and derives it from the charge percentage instead.
- **Read-only overrides.** `output.voltage.nominal` is locked, because UGOS uses it
  to *set the DC output voltage*, and a wrong value could damage the NAS.
  `input.transfer.*` (AC-only, meaningless on a DC unit) and the model name
  (the firmware returns a garbled suffix) are locked as well.

### US3000 quirks

- It reports `DISCHRG` even on mains with a full battery. Harmless; never set
  `onlinedischarge`, or DSM would treat that as a power cut.
- `battery.runtime` is `65535` ("unknown") on mains. It only estimates runtime
  on battery.
- The beeper is disabled by default (`ups.beeper.status: disabled`).
- There are two hardware suppliers: "TB" (`2b89:ffff`) and "JQH" (`2b89:3245`,
  called "UC300" in UGOS). Only the TB variant has been tested.

## Install

### 1. The addon

1. Download `us3000.addon` from the [latest release](../../releases/latest).
2. In the RR menu: **Addons → Upload a external addon**, choose the file.
3. Enable `us3000` under **Add an addon**, rebuild the loader and boot.
4. In DSM: **Control Panel → Hardware & Power → UPS**, tick **Enable UPS support**
   with type **USB UPS**.
5. Check `/var/log/rr-us3000.log`, then pull the UPS's mains plug and watch
   DSM report the switch to battery.

Optional addon parameters are passed to the driver, for example `-x pollinterval=5`.

### 2. The companion app (optional)

1. Download `us3000-companion-<version>-noarch.spk` from the release.
2. **Package Center → Settings → Trust level → Any publisher** (the package is
   unsigned).
3. **Package Center → Manual Install**, choose the `.spk`.
4. Open **US3000 Companion** from the main menu (administrators only).

The app is read-only: it reads DSM's `upsd` and never sends commands to the UPS.
It needs no extra packages (it uses DSM's built-in Python 3.8). Raw readings are
kept for 30 days and outage records are kept forever.

## Build from source

```sh
# Addon: the driver needs an Alpine environment for a fully static musl build
docker run --rm -v "$PWD":/w -w /w alpine:3.24 sh addon/build-driver.sh
sh addon/package.sh                       # -> addon/dist/us3000.addon

# Companion: run on DSM (or anywhere with python3, tar and md5sum)
sh companion/build.sh                     # -> companion/dist/*.spk
python3 -I companion/tests/test_backend.py
```

Rebuilt drivers are functionally identical but not byte-identical to the
released one (the compiler records build paths).

DSM loads `<app folder>/style.css` from **every** running package into its own
desktop page. That's why the companion's stylesheet is called `app.css`, and why
`build.sh` refuses to build if a `style.css` appears.

## Licence

MIT for this repository's code (see [LICENSE](LICENSE)). The `usbhid-ups` binary
inside the released `us3000.addon` is
[Network UPS Tools](https://github.com/networkupstools/nut) 2.8.5, licensed
GPL-2.0-or-later. It's built unmodified from the upstream release tarball by
[`addon/build-driver.sh`](addon/build-driver.sh).
