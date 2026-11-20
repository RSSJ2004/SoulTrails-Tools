typedef unsigned int u32;
u32 thumb_sqrt(u32 v) {
    register u32 r0 asm("r0") = v;
    asm volatile("swi 0x0D" : "+r"(r0) : : "r1", "r2", "r3", "memory");
    return r0;
}
