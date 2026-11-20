import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import builders as B  # noqa: E402
from sttools import formats as F  # noqa: E402
from sttools.assets import extract  # noqa: E402
from sttools.imaging import read_png_rgba  # noqa: E402
from sttools.nds import Rom, blz_decompress, lz_decompress, parse_narc  # noqa: E402

SAMPLE = (b"Spirit Tracks port test " * 40) + bytes(range(256)) + b"abcabcabcabcabc" * 20


class Compression(unittest.TestCase):
    def test_lz10(self):
        self.assertEqual(lz_decompress(B.lz10_compress(SAMPLE)), SAMPLE)

    def test_lz11(self):
        data = SAMPLE + b"x" * 1000
        self.assertEqual(lz_decompress(B.lz11_compress(data)), data)

    def test_blz(self):
        data = b"\x00\x01\x02\x03" * 300 + SAMPLE
        self.assertEqual(blz_decompress(B.blz_compress(data)), data)


class Containers(unittest.TestCase):
    def test_narc(self):
        files = [b"one", b"second file", b"3"]
        got = parse_narc(B.narc(files))
        self.assertEqual([d for _, d in got], files)


class Textures(unittest.TestCase):
    def test_pal16_and_direct(self):
        # 8x8 pal16: índice = x  (0 transparente)
        px = bytearray()
        for y in range(8):
            for x in range(0, 8, 2):
                px.append(x | ((x + 1) << 4))
        pal = b"".join(struct.pack("<H", i * 0x0421 & 0x7FFF) for i in range(16))
        direct = b"".join(struct.pack("<H", 0x801F) for _ in range(64))  # rojo opaco
        f = B.btx0([("grid", 3, 8, 8, bytes(px), True), ("red", 7, 8, 8, direct, False)],
                   [("grid_pl", pal)])
        with tempfile.TemporaryDirectory() as d:
            made = F.convert_tex0(f, Path(d))
            self.assertEqual(len(made), 2)
            w, h, rgba = read_png_rgba(Path(d) / "grid.png")
            self.assertEqual((w, h), (8, 8))
            self.assertEqual(rgba[3], 0)                  # índice 0 transparente
            self.assertEqual(rgba[4 * 1:4 * 1 + 4], bytes([8, 8, 8, 255]))  # índice 1 -> gris
            _, _, red = read_png_rgba(Path(d) / "red.png")
            self.assertEqual(red[:4], bytes([255, 0, 0, 255]))

    def test_a3i5(self):
        px = bytes([(7 << 5) | 1] * 64)  # alfa máx, color 1
        pal = struct.pack("<HH", 0, 0x7C00)  # azul
        f = B.btx0([("a", 1, 8, 8, px, False)], [("a_pl", pal)])
        with tempfile.TemporaryDirectory() as d:
            F.convert_tex0(f, Path(d))
            _, _, rgba = read_png_rgba(Path(d) / "a.png")
            self.assertEqual(rgba[:4], bytes([0, 0, 255, 255]))


class TwoD(unittest.TestCase):
    def test_ncgr_nscr(self):
        pal = [0, 0x001F, 0x03E0] + [0] * 13
        tile0 = bytes([0x11] * 32)  # todo color 1
        tile1 = bytes([0x22] * 32)  # todo color 2
        g = F.parse_ncgr(B.ncgr(tile0 + tile1, 2, 1))
        _, p = F.parse_nclr(B.nclr(pal))
        with tempfile.TemporaryDirectory() as d:
            F.ncgr_to_png(g, p, Path(d) / "t.png")
            w, h, rgba = read_png_rgba(Path(d) / "t.png")
            self.assertEqual((w, h), (16, 8))
            self.assertEqual(rgba[:4], bytes([255, 0, 0, 255]))
            self.assertEqual(rgba[8 * 4:8 * 4 + 4], bytes([0, 255, 0, 255]))
            # pantalla 16x8 con los tiles intercambiados y el primero espejado
            F.nscr_to_png(B.nscr(16, 8, [1 | (1 << 10), 0]), g, p, Path(d) / "s.png")
            _, _, s = read_png_rgba(Path(d) / "s.png")
            self.assertEqual(s[:4], bytes([0, 255, 0, 255]))


class Text(unittest.TestCase):
    def test_bmg(self):
        b = F.parse_bmg(B.bmg(["Hola Link", "¡Tren!"]))
        self.assertEqual([m["text"] for m in b["messages"]], ["Hola Link", "¡Tren!"])


class Audio(unittest.TestCase):
    def test_swar_pcm16(self):
        samples = [0, 1000, -1000, 32767, -32768, 5, 6, 7]
        data = b"".join(struct.pack("<h", s) for s in samples)
        with tempfile.TemporaryDirectory() as d:
            made = F.convert_swar(B.swar([(1, 22050, data)]), Path(d))
            with wave.open(made[0]) as w:
                self.assertEqual(w.getframerate(), 22050)
                frames = w.readframes(8)
            self.assertEqual(list(struct.unpack("<8h", frames)), samples)

    def test_ima_adpcm_runs(self):
        out = F.ima_adpcm(struct.pack("<hH", 100, 0) + bytes([0x17, 0x9F]))
        self.assertEqual(len(out), 4)


class EndToEnd(unittest.TestCase):
    def test_extract_rom(self):
        pal16 = b"".join(struct.pack("<H", i) for i in range(16))
        tex = B.btx0([("t", 3, 8, 8, bytes(32), False)], [("t_pl", pal16)])
        files = {
            "msg.bmg": B.bmg(["hola"]),
            "tex.nsbtx": tex,
            "pack.narc": B.narc([B.lz10_compress(tex), b"plain data"]),
            "comp.bin": B.lz10_compress(B.bmg(["comprimido"]) + b"\0" * 64),
        }
        ov_code = b"\x01\x02\x03\x04" * 200
        rom = B.rom(files, overlays=[(0x02100000, B.blz_compress(ov_code), True)])
        with tempfile.TemporaryDirectory() as d:
            rp = Path(d) / "x.nds"
            rp.write_bytes(rom)
            r = Rom.load(rp)
            self.assertEqual(r.overlays9()[0].code(), ov_code)
            s = extract(rp, Path(d) / "out")
            self.assertEqual(s["errors"], 0, (Path(d) / "out" / "report.txt").read_text())
            out = Path(d) / "out"
            self.assertTrue((out / "converted" / "tex.nsbtx" / "t.png").exists())
            self.assertTrue((out / "converted" / "pack.narc" / "0000" / "t.png").exists())
            self.assertEqual(json.loads((out / "converted" / "msg.json").read_text())["messages"][0]["text"], "hola")
            self.assertEqual(json.loads((out / "converted" / "comp.json").read_text())["messages"][0]["text"],
                             "comprimido")
            self.assertEqual((out / "code" / "overlays" / "ov000.bin").read_bytes(), ov_code)


if __name__ == "__main__":
    unittest.main()
