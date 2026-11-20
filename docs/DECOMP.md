# Decompilación (`RSSJ2004/SoulTrails`): contexto, estado y pasos

## Contexto
- `RSSJ2004/SoulTrails` es un fork de `zeldaret/st`, la decompilación con *matching* de
  Spirit Tracks (C/C++ que, compilado con el compilador original MWCC, produce exactamente los
  mismos bytes que la ROM). Avance aproximado: **~11 %**.
- Los mantenedores de zeldaret piden (iniciativa *no-agents.md*) que los agentes de IA no lean ni
  modifiquen ese repositorio. Acordado con el usuario: **el toolkit no lee su código**. Solo
  ejecuta sus herramientas en la máquina del usuario (`tools/configure.py`, `ninja`, `objdiff-cli`)
  y usa los **metadatos** del informe de objdiff: nombre, dirección, tamaño y % de match de cada
  función (`stk recomp` los escribe en `.stk/decomp_symbols.txt`).
- Para qué sirve al port: nombres en el código recompilado y en los fallos, direcciones exactas
  para sustituir funciones (`st_override`: hilos del SDK, sistema de archivos, GX, recorte para el
  panorámico...) y, a largo plazo, reemplazar el código traducido por el código fuente del decomp
  donde ya esté emparejado.
- **El port no depende del decomp**: la recompilación estática funciona sin él. Se puede dejar
  para después en cualquier momento.

## Estado en la máquina del usuario (Windows 10, 2026-11)
| Paso | Estado |
|---|---|
| Clon en `C:\Users\RSSJ\Desktop\SoulTrails` | hecho |
| `stk init --decomp ...` | hecho (en `.stk/config.json`) |
| Ninja instalado | hecho |
| `requests` de Python | hecho (`python -m pip install requests`; el repo no trae `requirements.txt`) |
| `dsd` (herramienta del decomp) | descargado por `ninja` |
| objdiff-cli | descargado por `stk setup` |
| **`ninja` / `stk build`** | **falla**: la regla que regenera `build.ninja` ejecuta `configure.py -v eur && ... delink.py dsd.exe`; en Windows ninja lanza el comando sin `cmd`, así que `&&` llega como argumento a `configure.py` (`unrecognized arguments: && ...`) |

Este diagnóstico sale solo del mensaje de error, sin leer el código del decomp.

## Pasos para terminar de prepararlo

Todos desde una consola `cmd` nueva.

**1. Ejecutar a mano los dos comandos que ninja junta con `&&`** (copiados del error):
```bat
cd /d C:\Users\RSSJ\Desktop\SoulTrails
python tools\configure.py -v eur
python tools\delink.py C:\Users\RSSJ\Desktop\SoulTrails\dsd.exe
ninja
```
Si `build.ninja` queda al día, ninja ya no intentará regenerarlo y compilará. Si vuelve a
intentarlo y falla igual, pasar al paso 1b.

**1b. Alternativa: WSL (Ubuntu en Windows)**, donde `&&` funciona:
```bat
wsl --install -d Ubuntu
```
Dentro de Ubuntu: `sudo apt install python3-pip ninja-build wine` (si el decomp usa wine para
MWCC en Linux), clonar el repo y ejecutar `python3 tools/configure.py -v eur && ninja`.

**1c. Avisar a los autores**: el arreglo correcto es que la regla use `cmd /c "..."` en Windows.
Puedes abrir un issue en `zeldaret/st` con el error de arriba (lo escribes tú: no lo modifico).

**2. Comprobar el ROM base**: `stk build` copia tu ROM a `SoulTrails\extract\baserom_eur.nds`;
si el decomp espera otro nombre, lo dirá en el error (revisa `extract\README.md` del decomp).

**3. Generar el informe y usarlo en el port**, desde `SoulTrails-Tools`:
```bat
cd /d C:\Users\RSSJ\Desktop\SoulTrails-Tools
python stk.py doctor
python stk.py progress
python stk.py recomp --learn
```
`doctor` debe marcar `[OK] objdiff.json generado`; `progress` muestra el % y las funciones
emparejadas; `recomp` imprime `decomp (objdiff): N funciones con dirección`.

## Cómo avanzar la decompilación (flujo diario)
Lo hace una persona (el código lo escriben los colaboradores, no el agente):
```bat
python stk.py status        :: dónde te quedaste
python stk.py next -n 10 --unit ov031 --max-size 200   :: fichas de funciones pequeñas
python stk.py loop          :: al guardar: build -> progreso -> marca las que llegan al 100 %
python stk.py journal "nota"
```
- Cada ficha (`work\*.md`) trae dirección, tamaño, % actual y el pseudo-C de Ghidra como
  referencia (`stk ghidra import/export`, necesita JDK 21).
- Herramientas: objdiff (GUI) para comparar ensamblador, decomp.me para colaborar en funciones
  difíciles, y el *permuter* cuando se atasca en 90-99 %.
- Prioridad útil para el port: funciones que el port tendrá que sustituir o entender (hilos del
  SDK, FS/cartucho, GX, cámara/recorte, posición de Link) y los overlays que más aparezcan en los
  fallos de `--learn`.

## Pendiente en el toolkit relacionado con el decomp
- Usar los nombres de `.stk/decomp_symbols.txt` en los mensajes de `--learn` y del vigilante.
- `stk hook <nombre>`: buscar la dirección por nombre para `st_override`.
- Verificar el formato real del informe de objdiff 3.x (campos `units[].functions[]`,
  `metadata.virtual_address`) en cuanto `stk progress` funcione.
