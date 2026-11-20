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

uint32_t st_boot_arm7_tags = 0xFFFFFFFFu;   /* tags del PXI que el ARM7 HLE declara atendidos */
int st_boot_language = 5;   /* idioma de la consola: 0 ja, 1 en, 2 fr, 3 de, 4 it, 5 es */

static void put16(uint32_t a, uint16_t v) { st_load(a, &v, 2); }
static void put32(uint32_t a, uint32_t v) { st_load(a, &v, 4); }

uint32_t st_boot_chip_id(const StRom* rom) {
    /* 1er byte: fabricante; 2º: tamaño en MB - 1 (capacidad de la cabecera: 128 KB << n) */
    uint32_t cap = rom->size >= 0x15 ? rom->data[0x14] : 9;
    uint32_t mb = cap >= 3 && cap < 22 ? (128u << cap) >> 10 : 1;
    return 0xC2u | ((mb ? mb - 1 : 0) & 0xFFu) << 8;
}

/* Lo que dejan el BIOS y el firmware en la RAM principal al arrancar un cartucho (GBATEK,
 * "BIOS RAM Usage" y "Firmware User Settings"): IDs del chip, CRC, indicador de arranque y
 * los ajustes de usuario (idioma, calibración táctil...). */
static void boot_system_area(const StRom* rom) {
    uint32_t chip = st_boot_chip_id(rom);
    uint16_t hcrc = rom->size >= 0x160 ? (uint16_t)(rom->data[0x15E] | rom->data[0x15F] << 8) : 0;
    uint16_t scrc = rom->size >= 0x6E ? (uint16_t)(rom->data[0x6C] | rom->data[0x6D] << 8) : 0;
    for (uint32_t base = 0x027FF800u; base <= 0x027FFC00u; base += 0x400u) {
        put32(base + 0x00, chip);
        put32(base + 0x04, chip);
        put16(base + 0x08, hcrc);
        put16(base + 0x0A, scrc);
        put16(base + 0x0C, 0);        /* CRC correcto */
        put16(base + 0x0E, 0);        /* área segura correcta */
        put16(base + 0x10, 0x5835u);  /* CRC del BIOS del ARM7 (en 27FFC10h); en 27FF810h, tarea de arranque */
    }
    put16(0x027FF810u, 0xFFFFu);
    put16(0x027FF850u, 0x5835u);
    put16(0x027FFC40u, 1);            /* indicador de arranque: normal */
    /* El ARM7 real marca aquí qué tags del PXI atiende al registrar sus callbacks; el ARM9 espera
     * a verlos antes de usar cada servicio (visto con la ROM: bucle sobre 027FFF8C). */
    put32(0x027FFF8Cu, st_boot_arm7_tags);
    /* Ajustes de usuario (0x70 bytes en 27FFC80h) */
    uint32_t u = 0x027FFC80u;
    static const uint16_t nick[] = {'L', 'i', 'n', 'k'};
    put16(u + 0x00, 5);
    for (unsigned i = 0; i < 4; i++) put16(u + 0x06 + 2 * i, nick[i]);
    put16(u + 0x1A, 4);
    /* Calibración táctil típica: ADC <-> píxel en dos puntos */
    put16(u + 0x58, 0x02DF); put16(u + 0x5A, 0x032C);
    uint8_t p1[2] = {0x20, 0x20}, p2[2] = {0xE0, 0xA0};
    st_load(u + 0x5C, p1, 2);
    put16(u + 0x5E, 0x0D3B); put16(u + 0x60, 0x0CE7);
    st_load(u + 0x62, p2, 2);
    put16(u + 0x64, (uint16_t)((st_boot_language & 7) | (3u << 4)));   /* idioma, brillo máximo */
}

uint32_t st_boot_prepare(StCpu* c, const StRom* rom) {
    st_init(c);
    st_hw_set_cart(rom->data, rom->size, st_boot_chip_id(rom));
    boot_system_area(rom);
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
