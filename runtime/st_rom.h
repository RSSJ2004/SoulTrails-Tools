/* Lectura de la ROM del usuario y arranque del código recompilado.
 * Ver HANDOFF.md, "Decisiones de diseño": el ARM9 se carga tal cual está en el cartucho y
 * el crt0 del juego hace descompresión, autoloads y BSS. */
#ifndef ST_ROM_H
#define ST_ROM_H

#include "strecomp.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct StRom {
    uint8_t* data;
    size_t size;
    char title[13], gamecode[5];
    uint32_t arm9_off, arm9_entry, arm9_ram, arm9_size;
    uint32_t arm7_off, arm7_entry, arm7_ram, arm7_size;
    uint32_t fnt_off, fnt_size, fat_off, fat_size, ovt9_off, ovt9_size;
} StRom;

typedef struct StOverlayInfo {
    uint32_t id, ram, ram_size, bss_size, sinit_start, sinit_end, file_id, flags;
} StOverlayInfo;

/* 0 si va bien; si no, escribe la causa en err (si no es NULL). */
int st_rom_open(StRom* rom, const char* path, char* err, size_t errlen);
void st_rom_close(StRom* rom);
const uint8_t* st_rom_file(const StRom* rom, uint32_t file_id, uint32_t* size);  /* NULL si no existe */
int st_rom_overlay_count(const StRom* rom);
int st_rom_overlay(const StRom* rom, int index, StOverlayInfo* out);              /* 0 si existe */

#define ST_HEADER_RAM 0x027FFE00u
#define ST_BOOT_RETURN 0x0FFFFFF0u   /* LR de la entrada: si el crt0 "vuelve", termina */

/* Carga cabecera + ARM9, deja la CPU como el BIOS (SVC, IRQ/FIQ off, pilas) y devuelve la
 * dirección de entrada. st_boot_run() además la ejecuta. */
extern int st_boot_language;   /* idioma de la consola (0 ja, 1 en, 2 fr, 3 de, 4 it, 5 es) */
uint32_t st_boot_chip_id(const StRom* rom);
uint32_t st_boot_prepare(StCpu* c, const StRom* rom);
void st_boot_run(StCpu* c, const StRom* rom);

#ifdef __cplusplus
}
#endif
#endif
