/* HLE del BIOS del ARM9: interrupciones (IME/IE/IF y entrada al manejador del juego) y las
 * SWI del BIOS (GBATEK, "BIOS Functions"). No contiene código del BIOS de Nintendo: todo
 * está escrito a partir de la descripción de su comportamiento. */
#include <stdio.h>
#include <string.h>

#include "strecomp.h"

StIrqState st_irq;

/* ------------------------------------------------------------------ IO */
static uint32_t* irq_reg(uint32_t addr) {
    switch (addr & ~3u) {
    case ST_IO_IME: return &st_irq.ime;
    case ST_IO_IE: return &st_irq.ie;
    case ST_IO_IF: return &st_irq.if_;
    default: return NULL;
    }
}

uint32_t st_io_read(StCpu* c, uint32_t addr, int size) {
    uint32_t* r = irq_reg(addr);
    if (!r) return st_platform.io_read(c, addr, size);
    uint32_t v = *r >> ((addr & 3) * 8);
    return size == 4 ? v : (v & (size == 2 ? 0xFFFFu : 0xFFu));
}

void st_io_write(StCpu* c, uint32_t addr, uint32_t v, int size) {
    uint32_t* r = irq_reg(addr);
    if (!r) { st_platform.io_write(c, addr, v, size); return; }
    uint32_t sh = (addr & 3) * 8;
    uint32_t mask = (size == 4 ? 0xFFFFFFFFu : (size == 2 ? 0xFFFFu : 0xFFu)) << sh;
    v <<= sh;
    if (r == &st_irq.if_) st_irq.if_ &= ~(v & mask);     /* escribir 1 = reconocer */
    else *r = (*r & ~mask) | (v & mask);
    if (r == &st_irq.ime) st_irq.ime &= 1;
}

void st_raise_irq(uint32_t mask) { st_irq.if_ |= mask; }

/* ----------------------------------------------------------- interrupción */
int st_irq_service(StCpu* c) {
    if (!(st_irq.ime & 1) || !(st_irq.ie & st_irq.if_)) return 0;
    uint32_t handler = st_read32(c, st_dtcm_base + 0x3FFC);
    if (!handler) return 0;
    /* Entrada a la excepción IRQ */
    uint32_t cpsr = st_get_cpsr(c);
    st_set_mode(c, 0x12);
    c->spsr = cpsr;
    c->cpsr_ctrl = (c->cpsr_ctrl & ~0xC0u) | 0x80u | (cpsr & 0x40u);
    uint32_t ret = ST_IRQ_BIOS_RET + 8;   /* LR_irq = dirección interrumpida + 4 */
    /* BIOS: stmfd sp!, {r0-r3, r12, lr} */
    uint32_t sp = c->r[13] - 24;
    uint32_t regs[6] = {c->r[0], c->r[1], c->r[2], c->r[3], c->r[12], ret};
    for (int i = 0; i < 6; i++) st_write32(c, sp + 4 * (uint32_t)i, regs[i]);
    c->r[13] = sp;
    c->r[0] = 0x04000000u;
    c->r[14] = ST_IRQ_BIOS_RET;
    st_irq.served++;
    st_call(c, handler);
    if (c->next_pc != ST_IRQ_BIOS_RET) {
        st_platform.halt(c, "el manejador IRQ no volvió al BIOS");
        return 1;
    }
    /* BIOS: ldmfd sp!, {r0-r3, r12, lr}; subs pc, lr, #4 */
    sp = c->r[13];
    for (int i = 0; i < 6; i++) regs[i] = st_read32(c, sp + 4 * (uint32_t)i);
    c->r[13] = sp + 24;
    if (regs[5] != ret) {
        /* El SDK cambia de hilo reescribiendo este marco: hace falta la capa de hilos
         * (fibras nativas, ver HANDOFF.md). Por ahora se detiene con un mensaje claro. */
        st_platform.halt(c, "cambio de hilo desde la IRQ (falta la capa de hilos)");
        return 1;
    }
    c->r[0] = regs[0]; c->r[1] = regs[1]; c->r[2] = regs[2]; c->r[3] = regs[3]; c->r[12] = regs[4];
    st_restore_cpsr(c);
    return 1;
}

/* Espera hasta que el manejador marque alguno de los bits 'mask' en DTCM+0x3FF8 */
static void intr_wait(StCpu* c, int discard, uint32_t mask) {
    uint32_t flags_addr = st_dtcm_base + 0x3FF8;
    if (discard) st_write32(c, flags_addr, st_read32(c, flags_addr) & ~mask);
    st_irq.ime = 1;
    for (unsigned idle = 0;;) {
        uint32_t f = st_read32(c, flags_addr);
        if (f & mask) { st_write32(c, flags_addr, f & ~mask); return; }
        if (!st_irq_service(c)) {
            if (!st_platform.idle(c)) { st_platform.halt(c, "espera abortada por la plataforma"); return; }
            if (++idle > 100000) { st_platform.halt(c, "IntrWait sin interrupciones que lo despierten"); return; }
        }
    }
}

