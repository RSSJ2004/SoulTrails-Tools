#include "st_rom.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t rd32(const uint8_t* p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

static int fail(char* err, size_t n, const char* msg) {
    if (err && n) snprintf(err, n, "%s", msg);
    return -1;
}

int st_rom_open(StRom* rom, const char* path, char* err, size_t errlen) {
    memset(rom, 0, sizeof *rom);
    FILE* f = fopen(path, "rb");
    if (!f) return fail(err, errlen, "no se puede abrir la ROM");
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0x200) { fclose(f); return fail(err, errlen, "la ROM es demasiado pequeña"); }
    rom->data = (uint8_t*)malloc((size_t)sz);
    if (!rom->data || fread(rom->data, 1, (size_t)sz, f) != (size_t)sz) {
        fclose(f);
        st_rom_close(rom);
        return fail(err, errlen, "error leyendo la ROM");
    }
    fclose(f);
    rom->size = (size_t)sz;
    const uint8_t* h = rom->data;
    memcpy(rom->title, h, 12);
    memcpy(rom->gamecode, h + 12, 4);
    uint32_t* fields[] = {&rom->arm9_off, &rom->arm9_entry, &rom->arm9_ram, &rom->arm9_size,
                          &rom->arm7_off, &rom->arm7_entry, &rom->arm7_ram, &rom->arm7_size,
                          &rom->fnt_off, &rom->fnt_size, &rom->fat_off, &rom->fat_size,
                          &rom->ovt9_off, &rom->ovt9_size};
    for (unsigned i = 0; i < sizeof fields / sizeof fields[0]; i++) *fields[i] = rd32(h + 0x20 + 4 * i);
    if ((uint64_t)rom->arm9_off + rom->arm9_size > rom->size) { st_rom_close(rom); return fail(err, errlen, "ARM9 fuera de la ROM"); }
    if ((uint64_t)rom->fat_off + rom->fat_size > rom->size || (uint64_t)rom->ovt9_off + rom->ovt9_size > rom->size) {
        st_rom_close(rom);
        return fail(err, errlen, "FAT u overlays fuera de la ROM");
    }
    if (rom->arm9_ram < 0x02000000u || (uint64_t)rom->arm9_ram + rom->arm9_size > 0x02400000u) {
        st_rom_close(rom);
        return fail(err, errlen, "dirección de carga del ARM9 fuera de la RAM principal");
    }
    return 0;
}

void st_rom_close(StRom* rom) {
    free(rom->data);
    memset(rom, 0, sizeof *rom);
}

const uint8_t* st_rom_file(const StRom* rom, uint32_t id, uint32_t* size) {
    if ((uint64_t)id * 8 + 8 > rom->fat_size) return NULL;
    uint32_t s = rd32(rom->data + rom->fat_off + id * 8), e = rd32(rom->data + rom->fat_off + id * 8 + 4);
    if (e < s || e > rom->size) return NULL;
    if (size) *size = e - s;
    return rom->data + s;
}

int st_rom_overlay_count(const StRom* rom) { return (int)(rom->ovt9_size / 32); }

int st_rom_overlay(const StRom* rom, int index, StOverlayInfo* o) {
    if (index < 0 || index >= st_rom_overlay_count(rom)) return -1;
    const uint8_t* p = rom->data + rom->ovt9_off + 32u * (uint32_t)index;
    uint32_t* f = &o->id;
    for (int i = 0; i < 8; i++) f[i] = rd32(p + 4 * i);
    return 0;
}

uint32_t st_boot_prepare(StCpu* c, const StRom* rom) {
    st_init(c);
    /* El cargador del cartucho copia la cabecera y el ARM9 (sin descomprimir) */
    st_load(ST_HEADER_RAM, rom->data, 0x170);
    st_load(rom->arm9_ram, rom->data + rom->arm9_off, rom->arm9_size);
    /* Estado del BIOS del ARM9 al saltar al juego (GBATEK): SVC con IRQ/FIQ desactivadas y
     * pilas en la DTCM por defecto. El crt0 del juego las vuelve a poner. */
    st_set_mode(c, 0x12); c->r[13] = 0x00803FA0u;
    st_set_mode(c, 0x1F); c->r[13] = 0x00803EC0u;
    st_set_mode(c, 0x13); c->r[13] = 0x00803FC0u;
    c->cpsr_ctrl = 0xD3;
    c->r[14] = ST_BOOT_RETURN;
    return rom->arm9_entry;
}

void st_boot_run(StCpu* c, const StRom* rom) {
    uint32_t entry = st_boot_prepare(c, rom);
    st_call(c, entry);
}
