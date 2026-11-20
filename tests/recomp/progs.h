/* Programas de prueba para el recompilador. Se compilan dos veces:
 * para ARM946E-S (y se recompilan) y de forma nativa (referencia). */
#pragma once
#include <stdint.h>

typedef uint32_t u32;
typedef int32_t s32;

/* ARM */
u32 t_fib(u32 n);
u32 t_div(s32 a, s32 b);
u32 t_switch(u32 x);
u32 t_sort(u32 seed);
u32 t_fnptr(u32 x);
u32 t_bytes(u32 x);
u32 t_bits(u32 x, u32 y);
uint64_t t_u64(u32 a, u32 b);
u32 t_struct(u32 x);
u32 t_float(u32 x);

/* Thumb */
u32 th_collatz(u32 n);
u32 th_mod(u32 a, u32 b);
u32 th_call_arm(u32 n);
u32 th_switch(u32 x);
u32 th_memstuff(u32 x);
s32 th_signed(s32 a, s32 b);
