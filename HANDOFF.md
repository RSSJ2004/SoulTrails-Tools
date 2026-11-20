# Traspaso de contexto — SoulTrails-Tools

Documento para continuar el proyecto en otra sesión (Claude Code u otra persona).

## Objetivo del usuario
Port a PC de *The Legend of Zelda: Spirit Tracks* (NDS) mediante **recompilación estática**, en el
estilo de N64Recomp / Ship of Harkinian: cada usuario aporta su propia ROM y nunca se distribuyen
assets, código ni binarios del juego. El usuario habla español; toda la UI, docs y mensajes están en
español.

Peticiones explícitas del usuario:
1. Herramientas automáticas que él ejecuta, para que lo repetitivo se haga solo y **no se pierda
   el contexto** (estado persistente en disco).
2. Recompilación estática (elegida en lugar de esperar a que termine la decompilación).
3. **Extraer bien los assets.**
4. Layout de dos pantallas ajustable desde un menú.
5. Controles excelentes de configurar; **el movimiento táctil de Link se traduce a la palanca
   izquierda**.
6. Capa de plataforma y mejoras del port: **más adelante** (aún no empezadas).

## Repositorios
- `RSSJ2004/SoulTrails-Tools` (**este repo**, rama `main`): todo el código propio.
- `RSSJ2004/SoulTrails`: fork de `zeldaret/st` (decompilación). **No se ha modificado ni analizado
  su código.** Sus `CLAUDE.md`/`AGENTS.md` traían un aviso de la iniciativa "no-agents.md" de los
  mantenedores de zeldaret pidiendo que los agentes de IA no lean ni modifiquen ese repositorio. El
  usuario borró esos archivos de su fork, pero en la sesión anterior se decidió respetar la
  petición de los autores: el toolkit es independiente y solo **invoca** las herramientas del repo
  decomp (`tools/configure.py`, `ninja`, `objdiff`) en la máquina del usuario. La siguiente sesión
  debería mantener ese criterio o hablarlo con el usuario.

## Estado actual
Todos los tests pasan (`python -m unittest discover -s tests` → 19 OK; además
`frontend/build/layout_test` e `input_test`). **Nada se ha probado aún con la ROM real**: el usuario
va a hacerlo.

### Estructura
```
stk.py                 CLI principal (solo stdlib). Estado en $STK_HOME/.stk (por defecto junto a stk.py)
sttools/nds.py         ROM NDS: cabecera, NitroFS, overlays, ModuleParams, BLZ, LZ10/LZ11, NARC, segmentos ARM9 (estático/ITCM/DTCM)
sttools/formats.py     TEX0 (7 formatos) → PNG, NCLR/NCGR/NSCR → PNG, BMG → JSON, SDAT, SWAR/SWAV/STRM → WAV (IMA-ADPCM)
sttools/imaging.py     RGB555, escritor/lector PNG sin dependencias
sttools/assets.py      pipeline: raw/ unpacked/ converted/ code/ manifest.json report.txt
sttools/recomp/        recompilador estático
  insn.py              Ins (kind: next/branch/call/ret/indirect/table/undef), condiciones
  arm.py               ARMv5TE → C (data-proc, mul/long/DSP, Q*, LDR/STR/H/SB/SH/D, LDM/STM, B/BL/BLX/BX, SWI, MRC/MCR, MRS/MSR, CLZ, SWP, PLD)
  thumb.py             Thumb ARMv5T → C (incl. BL/BLX de 32 bits, bx pc = veneer a ARM)
  program.py           módulos, descubrimiento (semillas, llamadas, prólogos ARM, escaneo de punteros con filtro "sin instrucciones indefinidas"), tablas addls pc,pc
  codegen.py           una función C por función guest; goto para saltos internos, ST_CALL directo si el destino se resuelve (mismo módulo o estático), si no por el despachador; switch de despacho para saltos indirectos
  driver.py            entrada (--rom o --bin), semillas de Ghidra/archivos/fallos
runtime/strecomp.[ch]  StCpu, mapa de memoria DS (RAM 4MB, ITCM, DTCM dinámica vía CP15, WRAM, paleta, VRAM, OAM), st_call/despachador, st_override (reemplazos nativos), overlays cargados, registro de fallos, StPlatform (callbacks IO/SWI/unimpl/miss)
port/CMakeLists.txt    compila runtime + build/recomp (solo biblioteca por ahora)
frontend/              SDL2 + Dear ImGui 1.91.5 (FetchContent)
  layout.*             cálculo de layout (vertical/horizontal/grande+pequeña/una), rotación, escala entera, WindowToTouch/TouchToWindow, layout.ini
  input.*              InputMapper sin SDL: acciones, 3 asignaciones por acción, palanca→toque, gestos (tajo, giro, voltereta, tocar Link, tocar cursor), cursor con palanca derecha, micrófono, controls.ini
  main.cpp             app: pestañas Pantallas/Controles, "pulsa para asignar", mando con hotplug, lápiz virtual; ScreenSource = interfaz que implementará el juego (hoy TestPattern)
ghidra_scripts/ExportFunctions.java  exporta {name, addr, size, block, thumb, c}
tests/                 cpu/ (modes.S, v5rules.S, harness.c, modes_expected.txt) + test_cpu_modes, test_discover (descubrimiento con ARM sintético a mano, tiempo), builders.py (archivos DS sintéticos y compresores), test_assets, test_recomp (compila C con arm-none-eabi-gcc, recompila y compara con nativo en -O0/-O2/-Os, con bucle de aprendizaje de fallos), test_pipeline (ROM sintética con código + overlay BLZ por todo stk)
```

