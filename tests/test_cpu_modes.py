"""Modos del ARM946E-S en el código recompilado: bancos de registros, SPSR, LDM/STM ^,
retornos de excepción (también a Thumb), MSR, reglas ARMv5 de LDM/STM con la base en la
lista, coprocesadores distintos de CP15 y bkpt.

Referencia: QEMU con -cpu arm946 ejecuta tests/cpu/modes.S; su salida está guardada en
tests/cpu/modes_expected.txt (si QEMU está instalado se vuelve a comprobar). Los casos que
QEMU no modela como el hardware (v5rules.S) se comparan con valores de GBATEK."""
import os
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

C = ROOT / "tests" / "cpu"
ARMCC = shutil.which("arm-none-eabi-gcc")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
QEMU = shutil.which("qemu-system-arm")
ENTRIES = {"test_modes": False, "ret1": False, "ret2": False, "ret3": False, "ret4": True, "ret5": False,
           "test_v5": False}


def sh(*cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def hex_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if len(ln.strip()) == 8 and all(ch in "0123456789ABCDEF" for ch in ln.strip())]


def build(d: Path) -> tuple[Path, Path, dict]:
    elf = d / "m.elf"
    sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", C / "modes.ld", C / "modes.S", C / "v5rules.S", "-o", elf)
    sh("arm-none-eabi-objcopy", "-O", "binary", elf, d / "m.bin")
    syms = {}
    for line in sh("arm-none-eabi-nm", elf).splitlines():
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    return elf, d / "m.bin", syms


def run_qemu(binary: Path, start: int) -> list[str]:
    env = dict(os.environ, QEMU_AUDIO_DRV="none")
    r = subprocess.run([QEMU, "-M", "versatilepb", "-cpu", "arm946", "-m", "128M", "-nographic", "-monitor", "none",
             "-serial", "none", "-semihosting", "-device", f"loader,file={binary},addr=0x02000000,force-raw=on",
             "-device", f"loader,addr={start:#x},cpu-num=0"], capture_output=True, text=True, env=env, timeout=60)
    return hex_lines(r.stdout + r.stderr)   # el semihosting escribe en stderr


@unittest.skipUnless(ARMCC and CC, "falta arm-none-eabi-gcc o cc")
class CpuModes(unittest.TestCase):
    def test_modes_against_reference(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _, binary, syms = build(d)
            expected = hex_lines((C / "modes_expected.txt").read_text())
            self.assertGreater(len(expected), 60)
            if QEMU:
                q = run_qemu(binary, syms["qemu_start"])
                self.assertEqual(q[:len(expected)], expected, "QEMU ya no coincide con modes_expected.txt")
            seeds = d / "seeds.txt"
            seeds.write_text("\n".join(f"{syms[n]:08X} {'thumb' if t else 'arm'}" for n, t in ENTRIES.items()))
            defs = [f"#define ADDR_{n} 0x{syms[n]:08X}u" for n in ("results", "results_end", "results2", "buf2")]
            defs += [f"#define ADDR_{n} 0x{syms[n] | int(t):08X}u" for n, t in ENTRIES.items()]
            (d / "cpu_addrs.h").write_text("\n".join(defs) + "\n")
            out = d / "gen"
            driver.run([Module("main", 0x02000000, binary.read_bytes())], out, None, seed_files=[seeds], scan=False)
            sh(CC, "-O1", "-w", "-I", ROOT / "runtime", "-I", out, "-I", d, C / "harness.c",
               ROOT / "runtime" / "strecomp.c", *sorted(out.glob("*.c")), "-o", d / "harness")
            got = sh(d / "harness", binary)
            modes, v5 = got.split("--\n")
            n = (syms["results_end"] - syms["results"]) // 4
            self.assertEqual(hex_lines(modes), (expected + ["00000000"] * n)[:n])
            buf, buf2 = syms["buf"], syms["buf2"]
            want = [buf + 8, 0x22,            # ldmia r0!, {r0, r1}
                    0x11, 0x22,               # ldmia r1!, {r0, r1}
                    buf + 4,                  # ldmia r0!, {r0}
                    buf2 + 8,                 # stmia r0!, {r0, r1}
                    buf2 + 16,                # stmia r1!, {r0, r1}
                    0xC0DE0000 | (3 << 16) | (14 << 12) | (1 << 4) | 2,   # mrc p14 -> plataforma
                    buf2, 0x77, 0x66, buf2 + 8,                        # memoria de los STM
                    0x0713455A,               # mcr p7 -> plataforma
                    0x42,                     # bkpt
                    0x11F]                    # vuelve en modo sistema con su pila
            self.assertEqual(hex_lines(v5), [f"{x:08X}" for x in want])


if __name__ == "__main__":
    unittest.main()
