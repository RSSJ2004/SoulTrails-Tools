"""Utilidades de imagen: colores RGB555 y escritura de PNG sin dependencias."""
from __future__ import annotations

import struct
import zlib


def rgb555(c: int, alpha: int = 255) -> tuple[int, int, int, int]:
    r = c & 0x1F
    g = (c >> 5) & 0x1F
    b = (c >> 10) & 0x1F
    return ((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), alpha)


def read_palette(data: bytes, offset: int, count: int) -> list[tuple[int, int, int, int]]:
    out = []
    for i in range(count):
        o = offset + i * 2
        if o + 2 > len(data):
            out.append((0, 0, 0, 255))
        else:
            out.append(rgb555(struct.unpack_from("<H", data, o)[0]))
    return out


def write_png(path, width: int, height: int, rgba: bytes | bytearray) -> None:
    """Escribe un PNG RGBA de 8 bits."""
    if len(rgba) != width * height * 4:
        raise ValueError("tamaño de buffer incorrecto")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def read_png_rgba(path) -> tuple[int, int, bytes]:
    """Lector mínimo (solo PNG RGBA 8 bits sin entrelazado, como los que escribimos). Para tests."""
    with open(path, "rb") as fh:
        data = fh.read()
    p = 8
    idat = b""
    w = h = 0
    while p < len(data):
        ln = struct.unpack_from(">I", data, p)[0]
        tag = data[p + 4:p + 8]
        body = data[p + 8:p + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack_from(">II", body)
        elif tag == b"IDAT":
            idat += body
        p += 12 + ln
    raw = zlib.decompress(idat)
    out = bytearray()
    stride = w * 4
    for y in range(h):
        assert raw[y * (stride + 1)] == 0
        out += raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
    return w, h, bytes(out)
