/* soultrails_port: arranca el código recompilado con la ROM del usuario.
 *
 *   soultrails_port --rom juego.nds [--misses .stk/recomp_misses.txt] [--max-frames N] [--watchdog S]
 *
 * Todavía no hay capa de plataforma (GPU, sonido, cartucho...): el juego avanzará hasta la
 * primera cosa que necesite de ella. Códigos de salida (los usa `stk recomp --learn`):
 *   0 el crt0 volvió o se llegó a --max-frames   2 uso/ROM
 *   3 función no traducida (apuntada en --misses)
 *   4 instrucción no soportada     5 detenido (bkpt u otro)
 *   6 vigilante: S segundos sin terminar (se vuelca la traza de las últimas funciones)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "st_rom.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <pthread.h>
#include <unistd.h>
#endif

static StCpu cpu;
static unsigned long frames, max_frames;

/* Capturas: cada N cuadros, las dos pantallas (2D por software) en un BMP de 256x384 */
static unsigned dump_every;
static const char* dump_dir = ".";

static void write_bmp(const char* path, const uint32_t* top, const uint32_t* bottom) {
    FILE* f = fopen(path, "wb");
    if (!f) return;
    const int w = 256, h = 384, row = w * 3, size = 54 + row * h;
    uint8_t hd[54] = {'B', 'M'};
    hd[2] = (uint8_t)size; hd[3] = (uint8_t)(size >> 8); hd[4] = (uint8_t)(size >> 16);
    hd[10] = 54; hd[14] = 40;
    hd[18] = (uint8_t)w; hd[19] = (uint8_t)(w >> 8);
    hd[22] = (uint8_t)h; hd[23] = (uint8_t)(h >> 8);
    hd[26] = 1; hd[28] = 24;
    fwrite(hd, 1, sizeof hd, f);
    uint8_t line[256 * 3];
    for (int y = h - 1; y >= 0; y--) {   /* BMP: de abajo arriba */
        const uint32_t* src = y < 192 ? top + y * 256 : bottom + (y - 192) * 256;
        for (int x = 0; x < w; x++) {
            line[x * 3] = (uint8_t)src[x];
            line[x * 3 + 1] = (uint8_t)(src[x] >> 8);
            line[x * 3 + 2] = (uint8_t)(src[x] >> 16);
        }
        fwrite(line, 1, sizeof line, f);
    }
    fclose(f);
}

static void dump_frame(void) {
    static uint32_t top[256 * 192], bottom[256 * 192];
    char path[512];
    st_gpu2d_frame(top, bottom);
    snprintf(path, sizeof path, "%s/frame_%05lu.bmp", dump_dir, frames);
    write_bmp(path, top, bottom);
}

/* Cada espera del juego cuenta como un cuadro con su VBlank (sin ventana todavía) */
static int on_idle(StCpu* c) {
    frames++;
    if (dump_every && frames % dump_every == 0) dump_frame();
    if (max_frames && frames >= max_frames) {
        if (dump_every) dump_frame();
        printf("[port] %lu cuadros sin fallos\n", frames);
        exit(0);
    }
    st_hw_frame_c(c);
    st_raise_irq(ST_IRQ_VBLANK);
    return 1;
}

static void on_miss(StCpu* c, uint32_t addr) {
    fprintf(stderr, "[port] función no traducida en %08X (LR=%08X)\n", addr, c->r[14]);
    exit(3);
}
static void on_unimpl(StCpu* c, uint32_t addr, uint32_t insn, int thumb) {
    (void)c;
    fprintf(stderr, "[port] instrucción no soportada %s %08X: %08X\n", thumb ? "Thumb" : "ARM", addr, insn);
    exit(4);
}
static void on_halt(StCpu* c, const char* why) {
    fprintf(stderr, "[port] detenido: %s (PC~%08X LR=%08X)\n", why, c->next_pc, c->r[14]);
    exit(5);
}

/* Vigilante: si el juego gira sin terminar (p. ej. esperando en RAM algo que escribe el ARM7),
 * vuelca dónde estaba antes de que stk agote el tiempo. Lee el estado sin sincronizar: es
 * solo para diagnóstico. */
static unsigned watchdog_secs;

static void sleep_ms(unsigned ms);

