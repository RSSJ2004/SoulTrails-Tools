# Compila el runtime + el código generado por `stk recomp`.
# Por ahora produce una biblioteca (la capa de plataforma y el ejecutable vienen después).
cmake_minimum_required(VERSION 3.16)
project(soultrails_port C)

set(ST_RECOMP_DIR "${CMAKE_CURRENT_LIST_DIR}/../build/recomp" CACHE PATH "Salida de stk recomp")
add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/../runtime ${CMAKE_BINARY_DIR}/runtime)
if(NOT EXISTS "${ST_RECOMP_DIR}/CMakeLists.txt")
  message(FATAL_ERROR "No encuentro ${ST_RECOMP_DIR}. Ejecuta antes: python stk.py recomp")
endif()
add_subdirectory(${ST_RECOMP_DIR} ${CMAKE_BINARY_DIR}/recomp)
