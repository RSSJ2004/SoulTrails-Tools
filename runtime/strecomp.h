/* Runtime del recompilador estático de SoulTrails.
 *
 * El código generado (recomp_*.c) opera sobre StCpu y accede a memoria con
 * st_read* / st_write*. Las llamadas que no se pueden resolver al compilar
 * pasan por st_call(), que busca la función en los módulos cargados
 * (binario estático, ITCM y overlays) o en los reemplazos registrados con
 * st_override() (para sustituir funciones del juego/SDK por código nativo).
 */
#ifndef STRECOMP_H
#define STRECOMP_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Índices de banco de registros por modo */
enum { ST_BANK_USR = 0, ST_BANK_FIQ, ST_BANK_IRQ, ST_BANK_SVC, ST_BANK_ABT, ST_BANK_UND, ST_BANK_COUNT };

typedef struct StCpu {
    uint32_t r[16];           /* registros visibles en el modo actual */
    uint32_t n, z, c, v, q;   /* flags (0/1) */
    uint32_t cpsr_ctrl;       /* modo, bits I/F (el bit T no se guarda: lo da el código) */
    uint32_t spsr;            /* SPSR del modo actual */
    uint32_t next_pc;         /* dirección a la que "volvió" la última función */
    void* user;
    /* Copias de los registros con banco de los modos que no están activos */
    uint32_t bank_r13[ST_BANK_COUNT], bank_r14[ST_BANK_COUNT], bank_spsr[ST_BANK_COUNT];
    uint32_t usr_r8_12[5], fiq_r8_12[5];
} StCpu;

typedef void (*StFn)(StCpu*);

/* sig: FNV-1a de los primeros bytes de la función (ST_SIG_BYTES, sin pasar del final del
 * módulo) tal como están en la ROM; solo se usa en overlays para saber cuál está cargado. */
typedef struct { uint32_t addr; uint32_t thumb; StFn fn; uint32_t sig; } StFnEntry;
#define ST_SIG_BYTES 32u
typedef struct {
    const char* name;
    int overlay;              /* -1 = siempre residente */
    uint32_t start, end;
    const StFnEntry* fns;
    unsigned count;
} StModule;

/* Tablas generadas por stk recomp */
extern const StModule st_modules[];
extern const unsigned st_module_count;

/* ---- Plataforma: el port rellena esto (IO, BIOS, coprocesador...) ---- */
typedef struct StPlatform {
    uint32_t (*io_read)(StCpu*, uint32_t addr, int size);
    void (*io_write)(StCpu*, uint32_t addr, uint32_t value, int size);
    void (*swi)(StCpu*, uint32_t num);
    void (*unimpl)(StCpu*, uint32_t addr, uint32_t insn, int thumb);
    void (*miss)(StCpu*, uint32_t addr);       /* función no encontrada */
    void (*halt)(StCpu*, const char* why);
    /* Coprocesador distinto de CP15 (en el ARM946E-S no existe: el hardware daría
     * "undefined"). read=1 para MRC (el valor devuelto va a Rd). */
    uint32_t (*coproc)(StCpu*, int cp, int op1, int crn, int crm, int op2, uint32_t v, int read);
    void (*bkpt)(StCpu*, uint32_t num);
} StPlatform;

extern StPlatform st_platform;

/* ---- Memoria ---- */
#define ST_MAIN_RAM_SIZE (4u * 1024 * 1024)
extern uint8_t st_main_ram[ST_MAIN_RAM_SIZE];
extern uint8_t st_itcm[32 * 1024];
extern uint8_t st_dtcm[16 * 1024];
extern uint8_t st_wram[32 * 1024];
extern uint8_t st_palette[2 * 1024];
extern uint8_t st_oam[2 * 1024];
extern uint8_t st_vram[656 * 1024];
extern uint32_t st_dtcm_base;

uint8_t* st_ptr(uint32_t addr, uint32_t size);   /* NULL si es IO o no mapeado */
uint32_t st_read8(StCpu* c, uint32_t addr);
uint32_t st_read16(StCpu* c, uint32_t addr);
uint32_t st_read32(StCpu* c, uint32_t addr);
void st_write8(StCpu* c, uint32_t addr, uint8_t v);
void st_write16(StCpu* c, uint32_t addr, uint16_t v);
void st_write32(StCpu* c, uint32_t addr, uint32_t v);
int st_load(uint32_t addr, const void* data, size_t size);  /* copia datos a memoria emulada */

/* ---- Ejecución ---- */
void st_init(StCpu* c);
void st_call(StCpu* c, uint32_t addr);
void st_override(uint32_t addr, StFn fn);           /* sustituye una función del juego */
/* Indicación forzada de overlay cargado (gana sobre la firma); loaded=0 la quita. */
void st_overlay_set_loaded(int id, int loaded);
int st_overlay_is_loaded(int id);
/* ¿El contenido de la memoria coincide con este overlay en esa función? */
uint32_t st_signature(uint32_t addr, uint32_t end);
const char* st_symbol_for(uint32_t addr);           /* "módulo:función" para depurar */

/* Registro de direcciones no encontradas: se usan como semillas en el siguiente
 * `stk recomp` para que el recompilador aprenda de cada ejecución. */
