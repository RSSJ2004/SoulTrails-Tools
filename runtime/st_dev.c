/* Dispositivos del ARM9 para la capa de plataforma (fase 1 de docs/PLAN.md):
 *
 * - Cartucho (0x040001A0-0x040001AF, 0x04100010): modo de datos ya desbloqueado, como lo deja
 *   el cargador. B7 = leer bloque de la ROM (las direcciones < 0x8000 se redirigen como en el
 *   hardware), B8 = ID del chip. Los datos salen de la ROM del usuario cargada en memoria.
 * - DMA (0x040000B0-0x040000EF): inmediata, al VBlank, del cartucho (una palabra por petición,
 *   como la usa el SDK) y de la FIFO 3D (se vuelca de golpe; el 3D llega en la fase 2).
 * - Timers (0x04000100-0x0400010F) sobre un reloj virtual de 33,51 MHz que avanza con las
 *   líneas de pantalla, los cuadros y cada lectura de un timer (para que las esperas activas
 *   avancen). Desbordamiento con recarga, cascada e IRQ.
 *
 * Escrito a partir de GBATEK; sin código de Nintendo. */
#include <stdio.h>
#include <string.h>

#include "strecomp.h"

/* ------------------------------------------------------------------ reloj */
#define CYCLES_PER_LINE 2130u   /* 355 puntos x 6 ciclos de 33,51 MHz */
static uint64_t clock_now;

typedef struct Timer {
    uint16_t reload, ctrl;
    uint32_t counter;   /* 16 bits efectivos */
    uint32_t frac;      /* ciclos acumulados por debajo del prescaler */
} Timer;
static Timer timers[4];
static const uint32_t prescale[4] = {1, 64, 256, 1024};

static void timers_advance(uint32_t cycles) {
    uint32_t prev_ovf = 0;
    for (int i = 0; i < 4; i++) {
        Timer* t = &timers[i];
        uint32_t ovf = 0;
        if (t->ctrl & 0x80u) {
            uint64_t ticks;
            if (i > 0 && (t->ctrl & 0x4u)) {
                ticks = prev_ovf;
            } else {
                uint64_t acc = (uint64_t)t->frac + cycles;
                ticks = acc / prescale[t->ctrl & 3u];
                t->frac = (uint32_t)(acc % prescale[t->ctrl & 3u]);
            }
            uint64_t v = (uint64_t)t->counter + ticks;
            if (v >= 0x10000u) {
                uint32_t period = 0x10000u - t->reload;
                uint64_t over = v - 0x10000u;
                ovf = (uint32_t)(1 + over / period);
                v = t->reload + over % period;
                if (t->ctrl & 0x40u) st_raise_irq(0x8u << i);
            }
            t->counter = (uint32_t)v;
        }
        prev_ovf = ovf;
    }
}

void st_clock_advance(uint32_t cycles) {
    clock_now += cycles;
    timers_advance(cycles);
}
uint64_t st_clock(void) { return clock_now; }

/* -------------------------------------------------------------- cartucho */
static const uint8_t* cart_data;
static size_t cart_size;
static uint32_t cart_chip_id = 0x00000FC2u;
static uint8_t cart_buf[0x4000];
static uint32_t cart_len, cart_pos;
static uint32_t romctrl, auxspicnt;
static uint8_t cart_cmd[8];

void st_hw_set_cart(const uint8_t* data, size_t size, uint32_t chip_id) {
    cart_data = data;
    cart_size = size;
    cart_chip_id = chip_id;
}

static void dma_card_ready(StCpu* c);

static void cart_finish(void) {
    romctrl &= ~((1u << 23) | (1u << 31));
    if (auxspicnt & (1u << 14)) st_raise_irq(1u << 19);
}

static void cart_start(StCpu* c) {
    uint32_t bs = (romctrl >> 24) & 7u;
    cart_len = bs == 0 ? 0 : bs == 7 ? 4 : 0x100u << bs;
    cart_pos = 0;
    if (cart_len > sizeof cart_buf) cart_len = sizeof cart_buf;
    switch (cart_cmd[0]) {
    case 0xB7: {
        uint32_t a = (uint32_t)cart_cmd[1] << 24 | (uint32_t)cart_cmd[2] << 16 | (uint32_t)cart_cmd[3] << 8 | cart_cmd[4];
        if (a < 0x8000u) a = 0x8000u + (a & 0x1FFu);   /* área segura protegida */
        for (uint32_t i = 0; i < cart_len; i++)
            cart_buf[i] = (cart_data && a + i < cart_size) ? cart_data[a + i] : 0xFF;
        break;
    }
    case 0xB8:
        for (uint32_t i = 0; i < cart_len; i += 4) memcpy(&cart_buf[i], &cart_chip_id, 4);
        break;
    default:
        memset(cart_buf, 0xFF, cart_len);
        break;
    }
    if (!cart_len) { cart_finish(); return; }
    romctrl |= (1u << 23) | (1u << 31);
    dma_card_ready(c);
}

