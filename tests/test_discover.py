"""Descubrimiento de funciones (sttools/recomp/program.py) con código ARM sintético:
semillas, prólogos, escaneo de punteros, filtros de candidatos y tiempo."""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import builders as B  # noqa: E402
from sttools.recomp.program import Module, Program  # noqa: E402

BASE = 0x02000000


def discover(data: bytes, scan: bool = True):
    m = Module("main", BASE, data)
    p = Program([m])
    p.discover([(BASE, False, None)], pointer_scan=scan)
    return p, {addr for (_, addr) in p.functions}


class Discover(unittest.TestCase):
    def test_calls_and_pointer_table(self):
        data, info = B.code_with_data(BASE, 20, 8, 2000)
        p, found = discover(data, scan=False)
        self.assertEqual(found, set(info["funcs"]))          # cadena de BL desde la entrada
        p, found = discover(data)
        self.assertTrue(set(info["leaves"]) <= found)        # solo por la tabla de punteros
        self.assertNotIn(info["bad_undef"], found)           # contiene una instrucción indefinida
        self.assertNotIn(info["off_end"], found)             # se sale del módulo
        for a in info["funcs"]:                              # cada función con su cuerpo exacto
            self.assertEqual(sorted(p.functions[("main", a)].insns), [a, a + 4, a + 8, a + 12])

    def test_arm_and_thumb_seed_prefers_valid_mode(self):
        """Una dirección sembrada como ARM (datos que no son ARM válido) y luego saltada en Thumb:
        se queda la versión Thumb (con la ROM, "vill" = 2 ldr Thumb tomados por ARM inválido)."""
        import struct
        data = struct.pack("<2I", 0xE7F000F0, 0x46C04770)   # ARM indefinida | Thumb: lsls; b; bx lr; nop
        m = Module("main", BASE, data)
        for seeds in ([(BASE, False, m), (BASE, True, m)], [(BASE, True, m), (BASE, False, m)]):
            p = Program([m])
            p.discover(seeds, pointer_scan=False)
            self.assertTrue(p.functions[("main", BASE)].thumb)

    def test_deterministic_and_reused(self):
        data, _ = B.code_with_data(BASE, 50, 10, 5000, seed=7)
        p1, f1 = discover(data)
        p2, f2 = discover(data)
        self.assertEqual(f1, f2)
        for k in p1.functions:
            self.assertEqual(sorted(p1.functions[k].insns), sorted(p2.functions[k].insns))

    def test_speed(self):
        # ~1,2 MB: 40000 funciones + 200000 palabras de datos. Antes de la memorización
        # tardaba varias veces más; el margen es amplio para máquinas lentas.
        data, info = B.code_with_data(BASE, 40000, 2000, 200000)
        t = time.perf_counter()
        p, found = discover(data)
        dt = time.perf_counter() - t
        real = set(info["funcs"]) | set(info["leaves"])
        self.assertTrue(real <= found)
        self.assertLess(len(found - real), len(real) // 100)  # falsos positivos desde datos < 1 %
        print(f"descubrimiento: {len(data) // 1024} KB, {len(found)} funciones, {dt:.1f} s")
        self.assertLess(dt, 60)


if __name__ == "__main__":
    unittest.main()
