// Layout de las dos pantallas de la DS. Sin dependencias de SDL para poder
// probarlo y reutilizarlo en cualquier backend (OpenGL, Vulkan, SDL_Renderer).
#pragma once

#include <string>

namespace st {

constexpr int kScreenW = 256;
constexpr int kScreenH = 192;

enum class LayoutMode : int {
    Vertical = 0,    // superior encima de la inferior (como la consola)
    Horizontal,      // lado a lado
    LargeSmall,      // una grande + otra pequeña al lado
    SingleScreen,    // solo una pantalla (con tecla para alternar)
    Count
};

enum class SmallPos : int { Top = 0, Center, Bottom, Count };
enum class Filter : int { Nearest = 0, Linear, Count };

struct LayoutConfig {
    LayoutMode mode = LayoutMode::Vertical;
    bool swap = false;            // intercambia superior/inferior
    int rotation = 0;             // 0, 90, 180, 270 (grados, horario)
    int gap = 0;                  // separación en píxeles DS
    bool integerScale = false;    // escala entera (pixel perfect)
    float largeRatio = 2.0f;      // tamaño grande / pequeño en LargeSmall
    SmallPos smallPos = SmallPos::Bottom;
    bool singleShowsBottom = false;
    Filter filter = Filter::Nearest;
    float bgColor[3] = {0.05f, 0.05f, 0.07f};
    bool showMenuOnStart = true;
};

// Rectángulo de una pantalla en coordenadas de ventana. (cx,cy) es el centro;
// (w,h) el tamaño SIN rotar; angle se aplica alrededor del centro.
struct ScreenRect {
    bool visible = false;
    float cx = 0, cy = 0, w = 0, h = 0;
    int angle = 0;
};

struct Layout {
    ScreenRect top;
    ScreenRect bottom;
};

Layout ComputeLayout(const LayoutConfig& cfg, int windowW, int windowH);

// Convierte un punto de la ventana a coordenadas táctiles de la pantalla
// inferior (0..255, 0..191). Devuelve false si cae fuera.
bool WindowToTouch(const Layout& l, float wx, float wy, int* tx, int* ty);

// Persistencia en un .ini sencillo.
bool LoadConfig(const std::string& path, LayoutConfig* cfg);
bool SaveConfig(const std::string& path, const LayoutConfig& cfg);

const char* LayoutModeName(LayoutMode m);

}  // namespace st
