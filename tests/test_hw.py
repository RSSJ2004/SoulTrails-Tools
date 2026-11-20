"""Hardware mínimo de la plataforma (runtime/st_hw.c): E/S con memoria, IPCSYNC con eco del
ARM7, FIFO del PXI y detector de bucles de espera."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


@unittest.skipUnless(CC, "necesita un compilador de C")
class Hw(unittest.TestCase):
    def test_hw(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "hw"
            src = [ROOT / "tests" / "hw" / "hw_test.c", *sorted((ROOT / "runtime").glob("*.c"))]
            r = subprocess.run([CC, "-O1", "-Wall", "-I", str(ROOT / "runtime"), *map(str, src), "-o", str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("hw: OK", r.stdout)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(CC, "necesita un compilador de C")
class Lookup(unittest.TestCase):
    def test_overlay_disambiguation(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "lk"
            src = [ROOT / "tests" / "hw" / "lookup_test.c", *sorted((ROOT / "runtime").glob("*.c"))]
            r = subprocess.run([CC, "-O1", "-I", str(ROOT / "runtime"), *map(str, src), "-o", str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
