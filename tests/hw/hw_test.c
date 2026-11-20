/* Prueba del hardware mínimo (runtime/st_hw.c) sin código invitado. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "strecomp.h"

const StModule st_modules[1];
const unsigned st_module_count = 0;

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FALLO línea %d: %s\n", __LINE__, #c); fails++; } } while (0)

static char halted[200];
static void on_halt(StCpu* c, const char* why) { (void)c; snprintf(halted, sizeof halted, "%s", why); }
static uint32_t sent[8];
static int nsent;
static void on_pxi(StCpu* c, uint32_t v) { (void)c; sent[nsent++ & 7] = v; }
static int idles;
static int on_idle(StCpu* c) { (void)c; idles++; st_hw_frame(); return 1; }

/* Bucle de arranque de la FIFO del PXI tal como lo hace el juego (ver HANDOFF.md): copiar el
 * valor del ARM7, esperar hasta 1000 lecturas a que cambie, y salir al verlo en 0 tras más de
 * 4 cambios seguidos. Devuelve las vueltas, o -1 si no termina. */
static int pxi_fifo_handshake(StCpu* c) {
    int changes = 0;
    for (int it = 0; it < 10000; it++) {
        uint32_t remote = st_read16(c, 0x04000180) & 15;
        st_write16(c, 0x04000180, (uint16_t)(remote << 8));
        if (remote == 0 && changes > 4) return it;
        if ((st_read16(c, 0x04000180) & 15) == remote) {
            int left = 1000;
            for (;;) {
                if (left <= 0) { changes = 0; break; }
                left--;
                if ((st_read16(c, 0x04000180) & 15) != remote) break;
            }
        }
        changes++;
    }
    return -1;
}

