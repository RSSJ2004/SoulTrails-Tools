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
Todos los tests pasan (`python -m unittest discover -s tests` → 23 OK; además
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
runtime/st_rom.[ch]    lector de ROM y arranque (st_boot_run)
runtime/st_bios.c      IME/IE/IF, entrada a IRQ como el BIOS, SWI del BIOS por HLE
port/main.c            ejecutable soultrails_port --rom X --misses F
runtime/strecomp.[ch]  StCpu, mapa de memoria DS (RAM 4MB, ITCM, DTCM dinámica vía CP15, WRAM, paleta, VRAM, OAM), st_call/despachador, st_override (reemplazos nativos), overlays cargados, registro de fallos, StPlatform (callbacks IO/SWI/unimpl/miss)
port/CMakeLists.txt    compila runtime + build/recomp + soultrails_port
frontend/              SDL2 + Dear ImGui 1.91.5 (FetchContent)
  layout.*             cálculo de layout (vertical/horizontal/grande+pequeña/una), rotación, escala entera, WindowToTouch/TouchToWindow, layout.ini
  input.*              InputMapper sin SDL: acciones, 3 asignaciones por acción, palanca→toque, gestos (tajo, giro, voltereta, tocar Link, tocar cursor), cursor con palanca derecha, micrófono, controls.ini
  main.cpp             app: pestañas Pantallas/Controles, "pulsa para asignar", mando con hotplug, lápiz virtual; ScreenSource = interfaz que implementará el juego (hoy TestPattern)
ghidra_scripts/ExportFunctions.java  exporta {name, addr, size, block, thumb, c}
tests/                 learn/ (prog.c para --learn), bios/ (bios.c, bios_thumb.c) + test_bios, boot/ (crt0.S, game.c, game.ld, ov0/ov1.c, harness.c) + test_boot, cpu/ (modes.S, v5rules.S, harness.c, modes_expected.txt) + test_cpu_modes, test_discover (descubrimiento con ARM sintético a mano, tiempo), builders.py (archivos DS sintéticos y compresores), test_assets, test_recomp (compila C con arm-none-eabi-gcc, recompila y compara con nativo en -O0/-O2/-Os, con bucle de aprendizaje de fallos), test_pipeline (ROM sintética con código + overlay BLZ por todo stk)
```

### Comandos de stk
`init --rom X [--decomp Y]`, `doctor`, `setup` (descarga Ghidra, NTRGhidra, objdiff-cli),
`rom`, `assets`, `recomp [--compile --learn --max-frames N --hook ADDR --seeds F --scan-thumb]`,
`ghidra import|export|open`, `build`, `progress`, `next`, `loop`, `status`, `journal`, `task`.

## Decisiones de diseño
1. **El port lee la ROM del usuario en tiempo de ejecución** (`soultrails_port --rom juego.nds`),
   como N64Recomp: no se empaqueta nada del juego en el ejecutable salvo el código traducido
   (que ya se genera en local). Arranque "como el hardware": cabecera en `0x027FFE00`, ARM9 **tal
   cual está en el cartucho** (comprimido con BLZ) en `arm9_ram`, modo SVC con IRQ/FIQ
   desactivadas, y se llama a la entrada. El **crt0 del propio juego** (recompilado) hace la
   descompresión, los autoloads (ITCM/DTCM), el BSS y los constructores estáticos. Así no hay
   que replicar a mano lo que hace el SDK y se valida a la vez la detección de ModuleParams.
   Lo que el BIOS/firmware deja en `0x027FF800-0x027FFFFF` (IDs de cartucho, ajustes de usuario)
   queda pendiente para la capa de plataforma.
2. **Overlays detectados por contenido, sin enganchar funciones del SDK.** Cada función de un
   overlay guarda en su tabla una firma (FNV-1a de sus primeros 32 bytes según la ROM). Cuando
   `st_call` cae en una dirección que cubren varios overlays (o uno solo), elige el que coincide
   con lo que hay en memoria en ese momento; si ninguno coincide es un fallo (overlay no cargado).
   El juego carga, descomprime y ejecuta los `sinit` de sus overlays él solo (en cuanto la
   plataforma emule las lecturas de cartucho), y no hace falta conocer la dirección de
   `FS_LoadOverlay`. `st_overlay_set_loaded()` se mantiene como indicación forzada (gana sobre la
   firma) por si algún hook la necesita. Las llamadas directas dentro del mismo overlay no
   pasan por el despachador (si se está ejecutando ese overlay, está cargado).

## Qué agregar / reparar (por prioridad)

### A. Validar con la ROM real (lo primero)
1. `stk rom`: ahora muestra si el **CRC de la cabecera** (0x15E) coincide. Comprobar que se encuentra ModuleParams (marca `21 06 C0 DE DE C0 06 21`, struct en
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
1. ~~**Ejecutable que arranca**~~ **hecho** (decisión de diseño 1): `runtime/st_rom.[ch]` (lector de
   ROM: cabecera, FAT, tabla de overlays; `st_boot_run`) y `port/main.c` → `soultrails_port --rom
   X --misses F` (códigos de salida: 0 vuelve, 2 ROM, 3 función no traducida, 4 instrucción no
   soportada, 5 detenido). `tests/test_boot.py` arranca una ROM sintética con crt0 propio,
   ModuleParams, ARM9 comprimido (lo descomprime el código invitado), autoload ITCM, BSS y dos
   overlays en la misma dirección; también ejecuta el `soultrails_port` real.
   Pendiente: estado que deja el BIOS/firmware en `0x027FF800+`; la capa de plataforma (C/E).
   - El código generado es una biblioteca CMake OBJECT (el runtime necesita `st_modules` y con
     dos estáticas el orden de enlazado fallaba).
   - Lección (vale para la ROM real): el código que se ejecuta **antes** de descomprimir (crt0 y
     descompresor) lee sus literales de la RAM aún comprimida; en el SDK está en la cabecera sin
     comprimir, así que funciona, pero si `stk rom` muestra un `compressed_static_end` raro, revisar
     esto primero.
2. ~~**Overlays**~~ **hecho** (decisión de diseño 2): firma FNV-1a de 32 bytes en `StFnEntry.sig`;
   `lookup()` elige por contenido. Los fallos se registran como `ADDR arm|thumb sig=XXXXXXXX` y el
   recompilador los asigna al overlay cuya firma coincide (`Program.candidates`); los punteros y
   llamadas hacia overlays desde otros módulos ahora también se siguen (antes se perdían porque
   `module_for` solo mira residentes). Nota para `--learn`: un fallo en una dirección que ya está
   traducida en otro overlay significa "overlay no cargado", no algo nuevo que aprender.
3. **ARM7**: no se recompila. Lo normal es HLE: emular por IPC/FIFO lo que hace el ARM7 (sonido,
   táctil, RTC, micrófono, Wi-Fi no).
4. ~~Interrupciones/VBlank~~ **hecho** (`runtime/st_bios.c`, `tests/test_bios.py`):
   - IME/IE/IF viven en el runtime (`st_io_read/st_io_write` interceptan 0x04000208/210/214;
     escribir 1 en IF reconoce). `st_raise_irq(mask)` las levanta.
   - `st_irq_service`: igual que el BIOS del ARM9 (modo IRQ, SPSR, `push {r0-r3,r12,lr}` en la
     pila IRQ, llamada a `[DTCM+0x3FFC]`, `pop` y retorno restaurando CPSR). Se entrega en
     Halt/IntrWait/VBlankIntrWait. Nuevo `StPlatform.idle`: la plataforma avanza un cuadro y
     levanta las IRQ (por defecto, un VBlank por espera).
   - SWI del BIOS por HLE: Div, Sqrt, CpuSet, CpuFastSet, GetCRC16 (CRC-16/MODBUS), IsDebugger,
     BitUnPack, LZ77 (WRAM/VRAM), Huffman 4/8 bits, RL (WRAM/VRAM), Diff8/Diff16, WaitByLoop,
     Halt, IntrWait, VBlankIntrWait, SoftReset (detiene). Desconocidas → aviso.
   - Pendiente: entregar IRQ también fuera de las esperas (al escribir IME/IE o hacer `msr` que
     desbloquee I, o cada N llamadas) si algún sistema del juego lo necesita; timers y DMA.
   - **Pendiente de diseño: hilos del SDK.** El `OS_IrqHandler` del SDK puede cambiar de hilo
     reescribiendo el marco del BIOS, y `OS_SaveContext/OS_LoadContext` saltan al punto donde
     otro hilo se quedó. En un recompilador estático eso no se puede hacer con una sola pila C.
     Propuesta (como N64Recomp): **una fibra/hilo nativo por hilo del juego**, con un testigo para
     que solo corra uno a la vez; enganchar SaveContext/LoadContext (localizarlos por su patrón
     de instrucciones, no por el código del decomp). Hoy el runtime **detecta** el cambio de hilo
     en la IRQ y se detiene con "cambio de hilo desde la IRQ (falta la capa de hilos)". Decidir
     con el usuario cuando aparezca con la ROM real.
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
8. ~~`stk recomp --learn`~~ **hecho**: recompila, compila `soultrails_port`, lo ejecuta con
   `--max-frames` (cada espera del juego = 1 cuadro con su VBlank) y `--timeout`, y si sale con 3
   y apuntó un fallo nuevo, recompila y repite (`--max-iter`). Se para y explica por qué cuando el
   código de salida es otro o el fallo ya era conocido (típico: overlay no cargado porque falta
   la capa de plataforma). Probado en `tests/test_pipeline.py::test_stk_learn` (3 funciones que
   solo se alcanzan con direcciones calculadas).

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
