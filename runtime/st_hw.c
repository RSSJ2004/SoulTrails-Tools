/* Hardware mínimo del ARM9 visto desde el juego (capa de plataforma, fase 1):
 *
 * - Registros de E/S con memoria: lo que no está emulado devuelve lo último escrito (muchos
 *   bucles leen lo que acaban de escribir) y se avisa una vez por dirección.
 * - IPCSYNC (0x04000180): el ARM7 no se ejecuta; por defecto "hace eco" de los 4 bits que
 *   publica el ARM9, que es el punto fijo de las esperas de sincronización entre CPU.
 * - FIFO del PXI (0x04000184/0x04000188/0x04100000): lo que envía el ARM9 se registra y se
 *   pasa a st_platform.pxi_send (la HLE del ARM7, por hacer); las respuestas se encolan con
 *   st_pxi_reply().
 * - VCOUNT/DISPSTAT simulados; cartucho, DMA y timers en st_dev.c.
 * - Detector de esperas: si el juego lee la misma dirección de E/S muchísimas veces seguidas
 *   sin que pase un cuadro, se detiene diciendo qué registro espera (en vez de agotar el tiempo).
 *
 * Todo está escrito a partir de la descripción del hardware (GBATEK), sin código de Nintendo. */
#include <stdio.h>
#include <string.h>

#include "strecomp.h"

#define IO_SIZE 0x2000u
static uint8_t io_regs[IO_SIZE];          /* 0x04000000-0x04001FFF */
static uint8_t io_warned[IO_SIZE / 2];    /* aviso único por dirección (media palabra) */

static uint32_t ipc_sync_arm7;            /* bits 0-3 que ve el ARM9 */
#define FIFO_LEN 16
static uint32_t fifo_rx[FIFO_LEN];
static unsigned fifo_rx_n, fifo_rx_head;
static unsigned fifo_cnt = 0x0101u;       /* envío vacío | recepción vacía */
static unsigned long pxi_logged;

static uint32_t poll_addr, poll_count;
static int vblank_done;                   /* DMA/IRQ del VBlank ya hechos en este cuadro */
static uint32_t vcount;                   /* línea simulada: avanza con cada lectura de VCOUNT/DISPSTAT */
#define LINES 263u
#define VBLANK_LINE 192u
unsigned long st_hw_poll_limit = 2000000ul;

int st_hw_ipcsync_echo = 1;

static uint32_t io_get(uint32_t addr, int size) {
    uint32_t o = addr & (IO_SIZE - 1), v = 0;
    memcpy(&v, &io_regs[o], (size_t)size);
    return v;
}
static void io_put(uint32_t addr, uint32_t v, int size) {
    memcpy(&io_regs[addr & (IO_SIZE - 1)], &v, (size_t)size);
}

static const char* io_name(uint32_t a) {
    switch (a & ~1u) {
    case 0x04000004: return "DISPSTAT";
    case 0x04000006: return "VCOUNT";
    case 0x04000130: return "KEYINPUT";
    case 0x04000180: return "IPCSYNC (espera al ARM7)";
    case 0x04000184: return "IPCFIFOCNT (FIFO con el ARM7)";
    case 0x040001A0: case 0x040001A2: case 0x040001A4: case 0x040001A6: return "cartucho (AUXSPI/ROMCTRL)";
    case 0x04100000: case 0x04100002: return "IPCFIFORECV (respuesta del ARM7)";
    case 0x04100010: case 0x04100012: return "cartucho (datos)";
    case 0x04000600: case 0x04000602: return "GXSTAT (3D)";
    default: return "";
    }
}

void st_hw_reset(void) {
    memset(io_regs, 0, sizeof io_regs);
    memset(io_warned, 0, sizeof io_warned);
    ipc_sync_arm7 = 0;
    fifo_rx_n = fifo_rx_head = 0;
    fifo_cnt = 0x0101u;
    poll_addr = poll_count = 0;
    pxi_logged = 0;
    vcount = 0;
    vblank_done = 0;
    st_dev_reset();
}

/* Un cuadro completo (la plataforma esperó al VBlank): el tiempo salta al principio del siguiente */
void st_hw_frame_c(StCpu* c) {
    if (!vblank_done && c) st_dev_vblank(c);
    if (vcount < LINES) st_clock_advance((LINES - vcount) * 2130u);
    poll_count = 0;
    vcount = 0;
    vblank_done = 0;
}
static StCpu* last_cpu;
void st_hw_frame(void) { st_hw_frame_c(last_cpu); }

/* Sin temporización real, cada lectura de VCOUNT/DISPSTAT avanza una línea. Al entrar en el
 * VBlank se levanta su IRQ (si DISPSTAT la pide) y al dar la vuelta cuenta como un cuadro
 * (st_platform.idle), así los bucles "espera a la línea N" o "espera al VBlank" terminan. */
static void advance_line(StCpu* c) {
    vcount++;
    st_clock_advance(2130u);
    uint16_t stat;
    memcpy(&stat, &io_regs[4], 2);
    if (vcount == VBLANK_LINE) {
        if (stat & 0x8u) st_raise_irq(ST_IRQ_VBLANK);
        st_dev_vblank(c);
        vblank_done = 1;
    }
    if (vcount == ((stat >> 8) | ((stat & 0x80u) << 1)) && (stat & 0x20u)) st_raise_irq(ST_IRQ_VCOUNT);
    if (vcount >= LINES) {
        vcount = 0;
        if (!st_platform.idle(c)) st_platform.halt(c, "espera abortada por la plataforma");
    }
}

