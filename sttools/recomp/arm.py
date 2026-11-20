"""Traductor de instrucciones ARM (ARMv5TE, ARM946E-S) a C."""
from __future__ import annotations

import re

from .insn import BRANCH, CALL, INDIRECT, NEXT, RET, TABLE, UNDEF, Ins, h, sx

DP_OPS = ["and", "eor", "sub", "rsb", "add", "adc", "sbc", "rsc", "tst", "teq", "cmp", "cmn", "orr", "mov", "bic", "mvn"]
SHIFT_NAMES = ["lsl", "lsr", "asr", "ror"]


def R(n: int, pc: int) -> str:
    return h(pc) if n == 15 else f"c->r[{n}]"


def _imm_shift(rm: str, typ: int, amt: int, want_carry: bool) -> tuple[str, str]:
    """Desplazamiento por inmediato. Devuelve (expr_valor, expr_carry)."""
    if typ == 0:
        if amt == 0:
            return rm, "c->c"
        return f"({rm} << {amt})", f"(({rm} >> {32 - amt}) & 1)"
    if typ == 1:
        if amt == 0:  # LSR #32
            return "0u", f"({rm} >> 31)"
        return f"({rm} >> {amt})", f"(({rm} >> {amt - 1}) & 1)"
    if typ == 2:
        if amt == 0:  # ASR #32
            return f"(uint32_t)((int32_t){rm} >> 31)", f"({rm} >> 31)"
        return f"(uint32_t)((int32_t){rm} >> {amt})", f"(({rm} >> {amt - 1}) & 1)"
    if amt == 0:  # RRX
        return f"((c->c << 31) | ({rm} >> 1))", f"({rm} & 1)"
    return f"st_ror({rm}, {amt})", f"(({rm} >> {amt - 1}) & 1)"


def _reg_shift_code(rm: str, typ: int, rs: str) -> str:
    fn = ["st_lsl_c", "st_lsr_c", "st_asr_c", "st_ror_c"][typ]
    return f"op2 = {fn}({rm}, {rs} & 0xFF, &sc);"


def _set_nz(var: str) -> str:
    return f"c->n = {var} >> 31; c->z = ({var} == 0);"


def _write_reg(rd: int, val: str, s: bool, *, link_ret=False) -> str:
    if rd != 15:
        return f"c->r[{rd}] = {val};"
    if s:  # retorno de excepción: CPSR = SPSR (puede volver a Thumb)
        return f"ST_EXC_RETURN({val}); /* ST_INDIRECT st_exception_return */"
    return f"ST_INDIRECT({val});"


def data_processing(ins: Ins, w: int, pc: int) -> None:
    op = (w >> 21) & 0xF
    s = bool(w & (1 << 20))
    rn = (w >> 16) & 0xF
    rd = (w >> 12) & 0xF
    lines = ["uint32_t op2, sc = c->c; (void)sc;"]
    if w & (1 << 25):
        rot = ((w >> 8) & 0xF) * 2
        imm = w & 0xFF
        imm = ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF if rot else imm
        lines.append(f"op2 = {h(imm)};")
        if rot:
            lines.append(f"sc = {imm >> 31};")
        op2_txt = f"#{imm:#x}"
    else:
        rm = w & 0xF
        typ = (w >> 5) & 3
        if w & 0x10:
            rs = (w >> 8) & 0xF
            rmv = R(rm, pc + 4)
            lines.append(_reg_shift_code(rmv, typ, R(rs, pc + 4)))
            op2_txt = f"r{rm}, {SHIFT_NAMES[typ]} r{rs}"
        else:
            amt = (w >> 7) & 0x1F
            val, car = _imm_shift(R(rm, pc), typ, amt, s)
            lines.append(f"op2 = {val};")
            if car != "c->c":
                lines.append(f"sc = {car};")
            op2_txt = f"r{rm}" + (f", {SHIFT_NAMES[typ]} #{amt}" if amt or typ else "")
    a = R(rn, pc if not (w & 0x10 and not w & (1 << 25)) else pc + 4)
    name = DP_OPS[op]
    if op in (13, 15):
        ins.asm = f"{name}{'s' if s else ''} r{rd}, {op2_txt}"
    elif op in (8, 9, 10, 11):
        ins.asm = f"{name} r{rn}, {op2_txt}"
    else:
        ins.asm = f"{name}{'s' if s else ''} r{rd}, r{rn}, {op2_txt}"
    logical = op in (0, 1, 8, 9, 12, 13, 14, 15)
    # Cálculo
    if op in (0, 8):
        expr = f"{a} & op2"
    elif op in (1, 9):
        expr = f"{a} ^ op2"
    elif op == 12:
        expr = f"{a} | op2"
    elif op == 13:
        expr = "op2"
    elif op == 14:
        expr = f"{a} & ~op2"
    elif op == 15:
        expr = "~op2"
    else:
        expr = None
    if logical:
        lines.append(f"uint32_t res = {expr};")
        if s:
            lines.append(_set_nz("res") + " c->c = sc;")
    else:
        # aritméticas: a + b + cin
        x, y, cin = {
            2: (a, "~op2", "1"), 3: ("op2", f"~{a}", "1"), 4: (a, "op2", "0"), 5: (a, "op2", "c->c"),
            6: (a, "~op2", "c->c"), 7: ("op2", f"~{a}", "c->c"), 10: (a, "~op2", "1"), 11: (a, "op2", "0"),
        }[op]
        if s:
            lines.append(f"uint32_t res = st_adc_flags(c, {x}, {y}, {cin});")
        else:
            lines.append(f"uint32_t res = {x} + {y} + {cin};")
    if op not in (8, 9, 10, 11):
        if rd == 15:
            # Retorno/salto
            if not s and op == 13 and not (w & (1 << 25)) and (w & 0xFFF) == 14:
                lines.append("ST_RETURN(res);")
                ins.kind = RET
            else:
                lines.append(_write_reg(15, "res", s))
                ins.kind = INDIRECT
        else:
            lines.append(f"c->r[{rd}] = res;")
    ins.code = "{ " + " ".join(lines) + " }"


