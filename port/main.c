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
#ifdef ST_MELONDS
#include "melon/st_melon.h"
#endif

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
#ifdef ST_MELONDS
    memcpy(top, st_melon_screen(0), sizeof top);
    memcpy(bottom, st_melon_screen(1), sizeof bottom);
#else
    st_gpu2d_frame(top, bottom);
#endif
    snprintf(path, sizeof path, "%s/frame_%05lu.bmp", dump_dir, frames);
    write_bmp(path, top, bottom);
}

/* Funciones más ejecutadas: en cada cuadro se suman las entradas del anillo de traza; al
 * terminar se listan (dice en qué bucle está el juego aunque no se haya atascado) */
#define HOT_N 4096
static uint32_t hot_addr[HOT_N];
static unsigned long hot_count[HOT_N];
static uint32_t hot_last_pos;

static void hot_sample(void) {
    uint32_t pos = st_trace_pos, n = pos - hot_last_pos;
    if (n > ST_TRACE_LEN) n = ST_TRACE_LEN;
    for (uint32_t i = pos - n; i != pos; i++) {
        uint32_t a = st_trace_ring[i & (ST_TRACE_LEN - 1)];
        uint32_t h = (a * 2654435761u) >> 20;
        for (uint32_t k = 0; k < HOT_N; k++) {
            uint32_t j = (h + k) & (HOT_N - 1);
            if (hot_addr[j] == a || !hot_addr[j]) { hot_addr[j] = a; hot_count[j]++; break; }
        }
    }
    hot_last_pos = pos;
}

/* --dump-mem DIR:LEN (repetible): volcado de memoria del juego al terminar */
static uint32_t dump_addr[16], dump_len[16];
static int ndumps;

static void mem_report(void) {
    for (int d = 0; d < ndumps; d++) {
        fprintf(stderr, "[port] memoria %08X (%u bytes):\n", dump_addr[d], dump_len[d]);
        for (uint32_t o = 0; o < dump_len[d]; o += 16) {
            fprintf(stderr, "  %08X:", dump_addr[d] + o);
            for (uint32_t k = 0; k < 16 && o + k < dump_len[d]; k += 4) {
                uint8_t* p = st_ptr(dump_addr[d] + o + k, 4);
                uint32_t v = 0;
                if (p) memcpy(&v, p, 4);
                fprintf(stderr, " %08X", v);
            }
            fprintf(stderr, "\n");
        }
    }
}

static void hot_report(void) {
    fprintf(stderr, "[port] funciones más ejecutadas (muestras por cuadro):\n");
    for (int r = 0; r < 25; r++) {
        int best = -1;
        for (int j = 0; j < HOT_N; j++)
            if (hot_addr[j] && hot_count[j] && (best < 0 || hot_count[j] > hot_count[best])) best = j;
        if (best < 0) break;
        fprintf(stderr, "  %8lu  %08X %s\n", hot_count[best], hot_addr[best], st_symbol_for(hot_addr[best]));
        hot_count[best] = 0;
    }
    fprintf(stderr, "[port] cambios de hilo: %lu, IRQ atendidas: %u\n", st_thread_switches, st_irq.served);
    mem_report();
}

/* Cada espera del juego cuenta como un cuadro con su VBlank (sin ventana todavía) */
static int on_idle(StCpu* c) {
    frames++;
    hot_sample();
    if (dump_every && frames % dump_every == 0) dump_frame();
    if (max_frames && frames >= max_frames) {
        if (dump_every) dump_frame();
        hot_report();
        printf("[port] %lu cuadros sin fallos\n", frames);
        exit(0);
    }
    st_hw_frame_c(c);
    st_raise_irq(ST_IRQ_VBLANK);
    return 1;
}

static void dump_trace(void) {
    fprintf(stderr, "[port] últimas funciones (la más reciente al final):\n");
    uint32_t pos = st_trace_pos, n = pos < ST_TRACE_LEN ? pos : ST_TRACE_LEN;
    for (uint32_t i = pos - n; i != pos; i++) {
        uint32_t a = st_trace_ring[i & (ST_TRACE_LEN - 1)];
        fprintf(stderr, "  %08X %s\n", a, st_symbol_for(a));
    }
    st_print_loaded_overlays(stderr);
}

static void on_miss(StCpu* c, uint32_t addr) {
    dump_trace();
    fprintf(stderr, "[port] función no traducida en %08X (LR=%08X)\n", addr, c->r[14]);
    exit(3);
}
static void on_unimpl(StCpu* c, uint32_t addr, uint32_t insn, int thumb) {
    dump_trace();
    fprintf(stderr, "[port] registros:");
    for (int i = 0; i < 15; i++) fprintf(stderr, " r%d=%08X", i, c->r[i]);
    fprintf(stderr, " cpsr=%08X\n[port] LR %08X está en %s\n", st_get_cpsr(c), c->r[14], st_symbol_near(c->r[14] - 2));
    fprintf(stderr, "[port] instrucción no soportada %s %08X: %08X\n", thumb ? "Thumb" : "ARM", addr, insn);
    exit(4);
}
static void on_halt(StCpu* c, const char* why) {
    dump_trace();
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

#ifdef ST_MELONDS
/* Bucle del port híbrido: melonDS avanza la consola cuadro a cuadro y cede el turno del ARM9 al
 * código recompilado (port/melon/st_melon.cpp) */
static int run_melon(StRom* rom, const char* rom_path, const char* save_path) {
    char err[160];
    if (st_melon_open(&cpu, rom, rom_path, save_path, st_boot_language, err, sizeof err)) {
        fprintf(stderr, "[port] melonDS: %s\n", err);
        return 2;
    }
    printf("[port] melonDS: ARM7, sonido, cartucho, guardado (%s) y gráficos\n", save_path ? save_path : "sin .sav");
    for (;;) {
        st_melon_run_frame();
        frames++;
        hot_sample();
        if (dump_every && frames % dump_every == 0) dump_frame();
        if (max_frames && frames >= max_frames) {
            hot_report();
            printf("[port] %lu cuadros sin fallos\n", frames);
            st_melon_close();
            return 0;
        }
    }
}
#endif

int main(int argc, char** argv) {
    const char* rom_path = NULL;
    const char* misses = NULL;
    const char* save_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--rom") && i + 1 < argc) rom_path = argv[++i];
        else if (!strcmp(argv[i], "--misses") && i + 1 < argc) misses = argv[++i];
        else if (!strcmp(argv[i], "--max-frames") && i + 1 < argc) max_frames = strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--dump-frames") && i + 1 < argc) dump_every = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--frames-dir") && i + 1 < argc) dump_dir = argv[++i];
        else if (!strcmp(argv[i], "--dump-mem") && i + 1 < argc && ndumps < 16) {
            char* end;
            dump_addr[ndumps] = (uint32_t)strtoul(argv[++i], &end, 16);
            dump_len[ndumps] = *end == ':' ? (uint32_t)strtoul(end + 1, NULL, 16) : 0x40;
            ndumps++;
        }
        else if (!strcmp(argv[i], "--lang") && i + 1 < argc) {
            static const char* langs[] = {"ja", "en", "fr", "de", "it", "es"};
            const char* l = argv[++i];
            for (int k = 0; k < 6; k++) if (!strcmp(l, langs[k])) st_boot_language = k;
        }
        else if (!strcmp(argv[i], "--watchdog") && i + 1 < argc) watchdog_secs = (unsigned)strtoul(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--save") && i + 1 < argc) save_path = argv[++i];
        else { fprintf(stderr, "uso: %s --rom juego.nds [--misses archivo] [--max-frames N] [--watchdog S] [--lang es|en|fr|de|it|ja] [--dump-frames N] [--frames-dir D] [--save partida.sav]\n", argv[0]); return 2; }
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
#ifdef ST_MELONDS
    {
        int rc = run_melon(&rom, rom_path, save_path);
        st_rom_close(&rom);
        return rc;
    }
#else
    (void)save_path;
#endif
    st_boot_run(&cpu, &rom);
    printf("[port] el crt0 volvió a %08X\n", cpu.next_pc);
    if (cpu.next_pc != ST_BOOT_RETURN) {   /* volvió a otro sitio: el flujo se perdió por el camino */
        fprintf(stderr, "[port] detenido: el arranque volvió a %08X en vez de al cargador\n", cpu.next_pc);
        st_rom_close(&rom);
        return 5;
    }
    st_rom_close(&rom);
    return 0;
}
