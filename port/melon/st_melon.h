/* Port híbrido con melonDS: interfaz C para port/main.c y el frontend.
 * Parte del port híbrido con melonDS (GPL-3.0, ver LICENSE). */
#ifndef ST_MELON_H
#define ST_MELON_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct StCpu;
struct StRom;

/* Crea la consola con la ROM (y el .sav si existe), arranque directo; 0 si va bien */
int st_melon_open(struct StCpu* c, const struct StRom* rom, const char* rom_path, const char* save_path,
                  int language, char* err, size_t errlen);
void st_melon_run_frame(void);                         /* un cuadro de la DS (59,8261 Hz) */
const uint32_t* st_melon_screen(int bottom);           /* 256x192, 0 = superior */
void st_melon_set_keys(uint32_t pressed);              /* bits A B Sel Start Der Izq Arr Abj R L X Y */
void st_melon_touch(int down, int x, int y);
int st_melon_audio(int16_t* out, int frames);          /* estéreo 16 bits */
void st_melon_close(void);

#ifdef __cplusplus
}
#endif
#endif
