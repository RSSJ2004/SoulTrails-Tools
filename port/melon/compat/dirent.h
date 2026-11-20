/* Compatibilidad para compilar melonDS con clang-cl: FATStorage.cpp incluye <dirent.h> pero no lo
 * usa (recorre carpetas con std::filesystem). Windows no tiene esta cabecera. */
#pragma once