/* ---------------------------------------------------------- descompresores */
static uint32_t rd8(StCpu* c, uint32_t* a) { return st_read8(c, (*a)++); }

/* Escritura de bytes con la restricción de VRAM (solo 16 bits) para las variantes "Vram" */
typedef struct { StCpu* c; uint32_t dst; uint32_t half; int vram; } Out;
static void out8(Out* o, uint32_t b) {
    if (!o->vram) { st_write8(o->c, o->dst++, (uint8_t)b); return; }
    if (o->dst & 1) { st_write16(o->c, o->dst - 1, (uint16_t)(o->half | b << 8)); }
    else o->half = b;
    o->dst++;
}
static uint32_t out_peek(Out* o, uint32_t back) {   /* byte ya escrito 'back' posiciones atrás */
    uint32_t a = o->dst - back;
    if (o->vram && a == o->dst - 1 && (o->dst & 1)) return o->half;
    return st_read8(o->c, a);
}

static void lz77(StCpu* c, uint32_t src, uint32_t dst, int vram) {
    uint32_t hdr = st_read32(c, src), size = hdr >> 8;
    src += 4;
    Out o = {c, dst, 0, vram};
    uint32_t done = 0;
    while (done < size) {
        uint32_t flags = rd8(c, &src);
        for (int i = 0; i < 8 && done < size; i++, flags <<= 1) {
            if (flags & 0x80) {
                uint32_t b0 = rd8(c, &src), b1 = rd8(c, &src);
                uint32_t len = (b0 >> 4) + 3, disp = ((b0 & 0xF) << 8 | b1) + 1;
                while (len-- && done < size) { out8(&o, out_peek(&o, disp)); done++; }
            } else {
                out8(&o, rd8(c, &src));
                done++;
            }
        }
    }
}

static void rl(StCpu* c, uint32_t src, uint32_t dst, int vram) {
    uint32_t size = st_read32(c, src) >> 8;
    src += 4;
    Out o = {c, dst, 0, vram};
    uint32_t done = 0;
    while (done < size) {
        uint32_t f = rd8(c, &src);
        if (f & 0x80) {
            uint32_t len = (f & 0x7F) + 3, b = rd8(c, &src);
            while (len-- && done < size) { out8(&o, b); done++; }
        } else {
            for (uint32_t len = f + 1; len-- && done < size; done++) out8(&o, rd8(c, &src));
        }
    }
}

static void huff(StCpu* c, uint32_t src, uint32_t dst) {
    uint32_t hdr = st_read32(c, src), bits = hdr & 0xF, size = hdr >> 8;
    uint32_t tree = src + 5;                                 /* raíz */
    uint32_t stream = src + 4 + (st_read8(c, src + 4) + 1) * 2;
    uint32_t node = tree, word = 0, nbits = 0, acc = 0, accn = 0, done = 0;
    while (done < size) {
        if (!nbits) { word = st_read32(c, stream); stream += 4; nbits = 32; }
        uint32_t bit = (word >> 31) & 1;
        word <<= 1;
        nbits--;
        uint32_t n = st_read8(c, node);
        uint32_t child = (node & ~1u) + (n & 0x3F) * 2 + 2 + bit;
        if (n & (bit ? 0x40 : 0x80)) {                         /* hoja */
            acc |= st_read8(c, child) << accn;
            accn += bits;
            node = tree;
            if (accn == 32) { st_write32(c, dst, acc); dst += 4; done += 4; acc = accn = 0; }
        } else {
            node = child;
        }
    }
}

static void bit_unpack(StCpu* c, uint32_t src, uint32_t dst, uint32_t info) {
    uint32_t len = st_read16(c, info), sw = st_read8(c, info + 2), dw = st_read8(c, info + 3);
    uint32_t off = st_read32(c, info + 4), zero = off >> 31;
    off &= 0x7FFFFFFF;
    uint32_t acc = 0, accn = 0;
    for (uint32_t i = 0; i < len; i++) {
        uint32_t b = st_read8(c, src + i);
        for (uint32_t k = 0; k < 8; k += sw) {
            uint32_t v = (b >> k) & ((1u << sw) - 1);
            if (v || zero) v += off;
            acc |= (dw == 32 ? v : (v & ((1u << dw) - 1))) << accn;
            accn += dw;
            if (accn >= 32) { st_write32(c, dst, acc); dst += 4; acc = 0; accn = 0; }
        }
    }
}

