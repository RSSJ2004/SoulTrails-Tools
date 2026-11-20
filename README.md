# SoulTrails — herramientas del port

Toolkit para el port a PC de Spirit Tracks. **No contiene código, assets ni
datos del juego**: todo lo que sale de la ROM se genera en tu máquina y está en
`.gitignore`. Nunca subas `assets/`, `build/` ni la ROM.

| Pieza | Qué hace |
|---|---|
| `stk.py` | CLI que automatiza todo y **guarda el estado en `.stk/`** para no perder el contexto. |
| `sttools/assets.py` | Extractor de assets: descomprime, desempaqueta y convierte a PNG/JSON/WAV. |
| `sttools/recomp/` | **Recompilador estático** ARM/Thumb → C (ARMv5TE, la CPU ARM9 de la DS). |
| `runtime/` | Runtime en C del código recompilado (CPU, mapa de memoria DS, despachador). |
| `frontend/` | App SDL2 + ImGui: dos pantallas con layout ajustable y **controles configurables**. |
| `ghidra_scripts/` | Exportación headless de funciones (pseudo-C + ARM/Thumb). |

## 1. Primera vez

```bash
python stk.py init --rom /ruta/a/tu_rom.nds [--decomp ../SoulTrails]
python stk.py setup      # descarga Ghidra, NTRGhidra y objdiff-cli en tools_bin/
python stk.py doctor
```

Requisitos: Python 3.10+. Para el resto: JDK 21 (Ghidra), CMake + compilador C/C++ (recomp y frontend), SDL2 (frontend).

## 2. Assets

```bash
python stk.py rom        # cabecera, overlays, segmentos del ARM9
python stk.py assets     # todo a assets/
```

```
assets/raw/          NitroFS tal cual
assets/unpacked/     LZ10/LZ11 descomprimido, NARC y SDAT expandidos (recursivo)
assets/converted/    PNG / JSON / WAV
assets/code/         arm9 descomprimido (BLZ) + ITCM/DTCM, arm7, overlays descomprimidos, layout.json
assets/manifest.json qué es cada archivo (tipo, sha1, compresión, salidas, errores)
assets/report.txt    resumen y lista de errores
```

| Formato | Resultado |
|---|---|
| NSBTX / NSBMD (texturas) | PNG por textura; los 7 formatos de la DS (paleta 4/16/256, A3I5, A5I3, 4x4 comprimido, directo) |
| NCLR / NCGR / NSCR | paleta, hoja de tiles y pantalla compuesta en PNG |
| BMG | JSON con todos los mensajes (códigos de control como `{hex}`) |
| SDAT | SSEQ/SBNK/SWAR/STRM con su nombre real |
| SWAR / SWAV / STRM | WAV (PCM8, PCM16, IMA-ADPCM) |
| NSBMD (geometría), NSBCA/NSBTA (animación), SSEQ (música) | se guardan desempaquetados; los conversores a glTF/MIDI vienen después |

Si `report.txt` muestra errores, pásamelos: cada uno lleva el archivo y la causa.

## 3. Recompilación estática

```bash
python stk.py ghidra import && python stk.py ghidra export   # opcional, mejora la detección de funciones
python stk.py recomp --compile
```

Genera `build/recomp/*.c` (código traducido de **tu** ROM, no se sube), comprueba que compila y
crea el ejecutable `soultrails_port`, que **lee tu ROM al arrancar**:

```bash
build/recomp-build/soultrails_port --rom /ruta/a/tu_rom.nds --misses .stk/recomp_misses.txt
```

Arranca como el hardware (ARM9 tal cual está en el cartucho y el crt0 del juego hace la
descompresión, los autoloads y el BSS). Sin la capa de plataforma, el juego avanza hasta lo
primero que la necesite.

Cómo funciona:
- Cada función ARM o Thumb pasa a una función C que opera sobre `StCpu` (registros y flags) y
  accede a memoria con `st_read*/st_write*` (mapa de memoria de la DS en `runtime/strecomp.c`).
- Las funciones se descubren desde el punto de entrada, las llamadas, la exportación de Ghidra,
  los prólogos `stmdb sp!, {..., lr}`, los punteros a código guardados en datos y **los fallos
  aprendidos**.
- Binario estático, ITCM y cada overlay son módulos separados; las llamadas a otro overlay pasan
  por el despachador, que **elige el overlay comparando la memoria con una firma** de cada
  función (no hace falta saber dónde está `FS_LoadOverlay`).
- **Aprende de cada ejecución:** si el código salta a una dirección que no se tradujo, el runtime
  la apunta en `.stk/recomp_misses.txt` (con la firma de la memoria, para saber de qué overlay
  es); el siguiente `stk recomp` la incluye.
- `--hook <dir>` + `st_override()` permiten **sustituir funciones del juego por código nativo**
  (gráficos, sonido, archivos…). Ahí se conectará la capa de plataforma.

Validación (`tests/test_recomp.py`): se compila C real para ARM946E-S (ARM + Thumb, interworking,
tablas de saltos, punteros a función, aritmética de 64 bits, coma flotante por software de libgcc),
se recompila y se compara con el mismo código nativo en -O0, -O2 y -Os.

## 4. Frontend: pantallas y controles

```bash
cd frontend && cmake -S . -B build -G Ninja && cmake --build build
./build/layout_test && ./build/input_test && ./build/soultrails
```

Menú (F1 o botón Guide del mando), pestañas **Pantallas** y **Controles**.

**Mover a Link con la palanca izquierda:** la palanca genera un toque virtual alrededor de Link,
como mantener el lápiz. Poca inclinación = caminar, inclinación completa = correr. Ajustable:
zona muerta, radio al caminar/correr, curva de respuesta, suavizado e invertir eje. Cuando la
plataforma conozca la posición real de Link en pantalla, el toque se centra en él.

**Acciones táctiles con un botón:** tajo (orientado con la palanca), ataque giratorio (círculo),
voltereta (doble toque en el borde), tocar a Link, y un **cursor** con la palanca derecha para
apuntar y tocar (bumerán, objetos, menús). Micrófono (flauta/soplar) en una tecla o gatillo.

**Asignación:** cada acción admite 3 asignaciones (teclado, botón, eje o ratón). Clic en la
casilla y pulsa la tecla o botón, o mueve el eje; Esc cancela. El ratón sigue funcionando como
lápiz y tiene prioridad. Todo se guarda en `controls.ini` y `layout.ini`.

Por defecto: mover WASD / stick izq. · cursor IJKL / stick der. · tocar en cursor Espacio / RT ·
tajo F / X · giro R / Y · voltereta Shift / B · tocar a Link C / A · micrófono M / LT ·
botones DS: A=X, B=Z, X=V, Y=G, L=Q, R=E, Start=Enter, Select=Retroceso, cruceta=flechas.

## 5. Decompilación (flujo diario)

```bash
python stk.py status         # dónde te quedaste
python stk.py loop           # build -> progreso -> marca 100 % -> nuevas fichas, al guardar
python stk.py next -n 10 --unit ov024 --max-size 200
python stk.py journal "nota"
```

## Tests

```bash
python -m unittest discover -s tests   # assets, recomp (necesita arm-none-eabi-gcc), pipeline
```

## Pendiente

- Capa de plataforma: GPU 2D/3D, sonido, archivos y BIOS sobre la API de hooks del runtime.
- Posición de Link para el control por palanca (lectura de memoria del juego).
- Conversores NSBMD → glTF, animaciones y SSEQ → MIDI.
