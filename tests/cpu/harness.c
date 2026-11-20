/* Ejecuta las pruebas de modos recompiladas e imprime 'results' y 'results2' en hex. */
#include <stdio.h>
#include <stdlib.h>

#include "strecomp.h"
#include "cpu_addrs.h"   /* generado: ADDR_* de los símbolos */

#define RET_MAGIC 0x0FFFFFF0u

static StCpu cpu;
static uint32_t cp_log[8];
static unsigned cp_n;

static uint32_t coproc(StCpu* c, int cp, int op1, int crn, int crm, int op2, uint32_t v, int read) {
    (void)c;
    if (read) return 0xC0DE0000u | (uint32_t)(cp << 12 | op1 << 8 | crn << 4 | crm) | (uint32_t)op2 << 16;
    if (cp_n < 8) cp_log[cp_n++] = (uint32_t)(cp << 24 | op1 << 20 | crn << 16 | crm << 12 | op2 << 8) | (v & 0xFF);
    return 0;
}
static uint32_t bkpt_num = 0xFFFFFFFFu;
static void bkpt(StCpu* c, uint32_t n) { (void)c; bkpt_num = n; }
static void miss(StCpu* c, uint32_t a) { printf("MISS %08X (lr %08X)\n", a, c->r[14]); exit(3); }

static void run(uint32_t addr) {
    cpu.r[13] = 0x02300000u;
    cpu.r[14] = RET_MAGIC;
    st_call(&cpu, addr);
    if (cpu.next_pc != RET_MAGIC) { printf("retorno inesperado a %08X\n", cpu.next_pc); exit(1); }
}

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    FILE* f = fopen(argv[1], "rb");
    if (!f) return 2;
    static uint8_t img[1 << 16];
    size_t n = fread(img, 1, sizeof img, f);
    fclose(f);
    st_init(&cpu);
    st_load(0x02000000u, img, n);
    st_platform.miss = miss;
    st_platform.coproc = coproc;
    st_platform.bkpt = bkpt;
    run(ADDR_test_modes);
    run(ADDR_test_jt);
    uint32_t mode = cpu.cpsr_ctrl & 0x1F, sp = cpu.r[13];
    for (uint32_t a = ADDR_results; a < ADDR_results_end; a += 4) printf("%08X\n", st_read32(&cpu, a));
    printf("--\n");
    run(ADDR_test_v5);
    for (uint32_t a = ADDR_results2; a < ADDR_results2 + 8 * 4; a += 4) printf("%08X\n", st_read32(&cpu, a));
    for (uint32_t a = ADDR_buf2; a < ADDR_buf2 + 16; a += 4) printf("%08X\n", st_read32(&cpu, a));
    printf("%08X\n%08X\n%08X\n", cp_log[0], bkpt_num, mode | (sp == 0x02300000u ? 0x100u : 0));
    return 0;
}
