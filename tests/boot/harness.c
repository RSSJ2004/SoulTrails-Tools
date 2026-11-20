/* Plataforma de test para el arranque: SWI 0x7F carga el overlay r0 desde la ROM en su
 * dirección (tal cual: comprimido si lo está), SWI 0x7E imprime 'results' y termina. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "st_rom.h"
#include "boot_addrs.h"

static StCpu cpu;
static StRom rom;

static void swi(StCpu* c, uint32_t num) {
    if (num == 0x7F) {
        StOverlayInfo o;
        uint32_t size;
        if (st_rom_overlay(&rom, (int)c->r[0], &o)) { printf("overlay %u inexistente\n", c->r[0]); exit(1); }
        const uint8_t* d = st_rom_file(&rom, o.file_id, &size);
        st_load(o.ram, d, size);
        c->r[0] = o.flags;
        return;
    }
    if (num == 0x7E) {
        for (uint32_t i = 0; i < 7; i++) printf("%08X\n", st_read32(c, ADDR_results + 4 * i));
        exit(0);
    }
    printf("SWI %02X\n", num);
    exit(1);
}
static void miss(StCpu* c, uint32_t a) { printf("MISS %08X (lr %08X)\n", a, c->r[14]); exit(3); }

int main(int argc, char** argv) {
    char err[128];
    if (argc < 2 || st_rom_open(&rom, argv[1], err, sizeof err)) { printf("ROM: %s\n", argc < 2 ? "?" : err); return 2; }
    memset(st_main_ram, 0xA5, ST_MAIN_RAM_SIZE);   /* RAM sucia: el BSS lo tiene que limpiar el crt0 */
    st_platform.swi = swi;
    st_platform.miss = miss;
    if (argc > 2) st_set_miss_log(argv[2]);
    st_boot_run(&cpu, &rom);
    printf("el crt0 volvió\n");
    return 1;
}
