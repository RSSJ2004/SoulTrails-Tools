# Parche mínimo e idempotente de melonDS: un gancho para ejecutar el ARM9 fuera del intérprete.
# melonDS sigue llevando el ARM7, timers, DMA, GPU, sonido y cartucho; en el turno del ARM9
# llama a ARM9ExternalExecute (el código recompilado) si está puesto.
# Uso: cmake -DMELON_SRC=<melonDS>/src -P patch_melonds.cmake
set(h "${MELON_SRC}/ARM.h")
set(c "${MELON_SRC}/ARM.cpp")
file(READ "${h}" H)
if(NOT H MATCHES "ARM9ExternalExecute")
  string(REPLACE "class ARMv5 : public ARM" "extern void (*ARM9ExternalExecute)();\n\nclass ARMv5 : public ARM" H "${H}")
  file(WRITE "${h}" "${H}")
endif()
file(READ "${c}" C)
if(NOT C MATCHES "ARM9ExternalExecute")
  string(REPLACE "template <CPUExecuteMode mode>\nvoid ARMv5::Execute()\n{"
    "void (*ARM9ExternalExecute)() = nullptr;\n\ntemplate <CPUExecuteMode mode>\nvoid ARMv5::Execute()\n{\n    if (ARM9ExternalExecute) { ARM9ExternalExecute(); return; }"
    C "${C}")
  if(NOT C MATCHES "ARM9ExternalExecute")
    message(FATAL_ERROR "No se pudo parchear ARMv5::Execute en ${c} (¿otra versión de melonDS?)")
  endif()
  file(WRITE "${c}" "${C}")
endif()
