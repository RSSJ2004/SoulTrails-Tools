/* Bancos de VRAM del ARM9 (GBATEK "DS Memory Control - VRAM"): nueve bancos A-I que el juego
 * asigna con VRAMCNT (0x04000240-0x04000249) a LCDC, fondos/objetos de cada motor 2D, texturas
 * o paletas del 3D, o paletas extendidas.
 *
 * Los bancos viven en st_vram con el mismo orden que su vista LCDC (A en 0, B en 20000h ... I en
 * A0000h), así que LCDC (0x06800000+) es una copia directa. Las vistas de fondos/objetos usan
 * tablas de páginas de 16 KB. Si dos bancos se solapan gana el último asignado (el hardware los
 * combina; ningún juego normal lo hace a propósito). */
#include <string.h>

#include "strecomp.h"

static const uint32_t bank_off[9] = {0x00000, 0x20000, 0x40000, 0x60000, 0x80000, 0x90000, 0x94000, 0x98000, 0xA0000};
static const uint32_t bank_size[9] = {0x20000, 0x20000, 0x20000, 0x20000, 0x10000, 0x4000, 0x4000, 0x8000, 0x4000};
static uint8_t vramcnt[9];

#define PAGE 0x4000u
/* Vistas: 0 BG-A (512 KB), 1 BG-B (128 KB), 2 OBJ-A (256 KB), 3 OBJ-B (128 KB) */
static const uint32_t view_pages[4] = {32, 8, 16, 8};
static uint8_t* pages[4][32];
/* Texturas: 4 ranuras de 128 KB; paletas de texturas: 6 de 16 KB; paletas extendidas */
static uint8_t* tex_slot[4];
static uint8_t* texpal_slot[8];
static uint8_t* bg_ext_pal[2][4];   /* motor A/B, ranuras 0-3 (8 KB cada una) */
static uint8_t* obj_ext_pal[2];

static void map_range(int view, uint32_t off, int bank) {
    uint32_t first = off / PAGE, n = bank_size[bank] / PAGE;
    for (uint32_t i = 0; i < n; i++)
        if (first + i < view_pages[view]) pages[view][first + i] = &st_vram[bank_off[bank] + i * PAGE];
}

static void remap(void) {
    memset(pages, 0, sizeof pages);
    memset(tex_slot, 0, sizeof tex_slot);
    memset(texpal_slot, 0, sizeof texpal_slot);
    memset(bg_ext_pal, 0, sizeof bg_ext_pal);
    memset(obj_ext_pal, 0, sizeof obj_ext_pal);
    for (int b = 0; b < 9; b++) {
        uint8_t v = vramcnt[b];
        if (!(v & 0x80u)) continue;
        int mst = v & 7, ofs = (v >> 3) & 3;
        uint8_t* base = &st_vram[bank_off[b]];
        switch (b) {
        case 0: case 1:   /* A, B */
            if (mst == 1) map_range(0, 0x20000u * (uint32_t)ofs, b);
            else if (mst == 2) map_range(2, 0x20000u * (uint32_t)(ofs & 1), b);
            else if (mst == 3) tex_slot[ofs] = base;
            break;
        case 2: case 3:   /* C, D */
            if (mst == 1) map_range(0, 0x20000u * (uint32_t)ofs, b);
            else if (mst == 3) tex_slot[ofs] = base;
            else if (mst == 4) map_range(b == 2 ? 1 : 3, 0, b);
            break;
        case 4:           /* E */
            if (mst == 1) map_range(0, 0, b);
            else if (mst == 2) map_range(2, 0, b);
            else if (mst == 3) for (int i = 0; i < 4; i++) texpal_slot[i] = base + PAGE * (uint32_t)i;
            else if (mst == 4) for (int i = 0; i < 4; i++) bg_ext_pal[0][i] = base + 0x2000u * (uint32_t)i;
            break;
        case 5: case 6: { /* F, G */
            uint32_t o = 0x4000u * (uint32_t)(ofs & 1) + 0x10000u * (uint32_t)(ofs >> 1);
            if (mst == 1) map_range(0, o, b);
            else if (mst == 2) map_range(2, o, b);
            else if (mst == 3) texpal_slot[(ofs & 1) + (ofs >> 1) * 4] = base;
            else if (mst == 4) { bg_ext_pal[0][(ofs & 1) * 2] = base; bg_ext_pal[0][(ofs & 1) * 2 + 1] = base + 0x2000u; }
            else if (mst == 5) obj_ext_pal[0] = base;
            break;
        }
        case 7:           /* H */
            if (mst == 1) map_range(1, 0, b);
            else if (mst == 2) for (int i = 0; i < 4; i++) bg_ext_pal[1][i] = base + 0x2000u * (uint32_t)i;
            break;
        case 8:           /* I */
            if (mst == 1) map_range(1, 0x8000u, b);
            else if (mst == 2) map_range(3, 0, b);
            else if (mst == 3) obj_ext_pal[1] = base;
            break;
        }
    }
}

