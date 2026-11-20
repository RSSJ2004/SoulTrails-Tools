/* Programa de prueba del arranque (no es código del juego). */
typedef unsigned int u32;
typedef unsigned char u8;
typedef unsigned short u16;

struct ModuleParams { u32 list_start, list_end, auto_start, bss_start, bss_end, compressed_end, sdk; };
extern struct ModuleParams module_params;
struct Autoload { u32 addr, size, bss; };

volatile u32 results[16];
u32 zeroed[8];               /* BSS: debe quedar a 0 aunque la RAM venga sucia */
u32 counter = 5;             /* .data comprimido */

/* El descompresor y boot_main (y sus literales) van en la cabecera sin comprimir, como en el SDK */
#define HEAD __attribute__((section(".head.c"), noinline))

HEAD void uncompress_backward(u8* end) {
    u32 hdr = ((u32*)end)[-2], inc = ((u32*)end)[-1];
    u8* src = end - (hdr >> 24);
    u8* dst = end + inc;
    u8* stop = end - (hdr & 0xFFFFFF);
    while (dst > stop) {
        u8 flags = *--src;
        for (int i = 0; i < 8 && dst > stop; i++, flags <<= 1) {
            if (flags & 0x80) {
                src -= 2;
                u32 p = src[0] | (src[1] << 8);
                int len = (p >> 12) + 3, disp = (p & 0xFFF) + 3;
                while (len-- && dst > stop) { dst--; *dst = dst[disp]; }
            } else {
                *--dst = *--src;
            }
        }
    }
}

/* Devuelve los flags de la tabla de overlays (tamaño comprimido + bit 24 de compresión) */
static u32 swi_load_overlay(u32 id) {
    register u32 r0 asm("r0") = id;
    asm volatile("swi 0x7F0000" : "+r"(r0) : : "r1", "r2", "r3", "r12", "lr", "memory");
    return r0;
}

/* Overlays en 0x02200000: el 0 viene comprimido, el 1 no */
static u32 call_overlay(u32 id, u32 x) {
    u32 flags = swi_load_overlay(id);
    if (flags & 0x01000000) uncompress_backward((u8*)0x02200000 + (flags & 0xFFFFFF));
    u32 (*fn)(u32) = (u32 (*)(u32))0x02200000;
    return fn(x);
}

/* Datos repetitivos (comprimibles) que viajan en la parte comprimida */
#define R4 1, 2, 3, 4
#define R16 R4, R4, R4, R4
#define R64 R16, R16, R16, R16
const u32 pattern[256] = {R64, R64, R64, R64};

__attribute__((section(".itcm"), noinline)) u32 itcm_mix(u32 a, u32 b) { return (a * 31) ^ (b + 7); }

__attribute__((noinline)) int main(void) {
    results[0] = itcm_mix(3, 4);
    results[1] = counter;
    u32 z = 0;
    for (int i = 0; i < 8; i++) z |= zeroed[i];
    results[2] = z;
    results[3] = call_overlay(0, 10);
    results[4] = call_overlay(1, 10);
    results[5] = call_overlay(0, 7);   /* volver al primero */
    u32 sum = 0;
    for (int i = 0; i < 256; i++) sum += pattern[i];
    results[6] = sum ^ 0xB0070000u;
    return 0;
}

HEAD void boot_main(void) {
    struct ModuleParams* mp = &module_params;
    if (mp->compressed_end) {
        uncompress_backward((u8*)mp->compressed_end);
        mp->compressed_end = 0;
    }
    const u8* src = (const u8*)mp->auto_start;
    for (struct Autoload* a = (struct Autoload*)mp->list_start; a < (struct Autoload*)mp->list_end; a++) {
        u8* dst = (u8*)a->addr;
        for (u32 i = 0; i < a->size; i++) dst[i] = src[i];
        for (u32 i = 0; i < a->bss; i++) dst[a->size + i] = 0;
        src += a->size;
    }
    for (u8* p = (u8*)mp->bss_start; p < (u8*)mp->bss_end; p++) *p = 0;
    main();
}
