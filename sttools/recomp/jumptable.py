"""Tablas de saltos Thumb sin depender del patrón exacto del compilador.

Ante un salto indirecto Thumb (`add pc, rX`, `mov pc, rX`, `bx rX`) se busca hacia atrás la
comprobación de rango `cmp rI, #N` + `bhi/bhs/bgt/bge` y se **evalúa el bloque de despacho**
(las instrucciones entre esa rama y el salto) con un intérprete Thumb mínimo para cada valor
de rI en 0..N. El bloque solo puede leer datos constantes del módulo (la tabla) y usar rI; si
hace otra cosa, se abandona y el salto queda como indirecto normal (lo resuelve el
despachador o el aprendizaje de fallos).

Sirve para el patrón de MWCC (desplazamientos de 16 bits con signo sumados al pc) y para
tablas de palabras absolutas; se validará con la ROM real (HANDOFF B.7).
"""
from __future__ import annotations

import struct

MAX_BACK = 16      # instrucciones hacia atrás para encontrar la comprobación de rango
MAX_CASES = 1024
M32 = 0xFFFFFFFF


class GiveUp(Exception):
    pass


def _cond_count(cond: int, imm: int) -> int | None:
    """Número de casos según la rama que salta al 'default' tras cmp rI, #imm."""
    return {8: imm + 1, 2: imm, 12: imm + 1, 10: imm}.get(cond)  # hi, cs(hs), gt, ge


def find_bound(read16, jump: int) -> tuple[int, int, int] | None:
    """(registro índice, casos, dirección de la primera instrucción del bloque)."""
    for back in range(1, MAX_BACK):
        a = jump - 2 * back
        hw = read16(a)
        if hw is None:
            return None
        if (hw & 0xF000) == 0xD000 and ((hw >> 8) & 0xF) < 14:          # Bcc
            prev = read16(a - 2)
            if prev is not None and (prev & 0xF800) == 0x2800:            # cmp rI, #imm8
                n = _cond_count((hw >> 8) & 0xF, prev & 0xFF)
                if n:
                    return (prev >> 8) & 7, n, a + 2
            return None
        if (hw & 0xF000) in (0xD000, 0xE000) or (hw & 0xF800) in (0xF000, 0xF800) or (hw & 0xFF00) in (0xBD00, 0x4700):
            return None   # otra rama: el bloque no es recto
    return None


