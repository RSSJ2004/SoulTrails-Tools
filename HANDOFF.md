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
  usuario borró esos archivos de su fork, pero se decidió respetar la petición de los autores: el
  toolkit es independiente y solo **invoca** las herramientas del repo decomp (`tools/configure.py`,
  `ninja`, `objdiff`) en la máquina del usuario. El usuario confirmó usar sus metadatos (nombres,
  direcciones, tamaños, % de match) vía el informe de objdiff, sin leer el código.

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
  jumptable.py         tablas de saltos Thumb por evaluación del bloque de despacho
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
tests/                 learn/ (prog.c para --learn), bios/ (bios.c, bios_thumb.c) + test_bios, boot/ (crt0.S, game.c, game.ld, ov0/ov1.c, harness.c) + test_boot, cpu/ (modes.S, v5rules.S, jt.S, harness.c, modes_expected.txt) + test_cpu_modes, test_discover (descubrimiento con ARM sintético a mano, tiempo), builders.py (archivos DS sintéticos y compresores), test_assets, test_recomp (compila C con arm-none-eabi-gcc, recompila y compara con nativo en -O0/-O2/-Os, con bucle de aprendizaje de fallos), test_pipeline (ROM sintética con código + overlay BLZ por todo stk)
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
**Primeros resultados (EUR, `BKIP`, sha1 9e99cc80…)**: CRC de la cabecera OK; ARM9 en 0x02000000,
entrada 0x02000800, comprimido con BLZ, SDK 0x04027539; ModuleParams encontrado; segmentos
arm9 282048 B, **ITCM 0x01FF8000** (32288 B), **DTCM 0x027E0000** (512 B); 114 overlays; 3442
archivos. `stk assets`: 0 errores en 108 s, pero 9692 `bin` sin identificar (falta ver
`report.txt`). `stk recomp`: 57564 funciones, 2,6 M instrucciones, 12 no soportadas, 31 s.
Avisos "llamada en ARM y Thumb a la vez" en 0x20b6520 de 8 overlays → causados por los punteros
del estático hacia overlays probados en todos los overlays: ahora exigen prólogo. El usuario no
tenía CMake: `stk` ahora lo explica (y `doctor` comprueba CMake y compilador; en Windows usa
Visual Studio si no hay compilador en el PATH).
**Primera compilación con MSVC** (VS 2026 / MSVC 19.51, generador Visual Studio): compila el
runtime y los 145 archivos salvo **un error C2094 (etiqueta no definida)**. Causa: un salto a la
segunda mitad de un BL Thumb de 32 bits decodifica una instrucción "en medio" y la caída del BL
emitía un `goto` sin etiqueta. Arreglado en `codegen.py` (`tests/test_codegen.py`), igual que las
entradas de tabla fuera de la función. También: `/MP /bigobj /wd4102` para MSVC, solo se reescriben
los `.c` que cambian (compilación incremental y evita el `PermissionError` de Windows por archivos
abiertos) y `/nodeReuse:false` en MSBuild. Ojo: la copia local del usuario era un zip antiguo
(compilaba en Debug y no escribía `recomp_warnings.txt`): que actualice antes de seguir.

**`stk recomp --opt fast|medium|release`** (`port/CMakeLists.txt`, `ST_OPT`): la primera compilación
`/O2` con MSVC tardaba >10 min; `--learn` usa `fast` (`/Od`) por defecto. Con la copia actualizada el
recomp dio 31956 funciones (antes 57564 con el zip viejo: probablemente falsos positivos que ya filtra
la regla de prólogo) y 2 "no soportadas" que son texto ASCII ("Titl", "vill"): falta filtrar
candidatos que contienen cadenas.

**Primera ejecución real** (`--opt fast`: compila en 0,4 min): el port arranca y se queda
leyendo `0x04000180` (IPCSYNC, espera al ARM7) hasta el tiempo límite. Nuevo `runtime/st_hw.c`
(`tests/test_hw.py`): registros de E/S con memoria (aviso único por dirección), IPCSYNC con **eco**
del ARM7 HLE (bits 8-11 del ARM9 → bits 0-3), FIFO del PXI (lo enviado se registra y va a
`st_platform.pxi_send`; respuestas con `st_pxi_reply`) y **detector de esperas** (misma E/S leída
2 M veces sin cuadro → `halt` con el nombre del registro, código 5). Siguiente previsible: mensajes
PXI sin respuesta o espera en RAM a que el ARM7 marque sus callbacks listos (zona de trabajo del
sistema al final de la RAM principal) → HLE del ARM7 por tag.

