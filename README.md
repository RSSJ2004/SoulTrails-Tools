# SoulTrails — herramientas del port

Toolkit independiente para trabajar en el port de Spirit Tracks. **No contiene
código, assets ni datos del juego**: todo lo que sale de la ROM se genera en tu
máquina y está en `.gitignore`.

| Pieza | Qué hace |
|---|---|
| `stk.py` | CLI que automatiza lo repetitivo (instalar herramientas, compilar, medir progreso, preparar la siguiente función) y **guarda el estado en `.stk/`** para no perder el contexto. |
| `ghidra_scripts/ExportFunctions.java` | Script headless de Ghidra: exporta el pseudo-C de cada función a `ghidra_export/functions.jsonl`. |
| `frontend/` | App SDL2 + Dear ImGui con las dos pantallas y un **menú de layout** (F1). |

## 1. Primera vez

```bash
python stk.py init --decomp ../SoulTrails --rom /ruta/a/tu_rom.nds
python stk.py setup      # descarga Ghidra, NTRGhidra y objdiff-cli en tools_bin/
python stk.py doctor     # comprueba que todo está listo
```

Requisitos: Python 3.10+, JDK 21 (para Ghidra), Ninja, y lo que pida `INSTALL.md` del repo decomp.

## 2. Flujo diario

```bash
python stk.py status         # dónde te quedaste: progreso, fichas abiertas, bitácora
python stk.py loop           # deja esto corriendo
```

`loop` repite automáticamente:
1. `ninja` en el repo decomp,
2. `objdiff-cli report` → % de match y histórico en `.stk/progress_history.jsonl`,
3. marca como **hechas** las funciones que llegaron al 100 %,
4. mantiene N fichas abiertas en `work/` con la siguiente función a atacar
   (dirección, tamaño, % actual y el pseudo-C de Ghidra como referencia).

Cada vez que guardas un `.c/.cpp` en `src/`, vuelve a compilar y medir solo.

Otros comandos:

```bash
python stk.py next -n 10 --unit ov024 --max-size 200   # fichas filtradas
python stk.py next --closest                           # las que están casi al 100 %
python stk.py task func_B skipped                      # aparcar una función
python stk.py journal "ov024: la struct de Link empieza en 0x20"
python stk.py rom info | rom extract                   # cabecera / NitroFS + overlays a assets/
python stk.py ghidra import && python stk.py ghidra export
python stk.py ghidra open                              # abre la GUI de Ghidra
```

En Windows puedes usar `stk.bat <comando>`; en Linux/macOS, `./stk <comando>`.

### Qué se guarda (contexto persistente)

```
.stk/config.json            rutas, versión de ROM, herramientas
.stk/state.json             fichas: in_progress / done / skipped
.stk/journal.md             bitácora automática + tus notas
.stk/progress_history.jsonl un registro por medición
.stk/report.json            último informe de objdiff
.stk/ghidra_project/        proyecto Ghidra (analizado una sola vez)
work/*.md                   fichas de trabajo
```

## 3. Frontend (layout de pantallas)

```bash
cd frontend
cmake -S . -B build -G Ninja      # necesita SDL2 (apt install libsdl2-dev / vcpkg / brew)
cmake --build build
./build/layout_test               # tests del cálculo de layout
./build/soultrails
```

Menú **Pantallas** (F1):
- Layout: vertical, horizontal, grande + pequeña, una pantalla
- Intercambiar pantallas (F2), separación en píxeles
- Proporción y posición de la pequeña (modo grande + pequeña)
- Rotación 0/90/180/270
- Escala entera (pixel perfect), filtro nítido/suavizado, color de fondo
- Se guarda solo en `layout.ini` (carpeta de preferencias de SDL)

Teclas: F1 menú · F2 swap · F3 siguiente layout · Tab alterna pantalla (modo una pantalla) · F11 pantalla completa.

El ratón/táctil se traduce a coordenadas de la pantalla inferior (0–255, 0–191)
en cualquier layout y rotación. El juego se conecta implementando `ScreenSource`
en `frontend/src/main.cpp` (entrega dos framebuffers 256×192 ARGB y recibe el toque).
Ahora mismo muestra un patrón de prueba.

## Notas

- NTRGhidra debe coincidir con tu versión de Ghidra. Si `setup` instala una que
  Ghidra rechaza, compílala desde su repo o instala una versión de Ghidra compatible.
- `rom extract` guarda arm9 y los overlays tal cual (pueden estar comprimidos en BLZ).
