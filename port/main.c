/* soultrails_port: arranca el código recompilado con la ROM del usuario.
 *
 *   soultrails_port --rom juego.nds [--misses .stk/recomp_misses.txt] [--max-frames N]
 *
 * Todavía no hay capa de plataforma (GPU, sonido, cartucho...): el juego avanzará hasta la
 * primera cosa que necesite de ella. Códigos de salida (los usa `stk recomp --learn`):
 *   0 el crt0 volvió o se llegó a --max-frames   2 uso/ROM
 *   3 función no traducida (apuntada en --misses)
 *   4 instrucción no soportada     5 detenido (bkpt u otro)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "st_rom.h"

static StCpu cpu;
static unsigned long frames, max_frames;

/* Sin GPU todavía: cada espera del juego cuenta como un cuadro con su VBlank */
static int on_idle(StCpu* c) {
    (void)c;
    if (max_frames && ++frames >= max_frames) {
        printf("[port] %lu cuadros sin fallos\n", frames);
        exit(0);
    }
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

int main(int argc, char** argv) {
    const char* rom_path = NULL;
    const char* misses = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--rom") && i + 1 < argc) rom_path = argv[++i];
        else if (!strcmp(argv[i], "--misses") && i + 1 < argc) misses = argv[++i];
        else if (!strcmp(argv[i], "--max-frames") && i + 1 < argc) max_frames = strtoul(argv[++i], NULL, 10);
        else { fprintf(stderr, "uso: %s --rom juego.nds [--misses archivo] [--max-frames N]\n", argv[0]); return 2; }
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
    st_boot_run(&cpu, &rom);
    printf("[port] el crt0 volvió a %08X\n", cpu.next_pc);
    st_rom_close(&rom);
    return 0;
}
