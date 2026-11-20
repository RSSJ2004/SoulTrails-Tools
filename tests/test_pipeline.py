"""Prueba de extremo a extremo de `stk` con una ROM sintética que contiene código
real (ARM9 + overlay comprimido) y assets. No usa datos del juego."""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import builders as B  # noqa: E402

T = ROOT / "tests" / "recomp"
ARMCC = shutil.which("arm-none-eabi-gcc")


def sh(*cmd, env=None, cwd=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env, cwd=cwd)
    if r.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r.stdout


@unittest.skipUnless(ARMCC and shutil.which("cmake"), "falta arm-none-eabi-gcc o cmake")
class Pipeline(unittest.TestCase):
    def test_stk_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fl = ["-mcpu=arm946e-s", "-O2", "-ffreestanding", "-fno-builtin", "-mthumb-interwork", "-I", T]
            sh(ARMCC, *fl, "-marm", "-c", T / "progs_arm.c", "-o", d / "a.o")
            sh(ARMCC, *fl, "-mthumb", "-c", T / "progs_thumb.c", "-o", d / "t.o")
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", T / "link.ld", d / "a.o", d / "t.o", "-lgcc", "-o", d / "p.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "p.elf", d / "p.bin")
            fib = next(int(l.split()[0], 16) for l in sh("arm-none-eabi-nm", d / "p.elf").splitlines()
                       if l.endswith(" t_fib"))
            # Overlay: código en 0x02100000 que llama al binario principal
            (d / "ov.c").write_text("unsigned t_fib(unsigned);\n"
                                    "unsigned ov_entry(unsigned x) { return t_fib(x) * 3 + 1; }\n")
            sh(ARMCC, *fl, "-marm", "-c", d / "ov.c", "-o", d / "ov.o")
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-Ttext=0x02100000", f"-Wl,--defsym,t_fib={fib:#x}",
               "-e", "ov_entry", d / "ov.o", "-o", d / "ov.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", "-j", ".text", d / "ov.elf", d / "ov.bin")
            ov = (d / "ov.bin").read_bytes() + bytes(512)
            pal = b"".join(struct.pack("<H", i * 0x421 & 0x7FFF) for i in range(16))
            files = {
                "tex.nsbtx": B.btx0([("stone", 3, 16, 16, bytes(range(128)), False)], [("stone_pl", pal)]),
                "msg.bmg": B.bmg(["¡Todos al tren!"]),
                "bg.narc": B.narc([B.nclr([0x7FFF] * 16), B.ncgr(bytes(64), 2, 1)]),
            }
            rom = B.rom(files, arm9=(d / "p.bin").read_bytes(),
                        overlays=[(0x02100000, B.blz_compress(ov), True)])
            (d / "game.nds").write_bytes(rom)

            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            info = sh(*stk, "rom", env=env)
            self.assertIn("overlays ARM9: 1", info)
            self.assertIn("CRC cabecera", info)
            self.assertNotIn("NO COINCIDE", info)
            out = sh(*stk, "assets", env=env)
            self.assertIn("Errores: 0", out, out)
            assets = d / "work" / "assets"
            self.assertTrue((assets / "converted" / "tex.nsbtx" / "stone.png").exists())
            self.assertTrue((assets / "converted" / "msg.json").exists())
            self.assertEqual((assets / "code" / "overlays" / "ov000.bin").read_bytes(), ov)
            out = sh(*stk, "recomp", "--compile", env=env)
            self.assertIn("compila correctamente", out, out)
            gen = (d / "work" / "build" / "recomp")
            table = (gen / "recomp_table.c").read_text()
            self.assertIn('"ov000", 0', table)          # el overlay es un módulo propio
            code = "".join(p.read_text() for p in gen.glob("rc_*.c"))
            self.assertIn("f_ov000_02100000", code)       # función del overlay
            self.assertRegex(code, rf"ST_CALLR?\(f_{fib:08X},")    # llamada directa overlay -> estático
            status = sh(*stk, "status", env=env)
            self.assertIn("recomp", status)


    def test_stk_learn(self):
        """stk recomp --learn: cada vuelta ejecuta el port real, aprende un fallo y recompila."""
        L = ROOT / "tests" / "learn"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fl = ["-mcpu=arm946e-s", "-marm", "-O2", "-ffreestanding", "-fno-builtin", "-I", d]
            offs = [0, 0, 0]
            for _ in range(2):   # 2 pasadas: la tabla de desplazamientos no cambia el tamaño
                (d / "offsets.h").write_text("static const u32 offsets[3] = {%s};\n" % ",".join(map(str, offs)))
                sh(ARMCC, *fl, "-c", L / "prog.c", "-o", d / "p.o")
                sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", L / "link.ld", d / "p.o", "-lgcc", "-o", d / "p.elf")
                syms = {l.split()[2]: int(l.split()[0], 16) for l in sh("arm-none-eabi-nm", d / "p.elf").splitlines()
                        if len(l.split()) == 3}
                offs = [syms[f] - 0x02000000 for f in ("f1", "f2", "f3")]
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "p.elf", d / "p.bin")
            self.assertEqual(syms["entry"], 0x02000000)
            (d / "game.nds").write_bytes(B.rom({"x.bin": bytes(16)}, arm9=(d / "p.bin").read_bytes()))
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--max-frames", "10", env=env)
            self.assertIn("terminó sin fallos", out, out)
            self.assertIn("Aprendizaje: 3 direcciones nuevas", out, out)
            misses = (d / "work" / ".stk" / "recomp_misses.txt").read_text().split("\n")
            self.assertEqual([int(m.split()[0], 16) for m in misses if m], [syms[f] for f in ("f1", "f2", "f3")])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("cmake"), "falta cmake")
