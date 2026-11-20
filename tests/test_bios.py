"""HLE del BIOS (runtime/st_bios.c): SWI de descompresión, memoria y matemáticas, y la
cadena de IRQ (IME/IE/IF, manejador en DTCM+3FFC, VBlankIntrWait/IntrWait). Un programa
invitado (tests/bios) llama a las SWI desde ARM y Thumb; las referencias están en Python
(compresores de tests/builders.py, fórmulas de GBATEK y valores de catálogo)."""
import math
import random
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
from sttools.recomp import driver  # noqa: E402
from sttools.recomp.program import Module  # noqa: E402

BI = ROOT / "tests" / "bios"
ARMCC = shutil.which("arm-none-eabi-gcc")
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def sh(*cmd, check=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"{' '.join(map(str, cmd))}\n{r.stdout}\n{r.stderr}")
    return r


def c_array(name: str, data: bytes, typ: str = "u8") -> str:
    return f"static const {typ} {name}[] __attribute__((aligned(4))) = {{{','.join(str(b) for b in data)}}};\n"


def crc16_modbus(crc: int, data: bytes) -> int:
    """CRC-16/MODBUS (polinomio reflejado A001h), el de la SWI GetCRC16 y el de la cabecera
    de la ROM. Se comprueba abajo con su valor de catálogo: crc("123456789") = 4B37h."""
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def bit_unpack(src: bytes, sw: int, dw: int, off: int, zero: bool) -> bytes:
    out, acc, n = bytearray(), 0, 0
    for b in src:
        for k in range(0, 8, sw):
            v = (b >> k) & ((1 << sw) - 1)
            if v or zero:
                v += off
            acc |= (v & ((1 << dw) - 1)) << n
            n += dw
            if n >= 32:
                out += struct.pack("<I", acc)
                acc = n = 0
    return bytes(out)