static uint32_t cart_read_word(void) {
    if (cart_pos >= cart_len) return 0xFFFFFFFFu;
    uint32_t v;
    memcpy(&v, &cart_buf[cart_pos], 4);
    cart_pos += 4;
    if (cart_pos >= cart_len) cart_finish();
    return v;
}

/* ------------------------------------------------------------------- DMA */
typedef struct Dma { uint32_t sad, dad, cnt; uint32_t src, dst, count; } Dma;
static Dma dma[4];
static uint32_t dma_fill[4];

static int dma_timing(int i) { return (int)((dma[i].cnt >> 27) & 7u); }

static void dma_run(StCpu* c, int i, uint32_t words) {
    Dma* d = &dma[i];
    int wide = (d->cnt >> 26) & 1;
    uint32_t unit = wide ? 4 : 2;
    int dctl = (int)((d->cnt >> 21) & 3u), sctl = (int)((d->cnt >> 23) & 3u);
    for (uint32_t k = 0; k < words; k++) {
        if (wide) st_write32(c, d->dst & ~3u, st_read32(c, d->src & ~3u));
        else st_write16(c, d->dst & ~1u, (uint16_t)st_read16(c, d->src & ~1u));
        if (sctl == 0) d->src += unit; else if (sctl == 1) d->src -= unit;
        if (dctl == 0 || dctl == 3) d->dst += unit; else if (dctl == 1) d->dst -= unit;
    }
    if (!(d->cnt & (1u << 25)) || dma_timing(i) == 0) {
        d->cnt &= ~(1u << 31);
    } else if (dctl == 3) {
        d->dst = d->dad;
    }
    if (d->cnt & (1u << 30)) st_raise_irq(0x100u << i);
}

static uint32_t dma_words(int i) {
    uint32_t n = dma[i].cnt & 0x1FFFFFu;
    return n ? n : 0x200000u;
}

static void dma_trigger(StCpu* c, int timing) {
    for (int i = 0; i < 4; i++)
        if ((dma[i].cnt & (1u << 31)) && dma_timing(i) == timing) dma_run(c, i, dma_words(i));
}

/* El cartucho tiene datos: cada canal en modo cartucho copia su bloque por petición */
static void dma_card_ready(StCpu* c) {
    for (int guard = 0; cart_pos < cart_len && guard < 0x10000; guard++) {
        int ch = -1;
        for (int i = 0; i < 4; i++)
            if ((dma[i].cnt & (1u << 31)) && dma_timing(i) == 5) { ch = i; break; }
        if (ch < 0) return;   /* la CPU leerá 0x04100010 */
        Dma* d = &dma[ch];
        d->src = 0x04100010u;
        uint32_t n = dma_words(ch), left = (cart_len - cart_pos + 3) / 4;
        dma_run(c, ch, n < left ? n : left);
    }
}

static void dma_write_cnt(StCpu* c, int i, uint32_t v) {
    uint32_t was = dma[i].cnt;
    dma[i].cnt = v;
    if ((v & (1u << 31)) && !(was & (1u << 31))) {
        dma[i].src = dma[i].sad;
        dma[i].dst = dma[i].dad;
        int t = dma_timing(i);
        if (t == 0) dma_run(c, i, dma_words(i));
        else if (t == 5) { if (cart_pos < cart_len) dma_card_ready(c); }
        else if (t == 7) dma_run(c, i, dma_words(i));   /* FIFO 3D: hasta la fase 2, de golpe */
        else if (t != 1 && t != 2) dma[i].cnt &= ~(1u << 31);   /* modos sin emular: terminan ya */
    }
}

/* VBlank: lo llama st_hw.c al entrar en la línea 192 */
void st_dev_vblank(StCpu* c) { dma_trigger(c, 1); }

/* ------------------------------------------------------------ registros */
void st_dev_reset(void) {
    clock_now = 0;
    memset(timers, 0, sizeof timers);
    memset(dma, 0, sizeof dma);
    memset(dma_fill, 0, sizeof dma_fill);
    cart_len = cart_pos = 0;
    romctrl = auxspicnt = 0;
    memset(cart_cmd, 0, sizeof cart_cmd);
}