class Watchdog(unittest.TestCase):
    def test_learn_reports_spin(self):
        """Un juego que gira sin E/S ni llamadas: el vigilante del port dice dónde (código 6)."""
        arm9 = struct.pack("<6I", 0xEB000000, 0xEAFFFFFE,     # bl spin ; b .
                           0xE59F1004,                         # spin: ldr r1, =0x027FFF8C
                           0xE5910000, 0xEAFFFFFD,             # 1: ldr r0, [r1] ; b 1b
                           0x027FFF8C)
        rom = B.rom({}, arm9=arm9 + bytes(64))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "game.nds").write_bytes(rom)
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--timeout", "15", env=env)
            self.assertIn("vigilante", out, out)
            self.assertRegex(out, r"02000008 arm9:02000008", out)    # la función que gira
            self.assertRegex(out, r"027FFF8C 4 bytes +lecturas", out)  # lo que lee el bucle
            self.assertIn("desensamblado automático", out)            # stk desensambla solo
            self.assertRegex(out, r"== arm9:02000008 ARM", out)
            dis = sh(*stk, "disasm", "02000008", "--c", env=env)
            self.assertIn("arm9:02000008 ARM", dis)
            self.assertIn("02000010", dis)


@unittest.skipUnless(shutil.which("cmake"), "falta cmake")
class HiddenCall(unittest.TestCase):
    def test_mov_lr_pc_bx(self):
        """'mov lr, pc' + 'bx rX' es una llamada: debe volver a la instrucción siguiente."""
        arm9 = struct.pack("<8I", 0xE52DE004,       # str lr, [sp, #-4]!
                           0xE59F0008,              # ldr r0, =func
                           0xE1A0E00F,              # mov lr, pc
                           0xE12FFF10,              # bx r0
                           0xE49DF004,              # ldr pc, [sp], #4
                           0x02000018,
                           0xE3A00007,              # func: mov r0, #7
                           0xE12FFF1E)              # bx lr
        rom = B.rom({}, arm9=arm9 + bytes(64))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "game.nds").write_bytes(rom)
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--timeout", "20", env=env)
            self.assertIn("terminó sin fallos", out, out)
            code = "".join(p.read_text() for p in (d / "work" / "build" / "recomp").glob("rc_*.c"))
            self.assertIn("ST_CALLR_DYN_EXPR(0x02000010u", code)

    def test_ldr_lr_bx(self):
        """'ldr lr, =X' + 'bx rX': llamada que al volver sigue en X (manejador de IRQ del SDK)."""
        arm9 = struct.pack("<10I", 0xE52DE004,      # str lr, [sp, #-4]!
                           0xE59F0008,              # ldr r0, =func
                           0xE59FE008,              # ldr lr, =after
                           0xE12FFF10,              # bx r0
                           0,
                           0x0200001C, 0x02000024,
                           0xE3A00007,              # func: mov r0, #7
                           0xE12FFF1E,              # bx lr
                           0xE49DF004)              # after: ldr pc, [sp], #4
        rom = B.rom({}, arm9=arm9 + bytes(64))
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "game.nds").write_bytes(rom)
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--timeout", "20", env=env)
            self.assertIn("terminó sin fallos", out, out)