def multiply(ins: Ins, w: int) -> None:
    rd = (w >> 16) & 0xF
    rn = (w >> 12) & 0xF
    rs = (w >> 8) & 0xF
    rm = w & 0xF
    s = bool(w & (1 << 20))
    acc = bool(w & (1 << 21))
    expr = f"c->r[{rm}] * c->r[{rs}]" + (f" + c->r[{rn}]" if acc else "")
    code = f"{{ uint32_t res = {expr}; c->r[{rd}] = res;" + (" " + _set_nz("res") if s else "") + " }"
    ins.asm = f"{'mla' if acc else 'mul'}{'s' if s else ''} r{rd}, r{rm}, r{rs}"
    ins.code = code


def multiply_long(ins: Ins, w: int) -> None:
    hi = (w >> 16) & 0xF
    lo = (w >> 12) & 0xF
    rs = (w >> 8) & 0xF
    rm = w & 0xF
    signed = bool(w & (1 << 22))
    acc = bool(w & (1 << 21))
    s = bool(w & (1 << 20))
    if signed:
        prod = f"(uint64_t)((int64_t)(int32_t)c->r[{rm}] * (int64_t)(int32_t)c->r[{rs}])"
    else:
        prod = f"((uint64_t)c->r[{rm}] * (uint64_t)c->r[{rs}])"
    accx = f" + (((uint64_t)c->r[{hi}] << 32) | c->r[{lo}])" if acc else ""
    code = (f"{{ uint64_t res = {prod}{accx}; c->r[{lo}] = (uint32_t)res; c->r[{hi}] = (uint32_t)(res >> 32);"
            + (" c->n = (uint32_t)(res >> 63); c->z = (res == 0);" if s else "") + " }")
    ins.asm = f"{'s' if signed else 'u'}{'mlal' if acc else 'mull'} r{lo}, r{hi}, r{rm}, r{rs}"
    ins.code = code


