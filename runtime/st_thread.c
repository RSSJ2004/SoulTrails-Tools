/* Hilos del juego (fase 1.5 de docs/PLAN.md): una fibra nativa por hilo del SDK.
 *
 * Un recompilador estático ejecuta el código del juego sobre la pila de C, así que cuando el
 * SDK cambia de hilo (desde la IRQ, reescribiendo el marco del BIOS, o con un "retorno de
 * excepción" que carga otro contexto) no basta con cambiar registros: hay que cambiar también
 * de pila nativa. Cada hilo del juego corre en su propia fibra y se identifica por su pila
 * (SP de usuario/sistema), que es única por hilo:
 *   - al cambiar, la fibra actual se suspende con el SP que tenía su hilo;
 *   - si alguna fibra suspendida tiene el SP del hilo destino, se reanuda;
 *   - si no, es un hilo que aún no ha corrido: fibra nueva que empieza en su PC.
 * El estado de la CPU invitada es único: quien cambia ya lo deja con los registros del hilo
 * destino. Una fibra reanudada tras un "retorno de excepción" vuelve con st_fiber_resumed = 1
 * y el código generado hace ST_RETURN hasta la función que llamó a SaveContext, que salta a
 * su punto de reanudación (st_resume en codegen).
 *
 * Windows: fibras del sistema (CreateFiber). Resto: ucontext. */
#ifndef _WIN32
#define _XOPEN_SOURCE 700
#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#endif
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "strecomp.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <ucontext.h>
#endif

#define MAX_FIBERS 64
#define FIBER_STACK (16u * 1024u * 1024u)   /* pila nativa por hilo: el código generado anida mucho */

typedef struct Fiber {
    int used, suspended;
    uint32_t sp;          /* SP del hilo invitado mientras está suspendido */
    uint32_t start_pc;    /* fibra nueva: dónde empieza el hilo */
    uint32_t resume_pc;   /* reanudación tras retorno de excepción: a dónde vuelve */
    void (*entry)(void);  /* fibra de arranque del invitado (modo con backend) */
#ifdef _WIN32
    void* native;
#else
    ucontext_t ctx;
    void* stack;
#endif
} Fiber;

static Fiber fibers[MAX_FIBERS];
static int cur = -1;
static StCpu* g_cpu;
volatile int st_fiber_resumed;
unsigned long st_thread_switches;

static uint32_t saved_sp;          /* pila del último contexto guardado (STM de r4-r14) */
static int saved_sp_valid;
void st_thread_note_saved_sp(uint32_t sp) { saved_sp = sp; saved_sp_valid = 1; }

static uint32_t prev_user_sp;
static int prev_user_sp_valid;

void st_thread_note_user_sp(uint32_t old_sp) {
    prev_user_sp = old_sp;
    prev_user_sp_valid = 1;
}
void st_thread_forget_user_sp(void) { prev_user_sp_valid = 0; }
int st_thread_take_prev_sp(uint32_t* sp) {
    if (!prev_user_sp_valid) return 0;
    prev_user_sp_valid = 0;
    *sp = prev_user_sp;
    return 1;
}

static void thread_main(int i) {
    StCpu* c = g_cpu;
    if (fibers[i].entry) {
        fibers[i].entry();
        st_platform.halt(c, "el arranque del juego volvió");
        return;
    }
    uint32_t pc = fibers[i].start_pc;
    for (;;) {   /* si la función del hilo vuelve, sigue donde diga su LR (p. ej. OS_ExitThread) */
        st_call(c, pc);
        if (c->next_pc == pc || !c->next_pc) break;
        pc = c->next_pc;
    }
    char why[160];
    snprintf(why, sizeof why, "un hilo del juego terminó sin cambiar a otro (fibra %d, empezó en %08X, "
             "vuelve a %08X)", i, fibers[i].start_pc, c->next_pc);
    st_platform.halt(c, why);
}

static unsigned long logged;
static void log_switch(const char* what, int t, uint32_t pc, uint32_t sp, uint32_t old_sp, uint32_t raw_old) {
    if (logged++ >= 48) return;
    fprintf(stderr, "[hilos] %s fibra %d -> %d: PC %08X pila %08X (saliente: pila %08X, en el cambio %08X)\n", what,
            cur, t, pc, sp, old_sp, raw_old);
}

#ifdef _WIN32
static void CALLBACK fiber_entry(void* p) { thread_main((int)(intptr_t)p); }
#else
static void fiber_entry(int i) { thread_main(i); }
#endif

