/* Motores 2D A y B (fase 2.1 de docs/PLAN.md), por software y a partir de GBATEK:
 * fondos de texto, afines, extendidos (mapa afín de 16 bits, mapa de bits de 256 colores y de
 * color directo), sprites normales/afines/de mapa de bits, paletas extendidas, prioridades,
 * mezcla (alfa y brillo) y brillo maestro. Modos de pantalla: apagada, gráficos y VRAM (LCDC).
 *
 * Se dibuja el cuadro entero con el estado de los registros al final del cuadro (los efectos
 * a mitad de cuadro llegarán al pasar a renderizado por línea). Las ventanas y el mosaico aún
 * no se aplican. BG0 en 3D (motor A) queda transparente hasta el renderizador 3D (fase 2.2). */
#include <string.h>

#include "strecomp.h"

#define W 256
#define H 192

uint16_t st_io16(uint32_t addr);
uint32_t st_io32(uint32_t addr);
uint8_t st_vram_view8(int view, uint32_t off);
uint16_t st_vram_view16(int view, uint32_t off);
uint8_t* st_vram_bg_ext_pal(int engine, int slot);
uint8_t* st_vram_obj_ext_pal(int engine);

typedef struct Eng {
    int id;            /* 0 = A, 1 = B */
    uint32_t io;       /* 0x04000000 o 0x04001000 */
    int bg_view, obj_view;
    const uint8_t* pal;   /* 1 KB: fondos 0-1FFh, objetos 200h-3FFh */
    const uint8_t* oam;
    uint32_t dispcnt;
} Eng;

/* Capa por píxel: color de 15 bits + bit 15 = opaco; prioridad y tipo para componer */
typedef struct Px { uint16_t color; uint8_t prio; uint8_t layer; } Px;   /* layer 0-3 BG, 4 OBJ, 5 fondo */

static uint16_t pal16(const uint8_t* p, uint32_t i) { return (uint16_t)(p[i * 2] | p[i * 2 + 1] << 8); }

/* ---------------------------------------------------------------- fondos */
static int bg_pixel_text(const Eng* e, int n, uint32_t cnt, int x, int y, uint16_t* out) {
    uint32_t hofs = st_io16(e->io + 0x10 + 4 * (uint32_t)n) & 0x1FFu, vofs = st_io16(e->io + 0x12 + 4 * (uint32_t)n) & 0x1FFu;
    int size = (int)(cnt >> 14);
    uint32_t wpx = (size & 1) ? 512 : 256, hpx = (size & 2) ? 512 : 256;
    uint32_t px = ((uint32_t)x + hofs) % wpx, py = ((uint32_t)y + vofs) % hpx;
    uint32_t char_base = ((cnt >> 2) & 15u) * 0x4000u, scr_base = ((cnt >> 8) & 31u) * 0x800u;
    if (e->id == 0) { char_base += ((e->dispcnt >> 24) & 7u) * 0x10000u; scr_base += ((e->dispcnt >> 27) & 7u) * 0x10000u; }
    uint32_t block = (px / 256) + (py / 256) * ((wpx == 512) ? 2u : 1u);
    uint32_t tx = (px % 256) / 8, ty = (py % 256) / 8;
    uint16_t ent = st_vram_view16(e->bg_view, scr_base + block * 0x800u + (ty * 32 + tx) * 2);
    uint32_t fx = px & 7, fy = py & 7;
    if (ent & 0x400) fx = 7 - fx;
    if (ent & 0x800) fy = 7 - fy;
    uint32_t tile = ent & 0x3FFu, idx;
    if (cnt & 0x80) {   /* 256 colores */
        idx = st_vram_view8(e->bg_view, char_base + tile * 64 + fy * 8 + fx);
        if (!idx) return 0;
        if (e->dispcnt & (1u << 30)) {   /* paleta extendida */
            int slot = n;
            if (n < 2 && (cnt & (1u << 13))) slot = n + 2;
            uint8_t* ep = st_vram_bg_ext_pal(e->id, slot);
            *out = ep ? pal16(ep, (ent >> 12) * 256u + idx) : 0;
        } else {
            *out = pal16(e->pal, idx);
        }
    } else {
        uint8_t b = st_vram_view8(e->bg_view, char_base + tile * 32 + fy * 4 + fx / 2);
        idx = (fx & 1) ? b >> 4 : b & 15u;
        if (!idx) return 0;
        *out = pal16(e->pal, (ent >> 12) * 16u + idx);
    }
    return 1;
}

