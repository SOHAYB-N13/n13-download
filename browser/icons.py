"""Generate simple PNG icons for the Chrome extension."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _make_png(pixels, width: int, height: int) -> bytes:
    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw += struct.pack("BBBB", r, g, b, a)
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def create_download_icon(filepath: Path, size: int = 48) -> None:
    pixels = []
    cx = size // 2
    shaft_w = max(2, size // 7)
    shaft_top = int(size * 0.25)
    shaft_bot = int(size * 0.50)
    head_top = shaft_bot
    head_bot = int(size * 0.72)
    head_w = int(size * 0.32)

    for y in range(size):
        for x in range(size):
            r, g, b, a = 15, 52, 96, 255
            if shaft_top <= y <= shaft_bot and abs(x - cx) <= shaft_w:
                r, g, b = 0, 212, 255
            elif head_top < y <= head_bot:
                t = (y - head_top) / max(1, (head_bot - head_top))
                w = int(head_w * t)
                if abs(x - cx) <= w:
                    r, g, b = 0, 212, 255
            pixels.append((r, g, b, a))

    filepath.write_bytes(_make_png(pixels, size, size))


def ensure_extension_icons(ext_dir: Path) -> None:
    for size in (16, 48, 128):
        path = ext_dir / f"icon{size}.png"
        if not path.exists():
            create_download_icon(path, size)