**Tras VCOUNT**: la ejecución agota el tiempo sin ninguna E/S (giro en RAM o bucle). Añadido
**vigilante** al port (`--watchdog S`, código 6; stk lo pasa como `--timeout`-10): vuelca registros
y las últimas 64 funciones en las que se entró (`ST_TRACE` al inicio de cada función generada;
`ST_NO_TRACE` lo quita). Prueba: `tests/test_pipeline.py::Watchdog`.

**Vigilante con la ROM real**: gira en `arm9:020263BC` (ARM; llamada desde `020261F0` ←
`0202641C` ← `020280EC`, antes un bucle de funciones Thumb 0202782x-0202793x). Para saber qué
espera: **sonda de memoria** (el vigilante anota 300 ms de lecturas/escrituras con valores) y
`stk disasm ADDR [--c]` (desensambla con el decodificador del recompilador; `ldr rX, =lit ; [valor]`).
`--learn` guarda la salida entera en `.stk/last_run.txt`.

**Con la fase 1 en la ROM real**: el juego avanza más (crt0 → 02011DA0 → bucle Thumb 0202782x) y
gira releyendo IPCSYNC (11,5 M lecturas y 11 478 escrituras en 300 ms, valor 0): el eco del ARM7
no satisface este segundo protocolo de sincronización. LR=0202B637 → la función que gira es
probablemente la Thumb de `0202B5EC`. La sonda ahora apunta los valores distintos leídos y
escritos y el vigilante dice en qué función está LR (`st_symbol_near`). Siguiente: con los valores
y `stk disasm 0202B5ED`, modelar la respuesta del ARM7 en IPCSYNC.

**Arranque de la FIFO del PXI** (`arm9:0202B5EC`, Thumb, visto con `stk disasm`): pone
IPCFIFOCNT=C408, limpia 027FFF88 (marcas de callbacks PXI del ARM9; las del ARM7 serían
027FFF8C), habilita la IRQ de recepción y entra en un bucle: lee IPCSYNC, copia el valor del ARM7
en su salida, espera hasta 1000 lecturas a que el ARM7 cambie y sale al ver 0 tras más de 4
cambios seguidos. Modelo del ARM7: además del eco, si el ARM9 muestra su mismo valor y lee 64
veces sin escribir, el ARM7 cuenta hacia atrás. Reproducido en `tests/hw/hw_test.c`. Siguiente
probable: espera a que el ARM7 marque sus callbacks en 027FFF8C o mensajes PXI sin respuesta.

**Tras la FIFO**: el juego espera en `arm9:02028188` llamando a `0202B6E0` (r0=0xC) que lee
`027FFF8C` = 0: marcas de tags del PXI registrados por el ARM7. El arranque ahora pone
`st_boot_arm7_tags` (todos) ahí. Siguiente previsible: mensajes PXI que esperan respuesta.

**`--learn` funcionando solo** (12 funciones aprendidas en 12 vueltas; carga el overlay 18 por el
cartucho emulado). Luego gira en `arm9:020300BC` leyendo `02050664`=1 tras mandar PXI tag 5
(RTC) datos 1000h: espera la respuesta del ARM7. Hecho: `runtime/st_arm7.c` (ARM7 HLE por
defecto si la plataforma no pone `pxi_send`): responde mismo tag y datos (resultado 0) y para el
RTC escribe la fecha del PC en BCD en 27FFDE8h (dirección supuesta). Y **IRQ en los saltos hacia
atrás** (`ST_POLL` en bucles del código generado): una espera activa sobre un flag que pone un
callback de IRQ no llama a nadie. Ranura GBA: lecturas a 0 sin avisos repetidos.

**Fase 2 empezada (sin probar con la ROM)**: `runtime/st_vram.c` (bancos A-I con VRAMCNT: BG/OBJ
de cada motor por páginas de 16 KB, LCDC, ranuras de texturas y paletas, paletas extendidas;
`st_ptr` de 0x06 pasa por aquí) y `runtime/st_gpu2d.c` (motores 2D por software, cuadro entero:
fondos de texto/afines/extendidos/bitmap, objetos normales/afines/bitmap, paletas extendidas,
mezcla, brillo y brillo maestro; sin ventanas ni mosaico; BG0 3D transparente). El port guarda
capturas BMP (`--dump-frames N --frames-dir D`); `stk recomp --learn` lo hace cada 60 cuadros en
`.stk/frames`. El frontend exporta NvOptimusEnablement/AmdPowerXpress (fase 2.4).

**Fase 1 de `docs/PLAN.md` (sin probar aún con la ROM)**:
- `runtime/st_dev.c`: cartucho en modo de datos (B7 bloque con redirección <0x8000, B8 ID),
  DMA (inmediata, VBlank, cartucho una palabra por petición, FIFO 3D de golpe), timers con
  recarga/cascada/IRQ sobre un reloj virtual de 33,51 MHz (avanza 2130 ciclos por línea, el
  resto del cuadro en cada espera y 16 ciclos por lectura de timer).
