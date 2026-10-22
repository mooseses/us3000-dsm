#!/usr/bin/env sh
#
# rr addon: us3000
# Makes DSM's NUT stack drive the UGREEN US3000 DC UPS.
#
# DSM 7.4's bundled NUT (2.8.0) has the Arduino HID subdriver but no
# 'subdriver' option, so it can't be told to use it for 2b89:*. This addon
# ships a static NUT 2.8.5 usbhid-ups (built with DSM's paths: confpath
# /etc/ups, statepath /run/ups_state, pidpath /var/run, user root) and:
#
#   1. Copies it to /usr/lib/us3000/usbhid-ups (basename kept, so the driver
#      socket name and DSM's "is the driver alive" ps check still match).
#   2. Moves DSM's /usr/bin/usbhid-ups to usbhid-ups.real and puts a wrapper
#      in its place. If a 2b89:* device is present the wrapper runs the 2.8.5
#      driver with the MGE subdriver (UGOS's "TOP HID" mapping), ignorelb with
#      battery.charge.low=20, and read-only overrides for the garbled model
#      string, output.voltage.nominal and the (AC-only) input.transfer.*
#      thresholds; otherwise it runs DSM's driver
#      untouched, so other UPSes still work.
#   3. Adds 2b89:ffff and 2b89:3245 to the udev UPS whitelist
#      (/lib/udev/devicetable/usb.nut-hid, read by ups-check.sh) and to the
#      driver table (/etc/ups/nutscan-usb.h, read by ups-usb.sh).
#
# Runs at the "late" stage on every boot and is idempotent, so a DSM update
# that replaces any of these files is re-patched on the next boot.
#
# Without RR (genuine Synology, or any DSM without the loader), run it as root
# from a Task Scheduler "Boot-up" task instead:  sh install.sh boot
# That patches the running system rather than /tmpRoot. Boot-up tasks run after
# DSM's udev has already rejected the UPS, so boot mode then replays the USB
# "add" event for 2b89:* devices. "sh install.sh boot-uninstall" undoes it.
#
# Licensed under the MIT License. The bundled usbhid-ups is NUT 2.8.5 (GPL-2.0+).

case "${1}" in
  boot | boot-uninstall) DEFAULT_ROOT="" ;;   # the running system
  *) DEFAULT_ROOT="/tmpRoot" ;;               # RR's view of DSM at the late stage
esac
ROOT="${RR_TMPROOT:-${DEFAULT_ROOT}}"   # override only for testing
HERE="$(cd "$(dirname "${0}")" && pwd)"
VID="2b89"
PIDS="ffff 3245"
MARK="rr-addon:us3000 wrapper"
DRV="/usr/bin/usbhid-ups"
NEWDIR="/usr/lib/us3000"
NEWDRV="${NEWDIR}/usbhid-ups"
NUTHID="/lib/udev/devicetable/usb.nut-hid"
SCAN_TABLES="/etc/ups/nutscan-usb.h /etc.defaults/ups/nutscan-usb.h"
LOG="${ROOT}/var/log/rr-us3000.log"

log() {
  echo "us3000: $*"
  mkdir -p "$(dirname "${LOG}")" 2>/dev/null
  echo "$(date '+%F %T') install: $*" >>"${LOG}" 2>/dev/null
  chmod 644 "${LOG}" 2>/dev/null   # readable by the US3000 Companion package
}

# Exact lines we add, so uninstall removes only these.
nuthid_line() {
  echo "libhidups      0x0003      0x${VID}   0x${1}    0x0000       0x0000       0x00         0x00            0x00            0x00            0x00               0x00               0x00000000"
}
scan_line() {
  printf '\t{ 0x%s, 0x%s, "usbhid-ups" },\n' "${VID}" "${1}"
}

