"""Símbolos del decomp vía el informe de objdiff (solo metadatos: nombre, dirección, tamaño):
semillas 'auto' con nombre en el recompilador y su generación desde stk."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sttools.recomp import driver  # noqa: E402
from sttools.recomp.program import Module  # noqa: E402

BASE = 0x02000000


class AutoSeeds(unittest.TestCase):
    def test_auto_mode_and_names(self):
        arm = struct.pack("<3I", 0xE92D4010, 0xE3A00001, 0xE8BD8010)        # push; mov r0,#1; pop
        thumb = struct.pack("<4H", 0xB510, 0x2001, 0xBD10, 0x46C0)          # push; movs r0,#1; pop
        data = arm + bytes(4) + thumb
        with tempfile.TemporaryDirectory() as d:
            seeds = Path(d) / "s.txt"
            seeds.write_text("02000000 auto # FuncArm size=12\n02000010 auto main # FuncThumb size=6\n")
            out = Path(d) / "out"
            res = driver.run([Module("main", BASE, data)], out, None, None, [seeds], scan=False)
            self.assertEqual(res["named"], 2)
            src = "".join(p.read_text() for p in out.glob("recomp_*.c"))
            self.assertRegex(src, r"main 0x02000000 ARM, 3 instr\. FuncArm")
            self.assertRegex(src, r"main 0x02000010 Thumb, 3 instr\. FuncThumb")


FAKE_OBJDIFF = textwrap.dedent('''\
    import json, sys
    out = sys.argv[sys.argv.index("-o") + 1]
    json.dump({"measures": {}, "units": [
        {"name": "main/Game", "functions": [
            {"name": "Game_Init", "size": "64", "fuzzy_match_percent": 100.0,
             "metadata": {"virtual_address": "33558528"}}]},
        {"name": "ov031/Land", "functions": [
            {"name": "Land_Update", "size": "128", "fuzzy_match_percent": 40.0,
             "metadata": {"virtual_address": "34603008"}}]}]}, open(out, "w"))
''')


class FromObjdiff(unittest.TestCase):
    def test_symbols_file(self):
        with tempfile.TemporaryDirectory() as d:
            home, dec = Path(d), Path(d) / "decomp"
            dec.mkdir()
            (dec / "objdiff.json").write_text("{}")
            script = home / "fake_objdiff.py"
            script.write_text(FAKE_OBJDIFF)
            if os.name == "nt":
                tool = home / "objdiff-cli.bat"
                tool.write_text(f'@"{sys.executable}" "{script}" %*\n')
            else:
                tool = home / "objdiff-cli"
                tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
                tool.chmod(0o755)
            (home / ".stk").mkdir()
            (home / ".stk" / "config.json").write_text(json.dumps(
                {"rom": "x.nds", "decomp": str(dec), "objdiff_cli": str(tool), "version": "eur"}))
            code = "import stk; print(stk.decomp_symbols(stk.config()))"
            r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                               env={**os.environ, "STK_HOME": str(home)})
            self.assertIn("True", r.stdout, r.stderr + r.stdout)
            lines = (home / ".stk" / "decomp_symbols.txt").read_text().splitlines()
            self.assertEqual(lines[0], "02001000 auto  # Game_Init size=64 match=100")
            self.assertEqual(lines[1], "02100000 auto ov031 # Land_Update size=128 match=40")
            self.assertEqual(driver.parse_seed_line(lines[1]), (0x02100000, None, "ov031", None))


if __name__ == "__main__":
    unittest.main()