@unittest.skipUnless(ARMCC and shutil.which("cmake"), "falta arm-none-eabi-gcc o cmake")
class Threads(unittest.TestCase):
    def test_fibers_save_load_context(self):
        """Dos hilos con SaveContext/LoadContext: cada uno corre en su fibra y A se reanuda tras
        su SaveContext (punto st_resume) cuando B le devuelve el control."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-Ttext=0x02000000", "-e", "_start",
               ROOT / "tests" / "threads" / "threads.S", "-o", d / "t.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "t.elf", d / "t.bin")
            rom = B.rom({}, arm9=(d / "t.bin").read_bytes() + bytes(64))
            (d / "game.nds").write_bytes(rom)
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--timeout", "30", env=env)
            self.assertIn("terminó sin fallos", out, out)


MELON_SRC = os.environ.get("ST_MELONDS_SOURCE")


@unittest.skipUnless(ARMCC and shutil.which("cmake") and shutil.which("c++") and MELON_SRC,
                     "falta arm-none-eabi-gcc, cmake, c++ o ST_MELONDS_SOURCE (copia local de melonDS 1.0)")
class Melon(unittest.TestCase):
    def test_vblank_irq_with_melonds(self):
        """Port híbrido: ARM9 recompilado + melonDS (ARM7, VBlank por la GPU, IRQ, IntrWait)."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            # El cargador (y melonDS) machaca los primeros 2 KB del ARM9 (área segura): el código va después
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-Ttext=0x02000800", "-e", "_start",
               ROOT / "tests" / "melon" / "vblank.S", "-o", d / "v.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "v.elf", d / "v.bin")
            counter = next(int(l.split()[0], 16) for l in sh("arm-none-eabi-nm", d / "v.elf").splitlines()
                           if l.endswith(" counter"))
            arm7 = struct.pack("<I", 0xEAFFFFFE)   # b .
            rom = B.rom({}, arm9=bytes(0x800) + (d / "v.bin").read_bytes() + bytes(64), arm7=arm7 + bytes(60),
                        arm9_entry=0x02000800)
            (d / "game.nds").write_bytes(rom)
            env = dict(os.environ, STK_HOME=str(d / "work"))
            stk = [sys.executable, ROOT / "stk.py"]
            sh(*stk, "init", "--rom", d / "game.nds", "--version", "eur", env=env)
            out = sh(*stk, "recomp", "--learn", "--melonds", "--max-frames", "30", "--timeout", "120",
                     "--dump-mem", f"{counter:08X}:4", env=env)
            self.assertIn("terminó sin fallos", out, out)
            m = __import__("re").search(rf"{counter:08X}: ([0-9A-F]{{8}})", out)
            self.assertIsNotNone(m, out)
            self.assertTrue(25 <= int(m.group(1), 16) <= 40, out)   # ~1 por cuadro (30 cuadros)
