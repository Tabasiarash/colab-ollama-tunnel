#!/usr/bin/env python3
"""Generate build/icon.png + build/icon.ico (pure Python, no PIL).

A rounded-square gradient tile with a white "TL" monogram. The .ico embeds the
256px PNG (Vista+ format) so both PyInstaller targets can use it:
  - macOS build converts icon.png -> .icns via sips + iconutil (mac_build.sh)
  - Windows build passes icon.ico to PyInstaller directly (win_build.bat)
"""
import struct
import sys
import zlib
from pathlib import Path

GLYPH = [
    "1111100000",
    "0010010000",
    "0010010000",
    "0010010000",
    "0010010000",
    "0010010000",
    "0010010000",
    "0010010000",
]
GW, GH = len(GLYPH[0]), len(GLYPH)
TOP = (91, 140, 255)
BOTTOM = (124, 108, 255)
WHITE = (255, 255, 255, 255)


def render(size, scale, rad):
    margin_x = (size - GW * scale) // 2
    margin_y = (size - GH * scale) // 2
    out = bytearray()
    for ny in range(size):
        row = bytearray()
        t = ny / (size - 1)
        bg = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        for nx in range(size):
            # rounded-rect coverage
            if not (rad <= nx < size - rad or rad <= ny < size - rad):
                cx = rad if nx < rad else size - rad
                cy = rad if ny < rad else size - rad
                if (nx - cx) ** 2 + (ny - cy) ** 2 > rad * rad:
                    row += b"\x00\x00\x00\x00"
                    continue
            gx = (nx - margin_x) // scale
            gy = (ny - margin_y) // scale
            filled = 0 <= gx < GW and 0 <= gy < GH and GLYPH[gy][gx] == "1"
            if filled:
                row += bytes(WHITE)
            else:
                row += bytes(bg + (255,))
        out += b"\x00" + row  # filter byte 0
    return bytes(out)


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_bytes(size, scale, rad):
    raw = render(size, scale, rad)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(embedded_png, size=256):
    header = b"\x00\x00\x01\x00\x01\x00"
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(embedded_png), 22)
    return header + entry + embedded_png


def main():
    build = Path(__file__).resolve().parent
    (build / "icon.png").write_bytes(png_bytes(1024, 60, 210))
    (build / "icon.ico").write_bytes(ico_bytes(png_bytes(256, 15, 52)))
    print("wrote build/icon.png, build/icon.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())