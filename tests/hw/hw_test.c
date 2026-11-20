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

    printf(fails ? "hw: %d fallos\n" : "hw: OK\n", fails);
    return fails != 0;
}
