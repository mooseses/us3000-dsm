#!/bin/sh
# Builds dist/us3000-companion-<version>-noarch.spk
set -e
VERSION="1.0.1-1"
R="$(cd "$(dirname "$0")" && pwd)"
B="${R}/build"
rm -rf "${B}" && mkdir -p "${B}/spk" "${B}/pkg" "${R}/dist"

# DSM injects <dsmuidir>/style.css of every running package into the desktop
# page itself, so a style.css here would restyle DSM (it hid the wallpaper).
if [ -e "${R}/package/app/style.css" ]; then
    echo "package/app/style.css must not exist: DSM loads it into the desktop" >&2
    exit 1
fi

cp -R "${R}/package/." "${B}/pkg/"
/bin/python3 -I "${R}/tools/make_icons.py" "${B}/icons"
mkdir -p "${B}/pkg/app/images" && cp "${B}/icons/images/"*.png "${B}/pkg/app/images/"
find "${B}/pkg" -name __pycache__ -prune -exec rm -rf {} +
find "${B}/pkg" -type d -exec chmod 755 {} +
find "${B}/pkg" -type f -exec chmod 644 {} +
chmod 755 "${B}/pkg/app/api.cgi" "${B}/pkg/bin/collector.py"

tar -czf "${B}/spk/package.tgz" -C "${B}/pkg" .
SUM="$(md5sum "${B}/spk/package.tgz" | cut -d' ' -f1)"
sed -e "s/@VERSION@/${VERSION}/" -e "s/@CHECKSUM@/${SUM}/" "${R}/INFO.in" >"${B}/spk/INFO"
cp -R "${R}/scripts" "${R}/conf" "${B}/spk/"
cp "${B}/icons/PACKAGE_ICON.PNG" "${B}/icons/PACKAGE_ICON_256.PNG" "${B}/spk/"

find "${B}/spk" -type d -exec chmod 755 {} +
find "${B}/spk" -type f -exec chmod 644 {} +
chmod 755 "${B}/spk/scripts/"*

OUT="${R}/dist/us3000-companion-${VERSION}-noarch.spk"
(cd "${B}/spk" && tar -cf "${OUT}" INFO package.tgz scripts conf PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG)
echo "${OUT}"
