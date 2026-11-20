/* Programa para probar `stk recomp --learn` (no es código del juego). Tres funciones hoja
 * solo se alcanzan con direcciones calculadas en ejecución (base + desplazamiento de una
 * tabla), así que ningún análisis estático las encuentra: hay que aprenderlas una a una. */
typedef unsigned int u32;
typedef u32 (*fn_t)(u32);
#include "offsets.h"   /* generado por el test: desplazamientos de f1..f3 desde 0x02000000 */

__attribute__((noinline)) u32 f1(u32 x) { return x * 3 + 1; }
__attribute__((noinline)) u32 f2(u32 x) { return x ^ 0x5A5A; }
__attribute__((noinline)) u32 f3(u32 x) { return x - 77; }

volatile u32 base_addr = 0x02000000;
volatile u32 result;

__attribute__((section(".text.entry"))) u32 entry(void) {
    u32 x = 5;
    for (int i = 0; i < 3; i++) x = ((fn_t)(base_addr + offsets[i]))(x);
    result = x;
    return x;
}
