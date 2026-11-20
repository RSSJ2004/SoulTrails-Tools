add_library(st_runtime STATIC
  ${CMAKE_CURRENT_LIST_DIR}/strecomp.c
  ${CMAKE_CURRENT_LIST_DIR}/st_bios.c
  ${CMAKE_CURRENT_LIST_DIR}/st_hw.c
  ${CMAKE_CURRENT_LIST_DIR}/st_dev.c
  ${CMAKE_CURRENT_LIST_DIR}/st_arm7.c
  ${CMAKE_CURRENT_LIST_DIR}/st_vram.c
  ${CMAKE_CURRENT_LIST_DIR}/st_gpu2d.c
  ${CMAKE_CURRENT_LIST_DIR}/st_thread.c
  ${CMAKE_CURRENT_LIST_DIR}/st_rom.c)
target_include_directories(st_runtime PUBLIC ${CMAKE_CURRENT_LIST_DIR})