### Comandos de stk
`init --rom X [--decomp Y]`, `doctor`, `setup` (descarga Ghidra, NTRGhidra, objdiff-cli),
`rom`, `assets`, `recomp [--compile --hook ADDR --seeds F --scan-thumb]`,
`ghidra import|export|open`, `build`, `progress`, `next`, `loop`, `status`, `journal`, `task`.

## Qué agregar / reparar (por prioridad)

### A. Validar con la ROM real (lo primero)
1. `stk rom`: comprobar que se encuentra ModuleParams (marca `21 06 C0 DE DE C0 06 21`, struct en
   marca−0x1C), que el ARM9 se descomprime bien (BLZ) y que salen los autoloads ITCM/DTCM con
   direcciones razonables (ITCM ~0x01FF8000, DTCM ~0x027E0000).
2. `stk assets`: revisar `report.txt`. Probables: formatos propios del juego, BMG con variantes
   (encoding, MID1, tamaño de entrada INF1), NCGR con tiles_x=0xFFFF, archivos LZ con cabecera
   extra. Comprobar visualmente algunas texturas (emparejamiento textura↔paleta es heurístico:
   `nombre_pl`, mismo índice o primera).
3. `stk recomp --compile`: número de funciones, instrucciones no soportadas, tiempo (Python puede
   ser lento con el escaneo de punteros: perfilar y, si hace falta, cachear o paralelizar).
4. `stk setup`: comprobar URLs y nombres de assets de las releases (NTRGhidra debe coincidir con la
   versión de Ghidra; objdiff-cli por plataforma). `objdiff-cli report generate` se llama sin
   `-f`; verificar el formato JSON real (campos `measures`, `units[].functions[]`,
   `metadata.virtual_address`) y ajustar `stk progress/next`.

### B. Recompilador
1. **Ejecutable que arranca**: cargar segmentos (estático, ITCM, overlays) en memoria con
   `st_load`, poner SP/modo, llamar a la entrada ARM9. Requiere la capa de plataforma (C).
2. **Overlays**: detectar carga/descarga (hook de las funciones FS_LoadOverlay/FS_UnloadOverlay del
   SDK con `st_override`) y llamar a `st_overlay_set_loaded`; ejecutar los `sinit` de cada overlay.
3. **ARM7**: no se recompila. Lo normal es HLE: emular por IPC/FIFO lo que hace el ARM7 (sonido,
   táctil, RTC, micrófono, Wi-Fi no).
4. Interrupciones/VBlank: `SWI WaitByLoop/IntrWait/VBlankIntrWait` deben ceder al frontend y
   ejecutar los handlers IRQ del juego (direcciones guardadas en DTCM).