static int ensure_main(void) {
    if (cur >= 0) return 1;
#ifdef _WIN32
    fibers[0].native = ConvertThreadToFiber(NULL);
    if (!fibers[0].native) return 0;
#endif
    fibers[0].used = 1;
    cur = 0;
    return 1;
}

static int new_fiber(uint32_t pc) {
    for (int i = 1; i < MAX_FIBERS; i++) {
        if (fibers[i].used) continue;
        Fiber* f = &fibers[i];
        f->used = 1;
        f->start_pc = pc;
#ifdef _WIN32
        f->native = CreateFiber(FIBER_STACK, fiber_entry, (void*)(intptr_t)i);
        if (!f->native) { f->used = 0; return -1; }
#else
        f->stack = malloc(FIBER_STACK);
        if (!f->stack || getcontext(&f->ctx) != 0) { free(f->stack); f->used = 0; return -1; }
        f->ctx.uc_stack.ss_sp = f->stack;
        f->ctx.uc_stack.ss_size = FIBER_STACK;
        f->ctx.uc_link = NULL;
        makecontext(&f->ctx, (void (*)(void))fiber_entry, 1, i);
#endif
        return i;
    }
    return -1;
}

static void switch_native(int to) {
    int from = cur;
    cur = to;
#ifdef _WIN32
    (void)from;
    SwitchToFiber(fibers[to].native);
#else
    swapcontext(&fibers[from].ctx, &fibers[to].ctx);
#endif
}

/* Cambia al hilo cuyo SP es target_sp (la CPU ya tiene su estado). La fibra actual queda
 * suspendida con old_sp. Devuelve 0 si no hace falta cambiar (mismo hilo) y 1 cuando, más
 * tarde, otro hilo vuelve a esta fibra. */
int st_thread_switch(StCpu* c, uint32_t target_pc, uint32_t target_sp, uint32_t old_sp) {
    /* El hilo saliente se reconocerá por la pila que quedó en su contexto guardado (puede
     * diferir de la actual si el SDK apiló algo entre SaveContext y LoadContext) */
    uint32_t raw_old = old_sp;
    if (saved_sp_valid) { old_sp = saved_sp; saved_sp_valid = 0; }
    if (target_sp == old_sp) return 0;
    g_cpu = c;
    if (!ensure_main()) { st_platform.halt(c, "no se pudo crear la fibra principal"); return 0; }
    int t = -1;
    for (int i = 0; i < MAX_FIBERS; i++)
        if (i != cur && fibers[i].used && fibers[i].suspended && fibers[i].sp == target_sp) { t = i; break; }
    if (t < 0) {
        if ((target_pc & ~1u) == ST_IRQ_BIOS_RET + 4) {
            st_platform.halt(c, "cambio a un hilo interrumpido que no se conoce (SP distinto)");
            return 0;
        }
        t = new_fiber(target_pc);
        if (t < 0) { st_platform.halt(c, "demasiados hilos (fibras)"); return 0; }
        st_fiber_resumed = 0;
    } else {
        fibers[t].resume_pc = target_pc;
        st_fiber_resumed = 1;
    }
    log_switch(st_fiber_resumed ? "reanuda" : "nuevo hilo", t, target_pc, target_sp, old_sp, raw_old);
    fibers[cur].suspended = 1;
    fibers[cur].sp = old_sp;
    fibers[t].suspended = 0;
    st_thread_switches++;
    switch_native(t);
    /* Otro hilo nos ha devuelto el control: la CPU ya tiene nuestro estado */
    return 1;
}

uint32_t st_thread_resume_pc(void) { return cur >= 0 ? fibers[cur].resume_pc : 0; }

/* Modo con backend (melonDS): la fibra 0 es el planificador de la consola; el juego corre en
 * fibras invitadas y cede el turno con st_fiber_yield_host cuando agota sus ciclos. */
static int guest_cur = -1;

int st_fiber_guest_start(StCpu* c, void (*entry)(void)) {
    g_cpu = c;
    if (!ensure_main()) return 0;
    int i = new_fiber(0);
    if (i < 0) return 0;
    fibers[i].entry = entry;
    guest_cur = i;
    return 1;
}
void st_fiber_run_guest(void) {
    if (guest_cur > 0 && cur == 0) switch_native(guest_cur);
}
void st_fiber_yield_host(void) {
    if (cur <= 0) return;   /* ya en el planificador */
    guest_cur = cur;
    switch_native(0);
}