@unittest.skipUnless(ARMCC and CC, "falta arm-none-eabi-gcc o cc")
class Bios(unittest.TestCase):
    def test_crc_reference(self):
        self.assertEqual(crc16_modbus(0xFFFF, b"123456789"), 0x4B37)

    def test_swis_and_irq(self):
        rnd = random.Random(3)
        text = bytes(b"Todos al tren! " * 20) + bytes(rnd.randrange(4) for _ in range(300))
        rl_data = bytes([1] * 50 + [2, 3, 4] + [5] * 3 + list(range(40)) + [9] * 200)
        nib = bytes(rnd.choice([0x11, 0x12, 0x21, 0x33, 0x3F, 0xF1]) for _ in range(256))
        b8 = bytes(rnd.choice(b"abcdefghijklmnop") for _ in range(400))
        d8 = bytes((i * 7 + (i >> 3)) & 0xFF for i in range(200))
        d16 = struct.pack("<100H", *((i * 1234 + 77) & 0xFFFF for i in range(100)))
        unpack = bytes([0x1B, 0xE4, 0x00, 0xFF, 0x55, 0xAA, 0x0F, 0xF0])
        cpuset = struct.pack("<16I", *(0x11111111 * (i + 1) & 0xFFFFFFFF for i in range(16)))
        crc = bytes(rnd.randrange(256) for _ in range(77))
        divs = [(100, 7), (-100, 7), (100, -7), (-2147483647, 3), (7, 100), (0x7FFFFFFF, 1), (-1, 2)]
        sqrts = [0, 1, 2, 15, 16, 17, 65535, 65536, 0xFFFFFFFF, 123456789]
        hdr = (c_array("lz_src", B.lz10_compress(text)) + c_array("rl_src", B.rl_compress(rl_data))
               + c_array("huff4_src", B.huff_compress(nib, 4)) + c_array("huff8_src", B.huff_compress(b8, 8))
               + c_array("d8_src", B.diff_filter(d8, False)) + c_array("d16_src", B.diff_filter(d16, True))
               + c_array("unpack_src", unpack) + c_array("crc_src", crc)
               + "static const u32 cpuset_src[] = {" + ",".join(f"{v}u" for v in struct.unpack("<16I", cpuset)) + "};\n"
               + "static const int div_in[][2] = {" + ",".join(f"{{{a},{b}}}" for a, b in divs) + "};\n"
               + "static const u32 sqrt_in[] = {" + ",".join(f"{v}u" for v in sqrts) + "};\n")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bios_data.h").write_text(hdr)
            fl = ["-mcpu=arm946e-s", "-O2", "-ffreestanding", "-fno-builtin", "-mthumb-interwork", "-I", d]
            sh(ARMCC, *fl, "-marm", "-c", BI / "bios.c", "-o", d / "a.o")
            sh(ARMCC, *fl, "-mthumb", "-c", BI / "bios_thumb.c", "-o", d / "t.o")
            sh(ARMCC, "-mcpu=arm946e-s", "-nostdlib", "-T", BI / "link.ld", d / "a.o", d / "t.o", "-lgcc", "-o", d / "p.elf")
            sh("arm-none-eabi-objcopy", "-O", "binary", d / "p.elf", d / "p.bin")
            syms = {}
            for line in sh("arm-none-eabi-nm", "-S", d / "p.elf").stdout.splitlines():
                p = line.split()
                if len(p) == 4:
                    syms[p[3]] = (int(p[0], 16), int(p[1], 16))
            seeds = d / "seeds.txt"
            seeds.write_text(f"{syms['bios_main'][0]:08X} arm\n{syms['irq_handler'][0]:08X} arm\n")
            out = d / "gen"
            res = driver.run([Module("main", 0x02000000, (d / "p.bin").read_bytes())], out, None, seed_files=[seeds])
            self.assertEqual(res["undefined"], 0)
            sh(CC, "-O1", "-w", "-I", ROOT / "runtime", "-I", out, BI / "harness.c",
               *sorted((ROOT / "runtime").glob("*.c")), *sorted(out.glob("*.c")), "-o", d / "h")
            ram = d / "ram.bin"
            r = sh(d / "h", d / "p.bin", ram, f"{syms['bios_main'][0]:x}", "400000", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            mem = ram.read_bytes()

            def get(name, n=None):
                a, size = syms[name]
                return mem[a - 0x02000000: a - 0x02000000 + (n if n is not None else size)]

            self.assertEqual(get("out_lz", len(text)), text)
            self.assertEqual(get("out_lzv", len(text)), text)
            self.assertEqual(get("out_rl", len(rl_data)), rl_data)
            self.assertEqual(get("out_huff4", len(nib)), nib)
            self.assertEqual(get("out_huff8", len(b8)), b8)
            self.assertEqual(get("out_d8", len(d8)), d8)
            self.assertEqual(get("out_d16", len(d16)), d16)
            self.assertEqual(get("out_unpack", 32), bit_unpack(unpack, 2, 8, 0x10, True))
            self.assertEqual(get("out_cpuset"), cpuset)
            self.assertEqual(get("out_fill"), cpuset[12:16] * 16)
            self.assertEqual(get("out_fast"), cpuset)                 # 13 se redondea a 16
            m = struct.unpack("<64I", get("math"))
            want = []
            for a, b in divs:
                q = int(a / b)
                want += [q & 0xFFFFFFFF, (a - q * b) & 0xFFFFFFFF, abs(q)]
            want += [math.isqrt(v) for v in sqrts] + [1000, crc16_modbus(0xFFFF, crc), 0]
            self.assertEqual(list(m[:len(want)]), want)
            info = struct.unpack("<16I", get("irq_info"))
            self.assertEqual(info[0], 0x12)        # el manejador corre en modo IRQ
            self.assertEqual(info[1], 4)           # 3 VBlankIntrWait + 1 IntrWait
            self.assertEqual(info[2], 0x1F)        # vuelve al modo sistema
            self.assertEqual(info[3], 0)           # IF reconocido por el manejador
            self.assertEqual(info[4], 0)           # IntrWait consumió su bit
            self.assertIn("irq servidas: 4", r.stdout)


if __name__ == "__main__":
    unittest.main()