int main(void) {
    StCpu c;
    st_init(&c);
    st_platform.halt = on_halt;
    st_platform.pxi_send = on_pxi;
    st_platform.idle = on_idle;

    /* Registro sin emular: devuelve lo último escrito */
    st_write16(&c, 0x04000304, 0x820F);
    CHECK(st_read16(&c, 0x04000304) == 0x820F);
    CHECK(st_read8(&c, 0x04000305) == 0x82);

    /* IPCSYNC: el ARM7 hace eco de los bits 8-11 en los bits 0-3 */
    CHECK((st_read16(&c, 0x04000180) & 0xF) == 0);
    st_write16(&c, 0x04000180, 0x0300 | 0x4000);
    CHECK(st_read16(&c, 0x04000180) == (0x4300 | 0x3));
    st_write8(&c, 0x04000181, 0x0C);
    CHECK((st_read16(&c, 0x04000180) & 0xF) == 0xC);

    /* Primera sincronización (eco) y arranque de la FIFO del PXI (cuenta atrás) */
    for (int i = 4; i >= 0; i--) {
        st_write16(&c, 0x04000180, (uint16_t)(i << 8));
        CHECK((st_read16(&c, 0x04000180) & 15) == (uint32_t)i);
    }
    CHECK(pxi_fifo_handshake(&c) > 0);

    /* FIFO: vacía al empezar; enviar llega a la plataforma; respuesta encolada */
    CHECK((st_read16(&c, 0x04000184) & 0x0101) == 0x0101);
    st_write16(&c, 0x04000184, 0x8000 | 0x0400 | 0x0008);
    st_write32(&c, 0x04000188, 0x12345 << 6 | 7);
    CHECK(nsent == 1 && sent[0] == (0x12345u << 6 | 7));
    st_pxi_reply(0xCAFE0001u);
    CHECK((st_read16(&c, 0x04000184) & 0x0100) == 0);
    CHECK(st_irq.if_ & ST_IRQ_FIFO_RECV);
    CHECK(st_read32(&c, 0x04100000) == 0xCAFE0001u);
    CHECK((st_read16(&c, 0x04000184) & 0x0100) == 0x0100);
    st_read32(&c, 0x04100000);                         /* vacía y activada: error */
    CHECK(st_read16(&c, 0x04000184) & 0x4000);
    st_write16(&c, 0x04000184, 0x8000 | 0x4000);       /* reconocer */
    CHECK(!(st_read16(&c, 0x04000184) & 0x4000));

    /* VCOUNT avanza al leerlo; VBlank con IRQ y una vuelta = un cuadro */
    st_write16(&c, 0x04000004, 0x0008);
    st_irq.if_ = 0;
    int seen_vblank = 0;
    for (int i = 0; i < 300; i++) {
        uint16_t vc = (uint16_t)st_read16(&c, 0x04000006);
        CHECK(vc < 263);
        if (vc == 200) seen_vblank = (st_read16(&c, 0x04000004) & 1) != 0;
    }
    CHECK(seen_vblank);
    CHECK(st_irq.if_ & ST_IRQ_VBLANK);
    CHECK(idles >= 1);

    /* ARM7 HLE por defecto: responde con el mismo tag y comando; el RTC deja la fecha en BCD */
    st_platform.pxi_send = NULL;
    while (!(st_read16(&c, 0x04000184) & 0x0100)) st_read32(&c, 0x04100000);   /* vaciar */
    st_write32(&c, 0x04000188, (0x1000u << 6) | 5u);
    CHECK(st_read32(&c, 0x04100000) == ((0x1000u << 6) | 5u));
    CHECK(st_main_ram[0x3FFDE8 + 1] >= 0x01 && st_main_ram[0x3FFDE8 + 1] <= 0x12);   /* mes en BCD */
    st_platform.pxi_send = on_pxi;

    /* Detector de esperas */
    st_hw_poll_limit = 1000;
    st_hw_frame();
    for (int i = 0; i < 999; i++) st_read16(&c, 0x04000180);
    CHECK(!halted[0]);
    st_hw_frame();                                     /* un cuadro reinicia la cuenta */
    for (int i = 0; i < 999; i++) st_read16(&c, 0x04000180);
    CHECK(!halted[0]);
    st_read16(&c, 0x04000180);
    st_read16(&c, 0x04000180);
    CHECK(strstr(halted, "04000180") && strstr(halted, "IPCSYNC"));
    halted[0] = 0;
    st_hw_frame();                                     /* alternando dos registros también */
    for (int i = 0; i < 1001; i++) st_read16(&c, i & 1 ? 0x04000180 : 0x04000184);
    CHECK(halted[0]);

    /* Cartucho: B7 lee un bloque de la ROM (por CPU y por DMA del cartucho); B8 da el ID */
    static uint8_t rom[0x20000];
    for (unsigned i = 0; i < sizeof rom; i++) rom[i] = (uint8_t)(i * 7 + (i >> 8));
    st_hw_set_cart(rom, sizeof rom, 0x00003FC2u);
    st_write16(&c, 0x040001A0, 0xC000);                   /* slot activado + IRQ al terminar */
    st_irq.if_ = 0;
    st_write32(&c, 0x040001A8, 0x010000B7u);              /* B7 00 00 01 | 00 ... -> 0x00000100 -> 0x8100 */
    st_write32(&c, 0x040001AC, 0x00000000u);
    st_write32(&c, 0x040001A4, 0x80000000u | (1u << 24));  /* 0x200 bytes */
    CHECK(st_read32(&c, 0x040001A4) & (1u << 23));
    int ok = 1;
    for (unsigned i = 0; i < 0x200; i += 4) {
        uint32_t w = st_read32(&c, 0x04100010), e;
        memcpy(&e, &rom[0x8100 + i], 4);
        ok &= w == e;
    }
    CHECK(ok);
    CHECK(!(st_read32(&c, 0x040001A4) & (1u << 31)));
    CHECK(st_irq.if_ & (1u << 19));
    st_write32(&c, 0x040001A8, 0x000000B8u);
    st_write32(&c, 0x040001A4, 0x80000000u | (7u << 24));
    CHECK(st_read32(&c, 0x04100010) == 0x00003FC2u);

    /* DMA del cartucho: una palabra por petición, con repetición */
    st_write32(&c, 0x040000D4, 0x04100010u);
    st_write32(&c, 0x040000D8, 0x02100000u);
    st_write32(&c, 0x040000DC, 0x80000000u | (5u << 27) | (1u << 26) | (1u << 25) | 2u << 23 | 1u);
    st_write32(&c, 0x040001A8, 0x900000B7u);              /* B7 00 00 90 00 -> 0x9000 */
    st_write32(&c, 0x040001A4, 0x80000000u | (1u << 24));
    CHECK(memcmp(&st_main_ram[0x100000], &rom[0x9000], 0x200) == 0);
    CHECK(!(st_read32(&c, 0x040001A4) & (1u << 31)));

    /* DMA inmediata de 16 bits */
    for (int i = 0; i < 8; i++) st_write16(&c, 0x02200000 + 2 * i, (uint16_t)(0x1111 * i));
    st_write32(&c, 0x040000B0, 0x02200000u);
    st_write32(&c, 0x040000B4, 0x02200100u);
    st_write32(&c, 0x040000B8, 0x80000000u | 8u);
    CHECK(st_read16(&c, 0x0220010E) == 0x7777);
    CHECK(!(st_read32(&c, 0x040000B8) & 0x80000000u));

    /* Timer 0: recarga FF00, sin prescaler, IRQ al desbordar; timer 1 en cascada */
    st_irq.if_ = 0;
    st_write32(&c, 0x04000100, 0x00C0FF00u);
    st_write32(&c, 0x04000104, 0x00840000u);
    st_clock_advance(0x100);
    CHECK(st_irq.if_ & 0x8u);
    CHECK(st_read16(&c, 0x04000104) == 1);
    st_clock_advance(0x100 * 3);
    CHECK(st_read16(&c, 0x04000104) == 4);

    /* IRQ pendiente: IME y IE */
    st_write32(&c, 0x04000208, 0);
    st_write32(&c, 0x04000210, 0x8u);
    CHECK(!st_irq_pending);
    st_write32(&c, 0x04000208, 1);
    CHECK(st_irq_pending);
    st_write32(&c, 0x04000214, 0x8u);
    CHECK(!st_irq_pending);

    /* VRAM y motor 2D A: banco A como fondos, banco B como objetos */
    st_write8(&c, 0x04000240, 0x81);                      /* A -> BG-A */
    st_write8(&c, 0x04000241, 0x82);                      /* B -> OBJ-A */
    st_write16(&c, 0x05000000, 0x7C00);                   /* fondo (backdrop): azul */
    st_write16(&c, 0x05000002, 0x001F);                   /* BG paleta 0, color 1: rojo */
    st_write16(&c, 0x05000204, 0x03E0);                   /* OBJ paleta 0, color 2: verde */
    for (int i = 0; i < 32; i += 2) st_write16(&c, 0x06000020 + (uint32_t)i, 0x1111);   /* tile 1 de fondo */
    st_write16(&c, 0x06000800, 0x0001);                   /* mapa (base 1 = 0x800): tile 1 en (0,0) */
    CHECK(st_vram[0x800] == 1);                           /* el banco A recibe lo escrito en BG-A */
    CHECK(st_read16(&c, 0x06800800) == 1);                /* y se ve por LCDC */
    for (int i = 0; i < 32; i += 2) st_write16(&c, 0x06400040 + (uint32_t)i, 0x2222);   /* tile 2 de objetos */
    st_write16(&c, 0x04000008, 0x0100);                   /* BG0CNT: char 0, screen 1 */
    st_write16(&c, 0x07000000, 0x0000);                   /* OBJ 0: y 0, 8x8 */
    st_write16(&c, 0x07000002, 0x0010);                   /* x 16 */
    st_write16(&c, 0x07000004, 0x0002);                   /* tile 2 */
    for (int i = 1; i < 128; i++) st_write16(&c, 0x07000000 + (uint32_t)i * 8, 0x0200);   /* resto ocultos */
    st_write32(&c, 0x04000000, 0x00011110u);              /* gráficos, BG0 + OBJ, OBJ 1D */
    static uint32_t img[256 * 192];
    st_gpu2d_render(0, img);
    CHECK(img[0] == 0xFFFF0000u);                         /* rojo */
    CHECK(img[8] == 0xFF0000FFu);                         /* fondo azul */
    CHECK(img[16] == 0xFF00FF00u);                        /* sprite verde */
    st_write16(&c, 0x0400006C, 0x8010);                   /* brillo maestro: negro */
    st_gpu2d_render(0, img);
    CHECK(img[0] == 0xFF000000u);

    printf(fails ? "hw: %d fallos\n" : "hw: OK\n", fails);
    return fails != 0;
}
