/* Programa invitado que usa las SWI del BIOS y la cadena de IRQ (no es código del juego).
 * Las entradas vienen de bios_data.h (generado por el test) y los resultados se dejan en
 * memoria para que el test los compare con sus referencias. */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
#include "bios_data.h"

#define SWI(n) asm volatile("swi " #n "<<16" : "+r"(r0), "+r"(r1), "+r"(r2), "+r"(r3) : : "r12", "lr", "memory")
static u32 swi4(u32 num, u32 a, u32 b, u32 c, u32* o1, u32* o3);

u8 out_lz[sizeof lz_src * 8] __attribute__((aligned(4)));
u8 out_lzv[sizeof lz_src * 8] __attribute__((aligned(4)));
u8 out_rl[1024] __attribute__((aligned(4)));
u8 out_huff4[1024] __attribute__((aligned(4)));
u8 out_huff8[1024] __attribute__((aligned(4)));
u8 out_d8[1024] __attribute__((aligned(4)));
u16 out_d16[512];
u32 out_unpack[64];
u32 out_cpuset[16], out_fill[16], out_fast[16];
u32 math[64];
u32 irq_info[16];

volatile u32 irq_count;

static inline u32 mrs_cpsr(void) { u32 v; asm volatile("mrs %0, cpsr" : "=r"(v)); return v; }

void irq_handler(void) {          /* lo que haría OS_IrqHandler del SDK, reducido */
    volatile u32* IE = (u32*)0x04000210, *IF = (u32*)0x04000214;
    u32 pend = *IE & *IF;
    *IF = pend;
    irq_count++;
    irq_info[0] = mrs_cpsr() & 0x1F;
    *(volatile u32*)(0x027E0000 + 0x3FF8) |= pend;
}

#define CALL(num, a, b, c) swi4(num, (u32)(a), (u32)(b), (u32)(c), 0, 0)

__attribute__((noinline)) static u32 swi4(u32 num, u32 a, u32 b, u32 c, u32* o1, u32* o3) {
    register u32 r0 asm("r0") = a, r1 asm("r1") = b, r2 asm("r2") = c, r3 asm("r3") = 0;
    switch (num) {
    case 0x04: SWI(0x04); break;
    case 0x05: SWI(0x05); break;
    case 0x06: SWI(0x06); break;
    case 0x09: SWI(0x09); break;
    case 0x0B: SWI(0x0B); break;
    case 0x0C: SWI(0x0C); break;
    case 0x0D: SWI(0x0D); break;
    case 0x0E: SWI(0x0E); break;
    case 0x0F: SWI(0x0F); break;
    case 0x10: SWI(0x10); break;
    case 0x11: SWI(0x11); break;
    case 0x12: SWI(0x12); break;
    case 0x13: SWI(0x13); break;
    case 0x14: SWI(0x14); break;
    case 0x16: SWI(0x16); break;
    case 0x18: SWI(0x18); break;
    }
    if (o1) *o1 = r1;
    if (o3) *o3 = r3;
    return r0;
}

u32 thumb_sqrt(u32 v);   /* bios_thumb.c: SWI desde Thumb */

static const struct { u16 len; u8 sw, dw; u32 off; } unpack_info = {8, 2, 8, 0x80000010u};

int bios_main(void) {
    /* Pila IRQ y DTCM en 0x027E0000 (CP15 c9,c1,0), como hace el crt0 del SDK */
    asm volatile("mrs r0, cpsr\n bic r1, r0, #0x1F\n orr r1, r1, #0x12\n msr cpsr_c, r1\n"
                 "ldr sp, =0x023F8000\n msr cpsr_c, r0\n" ::: "r0", "r1", "memory");
    asm volatile("ldr r0, =0x027E000A\n mcr p15, 0, r0, c9, c1, 0\n" ::: "r0");
    CALL(0x11, lz_src, out_lz, 0);
    CALL(0x12, lz_src, out_lzv, 0);
    CALL(0x14, rl_src, out_rl, 0);
    CALL(0x13, huff4_src, out_huff4, 0);
    CALL(0x13, huff8_src, out_huff8, 0);
    CALL(0x16, d8_src, out_d8, 0);
    CALL(0x18, d16_src, out_d16, 0);
    CALL(0x10, unpack_src, out_unpack, &unpack_info);
    CALL(0x0B, cpuset_src, out_cpuset, 16 | (1u << 26));
    CALL(0x0B, cpuset_src + 3, out_fill, 16 | (1u << 26) | (1u << 24));
    CALL(0x0C, cpuset_src, out_fast, 13);
    u32 k = 0;
    for (unsigned i = 0; i < sizeof div_in / sizeof div_in[0]; i++) {
        u32 r1, r3;
        math[k++] = swi4(0x09, div_in[i][0], div_in[i][1], 0, &r1, &r3);
        math[k++] = r1;
        math[k++] = r3;
    }
    for (unsigned i = 0; i < sizeof sqrt_in / sizeof sqrt_in[0]; i++) math[k++] = CALL(0x0D, sqrt_in[i], 0, 0);
    math[k++] = thumb_sqrt(1000000);
    math[k++] = CALL(0x0E, 0xFFFF, crc_src, sizeof crc_src);
    math[k++] = CALL(0x0F, 0, 0, 0);

    /* IRQ: manejador en DTCM+3FFC, IE = VBlank, IME = 1 */
    *(volatile u32*)(0x027E0000 + 0x3FFC) = (u32)irq_handler;
    *(volatile u32*)0x04000210 = 1;
    *(volatile u32*)0x04000208 = 1;
    for (int i = 0; i < 3; i++) CALL(0x05, 0, 0, 0);    /* VBlankIntrWait */
    CALL(0x04, 1, 1, 0);                               /* IntrWait(1, VBlank) */
    irq_info[1] = irq_count;
    irq_info[2] = mrs_cpsr() & 0x1F;                   /* de vuelta en el modo de antes */
    irq_info[3] = *(volatile u32*)0x04000214;          /* IF reconocido */
    irq_info[4] = *(volatile u32*)(0x027E0000 + 0x3FF8);   /* flags consumidos por IntrWait */
    return 0;
}
