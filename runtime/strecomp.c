#include "strecomp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

uint8_t st_main_ram[ST_MAIN_RAM_SIZE];
uint8_t st_itcm[32 * 1024];
uint8_t st_dtcm[16 * 1024];
uint8_t st_wram[32 * 1024];
uint8_t st_palette[2 * 1024];
uint8_t st_oam[2 * 1024];
uint8_t st_vram[656 * 1024];
uint32_t st_dtcm_base = 0x027E0000u;
static uint32_t st_itcm_size = 0x02000000u;  /* ITCM visible en 0..32MB (espejado) */

static FILE* miss_log;
static unsigned char overlay_loaded[1024];

/* ------------------------------------------------------------- plataforma */
static uint32_t default_io_read(StCpu* c, uint32_t addr, int size) {
    (void)c; (void)size;
    fprintf(stderr, "[st] IO read%d %08X\n", size * 8, addr);
    return 0;
}
static void default_io_write(StCpu* c, uint32_t addr, uint32_t v, int size) {
    (void)c;
    fprintf(stderr, "[st] IO write%d %08X = %08X\n", size * 8, addr, v);
}
static void default_swi(StCpu* c, uint32_t num) {
    (void)c;
    fprintf(stderr, "[st] SWI %02X sin implementar\n", num);
}
static void default_unimpl(StCpu* c, uint32_t addr, uint32_t insn, int thumb) {
    (void)c;
    fprintf(stderr, "[st] instrucción no soportada %s %08X: %08X\n", thumb ? "Thumb" : "ARM", addr, insn);
    abort();
}
static void default_miss(StCpu* c, uint32_t addr) {
    fprintf(stderr, "[st] función no encontrada en %08X (LR=%08X)\n", addr, c->r[14]);
    abort();
}
static void default_halt(StCpu* c, const char* why) {
    (void)c;
    fprintf(stderr, "[st] detenido: %s\n", why);
    abort();
}

StPlatform st_platform = {default_io_read, default_io_write, default_swi, default_unimpl, default_miss, default_halt};

/* ---------------------------------------------------------------- memoria */
uint8_t* st_ptr(uint32_t addr, uint32_t size) {
    if (addr - st_dtcm_base < sizeof st_dtcm && addr - st_dtcm_base + size <= sizeof st_dtcm)
        return &st_dtcm[addr - st_dtcm_base];
    if (addr < st_itcm_size) return &st_itcm[addr & (sizeof st_itcm - 1)];
    switch (addr >> 24) {
    case 0x02: return &st_main_ram[addr & (ST_MAIN_RAM_SIZE - 1)];
    case 0x03: return &st_wram[addr & (sizeof st_wram - 1)];
    case 0x05: return &st_palette[addr & (sizeof st_palette - 1)];
    case 0x06: {
        uint32_t o = addr & 0xFFFFF;
        return o + size <= sizeof st_vram ? &st_vram[o] : NULL;
    }
    case 0x07: return &st_oam[addr & (sizeof st_oam - 1)];
    default: return NULL;
    }
}

uint32_t st_read8(StCpu* c, uint32_t a) {
    uint8_t* p = st_ptr(a, 1);
    return p ? *p : st_platform.io_read(c, a, 1) & 0xFF;
}
uint32_t st_read16(StCpu* c, uint32_t a) {
    uint8_t* p = st_ptr(a, 2);
    if (p) { uint16_t v; memcpy(&v, p, 2); return v; }
    return st_platform.io_read(c, a, 2) & 0xFFFF;
}
uint32_t st_read32(StCpu* c, uint32_t a) {
    uint8_t* p = st_ptr(a, 4);
    if (p) { uint32_t v; memcpy(&v, p, 4); return v; }
    return st_platform.io_read(c, a, 4);
}
void st_write8(StCpu* c, uint32_t a, uint8_t v) {
    uint8_t* p = st_ptr(a, 1);
    if (p) *p = v; else st_platform.io_write(c, a, v, 1);
}
void st_write16(StCpu* c, uint32_t a, uint16_t v) {
    uint8_t* p = st_ptr(a, 2);
    if (p) memcpy(p, &v, 2); else st_platform.io_write(c, a, v, 2);
}
void st_write32(StCpu* c, uint32_t a, uint32_t v) {
    uint8_t* p = st_ptr(a, 4);
    if (p) memcpy(p, &v, 4); else st_platform.io_write(c, a, v, 4);
}
int st_load(uint32_t addr, const void* data, size_t size) {
    const uint8_t* s = (const uint8_t*)data;
    for (size_t i = 0; i < size; i++) {
        uint8_t* p = st_ptr(addr + (uint32_t)i, 1);
        if (!p) return -1;
        *p = s[i];
    }
    return 0;
}

/* -------------------------------------------------------------- ejecución */
typedef struct { uint32_t addr; StFn fn; } Override;
static Override* overrides;
static size_t n_overrides, cap_overrides;

void st_init(StCpu* c) {
    memset(c, 0, sizeof *c);
    c->cpsr_ctrl = 0x1F; /* modo sistema */
    c->r[13] = 0x02FFFC00u;
}

