@ ARM9 de prueba para el port híbrido con melonDS: instala un manejador de IRQ, activa la IRQ de
@ VBlank (DISPSTAT/IE/IME) y cuenta cuadros con VBlankIntrWait. Si el reparto de tiempo con
@ melonDS y la entrega de IRQ funcionan, 'counter' avanza uno por cuadro.
    .arm
    .syntax unified
    .global _start
_start:
    mov r11, lr
    ldr r0, =0x027E3FFC
    ldr r1, =irq_handler
    str r1, [r0]
    ldr r0, =0x04000004         @ DISPSTAT: IRQ de VBlank
    mov r1, #8
    strh r1, [r0]
    ldr r0, =0x04000210         @ IE = VBlank
    mov r1, #1
    str r1, [r0]
    ldr r0, =0x04000208         @ IME = 1
    str r1, [r0]
    mrs r0, cpsr
    bic r0, r0, #0x80
    msr cpsr_c, r0
loop:
    swi 0x50000                 @ VBlankIntrWait
    ldr r0, =counter
    ldr r1, [r0]
    add r1, r1, #1
    str r1, [r0]
    b loop

irq_handler:                    @ llamado por la HLE del BIOS con r0 = 0x04000000
    ldr r1, =0x04000214         @ reconocer IF
    mov r2, #1
    str r2, [r1]
    ldr r1, =0x027E3FF8         @ marca para IntrWait
    ldr r2, [r1]
    orr r2, r2, #1
    str r2, [r1]
    bx lr
    .ltorg
    .align 2
    .global counter
counter: .word 0
