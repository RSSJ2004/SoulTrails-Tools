"""Test de extremo a extremo del recompilador:
compila C para ARM946E-S (ARM + Thumb + libgcc), lo recompila a C, lo compila
de forma nativa y compara resultados con la versión nativa del mismo código.
Necesita arm-none-eabi-gcc y un compilador de C nativo."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sttools.recomp import driver  # noqa: E402
from sttools.recomp.program import Module  # noqa: E402

T = ROOT / "tests" / "recomp"
ARMCC = shutil.which("arm-none-eabi-gcc")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
FUNCS = ["t_fib", "t_div", "t_switch", "t_sort", "t_fnptr", "t_bytes", "t_bits", "t_u64", "t_struct", "t_float",
         "th_collatz", "th_mod", "th_call_arm", "th_switch", "th_memstuff", "th_signed"]


def sh(*cmd, cwd=None):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r.stdout


@unittest.skipUnless(ARMCC and CC, "falta arm-none-eabi-gcc o cc")
class RecompEndToEnd(unittest.TestCase):
    def build_and_run(self, opt: str):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            flags = ["-mcpu=arm946e-s", opt, "-ffreestanding", "-fno-builtin", "-mthumb-interwork", "-I", T]
            sh(ARMCC, *flags, "-marm", "-c", T / "progs_arm.c", "-o", d / "a.o")
            sh(ARMCC, *flags, "-mthumb", "-c", T / "progs_thumb.c", "-o", d / "t.o")
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", T / "link.ld", d / "a.o", d / "t.o", "-lgcc",
               "-o", d / "p.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "p.elf", d / "p.bin")
            syms = {}
            for line in sh("arm-none-eabi-nm", d / "p.elf").splitlines():
                parts = line.split()
                if len(parts) == 3:
                    syms[parts[2]] = int(parts[0], 16)
            # Las direcciones Thumb en nm no llevan el bit 0: se marca según el archivo
            seeds = []
            addrs = []
            for fn in FUNCS:
                thumb = fn.startswith("th_")
                seeds.append(f"{syms[fn]:08X} {'thumb' if thumb else 'arm'}")
                addrs.append(f"#define ADDR_{fn} 0x{syms[fn] | int(thumb):08X}u")
            (d / "seeds.txt").write_text("\n".join(seeds))
            (d / "test_addrs.h").write_text("\n".join(addrs) + "\n")
            out = d / "gen"
            misses = d / "misses.txt"
            misses.write_text("")
            # Bucle de aprendizaje: ejecutar, recoger direcciones desconocidas, recompilar
            for attempt in range(12):
                res = driver.run([Module("main", 0x02000000, (d / "p.bin").read_bytes())], out, None,
                                 seed_files=[d / "seeds.txt", misses], scan=False)
                srcs = sorted(out.glob("*.c"))
                sh(CC, "-O1", "-w", "-I", ROOT / "runtime", "-I", out, "-I", T, "-I", d,
                   T / "harness.c", T / "progs_arm.c", T / "progs_thumb.c", *sorted((ROOT / "runtime").glob("*.c")), *srcs,
                   "-o", d / "harness", "-lm")
                r = subprocess.run([d / "harness", d / "p.bin", misses], capture_output=True, text=True)
                if r.returncode != 3:
                    break
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            print(r.stdout.strip(), f"[{opt}: {res['functions']} funciones, {attempt} recompilaciones extra]")

    def test_O2(self):
        self.build_and_run("-O2")

    def test_O0(self):
        self.build_and_run("-O0")

    def test_Os(self):
        self.build_and_run("-Os")


if __name__ == "__main__":
    unittest.main()