void st_set_miss_log(const char* path);

/* ---- Sistema ---- */
void st_swi(StCpu* c, uint32_t num);
void st_bkpt(StCpu* c, uint32_t num);
void st_unimpl(StCpu* c, uint32_t addr, uint32_t insn, int thumb);
uint32_t st_mrc(StCpu* c, int cp, int op1, int crn, int crm, int op2);
void st_mcr(StCpu* c, int cp, int op1, int crn, int crm, int op2, uint32_t v);
uint32_t st_get_cpsr(StCpu* c);
void st_msr(StCpu* c, int spsr, int fields, uint32_t v);
void st_restore_cpsr(StCpu* c);
/* Retorno de excepción (MOVS/SUBS pc, LDM {..pc}^): CPSR = SPSR y devuelve el destino
 * con bit0 = Thumb según el bit T restaurado. */
uint32_t st_exception_return(StCpu* c, uint32_t pc);
void st_set_mode(StCpu* c, uint32_t mode);           /* cambia de modo y de banco */
int st_mode_bank(uint32_t mode);
uint32_t st_user_reg(StCpu* c, int r);                 /* LDM/STM con bit S */
void st_set_user_reg(StCpu* c, int r, uint32_t v);

/* ---- Ayudas aritméticas (inline) ---- */
static inline uint32_t st_ror(uint32_t v, uint32_t s) {
    s &= 31;
    return s ? (v >> s) | (v << (32 - s)) : v;
}
static inline uint32_t st_adc_flags(StCpu* c, uint32_t a, uint32_t b, uint32_t cin) {
    uint64_t r = (uint64_t)a + b + cin;
    uint32_t res = (uint32_t)r;
    c->n = res >> 31;
    c->z = res == 0;
    c->c = (uint32_t)(r >> 32);
    c->v = ((~(a ^ b)) & (a ^ res)) >> 31;
    return res;
}
static inline uint32_t st_lsl_c(uint32_t v, uint32_t a, uint32_t* c) {
    if (a == 0) return v;
    if (a < 32) { *c = (v >> (32 - a)) & 1; return v << a; }
    *c = a == 32 ? (v & 1) : 0;
    return 0;
}
static inline uint32_t st_lsr_c(uint32_t v, uint32_t a, uint32_t* c) {
    if (a == 0) return v;
    if (a < 32) { *c = (v >> (a - 1)) & 1; return v >> a; }
    *c = a == 32 ? (v >> 31) : 0;
    return 0;
}
static inline uint32_t st_asr_c(uint32_t v, uint32_t a, uint32_t* c) {
    if (a == 0) return v;
    if (a < 32) { *c = (v >> (a - 1)) & 1; return (uint32_t)((int32_t)v >> a); }
    *c = v >> 31;
    return (uint32_t)((int32_t)v >> 31);
}
static inline uint32_t st_ror_c(uint32_t v, uint32_t a, uint32_t* c) {
    if (a == 0) return v;
    a &= 31;
    if (a == 0) { *c = v >> 31; return v; }
    *c = (v >> (a - 1)) & 1;
    return st_ror(v, a);
}
static inline uint32_t st_clz(uint32_t v) {
    uint32_t n = 0;
    if (!v) return 32;
    while (!(v & 0x80000000u)) { v <<= 1; n++; }
    return n;
}
static inline uint32_t st_qadd(StCpu* c, uint32_t a, uint32_t b) {
    int64_t r = (int64_t)(int32_t)a + (int32_t)b;
    if (r > INT32_MAX) { c->q = 1; return (uint32_t)INT32_MAX; }
    if (r < INT32_MIN) { c->q = 1; return (uint32_t)INT32_MIN; }
    return (uint32_t)r;
}
static inline uint32_t st_qsub(StCpu* c, uint32_t a, uint32_t b) {
    int64_t r = (int64_t)(int32_t)a - (int32_t)b;
    if (r > INT32_MAX) { c->q = 1; return (uint32_t)INT32_MAX; }
    if (r < INT32_MIN) { c->q = 1; return (uint32_t)INT32_MIN; }
    return (uint32_t)r;
}
/* LDR de ARMv5: lectura no alineada => rotación */
static inline uint32_t st_ldr32(StCpu* c, uint32_t addr) {
    uint32_t v = st_read32(c, addr & ~3u);
    return st_ror(v, (addr & 3) * 8);
}

/* ---- Macros usadas por el código generado ---- */
#define ST_RETURN(v) do { c->next_pc = (v); return; } while (0)
#define ST_INDIRECT(v) do { jt = (v); goto st_dispatch; } while (0)
#define ST_TAIL(fn) do { fn(c); return; } while (0)
#define ST_TAIL_DYN(a) do { st_call(c, (a)); return; } while (0)
#define ST_CALL(fn, ret) do { fn(c); if (c->next_pc != (ret)) { ST_TAIL_DYN(c->next_pc); } } while (0)
#define ST_CALL_DYN(a, ret) do { st_call(c, (a)); if (c->next_pc != (ret)) { ST_TAIL_DYN(c->next_pc); } } while (0)

#ifdef __cplusplus
}
#endif
#endif
