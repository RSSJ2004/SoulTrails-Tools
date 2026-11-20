@ Casos que QEMU no define igual que el hardware (comportamiento "UNPREDICTABLE" del ARM
@ que el ARM946E-S sí fija, según GBATEK) y llamadas a la plataforma (coprocesador, bkpt).
@ Solo se ejecuta recompilado; los valores esperados están en test_cpu_modes.py.
    .syntax unified
    .arch armv5te
    .arm
    .text
    .global test_v5
test_v5:
    push {r4-r7, lr}
    ldr r7, =results2
    ldr r0, =buf
    .word 0xE8B00003                @ ldmia r0!, {r0, r1}  base no es la última: escribe
    str r0, [r7], #4
    str r1, [r7], #4
    ldr r1, =buf
    .word 0xE8B10003                @ ldmia r1!, {r0, r1}  base es la última: gana lo cargado
    str r0, [r7], #4
    str r1, [r7], #4
    ldr r0, =buf
    .word 0xE8B00001                @ ldmia r0!, {r0}  único registro: escribe
    str r0, [r7], #4
    ldr r0, =buf2
    mov r1, #0x77
    .word 0xE8A00003                @ stmia r0!, {r0, r1}  guarda la base antigua
    str r0, [r7], #4
    ldr r1, =buf2 + 8
    mov r0, #0x66
    .word 0xE8A10003                @ stmia r1!, {r0, r1}  ARMv5: también la antigua
    str r1, [r7], #4
    mrc p14, 0, r0, c1, c2, 3       @ coprocesador inexistente: lo resuelve la plataforma
    str r0, [r7], #4
    mov r0, #0x5A
    mcr p7, 1, r0, c3, c4, 5
    bkpt 0x42
    pop {r4-r7, pc}
    .pool

    .data
    .align 2
buf:       .word 0x11, 0x22, 0x33
buf2:      .word 0, 0, 0, 0
    .global results2, buf, buf2
results2:  .space 4 * 16
