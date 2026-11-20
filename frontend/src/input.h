// Sistema de controles del port.
//
// - Cada acción admite varias asignaciones (teclado, botón de mando, eje de
//   mando, botón de ratón) y todo se guarda en controls.ini.
// - "Mover con stick": la palanca izquierda genera un toque virtual alrededor
//   de Link en la pantalla táctil (como mantener el lápiz), con zona muerta,
//   radio y curva configurables: poca inclinación = caminar, mucha = correr.
// - Gestos: tajo, ataque giratorio, voltereta y toque en el cursor se generan
//   como trazos del lápiz, sin tener que dibujarlos con el ratón.
// - La palanca derecha mueve un cursor para apuntar/tocar (bumerán, objetos...).
//
// Este módulo no depende de SDL: recibe el estado crudo (RawInput) y produce
// lo que vería la DS (DsInput). Así se puede probar y reutilizar.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace st {

// Botones de la DS (mismo orden de bits que KEYINPUT + EXTKEYIN para X/Y)
enum DsButton : uint32_t {
    kBtnA = 1u << 0, kBtnB = 1u << 1, kBtnSelect = 1u << 2, kBtnStart = 1u << 3,
    kBtnRight = 1u << 4, kBtnLeft = 1u << 5, kBtnUp = 1u << 6, kBtnDown = 1u << 7,
    kBtnR = 1u << 8, kBtnL = 1u << 9, kBtnX = 1u << 10, kBtnY = 1u << 11,
};

enum class Action : int {
    // Botones DS
    A, B, X, Y, L, R, Start, Select, Up, Down, Left, Right,
    // Movimiento por toque (eje 2D)
    MoveUp, MoveDown, MoveLeft, MoveRight,
    // Cursor (eje 2D) y toque en cursor
    CursorUp, CursorDown, CursorLeft, CursorRight, CursorTap,
    // Gestos
    Slash, SpinAttack, Roll, TapLink,
    // Micrófono (flauta / soplar)
    Microphone,
    // Frontend
    ToggleMenu, SwapScreens, NextLayout, ToggleSingleScreen,
    Count
};

const char* ActionName(Action a);      // nombre visible (español)
const char* ActionKey(Action a);       // clave en el .ini
bool IsAxisAction(Action a);           // acciones analógicas (mover/cursor)

enum class BindType : uint8_t { None = 0, Key, PadButton, PadAxis, MouseButton };

struct Binding {
    BindType type = BindType::None;
    int code = 0;      // scancode / botón / eje
    int dir = 0;       // para ejes: +1 o -1
    std::string ToString() const;          // "key:26", "padaxis:1:-", ...
    static Binding FromString(const std::string& s);
    bool operator==(const Binding& o) const { return type == o.type && code == o.code && dir == o.dir; }
};

constexpr int kMaxBindings = 3;

struct InputConfig {
    std::array<std::array<Binding, kMaxBindings>, static_cast<size_t>(Action::Count)> bind{};

    // Palanca -> toque
    float deadzone = 0.20f;         // 0..1
    float minRadius = 18.0f;        // px DS: distancia del toque al empezar a moverse (caminar)
    float maxRadius = 80.0f;        // px DS: inclinación máxima (correr)
    float curve = 1.4f;             // >1 = más precisión con poca inclinación
    float anchorX = 128.0f;         // posición de Link en la pantalla táctil si el juego no la da
    float anchorY = 108.0f;
    bool useGameAnchor = true;      // usar la posición real de Link cuando la plataforma la conozca
    float stickSmoothing = 0.35f;   // 0 = sin suavizado

    // Cursor
    float cursorSpeed = 220.0f;     // px DS por segundo con la palanca al máximo
    bool cursorReturnsToLink = true;

    // Gestos
    float slashLength = 70.0f;
    float spinRadius = 40.0f;
    int gestureFrames = 8;          // duración de un trazo
    bool slashTowardsStick = true;  // el tajo se orienta según la palanca

    bool showVirtualStylus = true;
    bool invertY = false;

    void SetDefaults();
};

// Estado crudo del dispositivo en un frame (rellenado por el frontend).
struct RawInput {
    std::vector<int> keysDown;          // scancodes pulsados
    uint32_t padButtons = 0;            // bit i = botón i
    std::array<float, 6> padAxes{};     // -1..1 (LX, LY, RX, RY, LT, RT)
    uint32_t mouseButtons = 0;
    bool mouseTouch = false;            // ratón/dedo tocando la pantalla inferior
    int mouseTouchX = 0, mouseTouchY = 0;
    float dt = 1.0f / 60.0f;
    // Posición de Link proyectada en la pantalla táctil (si la plataforma la sabe)
    bool hasGameAnchor = false;
    float gameAnchorX = 0, gameAnchorY = 0;
};

// Lo que ve la DS en este frame.
struct DsInput {
    uint32_t buttons = 0;     // DsButton (1 = pulsado)
    bool touch = false;
    int touchX = 0, touchY = 0;
    bool microphone = false;
    // Para dibujar ayudas visuales
    bool virtualTouch = false;
    float cursorX = 128, cursorY = 96;
    bool cursorActive = false;
};

// Eventos de frontend (flancos de subida)
struct FrontendEvents {
    bool toggleMenu = false, swapScreens = false, nextLayout = false, toggleSingle = false;
};

class InputMapper {
public:
    explicit InputMapper(const InputConfig* cfg) : cfg_(cfg) {}
    DsInput Update(const RawInput& raw, FrontendEvents* ev = nullptr);
    float ActionValue(const RawInput& raw, Action a) const;   // 0..1
    bool GestureActive() const { return gestureFrame_ < static_cast<int>(gesture_.size()); }

private:
    struct Point { bool down; float x, y; };
    void StartGesture(Action a, float ax, float ay, float dirX, float dirY);

    const InputConfig* cfg_;
    std::vector<Point> gesture_;
    int gestureFrame_ = 0;
    bool prev_[static_cast<size_t>(Action::Count)] = {};
    float smX_ = 0, smY_ = 0;
    float lastDirX_ = 0, lastDirY_ = 1;
    float cursorX_ = 128, cursorY_ = 96;
    bool cursorUsed_ = false;
};

bool LoadInputConfig(const std::string& path, InputConfig* cfg);
bool SaveInputConfig(const std::string& path, const InputConfig& cfg);

}  // namespace st
