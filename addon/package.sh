#!/bin/sh
# Packages addon/dist/us3000.addon for RR's "Upload a external addon".
# Needs addon/build/us3000-usbhid-ups from build-driver.sh.
# Layout: us3000.addon = tar.gz { manifest.yml, all.tgz = tar.gz { install.sh, root/addons/{us3000-usbhid-ups,us3000-ctl.py} } }
# RR's installAddon copies install.sh to /addons/us3000.sh and only the root/ tree into the
# ramdisk (everything else in all.tgz is dropped), so the driver must live under root/.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DRV="${HERE}/build/us3000-usbhid-ups"
[ -x "${DRV}" ] || { echo "missing ${DRV}: run build-driver.sh first" >&2; exit 1; }
sh -n "${HERE}/install.sh"
python3 -I -m py_compile "${HERE}/us3000-ctl.py" && rm -rf "${HERE}/__pycache__"

T="$(mktemp -d)"
mkdir -p "${T}/all/root/addons" "${T}/out" "${HERE}/dist"
cp "${HERE}/install.sh" "${T}/all/"
cp "${DRV}" "${T}/all/root/addons/us3000-usbhid-ups"
cp "${HERE}/us3000-ctl.py" "${T}/all/root/addons/us3000-ctl.py"
find "${T}/all" -type d -exec chmod 755 {} +
chmod 755 "${T}/all/install.sh" "${T}/all/root/addons/"*
cp "${HERE}/manifest.yml" "${T}/out/"
tar -czf "${T}/out/all.tgz" -C "${T}/all" .
tar -czf "${HERE}/dist/us3000.addon" -C "${T}/out" .
rm -rf "${T}"
echo "${HERE}/dist/us3000.addon"
