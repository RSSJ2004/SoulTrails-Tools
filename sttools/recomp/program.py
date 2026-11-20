"""Análisis del binario: memoria, descubrimiento de funciones y tablas de saltos."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import arm, jumptable, thumb
from .insn import BRANCH, CALL, INDIRECT, NEXT, TABLE, UNDEF, Ins


@dataclass
class Module:
    """Rango de código que se carga junto: binario estático, ITCM o un overlay."""
    name: str
    base: int
    data: bytes
    overlay: int | None = None   # None = siempre residente

    def __post_init__(self) -> None:
        self.end = self.base + len(self.data)  # se consulta millones de veces: no es property

    def contains(self, addr: int) -> bool:
        return self.base <= addr < self.end


@dataclass
class Function:
    addr: int
    thumb: bool
    module: Module
    insns: dict[int, Ins] = field(default_factory=dict)
    calls: set[int] = field(default_factory=set)

    @property
    def key(self) -> int:
        return self.addr | int(self.thumb)

    @property
    def cname(self) -> str:
        prefix = f"f_{self.module.name}_" if self.module.overlay is not None else "f_"
        return f"{prefix}{self.addr:08X}{'_t' if self.thumb else ''}"


SIG_BYTES = 32  # = ST_SIG_BYTES en runtime/strecomp.h


def signature_at(m: Module, addr: int, n: int = SIG_BYTES) -> int:
    """FNV-1a de n bytes desde addr (igual que st_signature del runtime)."""
    o = addr - m.base
    hv = 2166136261
    chunk = m.data[o:o + n]
    for b in chunk + bytes(n - len(chunk)):
        hv = ((hv ^ b) * 16777619) & 0xFFFFFFFF
    return hv


class Program:
    def __init__(self, modules: list[Module], max_insns_per_function: int = 20000):
        self.modules = modules
        self.functions: dict[tuple[str, int], Function] = {}  # (módulo, addr)
        self.max_insns = max_insns_per_function
        self.warnings: list[str] = []
        self._cache: dict[tuple[str, int, bool], Ins] = {}
        # Trazados sin inicios conocidos (dependen solo de módulo, dirección y modo)
        self._traces: dict[tuple[str, int, bool], Function] = {}
        self._plausible: dict[tuple[str, int, bool], bool] = {}
        self._thumb_tables: dict[tuple[str, int], list[int] | None] = {}
        # Nombres conocidos (módulo, dirección) -> símbolo, p. ej. del informe de objdiff del decomp
        self.names: dict[tuple[str, int], str] = {}

    # ----------------------------------------------------------- memoria
    def module_for(self, addr: int, prefer: Module | None = None) -> Module | None:
        if prefer is not None and prefer.contains(addr):
            return prefer
        for m in self.modules:
            if m.overlay is None and m.contains(addr):
                return m
        return None

    def overlays_at(self, addr: int) -> list[Module]:
        return [m for m in self.modules if m.overlay is not None and m.contains(addr)]

    def candidates(self, addr: int, is_thumb: bool, sig: int | None = None) -> list[Module]:
        """Módulos donde puede estar 'addr': el residente si lo hay; si no, los overlays cuya
        firma coincide con 'sig' (fallos registrados por el runtime) o, sin firma, aquellos
        en los que el código es plausible."""
        m = self.module_for(addr)
        if m is not None:
            return [m]
        ovs = self.overlays_at(addr)
        if sig is not None:
            hit = [o for o in ovs if signature_at(o, addr) == sig]
            if hit:
                return hit
        return [o for o in ovs if self.plausible(o, addr, is_thumb)]

    @staticmethod
    def read(m: Module, addr: int, size: int) -> int | None:
        o = addr - m.base
        if o < 0 or o + size > len(m.data):
            return None
        return struct.unpack_from("<I" if size == 4 else "<H", m.data, o)[0]

    def decode(self, m: Module, addr: int, is_thumb: bool) -> Ins | None:
        key = (m.name, addr, is_thumb)
        if key in self._cache:
            return self._cache[key]
        if is_thumb:
            hw = self.read(m, addr, 2)
            if hw is None:
                return None
            ins = thumb.translate(addr, hw, self.read(m, addr + 2, 2))
        else:
            if addr & 3:
                return None
            w = self.read(m, addr, 4)
            if w is None:
                return None
            ins = arm.translate(addr, w)
            # "mov lr, pc" + salto indirecto (bx rX / ldr pc / mov pc) = llamada encubierta: el
            # destino vuelve a la instrucción siguiente (visto en el manejador de IRQ del SDK)
            prev = self.read(m, addr - 4, 4)
            # (no si el destino es el propio LR o un retorno de excepción: "ldr lr, =X; movs pc, lr")
            if (ins.kind == INDIRECT and "ST_INDIRECT(" in ins.code and ins.cond == 14 and prev is not None
                    and "c->r[14]" not in ins.code and "st_exception_return" not in ins.code):
                if prev == 0xE1A0E00F:
                    ins.code = ins.code.replace("ST_INDIRECT(", f"ST_CALL_DYN_EXPR({ins.next:#010x}u, ", 1)
                    ins.kind = NEXT
                    ins.asm += "  ; llamada (mov lr, pc)"
                elif (prev & 0xFFFFF000) == 0xE59FE000:
                    # "ldr lr, =X" + salto indirecto: llamada que vuelve a X (otra función; el
                    # manejador de IRQ del SDK continúa así en su cambio de hilo)
                    ret = self.read(m, addr - 4 + 8 + (prev & 0xFFF), 4)
                    if ret is not None:
                        ins.code = ins.code.replace("ST_INDIRECT(", "ST_CALL_THEN_RET(", 1)
                        ins.target = ret
                        ins.asm += f"  ; llamada que vuelve a {ret:#x}"
        self._cache[key] = ins
        return ins

    # ------------------------------------------------------- descubrimiento
    def table_bound(self, m: Module, ins: Ins) -> int:
        """Busca 'cmp rX, #N' antes de 'addls pc, pc, rX, lsl #2'."""
        reg = ins.raw & 0xF
        for back in range(1, 6):
            w = self.read(m, ins.addr - 4 * back, 4)
            if w is None:
                break
            if (w & 0x0FF0F000) == 0x03500000 and ((w >> 16) & 0xF) == reg:
                rot = ((w >> 8) & 0xF) * 2
                imm = w & 0xFF
                imm = ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF if rot else imm
                cond = ins.cond
                return imm + 1 if cond in (9, 14) else imm  # LS => <=, CC => <
        return 256

    def trace(self, m: Module, start: int, is_thumb: bool, starts: set[tuple[str, int]],
              strict: bool = False) -> Function | None:
        """Sigue el flujo desde 'start'. Con strict=True (candidatos del escaneo de punteros)
        devuelve None en cuanto el código deja de parecer una función real: instrucción
        indefinida, salida del módulo o tamaño desmesurado."""
        f = Function(start, is_thumb, m)
        work = [start]
        base, end = m.base, m.end
        while work:
            a = work.pop()
            if a in f.insns:
                continue
            if not (base <= a < end):
                if strict:
                    return None
                continue
            if len(f.insns) >= self.max_insns:
                if strict:
                    return None
                self.warnings.append(f"{m.name}:{start:#x}: función truncada en {self.max_insns} instrucciones")
                break
            ins = self.decode(m, a, is_thumb)
            if ins is None:
                if strict:
                    return None
                continue
            if strict and (ins.kind == UNDEF or (a == start and ins.cond != 14)):
                return None  # una función real no empieza con una instrucción condicional
            f.insns[a] = ins
            if ins.kind == BRANCH and ins.target is not None:
                tgt = ins.target & ~1
                if bool(ins.target & 1) != is_thumb:
                    f.calls.add(ins.target)  # cambio de modo: es otra función
                elif tgt == start or (m.name, tgt) not in starts:
                    if m.contains(tgt):
                        work.append(tgt)
            elif ins.kind == TABLE:
                n = self.table_bound(m, ins)
                ins.table = []
                for k in range(n):
                    ea = ins.addr + 8 + 4 * k
                    w = self.read(m, ea, 4)
                    if w is None or (w & 0x0F000000) != 0x0A000000:
                        break
                    ins.table.append(ea)
                    work.append(ea)
            elif ins.kind == CALL and ins.target is not None:
                f.calls.add(ins.target)
            elif ins.kind == INDIRECT and ins.target is not None:
                f.calls.add(ins.target)   # vuelta de una llamada "ldr lr, =X"
            elif ins.kind == INDIRECT and is_thumb:
                tg = self.thumb_table(m, a)
                if tg:
                    work.extend(tg)
            if not ins.ends_block:
                work.append(ins.next)
        return f

    def discover(self, seeds: list[tuple[int, bool, Module | None]], pointer_scan: bool = True) -> None:
        """Encuentra todas las funciones alcanzables desde las semillas (punto de entrada,
        exportación de Ghidra, prólogos, fallos registrados en ejecución) y, opcionalmente,
        desde punteros a código guardados en los datos (tablas de funciones, vtables)."""
        starts: dict[tuple[str, int], bool] = {}
        code: set[tuple[str, int]] = set()
        self._fixpoint(list(seeds), starts, code)
        if pointer_scan:
            self._fixpoint(self.pointer_seeds(starts, code), starts, code)
        # Pasada final con todos los inicios conocidos (B a otra función => llamada de cola)
        sk = set(starts)
        by_name = {x.name: x for x in self.modules}
        for (mname, addr), is_thumb in starts.items():
            m = by_name[mname]
            f = self.trace_free(m, addr, is_thumb)
            if self._branches_into(f, sk):
                f = self.trace(m, addr, is_thumb, sk)
            self.functions[(mname, addr)] = f

    def _branches_into(self, f: Function, starts: set[tuple[str, int]]) -> bool:
        """¿Algún salto del trazado libre cae en otra función conocida? (si no, el trazado
        con todos los inicios sería idéntico y se reutiliza)"""
        name = f.module.name
        for ins in f.insns.values():
            if ins.kind == BRANCH and ins.target is not None and bool(ins.target & 1) == f.thumb:
                t = ins.target & ~1
                if t != f.addr and (name, t) in starts:
                    return True
        return False

    def has_prologue(self, m: Module, addr: int, is_thumb: bool) -> bool:
        """'stmdb sp!, {..., lr}' (ARM) o 'push {..., lr}' (Thumb) en addr."""
        if is_thumb:
            hw = self.read(m, addr, 2)
            return hw is not None and (hw & 0xFF00) == 0xB500
        w = self.read(m, addr, 4)
        return w is not None and (w & 0xFFFF4000) == 0xE92D4000

    def thumb_table(self, m: Module, addr: int) -> list[int] | None:
        """Destinos de una tabla de saltos Thumb en addr (ver jumptable.py), memorizado."""
        key = (m.name, addr)
        if key not in self._thumb_tables:
            r16, rmem = jumptable.module_readers(m)
            self._thumb_tables[key] = jumptable.thumb_targets(r16, rmem, addr, m.contains)
        return self._thumb_tables[key]

    def trace_free(self, m: Module, addr: int, is_thumb: bool) -> Function:
        """trace() sin inicios conocidos, memorizado."""
        key = (m.name, addr, is_thumb)
        f = self._traces.get(key)
        if f is None:
            f = self._traces[key] = self.trace(m, addr, is_thumb, set())
        return f

    def plausible(self, m: Module, addr: int, is_thumb: bool) -> bool:
        """¿Puede haber una función real en addr? (memorizado: los punteros se repiten mucho)"""
        key = (m.name, addr, is_thumb)
        ok = self._plausible.get(key)
        if ok is None:
            f = self.trace(m, addr, is_thumb, set(), strict=True)
            ok = self._plausible[key] = f is not None
            if f is not None:
                self._traces.setdefault(key, f)  # sin cortes, coincide con trace_free
        return ok

    def pointer_seeds(self, starts, code) -> list:
        """Palabras alineadas fuera del código conocido que apuntan a código decodificable."""
        out = []
        resident = [(x.base, x.end, x) for x in self.modules if x.overlay is None]
        overlays = [x for x in self.modules if x.overlay is not None]
        for m in self.modules:
            d = m.data
            name, mbase, mend = m.name, m.base, m.end
            n = len(d) // 4
            words = struct.unpack_from(f"<{n}I", d, 0)
            for i, v in enumerate(words):
                tgt, th = v & ~1, bool(v & 1)
                if mbase <= tgt < mend:
                    mms = (m,)
                else:
                    mm = next((x for b, e, x in resident if b <= tgt < e), None)
                    if mm is not None:
                        mms = (mm,)
                    else:
                        # Puntero a un overlay desde otro módulo: no se sabe cuál estará cargado,
                        # así que solo se acepta si en ese overlay empieza con un prólogo real
                        # (si no, salen funciones falsas en los overlays que no tocan).
                        mms = tuple(x for x in overlays if x.base <= tgt < x.end and x is not m
                                    and self.has_prologue(x, tgt, th))
                        if not mms:
                            continue
                if (not th and tgt & 3) or (name, mbase + 4 * i) in code:
                    continue
                for mm in mms:
                    if (mm.name, tgt) not in starts and self.plausible(mm, tgt, th):
                        out.append((tgt, th, mm))
        return out

    def _fixpoint(self, queue, starts, code) -> None:
        while queue:
            addr, is_thumb, mod = queue.pop()
            addr &= ~1
            m = mod if (mod and mod.contains(addr)) else self.module_for(addr)
            if m is None:
                # Llamada a otro overlay (p. ej. de un overlay a otro cargado a la vez)
                for o in self.overlays_at(addr):
                    if (o is not mod and (o.name, addr) not in starts and self.plausible(o, addr, is_thumb)
                            and (len(self.overlays_at(addr)) == 1 or self.has_prologue(o, addr, is_thumb))):
                        queue.append((addr | int(is_thumb), is_thumb, o))
                continue
            key = (m.name, addr)
            if key in starts:
                if starts[key] != is_thumb:
                    self.warnings.append(f"{m.name}:{addr:#x} llamada en ARM y Thumb a la vez")
                continue
            starts[key] = is_thumb
            f = self.trace_free(m, addr, is_thumb)
            for a, ins in f.insns.items():
                for w in range(a & ~3, a + ins.size, 4):
                    code.add((m.name, w))
            for t in f.calls:
                queue.append((t & ~1, bool(t & 1), m))

    def scan_prologues(self, m: Module) -> list[tuple[int, bool, Module]]:
        """Heurística: 'stmdb sp!, {..., lr}' (ARM) y 'push {..., lr}' (Thumb)."""
        out = []
        d = m.data
        for o in range(0, len(d) - 3, 4):
            w = struct.unpack_from("<I", d, o)[0]
            if (w & 0xFFFF4000) == 0xE92D4000:
                out.append((m.base + o, False, m))
        for o in range(0, len(d) - 1, 2):
            hw = struct.unpack_from("<H", d, o)[0]
            if (hw & 0xFF00) == 0xB500:
                out.append((m.base + o, True, m))
        return out

    def find_function(self, m: Module, target: int) -> Function | None:
        mm = m if m.contains(target & ~1) else self.module_for(target & ~1)
        if mm is None:
            return None
        return self.functions.get((mm.name, target & ~1))