static int bg_pixel_affine(const Eng* e, int n, uint32_t cnt, int x, int y, int kind, uint16_t* out) {
    /* kind: 0 afín (mapa de 8 bits), 1 extendido de 16 bits, 2 bitmap 256 colores, 3 bitmap directo */
    uint32_t base = e->io + 0x20 + 0x10 * (uint32_t)(n - 2);
    int32_t pa = (int16_t)st_io16(base), pb = (int16_t)st_io16(base + 2), pc = (int16_t)st_io16(base + 4),
            pd = (int16_t)st_io16(base + 6);
    int32_t rx = (int32_t)(st_io32(base + 8) << 4) >> 4, ry = (int32_t)(st_io32(base + 12) << 4) >> 4;
    int32_t tx = (rx + pa * x + pb * y) >> 8, ty = (ry + pc * x + pd * y) >> 8;
    int size = (int)(cnt >> 14);
    int32_t wpx, hpx;
    if (kind == 0 || kind == 1) { wpx = hpx = 128 << size; }
    else { static const int sw[4] = {128, 256, 512, 512}, sh[4] = {128, 256, 256, 512}; wpx = sw[size]; hpx = sh[size]; }
    if (tx < 0 || ty < 0 || tx >= wpx || ty >= hpx) {
        if (!(cnt & (1u << 13))) return 0;   /* sin repetición */
        tx = ((tx % wpx) + wpx) % wpx;
        ty = ((ty % hpx) + hpx) % hpx;
    }
    uint32_t char_base = ((cnt >> 2) & 15u) * 0x4000u, scr_base = ((cnt >> 8) & 31u) * 0x800u;
    if (e->id == 0) { char_base += ((e->dispcnt >> 24) & 7u) * 0x10000u; scr_base += ((e->dispcnt >> 27) & 7u) * 0x10000u; }
    uint32_t bmp_base = ((cnt >> 8) & 31u) * 0x4000u;
    if (kind == 3) {
        uint16_t c = st_vram_view16(e->bg_view, bmp_base + ((uint32_t)ty * (uint32_t)wpx + (uint32_t)tx) * 2);
        if (!(c & 0x8000)) return 0;
        *out = c;
        return 1;
    }
    if (kind == 2) {
        uint8_t idx = st_vram_view8(e->bg_view, bmp_base + (uint32_t)ty * (uint32_t)wpx + (uint32_t)tx);
        if (!idx) return 0;
        *out = pal16(e->pal, idx);
        return 1;
    }
    uint32_t mx = (uint32_t)tx / 8, my = (uint32_t)ty / 8, mw = (uint32_t)wpx / 8;
    if (kind == 0) {
        uint8_t tile = st_vram_view8(e->bg_view, scr_base + my * mw + mx);
        uint8_t idx = st_vram_view8(e->bg_view, char_base + tile * 64u + ((uint32_t)ty & 7) * 8 + ((uint32_t)tx & 7));
        if (!idx) return 0;
        *out = pal16(e->pal, idx);
        return 1;
    }
    uint16_t ent = st_vram_view16(e->bg_view, scr_base + (my * mw + mx) * 2);
    uint32_t fx = (uint32_t)tx & 7, fy = (uint32_t)ty & 7;
    if (ent & 0x400) fx = 7 - fx;
    if (ent & 0x800) fy = 7 - fy;
    uint8_t idx = st_vram_view8(e->bg_view, char_base + (ent & 0x3FFu) * 64u + fy * 8 + fx);
    if (!idx) return 0;
    if (e->dispcnt & (1u << 30)) {
        uint8_t* ep = st_vram_bg_ext_pal(e->id, n);
        *out = ep ? pal16(ep, (ent >> 12) * 256u + idx) : 0;
    } else {
        *out = pal16(e->pal, idx);
    }
    return 1;
}

/* Tipo de fondo n según el modo: -1 apagado, 0 texto, 1 afín, 2 extendido, 3 grande */
static int bg_type(int mode, int n) {
    static const int t[8][4] = {
        {0, 0, 0, 0}, {0, 0, 0, 1}, {0, 0, 1, 1}, {0, 0, 0, 2}, {0, 0, 1, 2}, {0, 0, 2, 2}, {-1, -1, 3, -1}, {-1, -1, -1, -1}};
    return t[mode & 7][n];
}

