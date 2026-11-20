@ crt0 mínimo al estilo del SDK de la DS (código propio para tests, no del juego):
@ pilas por modo, ModuleParams con la marca "nitrocode" y salto a boot_main (C).
    .syntax unified
    .arch armv5te
    .arm
    .section .head, "ax"
    .global _start
_start:
    msr cpsr_c, #0xD2               @ IRQ
    ldr sp, =0x023F0000
    msr cpsr_c, #0xDF               @ sistema
    ldr sp, =0x023E0000
    mov r4, lr
    bl boot_main
    swi 0x7E0000                    @ fin (lo atiende la plataforma de test)
    bx r4
    .pool

    .align 2
    .global module_params
module_params:
    .word __autoload_list_start, __autoload_list_end, __autoload_start
    .word __bss_start, __bss_end
    .word 0                         @ compressed_static_end: lo rellena el test al comprimir
    .word 0x04027531                @ versión del SDK (ficticia)
    .word 0xDEC00621, 0x2106C0DE    @ marca nitrocode
