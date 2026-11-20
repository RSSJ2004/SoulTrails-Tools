"""Formatos en crudo (.nbf*, .ntf*) y volcador de formatos por bloques (sttools/gamefmt.py)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sttools import gamefmt as G  # noqa: E402
from sttools.imaging import read_png_rgba, rgb555  # noqa: E402


def pal(colors):
    return b"".join(struct.pack("<H", c) for c in colors)


def pixel(path, x, y):
    w, h, rgba = read_png_rgba(path)
    o = (y * w + x) * 4
    return tuple(rgba[o:o + 4])


class Raw2D(unittest.TestCase):
    def test_4bpp_screen_with_flip_and_bank(self):
        colors = [0] * 32
        colors[5], colors[16 + 5], colors[16 + 7] = 0x001F, 0x03E0, 0x7C00
        tile1 = bytes([0x55]) * 32                       # todo índice 5
        tile2 = bytes([0x77] * 3 + [0x50]) + bytes([0x77]) * 28  # esquina sup. izq.: 0 luego 5
        scr = [0] * (32 * 24)
        scr[0] = 1                                        # tile 1, paleta 0
        scr[1] = 1 | (1 << 12)                            # tile 1, paleta 1
        scr[2] = 2 | (1 << 12) | (1 << 10)                # tile 2, paleta 1, volteo horizontal
        items = {"m.nbfp": pal(colors), "m.nbfc": bytes(32) + tile1 + tile2,
                 "m.nbfs": struct.pack(f"<{len(scr)}H", *scr)}
        with tempfile.TemporaryDirectory() as d:
            res = G.convert_raw_2d(items, Path(d))
            self.assertEqual(res["m.nbfc"][1]["bpp"], 4)
            self.assertEqual(res["m.nbfs"][1]["size"], [256, 192])
            out = res["m.nbfs"][0][0]
            self.assertEqual(pixel(out, 3, 3)[:3], rgb555(0x001F)[:3])
            self.assertEqual(pixel(out, 11, 3)[:3], rgb555(0x03E0)[:3])
            self.assertEqual(pixel(out, 16 + 7, 7)[:3], rgb555(0x7C00)[:3])  # volteado: el 7 queda a la derecha
            self.assertTrue((Path(d) / "m.palette.png").exists())

    def test_8bpp_detected(self):
        colors = list(range(256))
        tiles = bytes(64) + bytes([200]) * 64
        scr = [1] * 4
        res_dir = tempfile.TemporaryDirectory()
        with res_dir as d:
            res = G.convert_raw_2d({"a.nbfp": pal(colors), "a.nbfc": tiles,
                                    "a.nbfs": struct.pack("<4H", *scr)}, Path(d))
            self.assertEqual(res["a.nbfc"][1]["bpp"], 8)
            self.assertEqual(pixel(res["a.nbfs"][0][0], 0, 0)[:3], rgb555(200)[:3])


class RawTex(unittest.TestCase):
    def test_pal16(self):
        colors = [0] * 16
        colors[3] = 0x7FFF
        tex = bytes([0x33]) * (32 * 32 // 2)
        with tempfile.TemporaryDirectory() as d:
            res = G.convert_raw_tex({"t.ntft": tex, "t.ntfp": pal(colors)}, Path(d))
            g = res["t.ntft"][1]
            self.assertEqual((g["format"], g["w"], g["h"]), ("pal16", 32, 32))
            self.assertEqual(pixel(res["t.ntft"][0][0], 5, 5), (255, 255, 255, 255))

    def test_4x4_and_direct(self):
        with tempfile.TemporaryDirectory() as d:
            res = G.convert_raw_tex({"s.ntft": bytes(16), "s.ntfi": bytes(8), "s.ntfp": pal([0x1F, 0, 0, 0])},
                                    Path(d))
            g = res["s.ntft"][1]
            self.assertEqual((g["format"], g["w"], g["h"]), ("tex4x4", 8, 8))
            self.assertEqual(pixel(res["s.ntft"][0][0], 0, 0)[:3], rgb555(0x1F)[:3])
        with tempfile.TemporaryDirectory() as d:
            res = G.convert_raw_tex({"x.ntft": pal([0x8000 | 0x3E0] * 64)}, Path(d))
            self.assertEqual(res["x.ntft"][1]["format"], "direct")


class Chunks(unittest.TestCase):
    @staticmethod
    def chunk(tag, payload):
        return tag + struct.pack("<I", 8 + len(payload)) + payload

    def test_chain_records_and_strings(self):
        objs = struct.pack("<I", 3) + b"".join(struct.pack("<II", i, i * 7) for i in range(3))
        body = self.chunk(b"LJBO", objs) + self.chunk(b"RTSN", b"link_house\0\0")
        data = b"ZOLB" + struct.pack("<II", 12 + len(body), 2) + body
        self.assertTrue(G.looks_chunked(data))
        info = G.parse_chunks(data)
        self.assertEqual(info["file_size_at"], 4)
        self.assertEqual([c["tag"] for c in info["chunks"]], ["LJBO", "RTSN"])
        self.assertEqual(info["chunks"][0]["tag_reversed"], "OBJL")
        self.assertEqual(info["chunks"][0]["records_guess"], {"count": 3, "record_size": 8, "count_bytes": 4})
        self.assertIn("link_house", info["chunks"][1]["strings"])
        self.assertEqual(G.chunk_signature(info), "ZOLB > LJBO RTSN")

    def test_nested_and_no_chunks(self):
        inner = self.chunk(b"AAAA", bytes(8)) + self.chunk(b"BBBB", bytes(4))
        data = b"BPAM" + struct.pack("<I", 0) + self.chunk(b"ROOT", inner)
        info = G.parse_chunks(data)
        self.assertEqual(G.chunk_signature(info), "BPAM > ROOT[AAAA BBBB]")
        plain = b"DCBZ" + bytes(range(1, 60))
        self.assertIn("(sin bloques)", G.chunk_signature(G.parse_chunks(plain)))
        self.assertFalse(G.looks_chunked(b"plain text here"))


if __name__ == "__main__":
    unittest.main()
