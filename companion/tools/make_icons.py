"""Draws the package icon (a battery on a blue tile) as PNGs, no libraries needed.
Usage: python3 -I tools/make_icons.py OUTDIR"""

import os
import struct
import sys
import zlib

BG = (42, 120, 214)       # series-1 blue
FG = (255, 255, 255)
FILL = (12, 163, 12)      # status good


def rrect(x, y, x0, y0, x1, y1, r):
    """Inside a rounded rectangle (unit coordinates)?"""
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def shade(x, y):
    if not rrect(x, y, 0.02, 0.02, 0.98, 0.98, 0.22):
        return None
    if rrect(x, y, 0.30, 0.42, 0.68, 0.58, 0.02):      # charge level
        return FILL
    if rrect(x, y, 0.22, 0.32, 0.76, 0.68, 0.07) and not rrect(x, y, 0.27, 0.37, 0.71, 0.63, 0.03):
        return FG                                       # battery outline
    if rrect(x, y, 0.77, 0.43, 0.83, 0.57, 0.02):       # terminal nub
        return FG
    return BG


def png(n, path, ss=4):
    rows = []
    for py in range(n):
        row = bytearray([0])
        for px in range(n):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = shade((px + (sx + 0.5) / ss) / n, (py + (sy + 0.5) / ss) / n)
                    if c:
                        acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 255
            k = ss * ss
            a = acc[3] // k
            if a:
                row += bytes([acc[0] * 255 // acc[3], acc[1] * 255 // acc[3], acc[2] * 255 // acc[3], a])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))

    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(os.path.join(out, "images"), exist_ok=True)
    for n in (16, 24, 32, 48, 64, 72, 256):
        png(n, os.path.join(out, "images", "icon-%d.png" % n))
    png(64, os.path.join(out, "PACKAGE_ICON.PNG"))
    png(256, os.path.join(out, "PACKAGE_ICON_256.PNG"))