def dsp_multiply(ins: Ins, w: int) -> None:
    op = (w >> 21) & 3
    rd = (w >> 16) & 0xF
    rn = (w >> 12) & 0xF
    rs = (w >> 8) & 0xF
    rm = w & 0xF
    x = (w >> 5) & 1
    y = (w >> 6) & 1
    hx = f"(int32_t)(int16_t)(c->r[{rm}] >> {16 if x else 0})"
    hy = f"(int32_t)(int16_t)(c->r[{rs}] >> {16 if y else 0})"
    if op == 0:  # SMLAxy
        ins.code = (f"{{ int32_t p = {hx} * {hy}; uint32_t a = c->r[{rn}]; uint32_t res = (uint32_t)p + a;"
                    f" if ((~((uint32_t)p ^ a) & ((uint32_t)p ^ res)) >> 31) c->q = 1; c->r[{rd}] = res; }}")
        ins.asm = "smlaxy"
    elif op == 1:
        prod = f"(uint32_t)(((int64_t)(int32_t)c->r[{rm}] * {hy}) >> 16)"
        if x:  # SMULWy
            ins.code = f"c->r[{rd}] = {prod};"
            ins.asm = "smulwy"
        else:  # SMLAWy
            ins.code = (f"{{ uint32_t p = {prod}; uint32_t a = c->r[{rn}]; uint32_t res = p + a;"
                        f" if ((~(p ^ a) & (p ^ res)) >> 31) c->q = 1; c->r[{rd}] = res; }}")
            ins.asm = "smlawy"
    elif op == 2:  # SMLALxy (rd=hi, rn=lo)
        ins.code = (f"{{ int64_t acc = (int64_t)(((uint64_t)c->r[{rd}] << 32) | c->r[{rn}]);"
                    f" acc += (int64_t){hx} * {hy}; c->r[{rn}] = (uint32_t)acc; c->r[{rd}] = (uint32_t)((uint64_t)acc >> 32); }}")
        ins.asm = "smlalxy"
    else:  # SMULxy
        ins.code = f"c->r[{rd}] = (uint32_t)({hx} * {hy});"
        ins.asm = "smulxy"


def qarith(ins: Ins, w: int) -> None:
    op = (w >> 21) & 3
    rn = (w >> 16) & 0xF
    rd = (w >> 12) & 0xF
    rm = w & 0xF
    b = f"c->r[{rn}]"
    if op & 2:
        b = f"st_qadd(c, c->r[{rn}], c->r[{rn}])"
    fn = "st_qsub" if op & 1 else "st_qadd"
    ins.code = f"c->r[{rd}] = {fn}(c, c->r[{rm}], {b});"
    ins.asm = ["qadd", "qsub", "qdadd", "qdsub"][op]


def single_transfer(ins: Ins, w: int, pc: int) -> None:
    p = bool(w & (1 << 24))
    u = bool(w & (1 << 23))
    b = bool(w & (1 << 22))
    wb = bool(w & (1 << 21))
    load = bool(w & (1 << 20))
    rn = (w >> 16) & 0xF
    rd = (w >> 12) & 0xF
    if w & (1 << 25):
        rm = w & 0xF
        typ = (w >> 5) & 3
        amt = (w >> 7) & 0x1F
        off, _ = _imm_shift(R(rm, pc), typ, amt, False)
    else:
        off = h(w & 0xFFF)
    sign = "+" if u else "-"
    base = R(rn, pc)
    lines = [f"uint32_t base = {base};", f"uint32_t off = {off};",
             f"uint32_t addr = {'base ' + sign + ' off' if p else 'base'};"]
    post_wb = not p or wb
    wb_line = f"c->r[{rn}] = base {sign} off;" if post_wb and rn != 15 else ""
    if load:
        rd_expr = "st_read8(c, addr)" if b else "st_ldr32(c, addr)"
        lines.append(f"uint32_t val = {rd_expr};")
        if wb_line:
            lines.append(wb_line)
        if rd == 15:
            if rn == 13 and not p and u:  # ldr pc, [sp], #4  => pop {pc}
                lines.append("ST_RETURN(val);")
                ins.kind = RET
            else:
                lines.append("ST_INDIRECT(val);")
                ins.kind = INDIRECT
        else:
            lines.append(f"c->r[{rd}] = val;")
    else:
        val = h(pc + 4) if rd == 15 else f"c->r[{rd}]"
        lines.append(f"{'st_write8(c, addr, (uint8_t)' if b else 'st_write32(c, addr & ~3u, '}{val});")
        if wb_line:
            lines.append(wb_line)
    name = f"{'ldr' if load else 'str'}{'b' if b else ''}"
    ins.asm = _mem_asm(name, rd, rn, off, sign, p, wb, pc)
    ins.code = "{ " + " ".join(lines) + " }"


def _mem_asm(name: str, rd: int, rn: int, off: str, sign: str, p: bool, wb: bool, pc: int) -> str:
    """Texto de ensamblador de una carga/almacenamiento; relativo al PC muestra '=dirección'."""
    o = off.replace("c->r[", "r").replace("]", "").replace("0x", "#0x")
    zero = off in ("0x00000000u", "0x0u") or re.fullmatch(r"0x0+u", off)
    if rn == 15 and p and re.fullmatch(r"0x[0-9A-Fa-f]+u", off):
        lit = (pc + int(off[:-1], 16)) if sign == "+" else (pc - int(off[:-1], 16))
        return f"{name} r{rd}, ={lit & 0xFFFFFFFF:#x}"
    o = f"#{int(o[3:].rstrip('u'), 16):#x}" if o.startswith("#0x") else o
    if sign == "-":
        o, sg = ("#-" + o[1:], "") if o.startswith("#") else (o, "-")
    else:
        sg = ""
    if not p:
        return f"{name} r{rd}, [r{rn}], {sg}{o}"
    return f"{name} r{rd}, [r{rn}]" if zero else f"{name} r{rd}, [r{rn}, {sg}{o}]{'!' if wb else ''}"


