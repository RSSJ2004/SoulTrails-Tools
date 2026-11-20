/* Ejecuta bios_main recompilado y vuelca la RAM usada a un archivo. */
#include <stdio.h>
#include <stdlib.h>

#include "strecomp.h"

static StCpu cpu;
static void miss(StCpu* c, uint32_t a) { printf("MISS %08X (lr %08X)\n", a, c->r[14]); exit(3); }
static void halt(StCpu* c, const char* why) { (void)c; printf("HALT %s\n", why); exit(5); }

int main(int argc, char** argv) {
    if (argc < 5) return 2;
    FILE* f = fopen(argv[1], "rb");
    static uint8_t img[1 << 20];
    size_t n = fread(img, 1, sizeof img, f);
    fclose(f);
    st_init(&cpu);
    st_load(0x02000000u, img, n);
    st_platform.miss = miss;
    st_platform.halt = halt;
    cpu.r[13] = 0x023F0000u;
    cpu.r[14] = 0x0FFFFFF0u;
    st_call(&cpu, (uint32_t)strtoul(argv[3], NULL, 16));
    if (cpu.next_pc != 0x0FFFFFF0u) { printf("retorno a %08X\n", cpu.next_pc); return 1; }
    f = fopen(argv[2], "wb");
    fwrite(st_main_ram, 1, (size_t)strtoul(argv[4], NULL, 16), f);
    fclose(f);
    printf("irq servidas: %u\n", st_irq.served);
    return 0;
}
