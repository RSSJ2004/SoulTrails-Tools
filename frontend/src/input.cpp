#include "input.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <sstream>

namespace st {
namespace {

constexpr float kPi = 3.14159265358979f;

struct ActionInfo {
    const char* key;
    const char* name;
};

const ActionInfo kInfo[] = {
    {"a", "A"}, {"b", "B"}, {"x", "X"}, {"y", "Y"}, {"l", "L"}, {"r", "R"},
    {"start", "Start"}, {"select", "Select"},
    {"up", "Cruceta arriba"}, {"down", "Cruceta abajo"}, {"left", "Cruceta izquierda"}, {"right", "Cruceta derecha"},
    {"move_up", "Mover arriba"}, {"move_down", "Mover abajo"}, {"move_left", "Mover izquierda"},
    {"move_right", "Mover derecha"},
    {"cursor_up", "Cursor arriba"}, {"cursor_down", "Cursor abajo"}, {"cursor_left", "Cursor izquierda"},
    {"cursor_right", "Cursor derecha"}, {"cursor_tap", "Tocar en el cursor"},
    {"slash", "Tajo (espada)"}, {"spin", "Ataque giratorio"}, {"roll", "Voltereta"}, {"tap_link", "Tocar a Link"},
    {"mic", "Micrófono (soplar)"},
    {"menu", "Abrir menú"}, {"swap", "Intercambiar pantallas"}, {"layout", "Siguiente layout"},
    {"single", "Alternar pantalla (modo una)"},
};
static_assert(sizeof(kInfo) / sizeof(kInfo[0]) == static_cast<size_t>(Action::Count), "faltan acciones");

// Scancodes de SDL (estables, no dependen de SDL aquí)
enum Sc { SC_A = 4, SC_D = 7, SC_E = 8, SC_F = 9, SC_Q = 20, SC_R = 21, SC_S = 22, SC_W = 26, SC_X = 27, SC_Z = 29,
          SC_C = 6, SC_V = 25, SC_G = 10, SC_I = 12, SC_J = 13, SC_K = 14, SC_L = 15, SC_M = 16, SC_SPACE = 44, SC_RETURN = 40,
          SC_BACKSPACE = 42, SC_LSHIFT = 225, SC_RIGHT = 79, SC_LEFT = 80, SC_DOWN = 81, SC_UP = 82,
          SC_F1 = 58, SC_F2 = 59, SC_F3 = 60, SC_TAB = 43 };
// Botones de SDL_GameController
enum Pad { P_A = 0, P_B = 1, P_X = 2, P_Y = 3, P_BACK = 4, P_GUIDE = 5, P_START = 6, P_LS = 7, P_RS = 8, P_LB = 9,
           P_RB = 10, P_DUP = 11, P_DDOWN = 12, P_DLEFT = 13, P_DRIGHT = 14 };
enum Axis { AX_LX = 0, AX_LY = 1, AX_RX = 2, AX_RY = 3, AX_LT = 4, AX_RT = 5 };

Binding K(int sc) { return {BindType::Key, sc, 0}; }
Binding P(int b) { return {BindType::PadButton, b, 0}; }
Binding AX(int a, int d) { return {BindType::PadAxis, a, d}; }
Binding MB(int b) { return {BindType::MouseButton, b, 0}; }

}  // namespace

const char* ActionName(Action a) { return kInfo[static_cast<int>(a)].name; }
const char* ActionKey(Action a) { return kInfo[static_cast<int>(a)].key; }
bool IsAxisAction(Action a) {
    return (a >= Action::MoveUp && a <= Action::MoveRight) || (a >= Action::CursorUp && a <= Action::CursorRight);
}

std::string Binding::ToString() const {
    switch (type) {
        case BindType::Key: return "key:" + std::to_string(code);
        case BindType::PadButton: return "pad:" + std::to_string(code);
        case BindType::PadAxis: return "padaxis:" + std::to_string(code) + (dir < 0 ? ":-" : ":+");
        case BindType::MouseButton: return "mouse:" + std::to_string(code);
        default: return "none";
    }
}

Binding Binding::FromString(const std::string& s) {
    Binding b;
    auto c1 = s.find(':');
    if (c1 == std::string::npos) return b;
    std::string t = s.substr(0, c1);
    std::string rest = s.substr(c1 + 1);
    b.code = std::atoi(rest.c_str());
    if (t == "key") b.type = BindType::Key;
    else if (t == "pad") b.type = BindType::PadButton;
    else if (t == "mouse") b.type = BindType::MouseButton;
    else if (t == "padaxis") {
        b.type = BindType::PadAxis;
        b.dir = rest.find(":-") != std::string::npos ? -1 : 1;
    }
    return b;
}

void InputConfig::SetDefaults() {
    *this = InputConfig{};
    auto set = [&](Action a, std::initializer_list<Binding> bs) {
        auto& slot = bind[static_cast<size_t>(a)];
        int i = 0;
        for (const auto& b : bs) if (i < kMaxBindings) slot[i++] = b;
    };
    // Teclado: mover WASD, cursor IJKL, cruceta en flechas. En mando, los botones
    // frontales van a los gestos (lo más usado en el juego); A/B/X/Y de la DS
    // quedan en el teclado y se pueden asignar al mando desde el menú.
    set(Action::A, {K(SC_X)});
    set(Action::B, {K(SC_Z)});
    set(Action::X, {K(SC_V)});
    set(Action::Y, {K(SC_G)});
    set(Action::L, {K(SC_Q), P(P_LB)});
    set(Action::R, {K(SC_E), P(P_RB)});
    set(Action::Start, {K(SC_RETURN), P(P_START)});
    set(Action::Select, {K(SC_BACKSPACE), P(P_BACK)});
    set(Action::Up, {K(SC_UP), P(P_DUP)});
    set(Action::Down, {K(SC_DOWN), P(P_DDOWN)});
    set(Action::Left, {K(SC_LEFT), P(P_DLEFT)});
    set(Action::Right, {K(SC_RIGHT), P(P_DRIGHT)});
    set(Action::MoveUp, {K(SC_W), AX(AX_LY, -1)});
    set(Action::MoveDown, {K(SC_S), AX(AX_LY, +1)});
    set(Action::MoveLeft, {K(SC_A), AX(AX_LX, -1)});
    set(Action::MoveRight, {K(SC_D), AX(AX_LX, +1)});
    set(Action::CursorUp, {K(SC_I), AX(AX_RY, -1)});
    set(Action::CursorDown, {K(SC_K), AX(AX_RY, +1)});
    set(Action::CursorLeft, {K(SC_J), AX(AX_RX, -1)});
    set(Action::CursorRight, {K(SC_L), AX(AX_RX, +1)});
    set(Action::CursorTap, {K(SC_SPACE), P(P_RS), AX(AX_RT, +1)});
    set(Action::Slash, {K(SC_F), P(P_X)});
    set(Action::SpinAttack, {K(SC_R), P(P_Y)});
    set(Action::Roll, {K(SC_LSHIFT), P(P_B)});
    set(Action::TapLink, {K(SC_C), P(P_A)});
    set(Action::Microphone, {K(SC_M), P(P_LS), AX(AX_LT, +1)});
    set(Action::ToggleMenu, {K(SC_F1), P(P_GUIDE)});
    set(Action::SwapScreens, {K(SC_F2)});
    set(Action::NextLayout, {K(SC_F3)});
    set(Action::ToggleSingleScreen, {K(SC_TAB)});
}

float InputMapper::ActionValue(const RawInput& raw, Action a) const {
    float v = 0;
    for (const Binding& b : cfg_->bind[static_cast<size_t>(a)]) {
        float x = 0;
        switch (b.type) {
            case BindType::Key:
                x = std::find(raw.keysDown.begin(), raw.keysDown.end(), b.code) != raw.keysDown.end() ? 1.f : 0.f;
                break;
            case BindType::PadButton: x = (raw.padButtons >> b.code) & 1 ? 1.f : 0.f; break;
            case BindType::MouseButton: x = (raw.mouseButtons >> b.code) & 1 ? 1.f : 0.f; break;
            case BindType::PadAxis:
                if (b.code >= 0 && b.code < static_cast<int>(raw.padAxes.size()))
                    x = std::max(0.f, raw.padAxes[b.code] * static_cast<float>(b.dir));
                break;
            default: break;
        }
        v = std::max(v, x);
    }
    return std::min(v, 1.f);
}

void InputMapper::StartGesture(Action a, float ax, float ay, float dx, float dy) {
    gesture_.clear();
    gestureFrame_ = 0;
    const int n = std::max(2, cfg_->gestureFrames);
    float len = std::hypot(dx, dy);
    if (len < 1e-3f) { dx = 0; dy = 1; len = 1; }
    dx /= len; dy /= len;
    auto clampPt = [](float& x, float& y) {
        x = std::clamp(x, 0.f, 255.f);
        y = std::clamp(y, 0.f, 191.f);
    };
    if (a == Action::Slash) {
        // Trazo perpendicular a la dirección, delante de Link
        float px = -dy, py = dx;
        float cx = ax + dx * 20, cy = ay + dy * 20;
        float L = cfg_->slashLength / 2;
        for (int i = 0; i < n; i++) {
            float t = static_cast<float>(i) / (n - 1);
            float x = cx + px * L * (2 * t - 1), y = cy + py * L * (2 * t - 1);
            clampPt(x, y);
            gesture_.push_back({true, x, y});
        }
    } else if (a == Action::SpinAttack) {
        // Círculo completo alrededor de Link (más un poco para que el juego lo cierre)
        int steps = n * 3;
        for (int i = 0; i <= steps; i++) {
            float ang = 2 * kPi * 1.15f * i / steps - kPi / 2;
            float x = ax + std::cos(ang) * cfg_->spinRadius, y = ay + std::sin(ang) * cfg_->spinRadius;
            clampPt(x, y);
            gesture_.push_back({true, x, y});
        }
    } else if (a == Action::Roll) {
        // Doble toque rápido en el borde, en la dirección de la palanca
        float x = ax + dx * 300, y = ay + dy * 300;
        clampPt(x, y);
        for (int rep = 0; rep < 2; rep++) {
            gesture_.push_back({true, x, y});
            gesture_.push_back({true, x, y});
            gesture_.push_back({false, x, y});
            gesture_.push_back({false, x, y});
        }
    } else if (a == Action::TapLink) {
        gesture_ = {{true, ax, ay}, {true, ax, ay}, {false, ax, ay}};
    } else if (a == Action::CursorTap) {
        gesture_ = {{true, cursorX_, cursorY_}, {true, cursorX_, cursorY_}, {false, cursorX_, cursorY_}};
    }
    gesture_.push_back({false, gesture_.empty() ? ax : gesture_.back().x, gesture_.empty() ? ay : gesture_.back().y});
}

DsInput InputMapper::Update(const RawInput& raw, FrontendEvents* ev) {
    DsInput out;
    bool now[static_cast<size_t>(Action::Count)];
    for (int i = 0; i < static_cast<int>(Action::Count); i++)
        now[i] = ActionValue(raw, static_cast<Action>(i)) > 0.5f;
    auto pressed = [&](Action a) { return now[static_cast<int>(a)] && !prev_[static_cast<int>(a)]; };

    // Botones DS
    const std::pair<Action, uint32_t> btns[] = {
        {Action::A, kBtnA}, {Action::B, kBtnB}, {Action::X, kBtnX}, {Action::Y, kBtnY}, {Action::L, kBtnL},
        {Action::R, kBtnR}, {Action::Start, kBtnStart}, {Action::Select, kBtnSelect}, {Action::Up, kBtnUp},
        {Action::Down, kBtnDown}, {Action::Left, kBtnLeft}, {Action::Right, kBtnRight}};
    for (auto& [a, bit] : btns)
        if (now[static_cast<int>(a)]) out.buttons |= bit;
    out.microphone = now[static_cast<int>(Action::Microphone)];

    if (ev) {
        ev->toggleMenu = pressed(Action::ToggleMenu);
        ev->swapScreens = pressed(Action::SwapScreens);
        ev->nextLayout = pressed(Action::NextLayout);
        ev->toggleSingle = pressed(Action::ToggleSingleScreen);
    }

    // Anclaje (Link)
    float ax = cfg_->anchorX, ay = cfg_->anchorY;
    if (cfg_->useGameAnchor && raw.hasGameAnchor) { ax = raw.gameAnchorX; ay = raw.gameAnchorY; }

    // Palanca de movimiento -> vector
    float mx = ActionValue(raw, Action::MoveRight) - ActionValue(raw, Action::MoveLeft);
    float my = ActionValue(raw, Action::MoveDown) - ActionValue(raw, Action::MoveUp);
    if (cfg_->invertY) my = -my;
    float mag = std::min(1.f, std::hypot(mx, my));
    float dz = std::clamp(cfg_->deadzone, 0.f, 0.95f);
    bool moving = mag > dz;
    float s = std::clamp(cfg_->stickSmoothing, 0.f, 0.95f);
    if (moving) {
        float nx = mx / std::max(mag, 1e-6f), ny = my / std::max(mag, 1e-6f);
        lastDirX_ = nx; lastDirY_ = ny;
        float t = (mag - dz) / (1 - dz);
        t = std::pow(std::clamp(t, 0.f, 1.f), std::max(0.1f, cfg_->curve));
        float radius = cfg_->minRadius + (cfg_->maxRadius - cfg_->minRadius) * t;
        float tx = nx * radius, ty = ny * radius;
        smX_ = smX_ * s + tx * (1 - s);
        smY_ = smY_ * s + ty * (1 - s);
    } else {
        smX_ = smY_ = 0;
    }

    // Cursor (palanca derecha)
    float cx = ActionValue(raw, Action::CursorRight) - ActionValue(raw, Action::CursorLeft);
    float cy = ActionValue(raw, Action::CursorDown) - ActionValue(raw, Action::CursorUp);
    if (std::hypot(cx, cy) > dz) {
        if (!cursorUsed_ && cfg_->cursorReturnsToLink) { cursorX_ = ax; cursorY_ = ay; }
        cursorUsed_ = true;
        cursorX_ = std::clamp(cursorX_ + cx * cfg_->cursorSpeed * raw.dt, 0.f, 255.f);
        cursorY_ = std::clamp(cursorY_ + cy * cfg_->cursorSpeed * raw.dt, 0.f, 191.f);
    } else if (cfg_->cursorReturnsToLink && !now[static_cast<int>(Action::CursorTap)]) {
        cursorUsed_ = false;
    }
    out.cursorX = cursorX_;
    out.cursorY = cursorY_;
    out.cursorActive = cursorUsed_;

    // Gestos (flanco de subida); no se interrumpen entre sí
    if (!GestureActive()) {
        float dx = lastDirX_, dy = lastDirY_;
        for (Action a : {Action::Slash, Action::SpinAttack, Action::Roll, Action::TapLink, Action::CursorTap}) {
            if (pressed(a)) {
                StartGesture(a, ax, ay, dx, dy);
                break;
            }
        }
    }

    // Prioridad: ratón/dedo real > gesto > palanca
    if (raw.mouseTouch) {
        out.touch = true;
        out.touchX = raw.mouseTouchX;
        out.touchY = raw.mouseTouchY;
        gesture_.clear();
        gestureFrame_ = 0;
    } else if (GestureActive()) {
        const Point& p = gesture_[gestureFrame_++];
        out.touch = p.down;
        out.touchX = static_cast<int>(std::lround(p.x));
        out.touchY = static_cast<int>(std::lround(p.y));
        out.virtualTouch = true;
    } else if (moving) {
        out.touch = true;
        out.touchX = static_cast<int>(std::lround(std::clamp(ax + smX_, 0.f, 255.f)));
        out.touchY = static_cast<int>(std::lround(std::clamp(ay + smY_, 0.f, 191.f)));
        out.virtualTouch = true;
    }

    for (int i = 0; i < static_cast<int>(Action::Count); i++) prev_[i] = now[i];
    return out;
}

// ------------------------------------------------------------------ ini
bool SaveInputConfig(const std::string& path, const InputConfig& c) {
    std::ofstream f(path);
    if (!f) return false;
    f << "[stick]\n"
      << "deadzone=" << c.deadzone << "\nmin_radius=" << c.minRadius << "\nmax_radius=" << c.maxRadius
      << "\ncurve=" << c.curve << "\nanchor_x=" << c.anchorX << "\nanchor_y=" << c.anchorY
      << "\nuse_game_anchor=" << c.useGameAnchor << "\nsmoothing=" << c.stickSmoothing
      << "\ninvert_y=" << c.invertY << "\n[cursor]\ncursor_speed=" << c.cursorSpeed
      << "\ncursor_returns=" << c.cursorReturnsToLink << "\n[gestures]\nslash_length=" << c.slashLength
      << "\nspin_radius=" << c.spinRadius << "\ngesture_frames=" << c.gestureFrames
      << "\nslash_towards_stick=" << c.slashTowardsStick << "\nshow_virtual_stylus=" << c.showVirtualStylus
      << "\n[bindings]\n";
    for (int i = 0; i < static_cast<int>(Action::Count); i++) {
        f << "bind_" << ActionKey(static_cast<Action>(i)) << "=";
        bool first = true;
        for (const Binding& b : c.bind[i]) {
            if (b.type == BindType::None) continue;
            f << (first ? "" : ",") << b.ToString();
            first = false;
        }
        f << "\n";
    }
    return static_cast<bool>(f);
}

bool LoadInputConfig(const std::string& path, InputConfig* c) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    while (std::getline(f, line)) {
        auto eq = line.find('=');
        if (eq == std::string::npos || line[0] == '[' || line[0] == '#') continue;
        std::string k = line.substr(0, eq), v = line.substr(eq + 1);
        float x = static_cast<float>(std::atof(v.c_str()));
        if (k == "deadzone") c->deadzone = std::clamp(x, 0.f, 0.95f);
        else if (k == "min_radius") c->minRadius = std::clamp(x, 0.f, 200.f);
        else if (k == "max_radius") c->maxRadius = std::clamp(x, 1.f, 255.f);
        else if (k == "curve") c->curve = std::clamp(x, 0.2f, 4.f);
        else if (k == "anchor_x") c->anchorX = std::clamp(x, 0.f, 255.f);
        else if (k == "anchor_y") c->anchorY = std::clamp(x, 0.f, 191.f);
        else if (k == "use_game_anchor") c->useGameAnchor = x != 0;
        else if (k == "smoothing") c->stickSmoothing = std::clamp(x, 0.f, 0.95f);
        else if (k == "invert_y") c->invertY = x != 0;
        else if (k == "cursor_speed") c->cursorSpeed = std::clamp(x, 10.f, 2000.f);
        else if (k == "cursor_returns") c->cursorReturnsToLink = x != 0;
        else if (k == "slash_length") c->slashLength = std::clamp(x, 10.f, 250.f);
        else if (k == "spin_radius") c->spinRadius = std::clamp(x, 10.f, 90.f);
        else if (k == "gesture_frames") c->gestureFrames = std::clamp(static_cast<int>(x), 2, 60);
        else if (k == "slash_towards_stick") c->slashTowardsStick = x != 0;
        else if (k == "show_virtual_stylus") c->showVirtualStylus = x != 0;
        else if (k.rfind("bind_", 0) == 0) {
            std::string key = k.substr(5);
            for (int i = 0; i < static_cast<int>(Action::Count); i++) {
                if (key != ActionKey(static_cast<Action>(i))) continue;
                auto& slot = c->bind[i];
                slot = {};
                std::stringstream ss(v);
                std::string item;
                int n = 0;
                while (std::getline(ss, item, ',') && n < kMaxBindings) {
                    Binding b = Binding::FromString(item);
                    if (b.type != BindType::None) slot[n++] = b;
                }
            }
        }
    }
    return true;
}

}  // namespace st
