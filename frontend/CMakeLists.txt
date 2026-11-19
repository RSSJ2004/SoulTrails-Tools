cmake_minimum_required(VERSION 3.16)
project(soultrails_frontend CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(st_layout STATIC src/layout.cpp)
target_include_directories(st_layout PUBLIC src)

enable_testing()
add_executable(layout_test tests/layout_test.cpp)
target_link_libraries(layout_test PRIVATE st_layout)
add_test(NAME layout_test COMMAND layout_test)

option(ST_BUILD_FRONTEND "Compila la app SDL2 + ImGui" ON)
if(ST_BUILD_FRONTEND)
  find_package(SDL2 REQUIRED)
  include(FetchContent)
  FetchContent_Declare(imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG v1.91.5)
  FetchContent_MakeAvailable(imgui)

  add_executable(soultrails
    src/main.cpp
    ${imgui_SOURCE_DIR}/imgui.cpp
    ${imgui_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdl2.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_sdlrenderer2.cpp)
  target_include_directories(soultrails PRIVATE ${imgui_SOURCE_DIR} ${imgui_SOURCE_DIR}/backends)
  if(TARGET SDL2::SDL2main)
    target_link_libraries(soultrails PRIVATE SDL2::SDL2main)
  endif()
  target_link_libraries(soultrails PRIVATE st_layout SDL2::SDL2)
endif()
