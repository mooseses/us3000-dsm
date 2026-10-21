#!/bin/sh
# Packages addon/dist/us3000.addon for RR's "Upload a external addon".
# Needs addon/build/us3000-usbhid-ups from build-driver.sh.
# Layout: us3000.addon = tar.gz { manifest.yml, all.tgz = tar.gz { install.sh, us3000-usbhid-ups } }
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DRV="${HERE}/build/us3000-usbhid-ups"
[ -x "${DRV}" ] || { echo "missing ${DRV}: run build-driver.sh first" >&2; exit 1; }
sh -n "${HERE}/install.sh"

T="$(mktemp -d)"
mkdir -p "${T}/all" "${T}/out" "${HERE}/dist"
cp "${HERE}/install.sh" "${DRV}" "${T}/all/"
chmod 755 "${T}/all" "${T}/all/"*
cp "${HERE}/manifest.yml" "${T}/out/"
tar -czf "${T}/out/all.tgz" -C "${T}/all" .
tar -czf "${HERE}/dist/us3000.addon" -C "${T}/out" .
rm -rf "${T}"
echo "${HERE}/dist/us3000.addon"
