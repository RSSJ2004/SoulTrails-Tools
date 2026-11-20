#include <cmath>
#include <cstdio>

#include "input.h"
#include "layout.h"

static int failures = 0;
#define CHECK(cond)                                                      \
    do {                                                                 \
        if (!(cond)) {                                                   \
            std::printf("FALLO %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                  \
        }                                                                \
    } while (0)

using namespace st;

int main() {
    InputConfig cfg;
    cfg.SetDefaults();
    cfg.stickSmoothing = 0;  // determinista
    InputMapper m(&cfg);

    // Sin entrada: sin toque
    RawInput raw;
    DsInput o = m.Update(raw);
    CHECK(!o.touch && o.buttons == 0);

    // Zona muerta
    raw.padAxes[1] = -0.1f;
    o = m.Update(raw);
    CHECK(!o.touch);

    // Palanca arriba a tope: toque encima de Link a maxRadius
    raw.padAxes[1] = -1.0f;
    o = m.Update(raw);
    CHECK(o.touch && o.virtualTouch);
    CHECK(o.touchX == (int)cfg.anchorX);
    CHECK(std::abs(o.touchY - (int)(cfg.anchorY - cfg.maxRadius)) <= 1);

    // Poca inclinación: cerca de Link (caminar)
    raw.padAxes[1] = -0.3f;
    o = m.Update(raw);
    float d = cfg.anchorY - o.touchY;
    CHECK(d >= cfg.minRadius - 1 && d < cfg.minRadius + 10);

    // Derecha con teclado (D = scancode 7)
    raw.padAxes = {};
    raw.keysDown = {7};
    o = m.Update(raw);
    CHECK(o.touch && o.touchX > (int)cfg.anchorX && o.touchY == (int)std::lround(cfg.anchorY));

    // Posición real de Link desde el juego
    raw.hasGameAnchor = true;
    raw.gameAnchorX = 50;
    raw.gameAnchorY = 60;
    o = m.Update(raw);
    CHECK(std::abs(o.touchX - (50 + (int)cfg.maxRadius)) <= 1 && o.touchY == 60);
    raw.hasGameAnchor = false;
    raw.keysDown = {};
    m.Update(raw);

    // El ratón tiene prioridad
    raw.padAxes[0] = 1.0f;
    raw.mouseTouch = true;
    raw.mouseTouchX = 10;
    raw.mouseTouchY = 20;
    o = m.Update(raw);
    CHECK(o.touch && !o.virtualTouch && o.touchX == 10 && o.touchY == 20);
    raw = RawInput{};
    m.Update(raw);

    // Botones DS: X (scancode 27) -> A
    raw.keysDown = {27};
    o = m.Update(raw);
    CHECK(o.buttons & kBtnA);
    raw.keysDown = {};
    m.Update(raw);

    // Gesto de tajo (F = 9): varios frames de toque y luego suelta
    raw.keysDown = {9};
    int touched = 0;
    float fx = 0, fy = 0, lx = 0, ly = 0;
    for (int i = 0; i < 30; i++) {
        o = m.Update(raw);
        if (o.touch) {
            if (!touched) { fx = (float)o.touchX; fy = (float)o.touchY; }
            lx = (float)o.touchX; ly = (float)o.touchY;
            touched++;
        }
        raw.keysDown = {};  // basta con pulsar una vez
    }
    CHECK(touched == cfg.gestureFrames);
    CHECK(std::hypot(lx - fx, ly - fy) >= cfg.slashLength - 2);  // trazo completo
    // Ataque giratorio (R = 21): el trazo recorre un círculo
    raw.keysDown = {21};
    float minA = 999, maxA = -999;
    for (int i = 0; i < 60; i++) {
        o = m.Update(raw);
        if (o.touch) {
            float a = std::atan2(o.touchY - cfg.anchorY, o.touchX - cfg.anchorX);
            minA = std::fmin(minA, a);
            maxA = std::fmax(maxA, a);
        }
        raw.keysDown = {};
    }
    CHECK(maxA - minA > 5.5f);

    // Asignaciones: guardar y cargar
    InputConfig c2;
    c2.SetDefaults();
    c2.bind[(int)Action::Slash][0] = {BindType::PadAxis, 5, -1};
    c2.deadzone = 0.33f;
    CHECK(SaveInputConfig("test_controls.ini", c2));
    InputConfig c3;
    CHECK(LoadInputConfig("test_controls.ini", &c3));
    CHECK(c3.bind[(int)Action::Slash][0] == c2.bind[(int)Action::Slash][0]);
    CHECK(std::fabs(c3.deadzone - 0.33f) < 1e-4f);
    CHECK(c3.bind[(int)Action::MoveUp][1] == c2.bind[(int)Action::MoveUp][1]);
    std::remove("test_controls.ini");

    // TouchToWindow es el inverso de WindowToTouch
    LayoutConfig lc;
    for (int rot : {0, 90, 180, 270}) {
        lc.rotation = rot;
        Layout l = ComputeLayout(lc, 800, 800);
        float wx, wy;
        CHECK(TouchToWindow(l, 100.5f, 50.5f, &wx, &wy));
        int tx, ty;
        CHECK(WindowToTouch(l, wx, wy, &tx, &ty) && tx == 100 && ty == 50);
    }

    if (failures == 0) std::printf("input_test: OK\n");
    return failures ? 1 : 0;
}