void st_pxi_reply(uint32_t v) {
    if (fifo_rx_n == FIFO_LEN) { fifo_cnt |= 0x4000u; return; }   /* error: llena */
    fifo_rx[(fifo_rx_head + fifo_rx_n++) % FIFO_LEN] = v;
    fifo_cnt &= ~0x0100u;
    if (fifo_rx_n == FIFO_LEN) fifo_cnt |= 0x0200u;
    if (fifo_cnt & 0x0400u) st_raise_irq(ST_IRQ_FIFO_RECV);
}

/* Lecturas de E/S seguidas sin ninguna escritura ni cuadro: el juego espera algo que no llega
 * (vale también para bucles que alternan varios registros). */
static void watch_poll(StCpu* c, uint32_t addr) {
    poll_addr = addr;
    if (++poll_count < st_hw_poll_limit) return;
    char why[160];
    const char* n = io_name(addr);
    snprintf(why, sizeof why, "bucle de espera sobre E/S (última lectura %08X%s%s; falta emularlo en la plataforma)", addr,
             *n ? " " : "", n);
    poll_count = 0;
    st_platform.halt(c, why);
}

/* Devuelve 1 si la dirección la gestiona este módulo. */
int st_hw_read(StCpu* c, uint32_t addr, int size, uint32_t* out) {
    uint32_t a = addr & ~3u;
    last_cpu = c;
    if (a >= 0x04000100u && a < 0x04000110u) return st_dev_read(c, addr, size, out);   /* timers: avanzan el tiempo */
    if (a == 0x04000004u) {   /* DISPSTAT + VCOUNT: avanzan el tiempo, no cuentan como espera */
        advance_line(c);
        uint32_t stat = io_get(0x04000004u, 2) & 0xFFB8u;
        uint32_t want = (stat >> 8) | ((stat & 0x80u) << 1);
        stat |= (vcount >= VBLANK_LINE && vcount < LINES - 1) ? 1u : 0u;
        stat |= vcount == want ? 4u : 0u;
        uint32_t v = stat | (vcount << 16);
        *out = (v >> ((addr & 3) * 8)) & (size == 4 ? 0xFFFFFFFFu : size == 2 ? 0xFFFFu : 0xFFu);
        return 1;
    }
    watch_poll(c, addr);
    if (a == 0x04000180u) {
        uint32_t v = (io_get(0x04000180u, 4) & 0x4F00u) | (ipc_sync_arm7 & 0xFu);
        *out = v >> ((addr & 3) * 8);
        return 1;
    }
    if (a == 0x04000184u) {
        *out = (fifo_cnt | (io_get(0x04000184u, 4) & 0x8404u)) >> ((addr & 3) * 8);
        return 1;
    }
    if (st_dev_read(c, addr, size, out)) return 1;
    if (a == 0x04100000u) {
        uint32_t v = 0;
        if (fifo_rx_n) {
            v = fifo_rx[fifo_rx_head];
            fifo_rx_head = (fifo_rx_head + 1) % FIFO_LEN;
            if (--fifo_rx_n == 0) fifo_cnt |= 0x0100u;
            fifo_cnt &= ~0x0200u;
        } else if (io_get(0x04000184u, 4) & 0x8000u) {
            fifo_cnt |= 0x4000u;   /* leer vacía con la FIFO activada = error */
        }
        *out = v;
        return 1;
    }
    if (addr >= 0x04000000u && addr < 0x04000000u + IO_SIZE) {
        uint32_t h = (addr & (IO_SIZE - 1)) / 2;
        if (!io_warned[h]) {
            io_warned[h] = 1;
            const char* n = io_name(addr);
            fprintf(stderr, "[st] E/S sin emular: read%d %08X%s%s (se devuelve lo último escrito)\n", size * 8,
                    addr, *n ? " " : "", n);
        }
        *out = io_get(addr, size);
        return 1;
    }
    (void)size;
    return 0;
}

int st_hw_write(StCpu* c, uint32_t addr, uint32_t v, int size) {
    poll_count = 0;
    last_cpu = c;
    if (st_dev_write(c, addr, v, size)) return 1;
    uint32_t a = addr & ~3u;
    if (a == 0x04000180u) {
        uint32_t sh = (addr & 3) * 8, full = io_get(0x04000180u, 4);
        uint32_t mask = (size == 4 ? 0xFFFFFFFFu : size == 2 ? 0xFFFFu : 0xFFu) << sh;
        full = (full & ~mask) | ((v << sh) & mask);
        io_put(0x04000180u, full & 0x4F00u, 4);
        if (st_hw_ipcsync_echo) ipc_sync_arm7 = (full >> 8) & 0xFu;
        if (st_platform.ipcsync) st_platform.ipcsync(c, (full >> 8) & 0xFu, &ipc_sync_arm7);
        return 1;
    }
    if (a == 0x04000184u) {
        uint32_t w = v << ((addr & 3) * 8);
        if (w & 0x0008u) fifo_cnt = (fifo_cnt & ~0x0003u) | 0x0001u;   /* vaciar envío */
        if (w & 0x4000u) fifo_cnt &= ~0x4000u;                         /* reconocer error */
        io_put(0x04000184u, w & 0x8404u, 4);
        fifo_cnt = (fifo_cnt & ~0x8404u) | (w & 0x8404u);
        return 1;
    }
    if (a == 0x04000188u) {
        if (pxi_logged++ < 64)
            fprintf(stderr, "[st] PXI ARM9->ARM7 %08X (tag %u, datos %07X)\n", v, v & 0x1Fu, v >> 6);
        if (st_platform.pxi_send) st_platform.pxi_send(c, v);
        if (fifo_cnt & 0x0004u) st_raise_irq(ST_IRQ_FIFO_SEND);
        return 1;
    }
    if (addr >= 0x04000000u && addr < 0x04000000u + IO_SIZE) {
        io_put(addr, v, size);
        return 1;
    }
    (void)c;
    return 0;
}
