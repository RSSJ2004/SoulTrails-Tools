add_library(st_runtime STATIC ${CMAKE_CURRENT_LIST_DIR}/strecomp.c)
target_include_directories(st_runtime PUBLIC ${CMAKE_CURRENT_LIST_DIR})