static uint32_t sub(uint32_t full, uint32_t addr, int size) {
    uint32_t v = full >> ((addr & 3) * 8);
    return size == 4 ? v : v & (size == 2 ? 0xFFFFu : 0xFFu);
}
static uint32_t merge(uint32_t full, uint32_t addr, uint32_t v, int size) {
    uint32_t sh = (addr & 3) * 8;
    uint32_t mask = (size == 4 ? 0xFFFFFFFFu : size == 2 ? 0xFFFFu : 0xFFu) << sh;
    return (full & ~mask) | ((v << sh) & mask);
}

int st_dev_read(StCpu* c, uint32_t addr, int size, uint32_t* out) {
    uint32_t a = addr & ~3u;
    if (a >= 0x040000B0u && a < 0x040000E0u) {
        int i = (int)((a - 0x040000B0u) / 12), r = (int)((a - 0x040000B0u) % 12);
        *out = sub(r == 0 ? dma[i].sad : r == 4 ? dma[i].dad : dma[i].cnt, addr, size);
        return 1;
    }
    if (a >= 0x040000E0u && a < 0x040000F0u) { *out = sub(dma_fill[(a - 0x040000E0u) / 4], addr, size); return 1; }
    if (a >= 0x04000100u && a < 0x04000110u) {
        st_clock_advance(16);   /* una espera activa sobre un timer tiene que avanzar */
        Timer* t = &timers[(a - 0x04000100u) / 4];
        *out = sub(t->counter | (uint32_t)t->ctrl << 16, addr, size);
        return 1;
    }
    if (a == 0x040001A0u) { *out = sub(auxspicnt, addr, size); return 1; }   /* AUXSPICNT/AUXSPIDATA */
    if (a == 0x040001A4u) { *out = sub(romctrl, addr, size); return 1; }
    if (a == 0x040001A8u || a == 0x040001ACu) {
        uint32_t w;
        memcpy(&w, &cart_cmd[a - 0x040001A8u], 4);
        *out = sub(w, addr, size);
        return 1;
    }
    if (a == 0x04100010u) { *out = sub(cart_read_word(), addr, size); return 1; }
    (void)c;
    return 0;
}

int st_dev_write(StCpu* c, uint32_t addr, uint32_t v, int size) {
    uint32_t a = addr & ~3u;
    if (a >= 0x040000B0u && a < 0x040000E0u) {
        int i = (int)((a - 0x040000B0u) / 12), r = (int)((a - 0x040000B0u) % 12);
        if (r == 0) dma[i].sad = merge(dma[i].sad, addr, v, size);
        else if (r == 4) dma[i].dad = merge(dma[i].dad, addr, v, size);
        else dma_write_cnt(c, i, merge(dma[i].cnt, addr, v, size));
        return 1;
    }
    if (a >= 0x040000E0u && a < 0x040000F0u) {
        uint32_t* f = &dma_fill[(a - 0x040000E0u) / 4];
        *f = merge(*f, addr, v, size);
        return 1;
    }
    if (a >= 0x04000100u && a < 0x04000110u) {
        Timer* t = &timers[(a - 0x04000100u) / 4];
        uint32_t full = merge(t->reload | (uint32_t)t->ctrl << 16, addr, v, size);
        uint16_t ctrl = (uint16_t)(full >> 16);
        t->reload = (uint16_t)full;
        if ((ctrl & 0x80u) && !(t->ctrl & 0x80u)) { t->counter = t->reload; t->frac = 0; }
        t->ctrl = ctrl & 0xC7u;
        return 1;
    }
    if (a == 0x040001A0u) {   /* AUXSPICNT (bajo) y AUXSPIDATA (alto): la copia de seguridad llega en la fase 3 */
        auxspicnt = merge(auxspicnt, addr, v, size) & 0x0000E043u;
        return 1;
    }
    if (a == 0x040001A4u) {
        uint32_t nv = merge(romctrl, addr, v, size);
        int start = (nv & (1u << 31)) && !(romctrl & (1u << 31));
        romctrl = (nv & ~(1u << 23)) | (romctrl & (1u << 23));
        if (start) cart_start(c);
        return 1;
    }
    if (a == 0x040001A8u || a == 0x040001ACu) {
        uint32_t w;
        memcpy(&w, &cart_cmd[a - 0x040001A8u], 4);
        w = merge(w, addr, v, size);
        memcpy(&cart_cmd[a - 0x040001A8u], &w, 4);
        return 1;
    }
    return 0;
}