static void diff_unfilter(StCpu* c, uint32_t src, uint32_t dst, int sixteen) {
    uint32_t size = st_read32(c, src) >> 8;
    src += 4;
    uint32_t acc = 0;
    for (uint32_t i = 0; i < size; i += sixteen ? 2 : 1) {
        if (sixteen) { acc = (acc + st_read16(c, src + i)) & 0xFFFF; st_write16(c, dst + i, (uint16_t)acc); }
        else { acc = (acc + st_read8(c, src + i)) & 0xFF; st_write8(c, dst + i, (uint8_t)acc); }
    }
}

/* --------------------------------------------------------------------- SWI */
static uint32_t isqrt(uint32_t v) {
    uint32_t r = 0, bit = 1u << 30;
    while (bit > v) bit >>= 2;
    while (bit) {
        if (v >= r + bit) { v -= r + bit; r = (r >> 1) + bit; }
        else r >>= 1;
        bit >>= 2;
    }
    return r;
}

static uint32_t crc16(StCpu* c, uint32_t crc, uint32_t addr, uint32_t len) {
    crc &= 0xFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= st_read8(c, addr + i);
        for (int j = 0; j < 8; j++) crc = (crc & 1) ? (crc >> 1) ^ 0xA001u : crc >> 1;
    }
    return crc;
}

int st_bios_swi(StCpu* c, uint32_t num) {
    uint32_t* r = c->r;
    switch (num) {
    case 0x00: st_platform.halt(c, "SoftReset"); return 1;
    case 0x03: return 1;                                     /* WaitByLoop: no hace falta esperar */
    case 0x04: intr_wait(c, (int)r[0], r[1]); return 1;      /* IntrWait */
    case 0x05: intr_wait(c, 1, 1); return 1;                 /* VBlankIntrWait */
    case 0x06:                                               /* Halt */
        if (!st_irq_service(c) && st_platform.idle(c)) st_irq_service(c);
        return 1;
    case 0x09: {                                             /* Div */
        int32_t n = (int32_t)r[0], d = (int32_t)r[1];
        if (d == 0) { r[0] = n < 0 ? 0xFFFFFFFFu : 1u; r[1] = (uint32_t)n; r[3] = 1; return 1; }
        if (n == INT32_MIN && d == -1) { r[0] = (uint32_t)n; r[1] = 0; r[3] = (uint32_t)n; return 1; }
        int32_t q = n / d;
        r[0] = (uint32_t)q; r[1] = (uint32_t)(n % d); r[3] = (uint32_t)(q < 0 ? -q : q);
        return 1;
    }
    case 0x0B: {                                             /* CpuSet */
        uint32_t cnt = r[2] & 0x1FFFFF, fill = r[2] & (1u << 24), w32 = r[2] & (1u << 26);
        uint32_t s = r[0], d = r[1], step = w32 ? 4 : 2;
        s &= ~(step - 1); d &= ~(step - 1);
        uint32_t fv = w32 ? st_read32(c, s) : st_read16(c, s);
        for (uint32_t i = 0; i < cnt; i++, d += step) {
            uint32_t v = fill ? fv : (w32 ? st_read32(c, s + i * 4) : st_read16(c, s + i * 2));
            if (w32) st_write32(c, d, v); else st_write16(c, d, (uint16_t)v);
        }
        return 1;
    }
    case 0x0C: {                                             /* CpuFastSet */
        uint32_t cnt = (r[2] & 0x1FFFFF), fill = r[2] & (1u << 24);
        cnt = (cnt + 7) & ~7u;
        uint32_t s = r[0] & ~3u, d = r[1] & ~3u, fv = st_read32(c, s);
        for (uint32_t i = 0; i < cnt; i++) st_write32(c, d + i * 4, fill ? fv : st_read32(c, s + i * 4));
        return 1;
    }
    case 0x0D: r[0] = isqrt(r[0]); return 1;                 /* Sqrt */
    case 0x0E: r[0] = crc16(c, r[0], r[1], r[2]); return 1;  /* GetCRC16 */
    case 0x0F: r[0] = 0; return 1;                           /* IsDebugger: no */
    case 0x10: bit_unpack(c, r[0], r[1], r[2]); return 1;
    case 0x11: lz77(c, r[0], r[1], 0); return 1;
    case 0x12: lz77(c, r[0], r[1], 1); return 1;
    case 0x13: huff(c, r[0], r[1]); return 1;
    case 0x14: rl(c, r[0], r[1], 0); return 1;
    case 0x15: rl(c, r[0], r[1], 1); return 1;
    case 0x16: diff_unfilter(c, r[0], r[1], 0); return 1;
    case 0x18: diff_unfilter(c, r[0], r[1], 1); return 1;
    default: return 0;
    }
}