static int bg_pixel(const Eng* e, int n, int x, int y, uint16_t* out) {
    uint32_t cnt = st_io16(e->io + 0x08 + 2 * (uint32_t)n);
    int type = bg_type((int)(e->dispcnt & 7u), n);
    switch (type) {
    case 0: return bg_pixel_text(e, n, cnt, x, y, out);
    case 1: return bg_pixel_affine(e, n, cnt, x, y, 0, out);
    case 2: {
        int kind = !(cnt & 0x80) ? 1 : (cnt & 4) ? 3 : 2;
        return bg_pixel_affine(e, n, cnt, x, y, kind, out);
    }
    case 3: return bg_pixel_affine(e, n, cnt, x, y, 2, out);
    default: return 0;
    }
}

/* --------------------------------------------------------------- objetos */
static const int obj_w[3][4] = {{8, 16, 32, 64}, {16, 32, 32, 64}, {8, 8, 16, 32}};
static const int obj_h[3][4] = {{8, 16, 32, 64}, {8, 8, 16, 32}, {16, 32, 32, 64}};

static void draw_objs(const Eng* e, Px* line_buf /* W*H */) {
    for (int i = 127; i >= 0; i--) {   /* el de índice menor queda encima */
        const uint8_t* o = e->oam + i * 8;
        uint16_t a0 = (uint16_t)(o[0] | o[1] << 8), a1 = (uint16_t)(o[2] | o[3] << 8), a2 = (uint16_t)(o[4] | o[5] << 8);
        int affine = (a0 >> 8) & 1;
        if (!affine && (a0 & 0x200)) continue;   /* desactivado */
        int shape = a0 >> 14, sz = a1 >> 14;
        if (shape == 3) continue;
        int mode = (a0 >> 10) & 3;
        if (mode == 2) continue;                 /* ventana de objetos: sin ventanas todavía */
        int w = obj_w[shape][sz], h = obj_h[shape][sz];
        int bw = w, bh = h;
        if (affine && (a0 & 0x200)) { bw *= 2; bh *= 2; }
        int y0 = a0 & 0xFF, x0 = a1 & 0x1FF;
        if (y0 >= 192) y0 -= 256;
        if (x0 >= 256) x0 -= 512;
        int prio = (a2 >> 10) & 3;
        int pa = 256, pb = 0, pc = 0, pd = 256;
        if (affine) {
            int g = (a1 >> 9) & 31;
            const uint8_t* m = e->oam + g * 32 + 6;
            pa = (int16_t)(m[0] | m[1] << 8); pb = (int16_t)(m[8] | m[9] << 8);
            pc = (int16_t)(m[16] | m[17] << 8); pd = (int16_t)(m[24] | m[25] << 8);
        }
        int c256 = (a0 >> 13) & 1;
        uint32_t tile = a2 & 0x3FFu;
        for (int py = 0; py < bh; py++) {
            int sy = y0 + py;
            if (sy < 0 || sy >= H) continue;
            for (int px = 0; px < bw; px++) {
                int sx = x0 + px;
                if (sx < 0 || sx >= W) continue;
                int tx, ty;
                if (affine) {
                    int cx = px - bw / 2, cy = py - bh / 2;
                    tx = ((pa * cx + pb * cy) >> 8) + w / 2;
                    ty = ((pc * cx + pd * cy) >> 8) + h / 2;
                    if (tx < 0 || ty < 0 || tx >= w || ty >= h) continue;
                } else {
                    tx = (a1 & 0x1000) ? w - 1 - px : px;
                    ty = (a1 & 0x2000) ? h - 1 - py : py;
                }
                uint16_t color;
                if (mode == 3) {   /* objeto de mapa de bits (color directo) */
                    uint32_t off;
                    if (e->dispcnt & (1u << 6)) off = tile * (128u << ((e->dispcnt >> 22) & 1u)) + (uint32_t)(ty * w + tx) * 2;
                    else {
                        uint32_t mask = (e->dispcnt & (1u << 5)) ? 0x1Fu : 0x0Fu;
                        uint32_t rowpx = (e->dispcnt & (1u << 5)) ? 256u : 128u;
                        off = ((tile & mask) * 0x10 + (tile & ~mask) * 0x80) + (uint32_t)ty * rowpx * 2 + (uint32_t)tx * 2;
                    }
                    color = st_vram_view16(e->obj_view, off);
                    if (!(color & 0x8000)) continue;
                } else {
                    uint32_t off, bpp_bytes = c256 ? 64u : 32u;
                    if (e->dispcnt & (1u << 4)) {   /* 1D */
                        uint32_t bound = 32u << ((e->dispcnt >> 20) & 3u);
                        uint32_t tiles_w = (uint32_t)w / 8;
                        off = tile * bound + ((uint32_t)(ty / 8) * tiles_w + (uint32_t)(tx / 8)) * bpp_bytes;
                    } else {                        /* 2D: matriz de 32x32 unidades de 32 bytes */
                        off = (tile + (uint32_t)(ty / 8) * 32u) * 32u + (uint32_t)(tx / 8) * bpp_bytes;
                    }
                    uint32_t fx = (uint32_t)tx & 7, fy = (uint32_t)ty & 7, idx;
                    if (c256) {
                        idx = st_vram_view8(e->obj_view, off + fy * 8 + fx);
                        if (!idx) continue;
                        if (e->dispcnt & (1u << 31)) {
                            uint8_t* ep = st_vram_obj_ext_pal(e->id);
                            color = ep ? pal16(ep, (a2 >> 12) * 256u + idx) : 0;
                        } else {
                            color = pal16(e->pal, 256 + idx);
                        }
                    } else {
                        uint8_t b = st_vram_view8(e->obj_view, off + fy * 4 + fx / 2);
                        idx = (fx & 1) ? b >> 4 : b & 15u;
                        if (!idx) continue;
                        color = pal16(e->pal, 256 + (a2 >> 12) * 16u + idx);
                    }
                }
                Px* d = &line_buf[sy * W + sx];
                if (d->layer == 4 && d->prio <= prio) continue;
                d->color = (uint16_t)(color | 0x8000);
                d->prio = (uint8_t)prio;
                d->layer = 4;
            }
        }
    }
}