5. ~~Huecos conocidos~~ **hecho** (`tests/test_cpu_modes.py`):
   - Bancos de registros por modo en `StCpu` (`bank_r13/r14/spsr`, `usr_r8_12/fiq_r8_12`);
     `st_set_mode` los cambia, y `st_msr` lo llama al cambiar el modo. En modo usuario MSR solo
     toca flags.
   - LDM/STM con bit S: sin PC usan los registros de usuario (`st_user_reg`); con PC es retorno
     de excepción (`st_exception_return`: CPSR=SPSR y vuelve a Thumb si T=1). Igual para
     `MOVS/SUBS pc, ...`.
   - Base en la lista (ARMv5, GBATEK): STM guarda siempre la base antigua; LDM escribe la base
     si es el único registro o no es el último, si no gana el valor cargado.
   - MRC/MCR a un coprocesador ≠ CP15 → `st_platform.coproc` (por defecto avisa una vez y
     devuelve 0); `bkpt` ARM y Thumb → `st_platform.bkpt` (por defecto `halt`). CDP/LDC/STC
     siguen como indefinidas (el ARM946E-S no tiene coprocesadores que las acepten).
   - **Referencia: QEMU `-cpu arm946`** (la misma CPU) ejecuta `tests/cpu/modes.S` con
     semihosting; su salida está en `tests/cpu/modes_expected.txt` y, si `qemu-system-arm` está
     instalado, el test la vuelve a generar y compara. Lo que QEMU no modela como el hardware
     (`tests/cpu/v5rules.S`) se compara con valores de GBATEK. Esto sirve también para futuras
     pruebas diferenciales de instrucciones (p. ej. flags con entradas aleatorias).
   - Nota: el bit T no se guarda en `cpsr_ctrl`; lo determina el código en ejecución.
6. Rendimiento:
   - ~~Descubrimiento en Python (escaneo de punteros)~~ **hecho**: trazados memorizados por
     (módulo, dirección, modo), candidatos descartados en cuanto aparece una instrucción
     indefinida, se salen del módulo, superan el máximo de instrucciones o **empiezan con una
     instrucción condicional**; la pasada final reutiliza el trazado libre si ningún salto cae en
     otra función. Sintético de 1,4 MB: 8,3 s → 1,7 s y falsos positivos desde datos del 3 % al
     0,3 % (`tests/test_discover.py`). Pendiente medir con la ROM real (A.3).
   - Código generado: los registros viven en `c->r[]`; opciones futuras: cachear en variables
     locales por función, plegar literales constantes de la ROM, eliminar cálculos de flags muertos.
7. Tablas de saltos Thumb de MWCC (el compilador del juego): identificar su patrón con la ROM real
   y tratarlo como `TABLE` en lugar de depender del aprendizaje de fallos.
8. Opción para reproducir el bucle de aprendizaje automáticamente (`stk recomp --learn` que lanza
   el port, recoge `.stk/recomp_misses.txt` y recompila hasta que no haya fallos).

### C. Assets
1. NSBMD → glTF (geometría por display lists del GX, materiales, huesos), NSBCA/NSBTA/NSBTP →
   animaciones glTF.
2. SSEQ → MIDI + SBNK/SWAR → SoundFont (SF2).
3. NCER/NANR (sprites y animaciones 2D) → PNG/JSON.
4. NFTR (fuentes) → PNG + JSON.
5. Visor de assets (ImGui) y empaquetado a un archivo único para el port (tipo `.o2r`).
6. `report.txt` con resumen de "no convertidos" por tipo, no solo errores.

### D. Controles
1. **Posición real de Link** (`ScreenSource::LinkTouchPosition`): leer la posición del jugador en
   memoria y proyectarla a la pantalla táctil; hoy se usa un punto fijo configurable.
2. Verificar en el juego real los gestos (duraciones, radio del giro, forma del doble toque de la
   voltereta) y el umbral caminar/correr.
3. Contexto: en el tren el control táctil es distinto (palanca de velocidad, silbato, cañón):
   perfiles de control por contexto (a pie / tren / menú) detectados por estado del juego.
4. Perfiles guardables, vibración, iconos de mando en vez de texto, navegación de menús con mando.
5. Micrófono real (SDL audio capture) como alternativa a la tecla.

### E. Capa de plataforma y mejoras (pendiente, pedido "después")
GPU 2D (fondos/sprites/ventanas/blend) y 3D (GX → OpenGL/Vulkan, a resolución alta), sonido
(mezclador de canales DS + reproductor SSEQ), archivos (FS del SDK sobre los assets extraídos),
guardado, 60 FPS, panorámico, filtros, remapeo completo y mods de texturas.

## Cómo trabajar en este repo
- Python 3.10+ solo stdlib. C11 para el runtime, C++17 para el frontend.
- Tests: `python -m unittest discover -s tests` (requiere `arm-none-eabi-gcc`, `cc`, `cmake`;
  opcional `qemu-system-arm` para regenerar la referencia de `test_cpu_modes`)
  y `cd frontend && cmake -S . -B build -G Ninja && cmake --build build && ./build/layout_test && ./build/input_test`.
- Nada derivado de la ROM se sube (`.gitignore` cubre `assets/`, `build/`, `.stk/`, `*.nds`...).
- Las pruebas usan solo datos sintéticos generados por `tests/builders.py`.
