#include <cmath>
#include <cstdio>
#include <cstdlib>

#include "layout.h"

static int failures = 0;
#define CHECK(cond)                                                    \
    do {                                                               \
        if (!(cond)) {                                                 \
            std::printf("FALLO %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                \
        }                                                              \
    } while (0)

static bool Near(float a, float b) { return std::fabs(a - b) < 0.51f; }

int main() {
    using namespace st;
    LayoutConfig c;

    // Vertical en 256x384 exacto: superior arriba, inferior abajo, escala 1.
    Layout l = ComputeLayout(c, 256, 384);
    CHECK(Near(l.top.cx, 128) && Near(l.top.cy, 96) && Near(l.top.w, 256));
    CHECK(Near(l.bottom.cy, 288));
    int tx, ty;
    CHECK(WindowToTouch(l, 10, 200, &tx, &ty) && tx == 10 && ty == 8);
    CHECK(!WindowToTouch(l, 10, 100, &tx, &ty));  // superior no es táctil

    // Swap
    c.swap = true;
    l = ComputeLayout(c, 256, 384);
    CHECK(Near(l.bottom.cy, 96) && Near(l.top.cy, 288));
    c.swap = false;

    // Horizontal 2x en 1024x384
    c.mode = LayoutMode::Horizontal;
    l = ComputeLayout(c, 1024, 384);
    CHECK(Near(l.top.w, 512) && Near(l.bottom.cx, 768));
    CHECK(WindowToTouch(l, 512 + 2, 2, &tx, &ty) && tx == 1 && ty == 1);

    // Rotación 90 en vertical: lienzo 384x256, la superior queda a la derecha.
    c.mode = LayoutMode::Vertical;
    c.rotation = 90;
    l = ComputeLayout(c, 384, 256);
    CHECK(l.top.cx > l.bottom.cx);
    CHECK(Near(l.top.w, 256) && Near(l.top.h, 192));
    // esquina sup-izq de la pantalla inferior rotada 90° horario está arriba-derecha de su rect
    CHECK(WindowToTouch(l, 191.5f, 0.5f, &tx, &ty) && tx == 0 && ty == 0);
    c.rotation = 0;

    // Escala entera
    c.integerScale = true;
    l = ComputeLayout(c, 600, 900);
    CHECK(Near(l.top.w, 512));
    c.integerScale = false;

    // Una pantalla
    c.mode = LayoutMode::SingleScreen;
    l = ComputeLayout(c, 512, 384);
    CHECK(l.top.visible && !l.bottom.visible);
    c.singleShowsBottom = true;
    l = ComputeLayout(c, 512, 384);
    CHECK(!l.top.visible && l.bottom.visible && Near(l.bottom.w, 512));
    CHECK(WindowToTouch(l, 511, 383, &tx, &ty) && tx == 255 && ty == 191);

    // Grande + pequeña
    c = LayoutConfig{};
    c.mode = LayoutMode::LargeSmall;
    c.largeRatio = 2.0f;
    l = ComputeLayout(c, 384, 192);
    CHECK(Near(l.top.w, 256) && Near(l.bottom.w, 128));

    // Ini ida y vuelta
    c.rotation = 270; c.gap = 12; c.swap = true;
    CHECK(SaveConfig("test_layout.ini", c));
    LayoutConfig d;
    CHECK(LoadConfig("test_layout.ini", &d));
    CHECK(d.mode == LayoutMode::LargeSmall && d.rotation == 270 && d.gap == 12 && d.swap);
    std::remove("test_layout.ini");

    if (failures == 0) std::printf("layout_test: OK\n");
    return failures ? 1 : 0;
}