def halfword_transfer(ins: Ins, w: int, pc: int) -> None:
    p = bool(w & (1 << 24))
    u = bool(w & (1 << 23))
    imm = bool(w & (1 << 22))
    wb = bool(w & (1 << 21))
    load = bool(w & (1 << 20))
    rn = (w >> 16) & 0xF
    rd = (w >> 12) & 0xF
    sh = (w >> 5) & 3
    off = h(((w >> 4) & 0xF0) | (w & 0xF)) if imm else R(w & 0xF, pc)
    sign = "+" if u else "-"
    lines = [f"uint32_t base = {R(rn, pc)};", f"uint32_t off = {off};",
             f"uint32_t addr = {'base ' + sign + ' off' if p else 'base'};"]
    wb_line = f"c->r[{rn}] = base {sign} off;" if (not p or wb) and rn != 15 else ""
    if load:
        if sh == 1:
            lines.append("uint32_t val = st_read16(c, addr & ~1u);")
            name = "ldrh"
        elif sh == 2:
            lines.append("uint32_t val = (uint32_t)(int32_t)(int8_t)st_read8(c, addr);")
            name = "ldrsb"
        else:
            lines.append("uint32_t val = (uint32_t)(int32_t)(int16_t)st_read16(c, addr & ~1u);")
            name = "ldrsh"
        if wb_line:
            lines.append(wb_line)
        if rd == 15:
            lines.append("ST_INDIRECT(val);")
            ins.kind = INDIRECT
        else:
            lines.append(f"c->r[{rd}] = val;")
    else:
        if sh == 1:
            lines.append(f"st_write16(c, addr & ~1u, (uint16_t)c->r[{rd}]);")
            name = "strh"
        elif sh == 2:  # LDRD
            lines.append(f"uint32_t v0 = st_read32(c, addr & ~3u); uint32_t v1 = st_read32(c, (addr + 4) & ~3u);")
            if wb_line:
                lines.append(wb_line)
            lines.append(f"c->r[{rd}] = v0; c->r[{rd + 1 & 15}] = v1;")
            wb_line = ""
            name = "ldrd"
        else:  # STRD
            lines.append(f"st_write32(c, addr & ~3u, c->r[{rd}]); st_write32(c, (addr + 4) & ~3u, c->r[{rd + 1 & 15}]);")
            name = "strd"
        if wb_line:
            lines.append(wb_line)
    ins.asm = _mem_asm(name, rd, rn, off, sign, p, wb, pc)
    ins.code = "{ " + " ".join(lines) + " }"


def block_transfer(ins: Ins, w: int, pc: int) -> None:
    p = bool(w & (1 << 24))
    u = bool(w & (1 << 23))
    s = bool(w & (1 << 22))
    wb = bool(w & (1 << 21))
    load = bool(w & (1 << 20))
    rn = (w >> 16) & 0xF
    regs = [i for i in range(16) if w & (1 << i)]
    n = len(regs)
    lines = [f"uint32_t base = c->r[{rn}];"]
    if u:
        start = "base + 4" if p else "base"
        new = f"base + {4 * n}"
    else:
        start = f"base - {4 * n}" if p else f"base - {4 * n} + 4"
        new = f"base - {4 * n}"
    lines.append(f"uint32_t a = ({start}) & ~3u;")
    user_bank = s and not (load and 15 in regs)   # LDM/STM ^ sin PC: registros de usuario
    if load:
        if user_bank and (w & 0x7FFF) == 0x7FFF:
            # Carga de un contexto completo de usuario (OS_LoadContext / cambio de hilo del SDK):
            # se apunta la pila del hilo saliente por si el retorno de excepción cambia de hilo
            lines.append("st_thread_note_user_sp(st_user_reg(c, 13));")
        for i, r in enumerate(regs):
            lines.append(f"uint32_t v{r} = st_read32(c, a + {4 * i});")
        # ARMv5 con la base en la lista: hay escritura si es el único registro o no es el
        # último (GBATEK); si no, gana el valor cargado.
        wb_now = wb and (rn not in regs or regs == [rn] or regs[-1] != rn)
        for r in regs:
            if r != 15:
                lines.append(f"st_set_user_reg(c, {r}, v{r});" if user_bank and r >= 8 else f"c->r[{r}] = v{r};")
        if wb_now:
            lines.append(f"c->r[{rn}] = {new};")
        if 15 in regs:
            if s:
                lines.append("ST_EXC_RETURN(v15); /* ST_INDIRECT st_exception_return */")
                ins.kind = INDIRECT
            elif rn == 13:
                lines.append("ST_RETURN(v15);")
                ins.kind = RET
            else:
                lines.append("ST_INDIRECT(v15);")
                ins.kind = INDIRECT
    else:
        # ARMv5: con la base en la lista siempre se guarda el valor antiguo
        for i, r in enumerate(regs):
            if r == 15:
                val = h(pc + 4)
            elif user_bank and r >= 8:
                val = f"st_user_reg(c, {r})"
            else:
                val = f"c->r[{r}]"
            lines.append(f"st_write32(c, a + {4 * i}, {val});")
        if (w & 0x7FF0) == 0x7FF0:
            # Guardado de un contexto (r4-r14): la pila guardada identifica al hilo cuando se
            # reanude (SaveContext del SDK o el cambio de hilo desde la IRQ)
            sp = "st_user_reg(c, 13)" if user_bank else "c->r[13]"
            lines.append(f"st_thread_note_saved_sp({sp});")
        if wb:
            lines.append(f"c->r[{rn}] = {new};")
    mode = ("i" if u else "d") + ("b" if p else "a")
    ins.asm = (f"{'ldm' if load else 'stm'}{mode} r{rn}{'!' if wb else ''}, "
               f"{{{','.join('r%d' % r for r in regs)}}}{'^' if s else ''}")
    ins.code = "{ " + " ".join(lines) + " }"


