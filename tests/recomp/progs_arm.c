#include "progs.h"

u32 t_fib(u32 n) { return n < 2 ? n : t_fib(n - 1) + t_fib(n - 2); }

u32 t_div(s32 a, s32 b) {
    if (b == 0) return 0xDEAD;
    return (u32)(a / b) ^ ((u32)(a % b) << 16) ^ ((u32)a / (u32)b);
}

u32 t_switch(u32 x) {
    switch (x) {
    case 0: return 11;
    case 1: return 22 + x;
    case 2: return 37;
    case 3: return x * 9;
    case 4: return 51;
    case 5: return 600;
    case 6: return x << 3;
    case 7: return 7777;
    case 8: return 0x1234;
    case 9: return x ^ 0xFF;
    default: return 99;
    }
}

static u32 data_buf[64];
static u32 rng;
static u32 next_rand(void) { rng = rng * 1103515245u + 12345u; return rng >> 8; }

u32 t_sort(u32 seed) {
    rng = seed;
    for (int i = 0; i < 64; i++) data_buf[i] = next_rand() % 1000;
    for (int i = 0; i < 64; i++)
        for (int j = 0; j < 63 - i; j++)
            if (data_buf[j] > data_buf[j + 1]) { u32 t = data_buf[j]; data_buf[j] = data_buf[j + 1]; data_buf[j + 1] = t; }
    u32 h = 0;
    for (int i = 0; i < 64; i++) h = h * 31 + data_buf[i];
    return h;
}

typedef u32 (*fn_t)(u32);
static u32 inc(u32 x) { return x + 1; }
static u32 dbl(u32 x) { return x * 2; }
static fn_t table[4] = {inc, dbl, th_collatz, th_call_arm};
volatile u32 fn_sel;

u32 t_fnptr(u32 x) {
    u32 acc = 0;
    for (u32 i = 0; i < 4; i++) {
        fn_sel = i;
        acc += table[fn_sel](x + i);
    }
    return acc;
}

static const char msg[] = "Spirit Tracks \x80\xFF test";
u32 t_bytes(u32 x) {
    u32 h = x;
    const signed char* s = (const signed char*)msg;
    for (int i = 0; s[i]; i++) h = h * 33 + (u32)(s32)s[i];
    const int16_t* hw = (const int16_t*)(const void*)msg;
    for (int i = 0; i < 8; i++) h ^= (u32)(s32)hw[i] << (i & 7);
    return h;
}

u32 t_bits(u32 x, u32 y) {
    u32 r = (x << (y & 31)) | (x >> ((32 - y) & 31));
    r ^= (u32)__builtin_clz(x | 1) * 0x01010101u;
    r += (u32)((s32)x >> (y & 15));
    r ^= x >> (y & 7);
    return r;
}

uint64_t t_u64(u32 a, u32 b) {
    uint64_t m = (uint64_t)a * b;
    int64_t s = (int64_t)(s32)a * (s32)b;
    uint64_t d = b ? m / ((uint64_t)b | 1) : 0;
    return m ^ (uint64_t)s ^ (d << 3);
}

struct Actor { int16_t x, y; uint8_t hp, flags; u32 id; struct Actor* next; };
static struct Actor actors[8];
u32 t_struct(u32 x) {
    for (int i = 0; i < 8; i++) {
        actors[i].x = (int16_t)(x * i - 100);
        actors[i].y = (int16_t)(i * -7);
        actors[i].hp = (uint8_t)(x + i * 30);
        actors[i].flags = (uint8_t)(1u << i);
        actors[i].id = x ^ (u32)i;
        actors[i].next = i < 7 ? &actors[i + 1] : 0;
    }
    u32 h = 0;
    for (struct Actor* a = &actors[0]; a; a = a->next) h = h * 7 + (u32)(a->x + a->y) + a->hp + a->flags + a->id;
    return h;
}

/* coma flotante por software (libgcc) */
u32 t_float(u32 x) {
    float f = (float)x / 3.0f + 0.5f;
    double d = (double)f * 1.25 - (double)(x & 7);
    return (u32)(s32)d + (u32)f;
}