/* ------------------------------------------------------------ composición */
static uint32_t rgb(uint16_t c) {
    uint32_t r = c & 31u, g = (c >> 5) & 31u, b = (c >> 10) & 31u;
    r = r << 3 | r >> 2; g = g << 3 | g >> 2; b = b << 3 | b >> 2;
    return 0xFF000000u | r << 16 | g << 8 | b;
}

static uint16_t blend(uint16_t a, uint16_t b, int ea, int eb) {
    uint32_t r = ((a & 31u) * (uint32_t)ea + (b & 31u) * (uint32_t)eb) / 16;
    uint32_t g = (((a >> 5) & 31u) * (uint32_t)ea + ((b >> 5) & 31u) * (uint32_t)eb) / 16;
    uint32_t bl = (((a >> 10) & 31u) * (uint32_t)ea + ((b >> 10) & 31u) * (uint32_t)eb) / 16;
    if (r > 31) r = 31;
    if (g > 31) g = 31;
    if (bl > 31) bl = 31;
    return (uint16_t)(r | g << 5 | bl << 10);
}
static uint16_t bright(uint16_t c, int up, int y) {
    uint32_t ch[3] = {c & 31u, (c >> 5) & 31u, (c >> 10) & 31u};
    for (int i = 0; i < 3; i++) ch[i] = up ? ch[i] + (31 - ch[i]) * (uint32_t)y / 16 : ch[i] - ch[i] * (uint32_t)y / 16;
    return (uint16_t)(ch[0] | ch[1] << 5 | ch[2] << 10);
}

static Px top[W * H], under[W * H];

