@ Tablas de saltos Thumb que el recompilador debe resolver sin aprender fallos:
@  sw_half: desplazamientos de 16 bits con signo sumados al pc (estilo MWCC), con un caso
@           colocado antes de la función (desplazamiento negativo)
@  sw_word: tabla de palabras absolutas con adr + ldr + mov pc
@ Resultados en results + 400 (los vuelca qemu_start de modes.S).
    .syntax unified
    .arch armv5te
    .thumb
    .text

    .global test_jt
    .thumb_func
test_jt:
    push {r4-r7, lr}
    ldr r7, =results + 400
    movs r4, #0
1:  movs r0, r4
    bl sw_half
    str r0, [r7]
    adds r7, #4
    movs r0, r4
    bl sw_word
    str r0, [r7]
    adds r7, #4
    adds r4, #1
    cmp r4, #7
    blt 1b
    pop {r4-r7, pc}
    .pool

    .global jt_h2
jt_h2:                              @ caso 2, antes de la función
    movs r0, #52
    bx lr

    .global sw_half
    .thumb_func
sw_half:
    cmp r0, #4
    bhi .Lh_def
    lsls r0, r0, #1
    add r0, pc
    ldrh r0, [r0, #6]
    lsls r0, r0, #16
    asrs r0, r0, #16
.Lh_jmp:
    add pc, r0
    .hword jt_h0 - (.Lh_jmp + 4)
    .hword jt_h1 - (.Lh_jmp + 4)
    .hword jt_h2 - (.Lh_jmp + 4)
    .hword jt_h3 - (.Lh_jmp + 4)
    .hword jt_h4 - (.Lh_jmp + 4)
    .global jt_h0, jt_h1, jt_h3, jt_h4
jt_h0:
    movs r0, #50
    bx lr
jt_h1:
    movs r0, #51
    bx lr
jt_h3:
    movs r0, #53
    adds r0, #100
    bx lr
jt_h4:
    movs r0, #54
    bx lr
.Lh_def:
    movs r0, #99
    bx lr

    .global sw_word
    .thumb_func
sw_word:
    cmp r0, #3
    bhs .Lw_def
    lsls r0, r0, #2
    adr r1, .Lw_tab
    ldr r0, [r1, r0]
    mov pc, r0
    .align 2
.Lw_tab:
    .word jt_w0, jt_w1, jt_w2
    .global jt_w0, jt_w1, jt_w2
jt_w0:
    movs r0, #70
    bx lr
jt_w1:
    movs r0, #71
    lsls r0, r0, #1
    bx lr
jt_w2:
    movs r0, #72
    bx lr
.Lw_def:
    movs r0, #98
    bx lr