void st_vram_reset(void) {
    memset(vramcnt, 0, sizeof vramcnt);
    remap();
}

/* 0x04000240-0x04000246 = A-G, 0x04000248 = H, 0x04000249 = I (0x04000247 es WRAMCNT) */
static int bank_of(uint32_t addr) {
    uint32_t o = addr - 0x04000240u;
    if (o < 7) return (int)o;
    if (o == 8) return 7;
    if (o == 9) return 8;
    return -1;
}

int st_vram_cnt_write(uint32_t addr, uint32_t v, int size) {
    if (addr < 0x04000240u || addr >= 0x0400024Au) return 0;
    for (int i = 0; i < size; i++) {
        int b = bank_of(addr + (uint32_t)i);
        if (b >= 0) vramcnt[b] = (uint8_t)(v >> (8 * i));
    }
    remap();
    return 1;
}

int st_vram_cnt_read(uint32_t addr, int size, uint32_t* out) {
    if (addr < 0x04000240u || addr >= 0x0400024Au) return 0;
    uint32_t v = 0;
    for (int i = 0; i < size; i++) {
        int b = bank_of(addr + (uint32_t)i);
        if (b >= 0) v |= (uint32_t)vramcnt[b] << (8 * i);
    }
    *out = v;
    return 1;
}

static uint8_t open_bus[PAGE];   /* zona sin banco: lecturas a 0, escrituras perdidas */

uint8_t* st_vram_ptr(uint32_t addr) {
    uint32_t region = (addr >> 21) & 7u;
    if (region >= 4) {   /* LCDC: 0x06800000-0x068A3FFF */
        uint32_t o = addr & 0xFFFFFu;
        return o < 0xA4000u ? &st_vram[o] : (memset(open_bus, 0, 16), open_bus);
    }
    static const uint32_t region_view[4] = {0, 1, 2, 3};   /* 0x060/0x062/0x064/0x066 */
    int view = (int)region_view[region];
    uint32_t span = view_pages[view] * PAGE;
    uint32_t o = (addr & 0x1FFFFFu) % span;
    uint8_t* p = pages[view][o / PAGE];
    if (!p) { memset(open_bus, 0, 16); return open_bus + (o & 3u); }
    return p + (o % PAGE);
}

/* Lectura para los renderizadores (sin pasar por la CPU) */
uint8_t st_vram_view8(int view, uint32_t off) {
    uint32_t span = view_pages[view] * PAGE;
    off %= span;
    uint8_t* p = pages[view][off / PAGE];
    return p ? p[off % PAGE] : 0;
}
uint16_t st_vram_view16(int view, uint32_t off) {
    return (uint16_t)(st_vram_view8(view, off) | st_vram_view8(view, off + 1) << 8);
}
uint8_t* st_vram_tex_slot(int i) { return (i >= 0 && i < 4) ? tex_slot[i] : NULL; }
uint8_t* st_vram_texpal_slot(int i) { return (i >= 0 && i < 8) ? texpal_slot[i] : NULL; }
uint8_t* st_vram_bg_ext_pal(int engine, int slot) { return bg_ext_pal[engine & 1][slot & 3]; }
uint8_t* st_vram_obj_ext_pal(int engine) { return obj_ext_pal[engine & 1]; }
