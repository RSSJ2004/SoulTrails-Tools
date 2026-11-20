"""Generación de C (sttools/recomp/codegen.py): el C sale compilable aunque haya
instrucciones solapadas, y los archivos que no cambian no se reescriben."""
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sttools.recomp.codegen import Generator  # noqa: E402
from sttools.recomp.program import Module, Program  # noqa: E402

BASE = 0x02000000
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def thumb(*hw: int) -> bytes:
    return struct.pack(f"<{len(hw)}H", *hw)


def generate(data: bytes, entry: int, out: Path) -> Program:
    p = Program([Module("main", BASE, data)])
    p.discover([(entry, True, None)], pointer_scan=False)
    Generator(p).write(out)
    return p


class Codegen(unittest.TestCase):
    # Un salto a la segunda mitad de un BL de 32 bits hace que se decodifique una instrucción
    # "en medio": la caída del BL a la siguiente necesita un goto con etiqueta (error C2094 en MSVC).
    DATA = thumb(
        0xB500,          # 00 push {lr}
        0xD000,          # 02 beq 0x06 (mitad del BL)
        0xF000, 0xF802,  # 04 bl 0x0C
        0xBD00,          # 08 pop {pc}
        0x46C0,          # 0A nop
        0x4770,          # 0C bx lr
        0x46C0)

    def test_overlapping_bl_has_labels(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            p = generate(self.DATA, BASE | 1, out)
            f = p.functions[("main", BASE)]
            self.assertIn(BASE + 6, f.insns)  # se decodificó la mitad del BL
            src = "".join(x.read_text() for x in sorted(out.glob("rc_*.c")))
            for lbl in set(__import__("re").findall(r"goto (L_[0-9A-F]{8});", src)):
                self.assertIn(f"{lbl}:", src, f"falta la etiqueta {lbl}")
            if CC:
                for c in sorted(out.glob("*.c")):
                    r = subprocess.run([CC, "-std=c11", "-fsyntax-only", "-Werror=implicit-function-declaration",
                                        "-I", str(ROOT / "runtime"), "-I", str(out), str(c)],
                                       capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_unchanged_files_not_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            generate(self.DATA, BASE | 1, out)
            stale = out / "recomp_0099.c"
            stale.write_text("/* sobra */")
            mtimes = {x.name: x.stat().st_mtime_ns for x in out.iterdir() if x != stale}
            generate(self.DATA, BASE | 1, out)
            self.assertFalse(stale.exists())
            self.assertEqual(mtimes, {x.name: x.stat().st_mtime_ns for x in out.iterdir()})
            self.assertIn("/bigobj", (out / "CMakeLists.txt").read_text())


if __name__ == "__main__":
    unittest.main()
