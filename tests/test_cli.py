"""La ayuda de todos los subcomandos de stk se puede mostrar. Python 3.14 valida los textos
de ayuda de argparse al arrancar (un '%' sin escapar rompía todos los comandos)."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Cli(unittest.TestCase):
    def test_help_of_every_command(self):
        out = subprocess.run([sys.executable, ROOT / "stk.py", "--help"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        cmds = re.search(r"\{([a-z,]+)\}", out.stdout).group(1).split(",")
        self.assertIn("recomp", cmds)
        for c in cmds:
            r = subprocess.run([sys.executable, ROOT / "stk.py", c, "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{c}: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