static void watchdog_dump(void) {
    /* Sonda: qué lee y escribe el bucle durante un momento */
    st_probe_n = 0;
    st_probe_on = 1;
    sleep_ms(300);
    st_probe_on = 0;
    fprintf(stderr, "[port] vigilante: %u s sin terminar, %lu cuadros\n", watchdog_secs, frames);
    fprintf(stderr, "[port] registros:");
    for (int i = 0; i < 15; i++) fprintf(stderr, " r%d=%08X", i, cpu.r[i]);
    fprintf(stderr, "\n[port] LR %08X está en %s (una función que gira sin llamar a nadie no sale en la lista: "
                    "suele ser la de LR o la que llamó a la última)", cpu.r[14], st_symbol_near(cpu.r[14] - 2));
    fprintf(stderr, "\n[port] últimas funciones (la más reciente al final):\n");
    uint32_t pos = st_trace_pos;
    uint32_t n = pos < ST_TRACE_LEN ? pos : ST_TRACE_LEN;
    uint32_t prev = 0, rep = 0;
    for (uint32_t i = pos - n; i != pos; i++) {
        uint32_t a = st_trace_ring[i & (ST_TRACE_LEN - 1)];
        if (a == prev) { rep++; continue; }
        if (rep) fprintf(stderr, "    (x%u)\n", rep + 1);
        rep = 0;
        prev = a;
        fprintf(stderr, "  %08X %s\n", a, st_symbol_for(a));
    }
    if (rep) fprintf(stderr, "    (x%u)\n", rep + 1);
    unsigned np = st_probe_n;
    fprintf(stderr, "[port] accesos del bucle en 300 ms (%u direcciones):\n", np);
    for (unsigned i = 0; i < np && i < 48; i++) {
        const StProbe* q = &st_probe[i];
        uint8_t* m = st_ptr(q->addr, (uint32_t)q->size);
        uint32_t v = 0;
        if (m) memcpy(&v, m, (size_t)q->size);
        fprintf(stderr, "  %08X %d bytes  lecturas %-9u escrituras %-9u valor %s%0*X  vistos:", q->addr, q->size,
                q->reads, q->writes, m ? "" : "(E/S) ", q->size * 2, v);
        for (unsigned k = 0; k < q->nvals; k++)
            fprintf(stderr, " %s%0*X", q->written[k] ? "w:" : "r:", q->size * 2, q->vals[k]);
        fprintf(stderr, "\n");
    }
    fflush(stderr);
    _Exit(6);
}

#ifdef _WIN32
static void sleep_ms(unsigned ms) { Sleep(ms); }
#else
static void sleep_ms(unsigned ms) { usleep(ms * 1000u); }
#endif

#ifdef _WIN32
static DWORD WINAPI watchdog_thread(LPVOID arg) {
    (void)arg;
    Sleep(watchdog_secs * 1000u);
    watchdog_dump();
    return 0;
}
static void start_watchdog(void) { CreateThread(NULL, 0, watchdog_thread, NULL, 0, NULL); }
#else
static void* watchdog_thread(void* arg) {
    (void)arg;
    sleep(watchdog_secs);
    watchdog_dump();
    return NULL;
}
static void start_watchdog(void) {
    pthread_t t;
    pthread_create(&t, NULL, watchdog_thread, NULL);
    pthread_detach(t);
}
#endif

int main(int argc, char** argv) {
    const char* rom_path = NULL;
    const char* misses = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--rom") && i + 1 < argc) rom_path = argv[++i];
        else if (!strcmp(argv[i], "--misses") && i + 1 < argc) misses = argv[++i];
        else if (!strcmp(argv[i], "--max-frames") && i + 1 < argc) max_frames = strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--dump-frames") && i + 1 < argc) dump_every = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--frames-dir") && i + 1 < argc) dump_dir = argv[++i];
        else if (!strcmp(argv[i], "--lang") && i + 1 < argc) {
            static const char* langs[] = {"ja", "en", "fr", "de", "it", "es"};
            const char* l = argv[++i];
            for (int k = 0; k < 6; k++) if (!strcmp(l, langs[k])) st_boot_language = k;
        }
        else if (!strcmp(argv[i], "--watchdog") && i + 1 < argc) watchdog_secs = (unsigned)strtoul(argv[++i], NULL, 10);
        else { fprintf(stderr, "uso: %s --rom juego.nds [--misses archivo] [--max-frames N] [--watchdog S] [--lang es|en|fr|de|it|ja] [--dump-frames N] [--frames-dir D]\n", argv[0]); return 2; }
    }
    if (!rom_path) { fprintf(stderr, "falta --rom\n"); return 2; }
    StRom rom;
    char err[128];
    if (st_rom_open(&rom, rom_path, err, sizeof err)) { fprintf(stderr, "ROM: %s\n", err); return 2; }
    printf("[port] %s (%s): ARM9 %u bytes en %08X, entrada %08X, %d overlays\n", rom.title, rom.gamecode,
           rom.arm9_size, rom.arm9_ram, rom.arm9_entry, st_rom_overlay_count(&rom));
    if (misses) st_set_miss_log(misses);
    st_platform.miss = on_miss;
    st_platform.unimpl = on_unimpl;
    st_platform.halt = on_halt;
    st_platform.idle = on_idle;
    if (watchdog_secs) start_watchdog();
    st_boot_run(&cpu, &rom);
    printf("[port] el crt0 volvió a %08X\n", cpu.next_pc);
    st_rom_close(&rom);
    return 0;
}
