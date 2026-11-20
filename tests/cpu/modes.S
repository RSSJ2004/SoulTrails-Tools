@ Prueba de modos del ARM946E-S: bancos de registros, SPSR, LDM/STM ^, retornos de
@ excepción (también a Thumb) y MSR. Se ejecuta en QEMU (-cpu arm946, referencia) y
@ recompilado; los resultados (palabras en 'results') deben coincidir.
    .syntax unified
    .arch armv5te
    .arm
    .text

    .global qemu_start
qemu_start:                         @ solo en QEMU: arranca en SVC
    ldr sp, =0x02300000
    bl test_modes
    ldr r4, =results
    ldr r5, =results_end
1:  cmp r4, r5
    bhs 2f
    ldr r0, [r4], #4
    bl puthex
    b 1b
2:  mov r0, #0x18                   @ SYS_EXIT
    ldr r1, =0x20026
    svc 0x123456
3:  b 3b

puthex:                             @ escribe r0 en hexadecimal + '\n' (semihosting SYS_WRITE0)
    ldr r1, =hexbuf
    mov r2, #8
4:  mov r3, r0, lsr #28
    cmp r3, #10
    addlo r3, r3, #'0'
    addhs r3, r3, #('A' - 10)
    strb r3, [r1], #1
    mov r0, r0, lsl #4
    subs r2, r2, #1
    bne 4b
    mov r3, #10
    strb r3, [r1], #1
    mov r3, #0
    strb r3, [r1]
    mov r0, #4
    ldr r1, =hexbuf
    svc 0x123456
    bx lr

    .macro setmode m
    msr cpsr_c, #\m
    .endm

    .global test_modes
test_modes:
    ldr r0, =saved
    str sp, [r0]
    str lr, [r0, #4]
    mrs r1, cpsr
    str r1, [r0, #8]
    ldr r7, =results

    @ 1. sp/lr distintos por modo, r8-r12 de FIQ
    setmode 0xD1
    ldr sp, =0x11110000
    ldr lr, =0x11110004
    mov r8, #0x81
    mov r9, #0x82
    mov r10, #0x83
    mov r11, #0x84
    mov r12, #0x85
    setmode 0xD2
    ldr sp, =0x12120000
    ldr lr, =0x12120004
    setmode 0xD3
    ldr sp, =0x13130000
    ldr lr, =0x13130004
    setmode 0xD7
    ldr sp, =0x17170000
    ldr lr, =0x17170004
    setmode 0xDB
    ldr sp, =0x1B1B0000
    ldr lr, =0x1B1B0004
    setmode 0xDF
    ldr sp, =0x1F1F0000
    ldr lr, =0x1F1F0004
    mov r8, #0x91
    mov r9, #0x92
    mov r10, #0x93
    mov r11, #0x94
    mov r12, #0x95
    .irp m, 0xD1, 0xD2, 0xD3, 0xD7, 0xDB, 0xDF
    setmode \m
    str sp, [r7], #4
    str lr, [r7], #4
    stmia r7!, {r8-r12}
    .endr

    @ 2. SPSR por modo
    setmode 0xD1
    ldr r1, =0x10000013
    msr spsr_fsxc, r1
    setmode 0xD2
    ldr r1, =0x6000001F
    msr spsr_fsxc, r1
    setmode 0xD3
    ldr r1, =0x80000010
    msr spsr_fsxc, r1
    setmode 0xD7
    ldr r1, =0x20000012
    msr spsr_fsxc, r1
    setmode 0xDB
    ldr r1, =0x40000011
    msr spsr_fsxc, r1
    .irp m, 0xD1, 0xD2, 0xD3, 0xD7, 0xDB
    setmode \m
    mrs r1, spsr
    str r1, [r7], #4
    .endr
    setmode 0xD2
    msr spsr_f, #0xA0000000          @ solo flags
    mrs r1, spsr
    str r1, [r7], #4

    @ 3. LDM/STM ^ (registros de usuario) desde IRQ y FIQ
    setmode 0xD2
    stmia r7, {r13, r14}^
    nop
    add r7, r7, #8
    ldr r0, =userdata
    ldmia r0, {r13, r14}^
    nop
    str sp, [r7], #4
    str lr, [r7], #4
    setmode 0xD1
    stmia r7, {r8-r14}^
    nop
    add r7, r7, #28
    ldr r0, =userdata
    ldmia r0, {r8, r9}^
    nop
    stmia r7!, {r8, r9}
    setmode 0xDF
    stmia r7!, {r8, r9, r13, r14}

    @ 4. MOVS pc, lr: vuelve a ARM en modo sistema con flags
    setmode 0xD3
    ldr r1, =0x200000DF
    msr spsr_fsxc, r1
    ldr lr, =ret1
    movs pc, lr

    .global ret1
ret1:
    mrs r1, cpsr
    str r1, [r7], #4
    @ 5. LDM {.., pc}^ desde IRQ a SVC
    setmode 0xD2
    ldr r1, =0x800000D3
    msr spsr_fsxc, r1
    ldr r0, =retframe
    ldmia r0, {r1, pc}^

    .global ret2
ret2:
    mrs r2, cpsr
    str r2, [r7], #4
    str r1, [r7], #4
    @ 6. SUBS pc, lr, #4 desde ABT
    setmode 0xD7
    ldr r1, =0x400000DF
    msr spsr_fsxc, r1
    ldr lr, =ret3 + 4
    subs pc, lr, #4

    .global ret3
ret3:
    mrs r1, cpsr
    str r1, [r7], #4
    str sp, [r7], #4
    @ 7. MOVS pc, lr con T en el SPSR: vuelve a Thumb
    setmode 0xDB
    ldr r1, =0x000000FF
    msr spsr_fsxc, r1
    ldr lr, =ret4
    movs pc, lr

    .thumb
    .global ret4
    .thumb_func
ret4:
    movs r1, #0x44
    str r1, [r7]
    adds r7, #4
    ldr r1, =ret5
    bx r1
    .pool

    .arm
    .global ret5
ret5:
    mrs r1, cpsr
    str r1, [r7], #4
    @ 8. MSR de flags (incluido Q) y de control en modo sistema
    msr cpsr_f, #0xF8000000
    mrs r1, cpsr
    str r1, [r7], #4
    msr cpsr_f, #0
    setmode 0x1F                    @ IRQ/FIQ activadas
    mrs r1, cpsr
    str r1, [r7], #4
    setmode 0xDF

    @ Fin: volver al modo y la pila de entrada
    ldr r0, =saved
    ldr r1, [r0, #8]
    msr cpsr_fsxc, r1
    ldr sp, [r0]
    ldr lr, [r0, #4]
    bx lr
    .pool

    .data
    .align 2
userdata:  .word 0xAAAA0000, 0xBBBB0000
retframe:  .word 0x00C0FFEE, ret2
saved:     .word 0, 0, 0
hexbuf:    .space 16
    .global results, results_end
results:   .space 4 * 96
results_end:
