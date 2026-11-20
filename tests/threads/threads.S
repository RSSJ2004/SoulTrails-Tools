@ Dos hilos que se ceden el control como el SDK (SaveContext / LoadContext con LDM ^ y
@ retorno de excepción). Si todo va bien, el hilo A vuelve al arranque; si no, bkpt.
    .arm
    .syntax unified
    .global _start
_start:
    mov r11, lr                 @ vuelta al arranque
    msr cpsr_c, #0xDF           @ modo sistema (como los hilos del SDK)
    ldr sp, =0x02300000         @ pila del hilo A
    ldr r0, =ctx_b              @ contexto inicial del hilo B
    mov r1, #0xDF
    str r1, [r0, #0]
    ldr r1, =0x02380000
    str r1, [r0, #56]
    ldr r1, =thread_b
    str r1, [r0, #64]
    bl yield_to_b               @ como el SDK: el planificador guarda y cambia de hilo
resumed:
    ldr r0, =shared
    ldr r1, [r0]
    cmp r1, #42
    bne fail
    ldr r0, =count              @ SaveContext de A, +1 de B y SaveContext de B
    ldr r1, [r0]
    cmp r1, #3
    bne fail
    mov lr, r11
    bx lr
fail:
    bkpt #2

thread_b:
    ldr r0, =shared
    mov r1, #42
    str r1, [r0]
    ldr r0, =count
    ldr r1, [r0]
    add r1, r1, #1
    str r1, [r0]
    ldr r0, =ctx_b
    bl save_context
    cmp r0, #0
    bne back_in_b
    push {r4-r7}                @ la pila cambia entre SaveContext y LoadContext (como el SDK)
    ldr r0, =ctx_a              @ vuelve a A
    bl load_context
    bkpt #3
back_in_b:
    bkpt #4

yield_to_b:                     @ planificador: SaveContext + LoadContext en otra función
    push {r4, lr}
    ldr r0, =ctx_a
    bl save_context
    cmp r0, #0
    bne 1f
    push {r4-r7}                @ la pila cambia entre SaveContext y LoadContext
    ldr r0, =ctx_b
    bl load_context
    bkpt #1
1:  pop {r4, pc}

@ contexto: +0 cpsr, +4 r0-r12, +56 sp, +60 lr, +64 pc
save_context:
    mrs r1, cpsr
    str r1, [r0]
    add r1, r0, #4
    stmia r1, {r0-r14}
    mov r1, #1
    str r1, [r0, #4]            @ al reanudarse devuelve 1
    adr r1, sc_resume           @ como el SDK: se reanuda dentro de SaveContext
    str r1, [r0, #64]
    ldr r1, =count
    ldr r2, [r1]
    add r2, r2, #1
    str r2, [r1]
    mov r0, #0
    bx lr
sc_resume:
    bx lr                       @ con r0 = 1 y el LR guardado

load_context:
    ldr r1, [r0]
    msr cpsr_c, #0xD3           @ supervisor
    msr spsr_fsxc, r1
    ldr lr, [r0, #64]
    add r0, r0, #4
    ldmia r0, {r0-r14}^         @ registros de usuario del hilo destino
    nop
    movs pc, lr                 @ retorno de excepción = cambio de hilo
    .ltorg
    .align 2
shared: .word 0
count:  .word 0
ctx_a:  .space 72
ctx_b:  .space 72
