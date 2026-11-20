"""Traductor de instrucciones Thumb (ARMv5T) a C."""
from __future__ import annotations

from .insn import BRANCH, CALL, INDIRECT, NEXT, RET, UNDEF, Ins, h, sx


def r(n: int) -> str:
    return f"c->r[{n}]"


def nz(v: str) -> str:
    return f"c->n = {v} >> 31; c->z = ({v} == 0);"


def translate(addr: int, hw: int, hw2: int | None = None) -> Ins:
    """hw2 es la media palabra siguiente (necesaria para BL/BLX de 32 bits)."""
    ins = Ins(addr, 2, True, hw)
    pc = addr + 4
    top = hw >> 11

    # --- desplazamientos inmediatos
    if top in (0, 1, 2):
        rd, rs, imm = hw & 7, (hw >> 3) & 7, (hw >> 6) & 0x1F
        rsv = r(rs)
        if top == 0:
            if imm == 0:
                ins.code = f"{{ uint32_t res = {rsv}; {nz('res')} {r(rd)} = res; }}"
            else:
                ins.code = (f"{{ uint32_t v = {rsv}; c->c = (v >> {32 - imm}) & 1; uint32_t res = v << {imm};"
                            f" {nz('res')} {r(rd)} = res; }}")
            ins.asm = f"lsls r{rd}, r{rs}, #{imm}"
        elif top == 1:
            n = imm or 32
            res = "0u" if n == 32 else f"v >> {n}"
            ins.code = (f"{{ uint32_t v = {rsv}; c->c = (v >> {n - 1}) & 1; uint32_t res = {res};"
                        f" {nz('res')} {r(rd)} = res; }}")
            ins.asm = f"lsrs r{rd}, r{rs}, #{n}"
        else:
            n = imm or 32
            res = f"(uint32_t)((int32_t)v >> {min(n, 31)})"
            ins.code = (f"{{ uint32_t v = {rsv}; c->c = (v >> {n - 1}) & 1; uint32_t res = {res};"
                        f" {nz('res')} {r(rd)} = res; }}")
            ins.asm = f"asrs r{rd}, r{rs}, #{n}"
        return ins
    if top == 3:
        rd, rs = hw & 7, (hw >> 3) & 7
        imm_flag = bool(hw & (1 << 10))
        sub = bool(hw & (1 << 9))
        rn_or_imm = (hw >> 6) & 7
        b = h(rn_or_imm) if imm_flag else r(rn_or_imm)
        if sub:
            ins.code = f"{r(rd)} = st_adc_flags(c, {r(rs)}, ~{b}, 1);"
        else:
            ins.code = f"{r(rd)} = st_adc_flags(c, {r(rs)}, {b}, 0);"
        ins.asm = f"{'subs' if sub else 'adds'} r{rd}, r{rs}, {'#' if imm_flag else 'r'}{rn_or_imm}"
        return ins
    if top in (4, 5, 6, 7):
        rd = (hw >> 8) & 7
        imm = hw & 0xFF
        op = top - 4
        if op == 0:
            ins.code = f"{{ uint32_t res = {h(imm)}; {nz('res')} {r(rd)} = res; }}"
            ins.asm = f"movs r{rd}, #{imm}"
        elif op == 1:
            ins.code = f"(void)st_adc_flags(c, {r(rd)}, ~{h(imm)}, 1);"
            ins.asm = f"cmp r{rd}, #{imm}"
        elif op == 2:
            ins.code = f"{r(rd)} = st_adc_flags(c, {r(rd)}, {h(imm)}, 0);"
            ins.asm = f"adds r{rd}, #{imm}"
        else:
            ins.code = f"{r(rd)} = st_adc_flags(c, {r(rd)}, ~{h(imm)}, 1);"
            ins.asm = f"subs r{rd}, #{imm}"
        return ins

    # --- ALU
    if (hw >> 10) == 0x10:
        op = (hw >> 6) & 0xF
        rd, rs = hw & 7, (hw >> 3) & 7
        d, s = r(rd), r(rs)
        names = ["ands", "eors", "lsls", "lsrs", "asrs", "adcs", "sbcs", "rors", "tst", "negs", "cmp", "cmn",
                 "orrs", "muls", "bics", "mvns"]
        ins.asm = f"{names[op]} r{rd}, r{rs}"
        if op in (0, 1, 12, 14, 15, 8):
            expr = {0: f"{d} & {s}", 1: f"{d} ^ {s}", 12: f"{d} | {s}", 14: f"{d} & ~{s}", 15: f"~{s}",
                    8: f"{d} & {s}"}[op]
            store = "" if op == 8 else f" {d} = res;"
            ins.code = f"{{ uint32_t res = {expr}; {nz('res')}{store} }}"
        elif op in (2, 3, 4, 7):
            fn = {2: "st_lsl_c", 3: "st_lsr_c", 4: "st_asr_c", 7: "st_ror_c"}[op]
            ins.code = f"{{ uint32_t sc = c->c; uint32_t res = {fn}({d}, {s} & 0xFF, &sc); c->c = sc; {nz('res')} {d} = res; }}"
        elif op == 5:
            ins.code = f"{d} = st_adc_flags(c, {d}, {s}, c->c);"
        elif op == 6:
            ins.code = f"{d} = st_adc_flags(c, {d}, ~{s}, c->c);"
        elif op == 9:
            ins.code = f"{d} = st_adc_flags(c, 0u, ~{s}, 1);"
        elif op == 10:
            ins.code = f"(void)st_adc_flags(c, {d}, ~{s}, 1);"
        elif op == 11:
            ins.code = f"(void)st_adc_flags(c, {d}, {s}, 0);"
        elif op == 13:
            ins.code = f"{{ uint32_t res = {d} * {s}; {nz('res')} {d} = res; }}"
        return ins

    # --- registros altos / BX / BLX
    if (hw >> 10) == 0x11:
        op = (hw >> 8) & 3
        h1, h2 = (hw >> 7) & 1, (hw >> 6) & 1
        rd = (hw & 7) | (h1 << 3)
        rm = ((hw >> 3) & 7) | (h2 << 3)
        mv = h(pc) if rm == 15 else r(rm)
        dv = h(pc) if rd == 15 else r(rd)
        if op == 0:
            ins.asm = f"add r{rd}, r{rm}"
            if rd == 15:
                ins.code = f"ST_INDIRECT((({dv} + {mv}) | 1u));"
                ins.kind = INDIRECT
            else:
                ins.code = f"{r(rd)} = {dv} + {mv};"
        elif op == 1:
            ins.asm = f"cmp r{rd}, r{rm}"
            ins.code = f"(void)st_adc_flags(c, {dv}, ~{mv}, 1);"
        elif op == 2:
            ins.asm = f"mov r{rd}, r{rm}"
            if rd == 15:
                if rm == 14:
                    ins.code = "ST_RETURN(c->r[14] | 1u);"
                    ins.kind = RET
                else:
                    ins.code = f"ST_INDIRECT(({mv}) | 1u);"
                    ins.kind = INDIRECT
            else:
                ins.code = f"{r(rd)} = {mv};"
        else:
            if h1:  # BLX reg
                ins.asm = f"blx r{rm}"
                ins.kind = CALL
                ins.code = f"uint32_t tgt = {mv}; c->r[14] = {h(addr + 2 | 1)};"
                ins.table_reg = "tgt"
            else:
                ins.asm = f"bx r{rm}"
                if rm == 15:  # bx pc: salto constante a ARM (veneer Thumb->ARM)
                    ins.kind = BRANCH
                    ins.target = pc & ~3
                elif rm == 14:
                    ins.code = "ST_RETURN(c->r[14]);"
                    ins.kind = RET
                else:
                    ins.code = f"ST_INDIRECT({mv});"
                    ins.kind = INDIRECT
        return ins

    # --- LDR pc-relativo
    if top == 9:
        rd = (hw >> 8) & 7
        a = ((pc & ~3) + (hw & 0xFF) * 4) & 0xFFFFFFFF
        ins.asm = f"ldr r{rd}, ={a:#x}"
        ins.code = f"{r(rd)} = st_read32(c, {h(a)});"
        return ins

    # --- load/store con registro
    if (hw >> 12) == 5:
        op = (hw >> 9) & 7
        rd, rb, ro = hw & 7, (hw >> 3) & 7, (hw >> 6) & 7
        a = f"{r(rb)} + {r(ro)}"
        ins.code, ins.asm = {
            0: (f"st_write32(c, ({a}) & ~3u, {r(rd)});", "str"),
            1: (f"st_write16(c, ({a}) & ~1u, (uint16_t){r(rd)});", "strh"),
            2: (f"st_write8(c, {a}, (uint8_t){r(rd)});", "strb"),
            3: (f"{r(rd)} = (uint32_t)(int32_t)(int8_t)st_read8(c, {a});", "ldrsb"),
            4: (f"{r(rd)} = st_ldr32(c, {a});", "ldr"),
            5: (f"{r(rd)} = st_read16(c, ({a}) & ~1u);", "ldrh"),
            6: (f"{r(rd)} = st_read8(c, {a});", "ldrb"),
            7: (f"{r(rd)} = (uint32_t)(int32_t)(int16_t)st_read16(c, ({a}) & ~1u);", "ldrsh"),
        }[op]
        ins.asm += f" r{rd}, [r{rb}, r{ro}]"
        return ins

    # --- load/store inmediato
    if (hw >> 13) == 3:
        byte = bool(hw & (1 << 12))
        load = bool(hw & (1 << 11))
        imm = (hw >> 6) & 0x1F
        rd, rb = hw & 7, (hw >> 3) & 7
        off = imm if byte else imm * 4
        a = f"{r(rb)} + {off}u"
        if load:
            ins.code = f"{r(rd)} = {'st_read8' if byte else 'st_ldr32'}(c, {a});"
        else:
            ins.code = (f"st_write8(c, {a}, (uint8_t){r(rd)});" if byte
                        else f"st_write32(c, ({a}) & ~3u, {r(rd)});")
        ins.asm = f"{'ldr' if load else 'str'}{'b' if byte else ''} r{rd}, [r{rb}, #{off}]"
        return ins
    if (hw >> 12) == 8:
        load = bool(hw & (1 << 11))
        off = ((hw >> 6) & 0x1F) * 2
        rd, rb = hw & 7, (hw >> 3) & 7
        a = f"({r(rb)} + {off}u) & ~1u"
        ins.code = f"{r(rd)} = st_read16(c, {a});" if load else f"st_write16(c, {a}, (uint16_t){r(rd)});"
        ins.asm = f"{'ldrh' if load else 'strh'} r{rd}, [r{rb}, #{off}]"
        return ins
    if (hw >> 12) == 9:
        load = bool(hw & (1 << 11))
        rd = (hw >> 8) & 7
        off = (hw & 0xFF) * 4
        a = f"c->r[13] + {off}u"
        ins.code = f"{r(rd)} = st_ldr32(c, {a});" if load else f"st_write32(c, ({a}) & ~3u, {r(rd)});"
        ins.asm = f"{'ldr' if load else 'str'} r{rd}, [sp, #{off}]"
        return ins
    if (hw >> 12) == 10:
        rd = (hw >> 8) & 7
        off = (hw & 0xFF) * 4
        if hw & (1 << 11):
            ins.code = f"{r(rd)} = c->r[13] + {off}u;"
            ins.asm = f"add r{rd}, sp, #{off}"
        else:
            ins.code = f"{r(rd)} = {h((pc & ~3) + off)};"
            ins.asm = f"adr r{rd}, {(pc & ~3) + off:#x}"
        return ins

    # --- misceláneas (1011)
    if (hw >> 12) == 0xB:
        if (hw & 0xFF00) == 0xB000:
            off = (hw & 0x7F) * 4
            sign = "-" if hw & 0x80 else "+"
            ins.code = f"c->r[13] = c->r[13] {sign} {off}u;"
            ins.asm = f"add sp, #{sign}{off}"
            return ins
        if (hw & 0xF600) == 0xB400:  # PUSH / POP
            load = bool(hw & (1 << 11))
            extra = bool(hw & (1 << 8))
            regs = [i for i in range(8) if hw & (1 << i)]
            if load:
                if extra:
                    regs.append(15)
                lines = ["uint32_t a = c->r[13] & ~3u;"]
                for i, rr in enumerate(regs):
                    lines.append(f"uint32_t v{rr} = st_read32(c, a + {4 * i});")
                lines.append(f"c->r[13] = c->r[13] + {4 * len(regs)};")
                for rr in regs:
                    if rr != 15:
                        lines.append(f"c->r[{rr}] = v{rr};")
                if extra:
                    lines.append("ST_RETURN(v15);")
                    ins.kind = RET
                ins.asm = f"pop {{{','.join('r%d' % x for x in regs)}}}"
            else:
                if extra:
                    regs.append(14)
                n = len(regs)
                lines = [f"uint32_t a = (c->r[13] - {4 * n}) & ~3u;"]
                for i, rr in enumerate(regs):
                    lines.append(f"st_write32(c, a + {4 * i}, c->r[{rr}]);")
                lines.append(f"c->r[13] = c->r[13] - {4 * n};")
                ins.asm = f"push {{{','.join('r%d' % x for x in regs)}}}"
            ins.code = "{ " + " ".join(lines) + " }"
            return ins
        if (hw & 0xFF00) == 0xBE00:
            ins.code = f"st_bkpt(c, {hw & 0xFF});"
            ins.asm = "bkpt"
            return ins
        return undefined(ins)

    # --- LDMIA / STMIA
    if (hw >> 12) == 0xC:
        load = bool(hw & (1 << 11))
        rb = (hw >> 8) & 7
        regs = [i for i in range(8) if hw & (1 << i)]
        lines = [f"uint32_t a = c->r[{rb}] & ~3u;"]
        if load:
            for i, rr in enumerate(regs):
                lines.append(f"uint32_t v{rr} = st_read32(c, a + {4 * i});")
            if rb not in regs:
                lines.append(f"c->r[{rb}] = c->r[{rb}] + {4 * len(regs)};")
            for rr in regs:
                lines.append(f"c->r[{rr}] = v{rr};")
        else:
            for i, rr in enumerate(regs):
                lines.append(f"st_write32(c, a + {4 * i}, c->r[{rr}]);")
            lines.append(f"c->r[{rb}] = c->r[{rb}] + {4 * len(regs)};")
        ins.code = "{ " + " ".join(lines) + " }"
        ins.asm = f"{'ldmia' if load else 'stmia'} r{rb}!, {{{','.join('r%d' % x for x in regs)}}}"
        return ins

    # --- saltos condicionales / SWI
    if (hw >> 12) == 0xD:
        cond = (hw >> 8) & 0xF
        if cond == 0xF:
            ins.code = f"st_swi(c, {hw & 0xFF});"
            ins.asm = f"swi {hw & 0xFF:#x}"
            return ins
        if cond == 0xE:
            return undefined(ins)
        ins.cond = cond
        ins.kind = BRANCH
        ins.target = ((pc + (sx(hw & 0xFF, 8) << 1)) | 1) & 0xFFFFFFFF
        ins.asm = f"b{cond} {ins.target & ~1:#x}"
        return ins
    if top == 0x1C:
        ins.kind = BRANCH
        ins.target = ((pc + (sx(hw & 0x7FF, 11) << 1)) | 1) & 0xFFFFFFFF
        ins.asm = f"b {ins.target & ~1:#x}"
        return ins

    # --- BL / BLX (dos medias palabras)
    if top == 0x1E:
        if hw2 is not None and (hw2 >> 11) in (0x1F, 0x1D):
            hi = sx(hw & 0x7FF, 11) << 12
            lo = (hw2 & 0x7FF) << 1
            target = (pc + hi + lo) & 0xFFFFFFFF
            ins.size = 4
            ins.kind = CALL
            ins.code = f"c->r[14] = {h((addr + 4) | 1)};"
            if (hw2 >> 11) == 0x1F:
                ins.target = target | 1
                ins.asm = f"bl {target:#x}"
            else:
                ins.target = target & ~3
                ins.asm = f"blx {target & ~3:#x}"
            return ins
        # prefijo suelto: LR = PC + (off << 12)
        ins.code = f"c->r[14] = {h(pc + (sx(hw & 0x7FF, 11) << 12))};"
        ins.asm = "bl.prefix"
        return ins
    if top in (0x1F, 0x1D):
        # sufijo suelto (el prefijo estaba en otra parte): salto relativo a LR
        lo = (hw & 0x7FF) << 1
        if top == 0x1F:
            ins.code = (f"{{ uint32_t tgt = (c->r[14] + {lo}u) | 1u; c->r[14] = {h((addr + 2) | 1)};"
                        f" ST_CALL_DYN(tgt, {h((addr + 2) | 1)}); }}")
        else:
            ins.code = (f"{{ uint32_t tgt = (c->r[14] + {lo}u) & ~3u; c->r[14] = {h((addr + 2) | 1)};"
                        f" ST_CALL_DYN(tgt, {h((addr + 2) | 1)}); }}")
        ins.asm = "bl.suffix"
        return ins
    return undefined(ins)


def undefined(ins: Ins) -> Ins:
    ins.kind = UNDEF
    ins.cond = 14
    ins.asm = f".hword {ins.raw:#06x}"
    ins.code = f"st_unimpl(c, {h(ins.addr)}, {h(ins.raw)}, 1); return;"
    return ins
