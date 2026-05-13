"""
generate_icons.py — creates icon16.png, icon48.png, icon128.png
Uses only Python stdlib (struct + zlib). No third-party packages.
Color: solid #3B82F6 (R=59, G=130, B=246)
"""

import struct
import zlib
import os

# Output directory is the same folder as this script
ICONS_DIR = os.path.dirname(os.path.abspath(__file__))

# Target color: #3B82F6
R, G, B = 59, 130, 246


def make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Build a minimal valid PNG with a solid RGB color, stdlib only."""

    def chunk(name: bytes, data: bytes) -> bytes:
        """Pack a PNG chunk: length + type + data + CRC."""
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # PNG signature
    sig = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk: width, height, bit-depth=8, color-type=2 (RGB), compression=0,
    #             filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = chunk(b"IHDR", ihdr_data)

    # Build raw image data: one filter byte (0 = None) per row, then RGB pixels
    row = bytes([0]) + bytes([r, g, b] * width)   # filter byte + pixel data
    raw = row * height

    # IDAT chunk: zlib-compress the raw image data
    idat = chunk(b"IDAT", zlib.compress(raw, level=9))

    # IEND chunk
    iend = chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


def main():
    sizes = [16, 48, 128]
    for size in sizes:
        png_bytes = make_png(size, size, R, G, B)
        filename = os.path.join(ICONS_DIR, f"icon{size}.png")
        with open(filename, "wb") as f:
            f.write(png_bytes)
        print(f"  Created {filename}  ({len(png_bytes)} bytes)")
    print("Done — all icons generated successfully.")


if __name__ == "__main__":
    main()
