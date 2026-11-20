"""Arranque del código recompilado con una ROM sintética que se parece a una real:
crt0 propio con ModuleParams, ARM9 comprimido con BLZ (lo descomprime el código invitado),
autoload ITCM, BSS y dos overlays en la misma dirección (uno comprimido). Comprueba:
- que sttools.nds encuentra ModuleParams, descomprime el ARM9 y separa la ITCM;
- que st_boot_run carga el ARM9 tal cual y el crt0 recompilado lo deja todo en su sitio;
- que el despachador elige el overlay correcto por contenido (sin hooks del SDK);
- que soultrails_port (el ejecutable real) registra el fallo con su firma y sale con 3."""
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import builders as B  # noqa: E402
from sttools.nds import Rom  # noqa: E402
from sttools.recomp import driver  # noqa: E402

BT = ROOT / "tests" / "boot"
ARMCC = shutil.which("arm-none-eabi-gcc")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
CFLAGS = ["-mcpu=arm946e-s", "-marm", "-O2", "-ffreestanding", "-fno-builtin", "-fno-tree-loop-distribute-patterns"]


def sh(*cmd, check=True, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if check and r.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r


def nm(elf: Path) -> dict[str, int]:
    out = {}
    for line in sh("arm-none-eabi-nm", elf).stdout.splitlines():
        p = line.split()
        if len(p) == 3:
            out[p[2]] = int(p[0], 16)
    return out


def build_rom(d: Path) -> tuple[bytes, dict, list[bytes]]:
    sh(ARMCC, *CFLAGS, "-c", BT / "game.c", "-o", d / "game.o")
    sh(ARMCC, "-mcpu=arm946e-s", "-c", BT / "crt0.S", "-o", d / "crt0.o")
    sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", BT / "game.ld", d / "crt0.o", d / "game.o", "-lgcc", "-o", d / "game.elf")
    sh("arm-none-eabi-objcopy", "-O", "binary", d / "game.elf", d / "game.bin")
    syms = nm(d / "game.elf")
    img = (d / "game.bin").read_bytes()
    img += bytes(-len(img) % 4)
    cut = syms["__compress_start"] - 0x02000000
    head, tail = bytearray(img[:cut]), img[cut:]
    arm9 = head + B.blz_compress(tail)
    struct.pack_into("<I", arm9, syms["module_params"] - 0x02000000 + 0x14, 0x02000000 + len(arm9))
    ovs = []
    for i in range(2):
        sh(ARMCC, *CFLAGS, "-c", BT / f"ov{i}.c", "-o", d / f"ov{i}.o")
        sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-Ttext=0x02200000", "-e", "ov_main", d / f"ov{i}.o", "-o", d / f"ov{i}.elf")
        sh("arm-none-eabi-objcopy", "-O", "binary", d / f"ov{i}.elf", d / f"ov{i}.bin")
        ovs.append((d / f"ov{i}.bin").read_bytes())
    rom = B.rom({"dummy.bin": b"\0" * 16}, arm9=bytes(arm9),
                overlays=[(0x02200000, B.blz_compress(ovs[0]), True), (0x02200000, ovs[1], False)])
    return rom, syms, ovs


@unittest.skipUnless(ARMCC and CC and shutil.which("cmake"), "falta arm-none-eabi-gcc, cc o cmake")
class Boot(unittest.TestCase):
    def test_boot_and_overlays(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rom_bytes, syms, ovs = build_rom(d)
            (d / "game.nds").write_bytes(rom_bytes)
            rom = Rom.load(d / "game.nds")
            # sttools.nds ve lo mismo que verá el crt0
            mp = rom.arm9_module_params()
            self.assertIsNotNone(mp)
            self.assertEqual(mp["autoload_start"], syms["__autoload_start"])
            segs = {s.name: s for s in rom.arm9_segments()}
            self.assertEqual(segs["itcm"].base, 0x01FF8000)
            self.assertEqual(segs["arm9"].bss, syms["__bss_end"] - syms["__bss_start"])
            self.assertEqual([o.code() for o in rom.overlays9()], ovs)

            out = d / "gen"
            (d / "boot_addrs.h").write_text(f"#define ADDR_results 0x{syms['results']:08X}u\n")
            rt = sorted((ROOT / "runtime").glob("*.c"))
            misses = d / "misses.txt"
            misses.write_text("")
            # ov_main solo se alcanza con una dirección calculada (mov r3, #0x2200000): lo
            # encuentra el aprendizaje, y la firma dice a qué overlay pertenece cada fallo.
            learned = []
            for attempt in range(5):
                res = driver.run(driver.modules_from_rom(rom), out, rom.header["arm9_entry"], seed_files=[misses])
                self.assertEqual(res["undefined"], 0, res)
                sh(CC, "-O1", "-w", "-I", ROOT / "runtime", "-I", out, "-I", d, BT / "harness.c", *rt,
                   *sorted(out.glob("*.c")), "-o", d / "h")
                r = sh(d / "h", d / "game.nds", misses, check=False)
                if r.returncode != 3:
                    break
                learned.append(r.stdout.strip())
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(len(learned), 2, learned)          # un fallo por overlay
            gen = "".join(p.read_text() for p in out.glob("rc_*.c"))
            self.assertIn("f_ov000_02200000", gen)
            self.assertIn("f_ov001_02200000", gen)
            got = [int(x, 16) for x in r.stdout.split()]
            want = [(3 * 31) ^ (4 + 7), 5, 0, 10 * 3 + 1, (10 << 4) - 9, 7 * 3 + 1, 640 ^ 0xB0070000]
            self.assertEqual(got, want)

            # El ejecutable real: sin plataforma, la SWI de carga no hace nada, así que la
            # llamada al overlay falla y queda registrada con la firma de la memoria.
            bdir = d / "port-build"
            sh("cmake", "-S", ROOT / "port", "-B", bdir, f"-DST_RECOMP_DIR={out}", "-G", "Ninja" if shutil.which("ninja") else "Unix Makefiles")
            sh("cmake", "--build", bdir)
            exe = next(p for p in bdir.rglob("soultrails_port*") if p.is_file() and p.suffix in ("", ".exe"))
            misses = d / "port_misses.txt"
            r = sh(exe, "--rom", d / "game.nds", "--misses", misses, check=False)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("ARM9", r.stdout)
            line = misses.read_text().split()
            self.assertEqual(line[:2], ["02200000", "arm"])
            self.assertTrue(line[2].startswith("sig="))


if __name__ == "__main__":
    unittest.main()