write_wrapper() {
  # $1 = extra driver options from addon params
  cat >"${ROOT}${DRV}" <<EOF
#!/bin/sh
# ${MARK}
REAL="${DRV}.real"
NEW="${NEWDRV}"
EXTRA="${1}"
LOG="/var/log/rr-us3000.log"
SYSFS="\${US3000_SYSFS:-/sys}"

PID=""
for D in "\${SYSFS}"/bus/usb/devices/*; do
  [ -f "\${D}/idVendor" ] || continue
  if [ "\$(cat "\${D}/idVendor")" = "${VID}" ]; then
    PID="\$(cat "\${D}/idProduct")"
    break
  fi
done

if [ -z "\${PID}" ] || [ ! -x "\${NEW}" ]; then
  exec "\${REAL}" "\$@"
fi

echo "\$(date '+%F %T') wrapper: US3000 (${VID}:\${PID}) found, args: \$*" >>"\${LOG}"
# MGE is the mapping UGOS's own "TOP HID" subdriver uses. Like UGOS, ignore the
# UPS's LB flag and derive LB from battery.charge. The overrides also make these
# variables read-only in upsd; output.voltage.nominal must never be written.
# shellcheck disable=SC2086
exec "\${NEW}" "\$@" -x subdriver=MGE -x vendorid=${VID} -x productid="\${PID}" -x pollonly \\
  -x ignorelb -x override.battery.charge.low=20 \\
  -x override.device.model=US3000 -x override.ups.model=US3000 \\
  -x override.output.voltage.nominal=12 \\
  -x override.input.transfer.high=264 -x override.input.transfer.low=0 \${EXTRA}
EOF
  chmod 755 "${ROOT}${DRV}"
}

# The bundled driver: RR copies the addon's root/ tree into the ramdisk, so it
# arrives as /addons/us3000-usbhid-ups next to this script (/addons/us3000.sh).
# Unpacked by hand for boot mode it may still sit under root/addons/.
bundled_driver() {
  for F in "${HERE}/us3000-usbhid-ups" "${HERE}/root/addons/us3000-usbhid-ups"; do
    [ -f "${F}" ] && { echo "${F}"; return 0; }
  done
  return 1
}

install_driver() {
  SRC="$(bundled_driver)" || {
    log "ERROR: bundled driver us3000-usbhid-ups not found next to ${HERE}, nothing installed"
    return 1
  }
  mkdir -p "${ROOT}${NEWDIR}" && chmod 755 "${ROOT}${NEWDIR}" &&
    cp -f "${SRC}" "${ROOT}${NEWDRV}" &&
    chmod 755 "${ROOT}${NEWDRV}" || return 1
  log "installed NUT 2.8.5 driver at ${NEWDRV}"

  if [ ! -f "${ROOT}${DRV}" ]; then
    log "${DRV} not found in DSM, nothing to wrap"
    return 1
  fi
  if grep -q "${MARK}" "${ROOT}${DRV}" 2>/dev/null; then
    [ -f "${ROOT}${DRV}.real" ] || {
      log "wrapper at ${DRV} but ${DRV}.real is missing, refusing to touch it"
      return 1
    }
    log "wrapper already in place at ${DRV}, refreshing it"
  else
    # Fresh install, or a DSM update dropped in a new real binary.
    mv -f "${ROOT}${DRV}" "${ROOT}${DRV}.real" || return 1
    log "moved DSM driver to ${DRV}.real"
  fi
  write_wrapper "${1}"
}

# $1 = file, $2 = line to add before the first line matching $3 (or append)
add_line() {
  if grep -qxF "${2}" "${ROOT}${1}"; then
    return 0
  fi
  awk -v line="${2}" -v anchor="${3}" '
    !done && anchor != "" && index($0, anchor) { print line; done = 1 }
    { print }
    END { if (!done) print line }
  ' "${ROOT}${1}" >"${ROOT}${1}.us3000.tmp" &&
    cat "${ROOT}${1}.us3000.tmp" >"${ROOT}${1}" &&
    rm -f "${ROOT}${1}.us3000.tmp" &&
    log "${1}: added ${VID} entry"
}

patch_tables() {
  if [ -f "${ROOT}${NUTHID}" ]; then
    for P in ${PIDS}; do
      add_line "${NUTHID}" "$(nuthid_line "${P}")" ""
    done
  else
    log "WARNING: ${NUTHID} not found, DSM's udev rule won't start UPS support for the US3000"
  fi
  for F in ${SCAN_TABLES}; do
    [ -f "${ROOT}${F}" ] || continue
    for P in ${PIDS}; do
      add_line "${F}" "$(scan_line "${P}")" '"usbhid-ups"'
    done
  done
}

# $1 = file, $2 = exact line to remove
remove_line() {
  [ -f "${ROOT}${1}" ] && grep -qxF "${2}" "${ROOT}${1}" || return 0
  grep -vxF "${2}" "${ROOT}${1}" >"${ROOT}${1}.us3000.tmp"
  cat "${ROOT}${1}.us3000.tmp" >"${ROOT}${1}" && rm -f "${ROOT}${1}.us3000.tmp"
  log "${1}: removed ${VID} entry"
}

uninstall_all() {
  if grep -q "${MARK}" "${ROOT}${DRV}" 2>/dev/null && [ -f "${ROOT}${DRV}.real" ]; then
    mv -f "${ROOT}${DRV}.real" "${ROOT}${DRV}"
    log "restored DSM ${DRV}"
  fi
  rm -rf "${ROOT}${NEWDIR}"
  for P in ${PIDS}; do
    remove_line "${NUTHID}" "$(nuthid_line "${P}")"
    for F in ${SCAN_TABLES}; do
      remove_line "${F}" "$(scan_line "${P}")"
    done
  done
  rm -f "${ROOT}/usr/rr/addons/us3000.sh" "${ROOT}/usr/rr/addons/us3000-usbhid-ups"
  log "uninstalled"
}

# Boot mode only: DSM's udev checked the UPS against its whitelist before we
# patched it, so ask udev to send the "add" event again for 2b89:* devices.
replay_udev() {
  SYSFS="${US3000_SYSFS:-/sys}"
  FOUND=""
  for D in "${SYSFS}"/bus/usb/devices/*; do
    [ -f "${D}/idVendor" ] || continue
    [ "$(cat "${D}/idVendor")" = "${VID}" ] || continue
    NAME="$(basename "${D}")"
    FOUND="${NAME}"
    if udevadm trigger --action=add --subsystem-match=usb --sysname-match="${NAME}"; then
      log "replayed USB add event for ${NAME} ($(cat "${D}/idVendor"):$(cat "${D}/idProduct"))"
    else
      log "WARNING: udevadm trigger failed for ${NAME}; replug the UPS's USB cable"
    fi
  done
  [ -n "${FOUND}" ] || log "no ${VID}:* device on USB, nothing to replay"
}

case "${1}" in
  boot)
    shift
    log "installing (boot mode, params: ${*:-none})"
    RC=0
    install_driver "$*" || RC=1
    patch_tables
    replay_udev
    exit "${RC}"
    ;;
  boot-uninstall)
    uninstall_all
    ;;
  late)
    shift
    log "installing (params: ${*:-none})"
    # Keep a copy in DSM so uninstall works the same way as RR's own addons.
    mkdir -p "${ROOT}/usr/rr/addons"
    cp -pf "${0}" "${ROOT}/usr/rr/addons/us3000.sh" 2>/dev/null
    install_driver "$*"
    patch_tables
    ;;
  uninstall)
    uninstall_all
    ;;
esac
exit 0
