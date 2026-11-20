/* Dos overlays en la misma dirección con la primera función idéntica (misma biblioteca) y el
 * resto distinto: st_call debe elegir el que de verdad está en memoria. */
#include <stdio.h>
#include <string.h>

#include "strecomp.h"

static int which;
static void a_f0(StCpu* c) { (void)c; which = 1; }
static void b_f0(StCpu* c) { (void)c; which = 2; }
static void dummy(StCpu* c) { (void)c; }

#define BASE 0x020B0000u
static StFnEntry fa[17], fb[17];
const StModule st_modules[2] = {
    {"ov001", 1, BASE, BASE + 0x4000, fa, 17},
    {"ov002", 2, BASE, BASE + 0x4000, fb, 17},
};
const unsigned st_module_count = 2;

int main(void) {
    StCpu c;
    st_init(&c);
    /* Memoria = ov002: función 0 común; funciones 1..16 con bytes propios de ov002 */
    for (uint32_t i = 0; i < 0x4000; i++) st_write8(&c, BASE + i, (uint8_t)(i * 13));
    for (int k = 0; k < 17; k++) {
        uint32_t a = BASE + (uint32_t)k * 0x100;
        fa[k] = (StFnEntry){a, 0, k ? dummy : a_f0, 0};
        fb[k] = (StFnEntry){a, 0, k ? dummy : b_f0, 0};
        fb[k].sig = st_signature(a, BASE + 0x4000);
        fa[k].sig = k ? fb[k].sig ^ 0x55u : fb[k].sig;   /* ov001 solo coincide en la función 0 */
    }
    st_call(&c, BASE);
    if (which != 2) { printf("lookup: eligió ov00%d en vez de ov002\n", which); return 1; }
    printf("lookup: OK\n");
    return 0;
}
