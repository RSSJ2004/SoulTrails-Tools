#include "progs.h"

u32 th_collatz(u32 n) {
    u32 steps = 0;
    while (n > 1 && steps < 1000) {
        n = (n & 1) ? 3 * n + 1 : n / 2;
        steps++;
    }
    return steps;
}

u32 th_mod(u32 a, u32 b) { return b ? (a % b) * 1000 + a / b : 0; }

u32 th_call_arm(u32 n) { return t_fib(n % 12) + t_switch(n % 12); }

u32 th_switch(u32 x) {
    switch (x & 15) {
    case 0: return 5;
    case 1: return x + 100;
    case 2: return 77;
    case 3: return x * 3;
    case 4: return 1;
    case 5: return 900;
    case 6: return x - 1;
    case 7: return 4242;
    case 8: return 8;
    case 9: return x | 0x100;
    case 10: return 31337;
    default: return 0;
    }
}

static uint8_t buf8[96];
static uint16_t buf16[48];
u32 th_memstuff(u32 x) {
    for (int i = 0; i < 96; i++) buf8[i] = (uint8_t)(x + i * 13);
    for (int i = 0; i < 48; i++) buf16[i] = (uint16_t)(buf8[i * 2] | (buf8[i * 2 + 1] << 8)) ^ (uint16_t)x;
    u32 h = 0;
    for (int i = 47; i >= 0; i--) h = (h << 5) - h + (int16_t)buf16[i];
    return h;
}

s32 th_signed(s32 a, s32 b) {
    s32 r = 0;
    if (a < b) r -= a; else r += b;
    if (a > -5 && b <= 10) r ^= 0x55;
    return r * (a >= 0 ? 3 : -3) + (b / (a | 1));
}
