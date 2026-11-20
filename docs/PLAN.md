# Plan hacia un port excelente

Orden pensado para que cada fase deje algo que se puede ejecutar y medir. Las fases 1-3 son
imprescindibles para ver el juego; 4-7 lo convierten en un buen port; 8-9 lo pulen.

Referencia de hardware del desarrollo: Ryzen 7 6800H (8 núcleos / 16 hilos), 16 GB DDR5,
RTX 3050 Ti 4 GB (+ Radeon 680M integrada). Objetivo mínimo: 60 FPS estables a 1080p con 3D a 4x
de resolución interna en ese equipo, y que funcione también en la integrada.

## Fase 0 — Ciclo de trabajo rápido (ya en marcha)
- `stk recomp --learn` con `--opt fast` (sin optimizar) para iterar; `release` solo para medir.
- `/MP` limitado por la RAM (≈1,5 GB por `cl.exe`): en 16 GB, 10 en paralelo.
- Windows: añadir `build\` a las exclusiones de Microsoft Defender acelera mucho MSVC.
- Probar **clang-cl** (viene con Visual Studio: componente "Clang"): suele compilar funciones
  enormes con muchos `goto` bastante más rápido que `cl.exe` con `/O2`.
- Símbolos del decomp vía objdiff (`.stk/decomp_symbols.txt`): nombres en el C generado y en los
  fallos, y direcciones exactas para hooks, **sin leer el código del decomp**.

## Fase 1 — Que arranque hasta el título (capa de plataforma mínima)
1. **Cartucho**: registros `0x040001A0-0x040001BF` y `0x04100010`, comandos `B7` (lectura) y
   `B8` (ID). Las lecturas salen de la ROM ya abierta. Con esto el juego carga sus overlays y
   archivos él solo (FS del SDK sin tocar).
2. **Zona del firmware `0x027FF800+`**: IDs de cartucho, ajustes de usuario (idioma, nombre),
   cabecera copiada; lo que deja el BIOS al arrancar.
3. **ARM7 por HLE** (IPC `0x04000180`, FIFO `0x04000184/0x04100000`): responder a los mensajes del
   SDK (PXI) para táctil, RTC, energía, micrófono y sonido. Se identifica cada canal por su tag.
4. **Timers y DMA** (`0x04000100+`, `0x040000B0+`): DMA inmediato, en VBlank/HBlank y de FIFO
   3D; IRQ fuera de las esperas (al escribir IME/IE y cada N llamadas).
5. **Hilos del SDK**: una **fibra por hilo del juego** (Windows `CreateFiber`, POSIX
   `ucontext`/hilos con testigo), con `OS_SaveContext`/`OS_LoadContext`/`OS_SwitchThread`
   sustituidos por `st_override` en las direcciones que da objdiff.

Estado (2026-11): hechos IPCSYNC con eco y cuenta atrás, FIFO del PXI con respuesta por tag
(RTC con fecha del PC), marcas de tags del ARM7, IRQ en bucles, OS_Halt, hilos como fibras
(cambio desde la IRQ y por LoadContext), VCOUNT/DISPSTAT,
cartucho B7/B8, DMA, timers, zona del firmware e IRQ fuera de las esperas. Pendiente: respuestas
específicas del ARM7 por tag (táctil, sonido, energía), copia de seguridad.

**Hecho cuando**: `--learn` llega a cientos de cuadros sin fallos y el juego espera input.

## Cambio de rumbo (2026-11): híbrido con melonDS
Tras estudiar los ports de *Phantom Hourglass* (mismo motor), *Dragon Quest IX*, *Jump Super Stars* y
*Pokémon Black*, las fases 1.3 (ARM7), 2 (imagen) y 3 (sonido y guardado) se cubren con **melonDS** como
backend (`stk recomp --melonds`): ARM7 con su código real, SPU, chip de guardado, cartucho, 2D y 3D
(software; OpenGL 2-8x y panorámico después). Lo propio que queda: el recompilador, las fibras, la
HLE del BIOS, el frontend (ventana, controles, Link con la palanca) y las mejoras. melonDS se irá
sustituyendo por piezas propias cuando convenga. El hardware simplificado propio (st_hw, st_dev,
st_vram, st_gpu2d, st_arm7) sigue para el modo sin melonDS y las pruebas.

## Fase 2 — Imagen
Estado (2026-11): bancos de VRAM y 2D por software (cuadro entero, sin ventanas ni mosaico) con
capturas BMP; exportaciones de GPU dedicada. Pendiente: por línea, ventanas, 3D, ventana SDL.

1. **2D por software, fiel** (fondos texto/affine/bitmap, OBJ, ventanas, blending, brillo,
   captura): por línea, usando VRAM/OAM/paleta del runtime. Sirve de referencia para todo lo demás.
2. **3D**: intérprete de la FIFO del GX (`0x04000400+`, comandos de matrices, vértices, texturas,
   luces) → renderizador propio en **OpenGL 3.3** (con opción Vulkan más adelante):
   - resolución interna 1x-8x, MSAA, filtrado de texturas opcional;
   - toon/edge marking/fog/alpha test como el hardware (shaders);
   - caché de texturas por hash de VRAM con límite de memoria (LRU).
3. Composición 2D+3D como la DS y salida a las dos pantallas del frontend (`ScreenSource`).
4. En portátiles con dos GPU, exportar `NvOptimusEnablement` y `AmdPowerXpressRequestHighPerformance`
   para usar la RTX.

## Fase 3 — Sonido, guardado y entrada
1. **Sonido**: HLE del driver de sonido del ARM7 (16 canales: PCM8/PCM16/ADPCM/PSG/ruido) a partir
   de los comandos SDAT que manda el ARM9, mezclado a 48 kHz con SDL (remuestreo de calidad).
2. **Guardado**: backup del cartucho (comandos SPI del EEPROM/flash) → archivo `.sav` junto al
   ejecutable; escritura atómica (archivo temporal + renombrar) para no corromperlo nunca.
3. **Entrada**: conectar el `InputMapper` del frontend a los registros de teclas y al táctil;
   posición real de Link en pantalla (leída de memoria; la dirección sale del informe de objdiff).
   Perfiles por contexto (a pie / tren / menú).

## Fase 4 — 60 FPS
- Medir primero: si el juego ya va a 60 en 2D y a 30 en 3D, como muchos títulos de DS.
- **Interpolación de cuadros** (enfoque de Ship of Harkinian): el juego sigue a su ritmo, el
  renderizador guarda las matrices de cada objeto en dos cuadros lógicos y dibuja cuadros
  intermedios interpolando. Requiere identificar a qué objeto pertenece cada lista de
  comandos 3D (por dirección de la lista o por el puntero de la matriz).
- Cámara y HUD interpolados aparte; desactivable por si algo tiembla.
- Ritmo: VSync + temporizador de alta resolución; cuadros lógicos a 59,8261 Hz exactos.

## Fase 5 — Pantalla panorámica
- **3D**: ajustar la matriz de proyección que manda el juego (`MTX_PROJ`) al aspecto de la
  ventana (16:9, 21:9) y renderizar un búfer más ancho.
- **Recorte**: el juego descarta objetos fuera de los 256 px; sustituir esas comprobaciones de
  visibilidad (localizadas con los nombres de objdiff) para ampliar el margen.
- **2D/HUD**: anclar elementos a los bordes (izquierda/derecha) moviendo los OBJ por rangos de
  X; fondos 2D que no llegan al ancho se rellenan con bordes o se estiran según la capa.
- Layout de dos pantallas ya ajustable en el frontend (pantalla superior grande + táctil pequeña).

## Fase 6 — Sin fugas de memoria y estable
- **Del juego no puede haber fugas** hacia el sistema: su montón vive dentro de la RAM emulada
  de 4 MB. Las fugas posibles son del lado del port (texturas, búferes, SDL, fibras).
- Reglas: todo recurso del host con dueño claro (RAII en C++, `init/shutdown` en C), cachés con
  tope de memoria, ninguna reserva por cuadro en el camino crítico (búferes reutilizables).
- **Sanitizers en CI**: compilaciones con AddressSanitizer + LeakSanitizer y UBSan (clang en
  Linux; `/fsanitize=address` en MSVC) que ejecutan el port N cuadros con entradas grabadas.
- **Pruebas de resistencia**: repeticiones de input deterministas de horas; contador de memoria
  del proceso y de cada caché en el overlay de depuración; falla si la memoria crece sin parar.
- **Comparación con un emulador** (melonDS como herramienta externa, en local): mismo input,
  comparar RAM/registros por cuadro para encontrar fallos de la traducción o de la plataforma.

## Fase 7 — Rendimiento
Código generado:
1. Registros en variables locales dentro de cada función (volcar a `StCpu` solo en llamadas).
2. Eliminar cálculos de flags que nadie lee (análisis de vida por bloque).
3. Lecturas de literales constantes de la ROM plegadas como constantes.
4. Ruta rápida en línea para la RAM principal en `st_read*/st_write*` (sin pasar por el mapa).
5. Llamadas directas entre overlays cuando el destino es único.
Compilación: `/O2` + LTO (`/GL /LTCG`) o clang-cl ThinLTO; PGO con una partida grabada.
Medición: Tracy o Superluminal; objetivo < 4 ms de CPU por cuadro en la máquina de referencia.

## Fase 8 — Mejoras del port
Resolución alta y filtros, texturas de alta resolución por hash (mods), menú de opciones completo
(ImGui), remapeo total, vibración, iconos de mando, guardado rápido opcional, empaquetado de assets
en un archivo único, y comprobación de la ROM del usuario por hash al arrancar.

## Fase 9 — Assets
Parsers semánticos de los formatos propios (ZOLB, BPAM, BLCM, DCBZ, JNCL...) a partir de los
volcados de bloques; NSBMD → glTF, animaciones, SSEQ → MIDI + SF2; visor de assets.

## Riesgos principales
| Riesgo | Mitigación |
|---|---|
| Hilos del SDK con una sola pila C | fibras por hilo (fase 1.5) |
| Código automodificable / copiado a ITCM en tiempo de ejecución | firmas por contenido (ya) y fallos aprendidos |
| Tiempo de compilación de MSVC | `--opt fast`, clang-cl, compilación incremental (ya) |
| Interpolación a 60 FPS con artefactos | por objeto, desactivable, cámara aparte |
| Panorámico con objetos que desaparecen | ampliar recorte con hooks localizados por nombre |