def evaluate(read16, read_mem, block: int, jump: int, ireg: int, k: int) -> int:
    """Ejecuta [block, jump] con r[ireg] = k y devuelve el destino (con bit0 = Thumb)."""
    r: dict[int, int] = {ireg: k}

    def g(n: int) -> int:
        if n not in r:
            raise GiveUp
        return r[n]

    def ld(addr: int, size: int, signed: bool = False) -> int:
        v = read_mem(addr & M32, size)
        if v is None:
            raise GiveUp
        if signed:
            bits = size * 8
            v = v - (1 << bits) if v & (1 << (bits - 1)) else v
        return v & M32

    a = block
    while a <= jump:
        hw = read16(a)
        if hw is None:
            raise GiveUp
        pc = a + 4
        rd, rs = hw & 7, (hw >> 3) & 7
        top = hw >> 11
        if top < 3:                                   # lsl/lsr/asr imm
            imm = (hw >> 6) & 0x1F
            v = g(rs)
            if top == 0:
                v = (v << imm) & M32
            elif top == 1:
                v = v >> (imm or 32)
            else:
                v = ((v - (1 << 32) if v >> 31 else v) >> (imm or 32)) & M32
            r[rd] = v
        elif top == 3:                                # add/sub reg o imm3
            op = (hw >> 9) & 1
            o = (hw >> 6) & 7
            b = o if hw & 0x400 else g(o)
            r[rd] = (g(rs) - b if op else g(rs) + b) & M32
        elif (hw >> 13) == 1:                         # mov/cmp/add/sub imm8
            op, rd8, imm = (hw >> 11) & 3, (hw >> 8) & 7, hw & 0xFF
            if op == 0:
                r[rd8] = imm
            elif op == 2:
                r[rd8] = (g(rd8) + imm) & M32
            elif op == 3:
                r[rd8] = (g(rd8) - imm) & M32
        elif (hw >> 10) == 0x10:                      # ALU
            op = (hw >> 6) & 0xF
            x, y = (g(rd) if op not in (9, 15) else 0), g(rs)
            res = {0: x & y, 1: x ^ y, 2: (x << (y & 0xFF)) & M32 if (y & 0xFF) < 32 else 0,
                   3: x >> (y & 0xFF) if (y & 0xFF) < 32 else 0, 9: (-y) & M32, 12: x | y,
                   13: (x * y) & M32, 14: x & ~y & M32, 15: ~y & M32}.get(op)
            if op in (8, 10, 11):
                pass                                  # tst/cmp/cmn: solo flags
            elif res is None:
                raise GiveUp
            else:
                r[rd] = res
        elif (hw >> 10) == 0x11:                      # hi-reg
            op = (hw >> 8) & 3
            hd = rd | ((hw >> 4) & 8)
            hs = (hw >> 3) & 0xF
            src = pc if hs == 15 else g(hs)
            if a == jump:
                if op == 0 and hd == 15:
                    return ((pc + src) & ~1 | 1) & M32
                if op == 2 and hd == 15:
                    return (src & ~1 | 1) & M32
                if op == 3:
                    return src & M32
                raise GiveUp
            if op == 0:
                r[hd] = ((pc if hd == 15 else g(hd)) + src) & M32
            elif op == 2:
                r[hd] = src
            elif op != 1:
                raise GiveUp
        elif top == 9:                                # ldr rd, [pc, #imm]
            r[(hw >> 8) & 7] = ld((pc & ~3) + (hw & 0xFF) * 4, 4)
        elif (hw >> 12) == 5:                         # ldr/str con registro
            ro, rb = (hw >> 6) & 7, rs
            addr = g(rb) + g(ro)
            if hw & 0x200:                            # formato 8
                kind = (hw >> 10) & 3                 # 0 strh, 1 ldsb, 2 ldrh, 3 ldsh
                if kind == 0:
                    raise GiveUp
                r[rd] = ld(addr, 1, True) if kind == 1 else ld(addr, 2, kind == 3)
            else:
                if not hw & 0x800:
                    raise GiveUp                      # store
                r[rd] = ld(addr, 1 if hw & 0x400 else 4)
        elif (hw >> 13) == 3:                         # ldr/ldrb imm5
            if not hw & 0x800:
                raise GiveUp
            imm = (hw >> 6) & 0x1F
            b = bool(hw & 0x1000)
            r[rd] = ld(g(rs) + (imm if b else imm * 4), 1 if b else 4)
        elif (hw >> 12) == 8:                         # ldrh imm5
            if not hw & 0x800:
                raise GiveUp
            r[rd] = ld(g(rs) + ((hw >> 6) & 0x1F) * 2, 2)
        elif (hw >> 12) == 0xA and not hw & 0x800:    # add rd, pc, #imm (adr)
            r[(hw >> 8) & 7] = (pc & ~3) + (hw & 0xFF) * 4
        else:
            raise GiveUp
        a += 2
    raise GiveUp


def thumb_targets(read16, read_mem, jump: int, contains) -> list[int] | None:
    """Destinos (sin bit 0) del salto Thumb en 'jump', o None si no es una tabla reconocible."""
    b = find_bound(read16, jump)
    if b is None:
        return None
    ireg, n, block = b
    if n <= 0 or n > MAX_CASES:
        return None
    out = []
    try:
        for k in range(n):
            t = evaluate(read16, read_mem, block, jump, ireg, k)
            if not t & 1 or not contains(t & ~1):
                return None
            out.append(t & ~1)
    except GiveUp:
        return None
    return out


def module_readers(m):
    """(read16, read_mem) sobre los datos de un módulo."""
    def read16(a):
        o = a - m.base
        return struct.unpack_from("<H", m.data, o)[0] if 0 <= o <= len(m.data) - 2 else None

    def read_mem(a, size):
        o = a - m.base
        if o < 0 or o + size > len(m.data):
            return None
        return int.from_bytes(m.data[o:o + size], "little")
    return read16, read_mem
