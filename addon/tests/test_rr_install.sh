#!/bin/sh
# End-to-end test of the packaged addon through RR's own install code:
#   RR installAddon (verbatim from RROrg/rr) -> /addons/us3000.sh late -> checks -> uninstall.
# Uses a fake DSM root built from copies of this system's files; touches nothing live.
# Usage: sh addon/tests/test_rr_install.sh [path/to/us3000.addon] [path/to/rr/addons.sh]
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ADDON="${1:-${HERE}/dist/us3000.addon}"
RR_ADDONS_SH="${2:-}"
T="$(mktemp -d "${TMPDIR:-/tmp}/us3000-rrtest.XXXXXX")"
FAILS=0
check() { if eval "$1"; then echo "PASS $2"; else echo "FAIL $2"; FAILS=$((FAILS + 1)); fi; }

if [ -z "${RR_ADDONS_SH}" ]; then
  RR_ADDONS_SH="${T}/addons.sh"
  curl -fsSL -o "${RR_ADDONS_SH}" https://raw.githubusercontent.com/RROrg/rr/main/files/initrd/opt/rr/include/addons.sh || exit 2
fi

# A fake DSM root. Prefer DSM's untouched originals (.bak-us3000 / .real) when present.
mkroot() {
  R="$1"
  mkdir -p "$R/usr/bin" "$R/lib/udev/devicetable" "$R/etc/ups" "$R/etc.defaults/ups"
  for f in /usr/bin/usbhid-ups.real /usr/bin/usbhid-ups.bak-us3000 /usr/bin/usbhid-ups; do
    if [ -f "$f" ] && ! grep -q "rr-addon:us3000" "$f"; then cp -p "$f" "$R/usr/bin/usbhid-ups"; break; fi
  done
  for f in /lib/udev/devicetable/usb.nut-hid.bak-us3000 /lib/udev/devicetable/usb.nut-hid; do
    [ -f "$f" ] && { grep -v -i "0x2b89" "$f" >"$R/lib/udev/devicetable/usb.nut-hid"; break; }
  done
  printf '\t{ 0x0463, 0x0001, "usbhid-ups" },\n' >"$R/etc/ups/nutscan-usb.h"
  cp "$R/etc/ups/nutscan-usb.h" "$R/etc.defaults/ups/"
}
sums() { (cd "$1" && find . -type f ! -path ./var/log/rr-us3000.log -exec sha256sum {} \; | sort -k2); }

# RR: upload (untarAddon) then installAddon into a fake ramdisk
mkdir -p "${T}/store/us3000" "${T}/tmp" "${T}/ramdisk/addons"
tar -xaf "${ADDON}" -C "${T}/store/us3000"
{
  echo 'TMP_PATH="$1"; ADDONS_PATH="$2"; RAMDISK_PATH="$3"; LOG_FILE="$4"'
  sed -n '/^function installAddon() {/,/^}/p' "${RR_ADDONS_SH}"
  echo 'installAddon us3000 epyc7002 5.10.55'
} >"${T}/rr.sh"
bash "${T}/rr.sh" "${T}/tmp" "${T}/store" "${T}/ramdisk" "${T}/rr.log"
check '[ $? -eq 0 ] && [ -x "${T}/ramdisk/addons/us3000.sh" ]' "RR installAddon put us3000.sh in /addons"
check '[ -f "${T}/ramdisk/addons/us3000-usbhid-ups" ] && [ -f "${T}/ramdisk/addons/us3000-ctl.py" ]' "driver and us3000-ctl.py reach /addons"

# Boot: late stage against the fake root
mkroot "${T}/root"; sums "${T}/root" >"${T}/before"
RR_TMPROOT="${T}/root" sh "${T}/ramdisk/addons/us3000.sh" late >/dev/null
R="${T}/root"
check 'grep -q "rr-addon:us3000 wrapper" "$R/usr/bin/usbhid-ups"' "wrapper installed"
check 'cmp -s "$R/usr/lib/us3000/usbhid-ups" "${T}/ramdisk/addons/us3000-usbhid-ups"' "driver installed"
check 'grep -q "override.input.transfer.low=0" "$R/usr/bin/usbhid-ups"' "wrapper has the current locks"
check '[ -x "$R/usr/lib/us3000/us3000-ctl.py" ] && grep -q "ExecStart=/bin/python3 -I /usr/lib/us3000/us3000-ctl.py" "$R/usr/lib/systemd/system/us3000-ctl.service"' "control service installed"
check '[ "$(readlink "$R/usr/lib/systemd/system/multi-user.target.wants/us3000-ctl.service")" = /usr/lib/systemd/system/us3000-ctl.service ]' "control service enabled"
check 'grep -q "^result=ok$" "$R/var/lib/us3000/status" && grep -q "^boot_id=$(cat /proc/sys/kernel/random/boot_id)$" "$R/var/lib/us3000/status"' "status file: result=ok with this boot's ID"
check '[ "$(stat -c %a "$R/var/lib/us3000/status")" = 644 ] && [ "$(stat -c %a "$R/var/log/rr-us3000.log")" = 644 ]' "status file and log readable by the companion"
check '[ "$(grep -c -i 0x2b89 "$R/lib/udev/devicetable/usb.nut-hid")" = 2 ]' "whitelist patched"

RR_TMPROOT="${T}/root" sh "${T}/ramdisk/addons/us3000.sh" late >/dev/null
check '[ "$(grep -c -i 0x2b89 "$R/lib/udev/devicetable/usb.nut-hid")" = 2 ]' "second boot is idempotent"

RR_TMPROOT="${T}/root" sh "${T}/ramdisk/addons/us3000.sh" uninstall >/dev/null
sums "${T}/root" >"${T}/after"
check 'diff -q "${T}/before" "${T}/after" >/dev/null' "uninstall restores the fake root byte-for-byte"

# A broken package (driver missing) must be recorded as an error, not just logged
rm -f "${T}/ramdisk/addons/us3000-usbhid-ups"; mkroot "${T}/root2"
RR_TMPROOT="${T}/root2" sh "${T}/ramdisk/addons/us3000.sh" late >/dev/null
check 'grep -q "^result=error$" "${T}/root2/var/lib/us3000/status" && grep -q "driver us3000-usbhid-ups not found" "${T}/root2/var/lib/us3000/status"' "missing driver recorded as result=error"

rm -rf "${T}"
echo; echo "${FAILS} failure(s)"
[ "${FAILS}" -eq 0 ]
