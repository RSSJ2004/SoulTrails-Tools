/* Compara cada función recompilada con su versión nativa. */
#include <stdio.h>
#include <stdlib.h>

#include "progs.h"
#include "strecomp.h"
#include "test_addrs.h"   /* generado: direcciones de los símbolos ARM */

#define RET_MAGIC 0x0FFFFFF0u

static StCpu cpu;
static int failures;

static uint64_t guest(uint32_t addr, u32 a, u32 b) {
    cpu.r[0] = a;
    cpu.r[1] = b;
    cpu.r[13] = 0x02F00000u;
    cpu.r[14] = RET_MAGIC;
    st_call(&cpu, addr);
    if (cpu.next_pc != RET_MAGIC) {
        printf("  retorno inesperado a %08X\n", cpu.next_pc);
        failures++;
    }
    return ((uint64_t)cpu.r[1] << 32) | cpu.r[0];
}

#define CHECK32(name, expr_native, a, b)                                                     \
    do {                                                                                     \
        u32 n = (u32)(expr_native);                                                          \
        u32 g = (u32)guest(ADDR_##name, (u32)(a), (u32)(b));                                 \
        if (n != g) { printf("FALLO %-12s(%d,%d): nativo=%08X recomp=%08X\n", #name, (int)(a), (int)(b), n, g); failures++; } \
    } while (0)

static void miss(StCpu* c, uint32_t a) {
    printf("MISS %08X (lr %08X)\n", a, c->r[14]);
    exit(3);  /* el test vuelve a recompilar con esta dirección como semilla */
}

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    FILE* f = fopen(argv[1], "rb");
    if (!f) return 2;
    static uint8_t img[1 << 20];
    size_t n = fread(img, 1, sizeof img, f);
    fclose(f);
    st_init(&cpu);
    st_load(0x02000000u, img, n);
    st_platform.miss = miss;
    if (argc > 2) st_set_miss_log(argv[2]);

    for (u32 i = 0; i < 20; i++) CHECK32(t_fib, t_fib(i), i, 0);
    s32 divs[][2] = {{100, 7}, {-100, 7}, {100, -7}, {-2147483647, 3}, {5, 0}, {123456789, 1000}};
    for (unsigned i = 0; i < sizeof divs / sizeof divs[0]; i++) CHECK32(t_div, t_div(divs[i][0], divs[i][1]), divs[i][0], divs[i][1]);
    for (u32 i = 0; i < 14; i++) CHECK32(t_switch, t_switch(i), i, 0);
    for (u32 i = 0; i < 4; i++) CHECK32(t_sort, t_sort(i * 977), i * 977, 0);
    for (u32 i = 0; i < 5; i++) CHECK32(t_fnptr, t_fnptr(i * 5 + 3), i * 5 + 3, 0);
    for (u32 i = 0; i < 4; i++) CHECK32(t_bytes, t_bytes(i), i, 0);
    for (u32 i = 0; i < 40; i++) CHECK32(t_bits, t_bits(0x80000001u * i + 0x1234567u, i), 0x80000001u * i + 0x1234567u, i);
    for (u32 i = 0; i < 3; i++) CHECK32(t_struct, t_struct(i + 40), i + 40, 0);
    for (u32 i = 0; i < 30; i += 3) CHECK32(t_float, t_float(i * 1001), i * 1001, 0);
    for (u32 i = 1; i < 30; i++) CHECK32(th_collatz, th_collatz(i * 7), i * 7, 0);
    for (u32 i = 0; i < 10; i++) CHECK32(th_mod, th_mod(i * 12345, i + 3), i * 12345, i + 3);
    for (u32 i = 0; i < 24; i++) CHECK32(th_call_arm, th_call_arm(i), i, 0);
    for (u32 i = 0; i < 20; i++) CHECK32(th_switch, th_switch(i), i, 0);
    for (u32 i = 0; i < 3; i++) CHECK32(th_memstuff, th_memstuff(i * 77), i * 77, 0);
    s32 sg[][2] = {{3, 4}, {-3, 4}, {10, -20}, {-7, -7}, {100, 11}, {0, 0}};
    for (unsigned i = 0; i < sizeof sg / sizeof sg[0]; i++) CHECK32(th_signed, th_signed(sg[i][0], sg[i][1]), sg[i][0], sg[i][1]);
    for (u32 i = 0; i < 6; i++) {
        uint64_t nat = t_u64(0xFFFFFFFFu - i * 99991u, 0x12345u * (i + 1));
        uint64_t g = guest(ADDR_t_u64, 0xFFFFFFFFu - i * 99991u, 0x12345u * (i + 1));
        if (nat != g) { printf("FALLO t_u64 %u\n", i); failures++; }
    }
    if (failures) { printf("%d fallos\n", failures); return 1; }
    printf("recomp: todas las comprobaciones OK\n");
    return 0;
}
