# Compila el runtime + el código generado por `stk recomp` y el ejecutable soultrails_port.
cmake_minimum_required(VERSION 3.16)
project(soultrails_port C)

set(ST_RECOMP_DIR "${CMAKE_CURRENT_LIST_DIR}/../build/recomp" CACHE PATH "Salida de stk recomp")
add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/../runtime ${CMAKE_BINARY_DIR}/runtime)
if(NOT EXISTS "${ST_RECOMP_DIR}/CMakeLists.txt")
  message(FATAL_ERROR "No encuentro ${ST_RECOMP_DIR}. Ejecuta antes: python stk.py recomp")
endif()
add_subdirectory(${ST_RECOMP_DIR} ${CMAKE_BINARY_DIR}/recomp)
add_executable(soultrails_port ${CMAKE_CURRENT_LIST_DIR}/main.c)
target_link_libraries(soultrails_port PRIVATE st_recomp_generated)
