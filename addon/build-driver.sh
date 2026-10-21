#!/bin/sh
# Builds the static NUT 2.8.5 usbhid-ups bundled in us3000.addon.
# Run inside Alpine Linux (musl gives a truly static binary), e.g.:
#   docker run --rm -v "$PWD":/w -w /w alpine:3.24 sh addon/build-driver.sh
# Output: addon/build/us3000-usbhid-ups
set -e
NUT_VERSION=2.8.5
NUT_SHA256=18bf32e59eb764b13da3c4fa70384926d7fa584cb31d2fe7f137a570633eeec1
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${HERE}/build"
WORK="$(mktemp -d)"

apk add -q build-base linux-headers libusb-dev pkgconf perl python3 curl
cd "${WORK}"
curl -sSLO "https://github.com/networkupstools/nut/releases/download/v${NUT_VERSION}/nut-${NUT_VERSION}.tar.gz"
echo "${NUT_SHA256}  nut-${NUT_VERSION}.tar.gz" | sha256sum -c -
tar -xzf "nut-${NUT_VERSION}.tar.gz"
cd "nut-${NUT_VERSION}"

# Paths match DSM 7.4's own NUT so the driver finds ups.conf and DSM's upsd finds its socket.
./configure --prefix=/usr --sysconfdir=/etc/ups --with-statepath=/run/ups_state \
  --with-altpidpath=/var/run --with-pidpath=/var/run --with-drvpath=/usr/bin \
  --with-user=root --with-group=root --with-usb=libusb-1.0 --with-drivers=usbhid-ups \
  --without-serial --without-snmp --without-neon --without-powerman --without-modbus \
  --without-ipmi --without-avahi --without-ssl --without-wrap --without-cgi --without-libltdl \
  --without-nut-scanner --without-dev --without-docs --without-gpio --without-linux_i2c \
  --without-macosx_ups --without-freeipmi --without-systemdsystemunitdir --without-udev-dir \
  --enable-static --disable-shared >/dev/null
make -j"$(nproc)" -C common >/dev/null
# libtool drops a plain -static at link time; -all-static makes it stick.
make -C drivers usbhid-ups LDFLAGS="-all-static -no-pie" >/dev/null

mkdir -p "${OUT}"
strip -o "${OUT}/us3000-usbhid-ups" drivers/usbhid-ups
chmod 755 "${OUT}/us3000-usbhid-ups"
rm -rf "${WORK}"
"${OUT}/us3000-usbhid-ups" -V | head -1
echo "${OUT}/us3000-usbhid-ups"