void st_override(uint32_t addr, StFn fn) {
    addr &= ~1u;
    for (size_t i = 0; i < n_overrides; i++)
        if (overrides[i].addr == addr) { overrides[i].fn = fn; return; }
    if (n_overrides == cap_overrides) {
        cap_overrides = cap_overrides ? cap_overrides * 2 : 64;
        overrides = (Override*)realloc(overrides, cap_overrides * sizeof *overrides);
    }
    overrides[n_overrides].addr = addr;
    overrides[n_overrides].fn = fn;
    n_overrides++;
}

void st_overlay_set_loaded(int id, int loaded) {
    if (id >= 0 && id < (int)sizeof overlay_loaded) overlay_loaded[id] = (unsigned char)(loaded != 0);
}
int st_overlay_is_loaded(int id) {
    return id >= 0 && id < (int)sizeof overlay_loaded && overlay_loaded[id];
}

static const StFnEntry* find_in(const StModule* m, uint32_t addr) {
    unsigned lo = 0, hi = m->count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (m->fns[mid].addr < addr) lo = mid + 1;
        else hi = mid;
    }
    return (lo < m->count && m->fns[lo].addr == addr) ? &m->fns[lo] : NULL;
}

static const StFnEntry* lookup(uint32_t addr, const StModule** mod) {
    for (unsigned i = 0; i < st_module_count; i++) {
        const StModule* m = &st_modules[i];
        if (addr < m->start || addr >= m->end) continue;
        if (m->overlay >= 0 && !st_overlay_is_loaded(m->overlay)) continue;
        const StFnEntry* e = find_in(m, addr);
        if (e) { if (mod) *mod = m; return e; }
    }
    return NULL;
}

void st_set_miss_log(const char* path) {
    if (miss_log) fclose(miss_log);
    miss_log = path ? fopen(path, "a") : NULL;
}

void st_call(StCpu* c, uint32_t target) {
    uint32_t addr = target & ~1u;
    for (size_t i = 0; i < n_overrides; i++)
        if (overrides[i].addr == addr) { overrides[i].fn(c); return; }
    const StFnEntry* e = lookup(addr, NULL);
    if (e) { e->fn(c); return; }
    if (miss_log) {
        fprintf(miss_log, "%08X %s\n", addr, (target & 1) ? "thumb" : "arm");
        fflush(miss_log);
    }
    st_platform.miss(c, target);
}

const char* st_symbol_for(uint32_t addr) {
    static char buf[64];
    const StModule* m = NULL;
    const StFnEntry* e = lookup(addr & ~1u, &m);
    if (!e) return "?";
    snprintf(buf, sizeof buf, "%s:%08X", m->name, e->addr);
    return buf;
}

/* ----------------------------------------------------------------- sistema */
void st_swi(StCpu* c, uint32_t num) { st_platform.swi(c, num); }
void st_bkpt(StCpu* c, uint32_t num) { (void)num; st_platform.halt(c, "bkpt"); }
void st_unimpl(StCpu* c, uint32_t addr, uint32_t insn, int thumb) { st_platform.unimpl(c, addr, insn, thumb); }

static uint32_t cp15_regs[16][16][8];
uint32_t st_mrc(StCpu* c, int cp, int op1, int crn, int crm, int op2) {
    (void)c; (void)op1;
    if (cp != 15) return 0;
    if (crn == 0 && crm == 0 && op2 == 0) return 0x41059461u; /* ID ARM946E-S */
    return cp15_regs[crn & 15][crm & 15][op2 & 7];
}
void st_mcr(StCpu* c, int cp, int op1, int crn, int crm, int op2, uint32_t v) {
    (void)c; (void)op1;
    if (cp != 15) return;
    cp15_regs[crn & 15][crm & 15][op2 & 7] = v;
    if (crn == 9 && crm == 1 && op2 == 0) st_dtcm_base = v & 0xFFFFF000u;         /* DTCM */
    if (crn == 9 && crm == 1 && op2 == 1) {                                          /* ITCM */
        uint32_t sz = 512u << ((v >> 1) & 0x1F);
        st_itcm_size = sz ? sz : 0x02000000u;
    }
}
uint32_t st_get_cpsr(StCpu* c) {
    return (c->n << 31) | (c->z << 30) | (c->c << 29) | (c->v << 28) | (c->q << 27) | (c->cpsr_ctrl & 0xFF);
}
void st_msr(StCpu* c, int spsr, int fields, uint32_t v) {
    uint32_t mask = 0;
    if (fields & 1) mask |= 0x000000FFu;
    if (fields & 8) mask |= 0xFF000000u;
    if (spsr) { c->spsr = (c->spsr & ~mask) | (v & mask); return; }
    uint32_t cur = st_get_cpsr(c);
    cur = (cur & ~mask) | (v & mask);
    c->n = cur >> 31; c->z = (cur >> 30) & 1; c->c = (cur >> 29) & 1; c->v = (cur >> 28) & 1; c->q = (cur >> 27) & 1;
    c->cpsr_ctrl = cur & 0xFF;
}
void st_restore_cpsr(StCpu* c) { st_msr(c, 0, 9, c->spsr); }