def translate(addr: int, w: int) -> Ins:
    ins = Ins(addr, 4, False, w)
    pc = addr + 8
    cond = w >> 28
    ins.cond = cond
    if cond == 0xF:
        ins.cond = 14
        if (w & 0x0E000000) == 0x0A000000:  # BLX imm
            off = sx(w & 0xFFFFFF, 24) << 2
            hbit = (w >> 24) & 1
            ins.kind = CALL
            ins.target = ((pc + off + (hbit << 1)) | 1) & 0xFFFFFFFF
            ins.asm = f"blx {ins.target & ~1:#x}"
            ins.code = f"c->r[14] = {h(addr + 4)};"
            return ins
        if (w & 0x0D70F000) == 0x0550F000:  # PLD
            ins.asm = "pld"
            ins.code = "/* pld */"
            return ins
        return undefined(ins)

    t = (w >> 25) & 7
    if t in (0, 1):
        if (w & 0x0FFFFFF0) == 0x012FFF10:  # BX
            rm = w & 0xF
            ins.asm = f"bx r{rm}"
            if rm == 14:
                ins.code = "ST_RETURN(c->r[14]);"
                ins.kind = RET
            else:
                ins.code = f"ST_INDIRECT({R(rm, pc)});"
                ins.kind = INDIRECT
            return ins
        if (w & 0x0FFFFFF0) == 0x012FFF30:  # BLX reg
            rm = w & 0xF
            ins.asm = f"blx r{rm}"
            ins.kind = CALL
            ins.target = None
            ins.code = f"uint32_t tgt = c->r[{rm}]; c->r[14] = {h(addr + 4)};"
            ins.table_reg = "tgt"
            return ins
        if t == 0:
            if (w & 0x0FFF0FF0) == 0x016F0F10:
                rd, rm = (w >> 12) & 0xF, w & 0xF
                ins.asm = f"clz r{rd}, r{rm}"
                ins.code = f"c->r[{rd}] = st_clz(c->r[{rm}]);"
                return ins
            if (w & 0x0F900FF0) == 0x01000050:
                qarith(ins, w)
                return ins
            if (w & 0x0F900090) == 0x01000080:
                dsp_multiply(ins, w)
                return ins
            if (w & 0x0FC000F0) == 0x00000090:
                multiply(ins, w)
                return ins
            if (w & 0x0F8000F0) == 0x00800090:
                multiply_long(ins, w)
                return ins
            if (w & 0x0FB00FF0) == 0x01000090:  # SWP
                b = bool(w & (1 << 22))
                rn, rd, rm = (w >> 16) & 0xF, (w >> 12) & 0xF, w & 0xF
                if b:
                    ins.code = (f"{{ uint32_t a = c->r[{rn}]; uint32_t v = st_read8(c, a); st_write8(c, a, (uint8_t)c->r[{rm}]);"
                                f" c->r[{rd}] = v; }}")
                else:
                    ins.code = (f"{{ uint32_t a = c->r[{rn}]; uint32_t v = st_ldr32(c, a); st_write32(c, a & ~3u, c->r[{rm}]);"
                                f" c->r[{rd}] = v; }}")
                ins.asm = "swp"
                return ins
            if (w & 0x0E000090) == 0x00000090 and (w & 0x60):
                halfword_transfer(ins, w, pc)
                return ins
            if (w & 0x0FBF0FFF) == 0x010F0000:  # MRS
                rd = (w >> 12) & 0xF
                spsr = bool(w & (1 << 22))
                ins.code = f"c->r[{rd}] = {'c->spsr' if spsr else 'st_get_cpsr(c)'};"
                ins.asm = "mrs"
                return ins
            if (w & 0x0FB0FFF0) == 0x0120F000:  # MSR reg
                ins.code = f"st_msr(c, {int(bool(w & (1 << 22)))}, {(w >> 16) & 0xF}, c->r[{w & 0xF}]);"
                ins.asm = "msr"
                return ins
            if (w & 0x0FF000F0) == 0x01200070:  # BKPT
                num = ((w >> 4) & 0xFFF0) | (w & 0xF)
                ins.code = f"st_bkpt(c, {num});"
                ins.asm = f"bkpt {num:#x}"
                return ins
            if (w & 0x01900000) == 0x01000000:  # TST/TEQ/CMP/CMN sin S: otro espacio
                return undefined(ins)
        else:
            if (w & 0x0FB0F000) == 0x0320F000:  # MSR imm
                rot = ((w >> 8) & 0xF) * 2
                imm = w & 0xFF
                imm = ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF if rot else imm
                ins.code = f"st_msr(c, {int(bool(w & (1 << 22)))}, {(w >> 16) & 0xF}, {h(imm)});"
                ins.asm = "msr"
                return ins
            if (w & 0x01900000) == 0x01000000:
                return undefined(ins)
        data_processing(ins, w, pc)
        # Tabla de saltos: addls pc, pc, rX, lsl #2
        if ((w & 0x0FFFFFF0) == 0x008FF100) and ins.kind == INDIRECT:
            ins.kind = TABLE
            ins.table_reg = f"c->r[{w & 0xF}]"
        return ins
    if t in (2, 3):
        if t == 3 and (w & 0x10):
            return undefined(ins)
        single_transfer(ins, w, pc)
        return ins
    if t == 4:
        block_transfer(ins, w, pc)
        return ins
    if t == 5:
        off = sx(w & 0xFFFFFF, 24) << 2
        target = (pc + off) & 0xFFFFFFFF
        if w & (1 << 24):
            ins.kind = CALL
            ins.target = target
            ins.code = f"c->r[14] = {h(addr + 4)};"
            ins.asm = f"bl {target:#x}"
        else:
            ins.kind = BRANCH
            ins.target = target
            ins.asm = f"b {target:#x}"
        return ins
    if t == 7:
        if w & (1 << 24):
            ins.code = f"st_swi(c, {(w >> 16) & 0xFF});"
            ins.asm = f"swi {(w >> 16) & 0xFF:#x}"
            return ins
        if w & 0x10:  # MRC / MCR
            cp = (w >> 8) & 0xF
            op1 = (w >> 21) & 7
            crn = (w >> 16) & 0xF
            rd = (w >> 12) & 0xF
            op2 = (w >> 5) & 7
            crm = w & 0xF
            if w & (1 << 20):
                ins.code = f"c->r[{rd}] = st_mrc(c, {cp}, {op1}, {crn}, {crm}, {op2});"
                ins.asm = "mrc"
            else:
                ins.code = f"st_mcr(c, {cp}, {op1}, {crn}, {crm}, {op2}, c->r[{rd}]);"
                ins.asm = "mcr"
            return ins
    return undefined(ins)


def undefined(ins: Ins) -> Ins:
    ins.kind = UNDEF
    ins.cond = 14
    ins.asm = f".word {ins.raw:#010x}"
    ins.code = f"st_unimpl(c, {h(ins.addr)}, {h(ins.raw)}, 0); return;"
    return ins