void st_gpu2d_render(int engine, uint32_t* out /* W*H, 0xAARRGGBB */) {
    Eng e;
    e.id = engine;
    e.io = engine ? 0x04001000u : 0x04000000u;
    e.bg_view = engine ? 1 : 0;
    e.obj_view = engine ? 3 : 2;
    e.pal = st_palette + (engine ? 0x400 : 0);
    e.oam = st_oam + (engine ? 0x400 : 0);
    e.dispcnt = st_io32(e.io);
    int dmode = (int)((e.dispcnt >> 16) & 3u);
    if (engine) dmode = dmode ? 1 : 0;
    if (dmode == 0 || (e.dispcnt & 0x80u)) {   /* apagada o en blanco forzado */
        for (int i = 0; i < W * H; i++) out[i] = 0xFFFFFFFFu;
        return;
    }
    if (dmode == 2) {   /* VRAM: banco LCDC elegido (A-D) como imagen de 16 bits */
        const uint8_t* bank = st_vram + ((e.dispcnt >> 18) & 3u) * 0x20000u;
        for (int i = 0; i < W * H; i++) out[i] = rgb((uint16_t)(bank[i * 2] | bank[i * 2 + 1] << 8));
        return;
    }
    if (dmode == 3) {   /* FIFO de la memoria principal: aún no */
        for (int i = 0; i < W * H; i++) out[i] = 0xFF000000u;
        return;
    }
    uint16_t backdrop = pal16(e.pal, 0);
    for (int i = 0; i < W * H; i++) {
        top[i] = (Px){backdrop, 4, 5};
        under[i] = (Px){backdrop, 4, 5};
    }
    /* Fondos: de menor a mayor prioridad visual; se guarda la capa superior y la de debajo */
    for (int n = 0; n < 4; n++) {
        if (!(e.dispcnt & (0x100u << n))) continue;
        if (n == 0 && engine == 0 && (e.dispcnt & 8u)) continue;   /* BG0 = 3D (fase 2.2) */
        int prio = st_io16(e.io + 0x08 + 2 * (uint32_t)n) & 3;
        for (int y = 0; y < H; y++)
            for (int x = 0; x < W; x++) {
                uint16_t c;
                if (!bg_pixel(&e, n, x, y, &c)) continue;
                Px* t = &top[y * W + x];
                Px p = {(uint16_t)(c & 0x7FFF), (uint8_t)prio, (uint8_t)n};
                if (prio < t->prio || (prio == t->prio && n < t->layer)) { under[y * W + x] = *t; *t = p; }
                else {
                    Px* u = &under[y * W + x];
                    if (prio < u->prio || (prio == u->prio && n < u->layer)) *u = p;
                }
            }
    }
    if (e.dispcnt & 0x1000u) {
        static Px objs[W * H];
        for (int i = 0; i < W * H; i++) objs[i] = (Px){0, 0xFF, 0xFF};
        draw_objs(&e, objs);
        for (int i = 0; i < W * H; i++) {
            if (objs[i].layer != 4) continue;
            Px p = {(uint16_t)(objs[i].color & 0x7FFF), objs[i].prio, 4};
            if (p.prio <= top[i].prio) { under[i] = top[i]; top[i] = p; }
            else if (p.prio <= under[i].prio) under[i] = p;
        }
    }
    /* Efectos de color */
    uint16_t bldcnt = st_io16(e.io + 0x50), bldalpha = st_io16(e.io + 0x52), bldy = st_io16(e.io + 0x54);
    int effect = (bldcnt >> 6) & 3;
    int eva = bldalpha & 31, evb = (bldalpha >> 8) & 31, ey = bldy & 31;
    if (eva > 16) eva = 16;
    if (evb > 16) evb = 16;
    if (ey > 16) ey = 16;
    uint32_t mb = st_io32(e.io + 0x6C);
    int mmode = (int)((mb >> 14) & 3u), mfac = (int)(mb & 31u);
    if (mfac > 16) mfac = 16;
    for (int i = 0; i < W * H; i++) {
        uint16_t c = top[i].color;
        int l1 = top[i].layer, l2 = under[i].layer;
        if (effect && (bldcnt & (1u << l1))) {
            if (effect == 1 && (bldcnt & (0x100u << l2))) c = blend(c, under[i].color, eva, evb);
            else if (effect == 2) c = bright(c, 1, ey);
            else if (effect == 3) c = bright(c, 0, ey);
        }
        if (mmode == 1) c = bright(c, 1, mfac);
        else if (mmode == 2) c = bright(c, 0, mfac);
        out[i] = rgb(c);
    }
}

/* Pantalla superior/inferior según POWCNT1 bit 15 (1: motor A arriba) */
void st_gpu2d_frame(uint32_t* top_out, uint32_t* bottom_out) {
    int a_top = (st_io16(0x04000304u) >> 15) & 1;
    st_gpu2d_render(a_top ? 0 : 1, top_out);
    st_gpu2d_render(a_top ? 1 : 0, bottom_out);
}