- `st_rom.c`: zona del BIOS/firmware (IDs del chip en 27FF800h/27FFC00h, CRC, indicador de
  arranque 27FFC40h, ajustes de usuario en 27FFC80h con idioma `--lang`, por defecto es, y
  calibración táctil).
- IRQ fuera de las esperas: `st_irq_pending` y `st_irq_poll` en `ST_TRACE` (entrada de cada
  función generada) si el bit I del CPSR está a 0. Puede destapar el cambio de hilo desde la IRQ
  (hilos del SDK → fibras, fase 1.5).
- Pruebas en `tests/hw/hw_test.c`.

**Decomp**: estado y pasos en `docs/DECOMP.md` (en Windows su regla de regenerar `build.ninja`
usa `&&` sin `cmd` y falla; el usuario lo dejó aparcado).

**Símbolos del decomp vía objdiff** (acordado con el usuario: **solo** el informe de
`objdiff-cli report`, nunca leer el código del repo decomp): `stk recomp` escribe
`.stk/decomp_symbols.txt` (`ADDR auto ovNNN # nombre size= match=`) si hay decomp configurado y
objdiff-cli; las semillas `auto` prueban ARM y Thumb y el nombre va al comentario de la función
generada (`tests/test_decomp_symbols.py`). `--no-decomp` lo desactiva. Pendiente: usar los nombres
para los hooks de la capa de plataforma (OS_SaveContext, FS, GX...) y para mostrar fallos.
`/MP` de MSVC limitado por la RAM (1,5 GB por proceso). Plan completo del port en `docs/PLAN.md`.

**Assets propios (`sttools/gamefmt.py`, `tests/test_gamefmt.py`)**: `.nbf*` → PNG (bpp deducido por
la pantalla y la paleta), `.ntf*` → PNG (formato/tamaño supuestos: `notes` en el manifiesto), y
cualquier archivo con magia de 4 mayúsculas → JSON de bloques (`parse_chunks`: cadena
etiqueta+tamaño, con o sin cabecera incluida, anidada; registros y cadenas) y resumen de estructuras
en `report.txt`. Pendiente: ver con la ROM real qué sale y escribir parsers semánticos (ZOLB, BPAM...).

**`report.txt` real, desconocidos (9692)**: `.zob` ZOLB 6476 (listas de objetos por mapa),
`.zcb` BLCM 345, `.zmb` BPAM 305, `.zbcd` DCBZ 181, `.zab` ZCAB 94, `.ztb` BRTM 17 (formatos de mapa
del juego con magia de 4 letras invertida, por bloques); `.bncl/.bnbl/.bnll` JNCL/JNBL/JNLL 812
(disposición de pantallas 2D); `.nbfs/.nbfc/.nbfp` ~900 (pantalla/tiles/paleta **en crudo sin
cabecera**: conversión directa con `imaging`); `.ntft/.ntfp/.ntfi` ~80 (texturas en crudo: el
formato y tamaño hay que deducirlos); `.spa` 61 (partículas SPL); `.stb` 46; `.ncl` NCCL 26.

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
7. ~~Tablas de saltos Thumb~~ **hecho de forma genérica** (`sttools/recomp/jumptable.py`): ante
   `add pc, rX` / `mov pc, rX` / `bx rX` se busca `cmp rI, #N` + `bhi/bhs/bgt/bge` y se **evalúa
   el bloque de despacho** para cada rI en 0..N con un intérprete Thumb mínimo (solo lee datos
   constantes del módulo). No depende del patrón exacto: cubre desplazamientos de 16 bits con
   signo sumados al pc y tablas de palabras absolutas (`tests/cpu/jt.S`, comparado con QEMU). En
   `test_recomp` -O0/-O2 se pasa de 10-11 recompilaciones de aprendizaje a 0. No cubre las tablas
   por llamada a ayudante de GCC (`__gnu_thumb1_case_*`, solo -Os; MWCC no las usa).
   Con la ROM real: contar cuántos `ST_INDIRECT` Thumb quedan sin resolver y mirar su forma.
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
6. ~~`report.txt` con resumen de "no convertidos" por tipo~~ **hecho**: tabla por tipo (total,
   convertidos, sin convertir, errores), ejemplos de lo no convertido y **desconocidos agrupados
   por extensión + cabecera** (pista directa de formatos propios del juego para A.2). El manifiesto
   guarda `head` en los `bin`, y los NCLR/NCGR/NSCR combinados marcan sus salidas o errores.

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
