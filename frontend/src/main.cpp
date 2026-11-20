// Frontend de SoulTrails: ventana SDL2 con las dos pantallas DS y un menú
// (Dear ImGui) con pestañas Pantallas y Controles. El "juego" se conecta
// implementando ScreenSource; por ahora se muestra un patrón de prueba.

#include <SDL.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "imgui.h"
#include "imgui_impl_sdl2.h"
#include "imgui_impl_sdlrenderer2.h"
#include "input.h"
#include "layout.h"

namespace {

// Interfaz que implementará el port.
class ScreenSource {
public:
    virtual ~ScreenSource() = default;
    virtual void Frame(uint32_t* top, uint32_t* bottom) = 0;
    virtual void Input(const st::DsInput& in) = 0;
    // Posición de Link en la pantalla táctil (si se conoce) para el control por palanca
    virtual bool LinkTouchPosition(float* x, float* y) { (void)x; (void)y; return false; }
};

class TestPattern : public ScreenSource {
public:
    void Frame(uint32_t* top, uint32_t* bottom) override {
        ++t_;
        // Link de prueba: se mueve hacia el lápiz como en el juego
        if (in_.touch) {
            float dx = in_.touchX - lx_, dy = in_.touchY - ly_;
            float d = std::hypot(dx, dy);
            if (d > 6) {
                float speed = d > 50 ? 2.0f : 0.8f;  // lejos = correr
                lx_ += dx / d * speed;
                ly_ += dy / d * speed;
            }
        }
        lx_ = std::clamp(lx_, 8.f, 248.f);
        ly_ = std::clamp(ly_, 8.f, 184.f);
        for (int y = 0; y < st::kScreenH; ++y) {
            for (int x = 0; x < st::kScreenW; ++x) {
                uint8_t r = (uint8_t)(x + t_), g = (uint8_t)(y * 255 / st::kScreenH), b = 160;
                bool border = x < 2 || y < 2 || x >= st::kScreenW - 2 || y >= st::kScreenH - 2;
                top[y * st::kScreenW + x] = border ? 0xFFFFFFFF : (0xFF000000u | r << 16 | g << 8 | b);
                uint8_t c = ((x / 16 + y / 16) & 1) ? 70 : 40;
                bottom[y * st::kScreenW + x] = border ? 0xFFFFD040 : (0xFF000000u | c << 16 | c << 8 | (c + 30));
            }
        }
        for (int i = 0; i < 40; ++i)
            for (int j = -i / 3; j <= i / 3; ++j) top[(20 + i) * st::kScreenW + 128 + j] = 0xFFFFFFFF;
        // "Link" (círculo verde)
        for (int dy = -6; dy <= 6; ++dy)
            for (int dx = -6; dx <= 6; ++dx)
                if (dx * dx + dy * dy <= 36) {
                    int px = (int)lx_ + dx, py = (int)ly_ + dy;
                    if (px >= 0 && py >= 0 && px < st::kScreenW && py < st::kScreenH)
                        bottom[py * st::kScreenW + px] = 0xFF30C040;
                }
        // Botones pulsados en la superior
        for (int b = 0; b < 12; ++b)
            if (in_.buttons & (1u << b))
                for (int y = 170; y < 182; ++y)
                    for (int x = 10 + b * 20; x < 24 + b * 20; ++x) top[y * st::kScreenW + x] = 0xFFFFE000;
        if (in_.microphone)
            for (int y = 150; y < 160; ++y)
                for (int x = 10; x < 246; ++x) top[y * st::kScreenW + x] = 0xFF40A0FF;
    }
    void Input(const st::DsInput& in) override { in_ = in; }
    bool LinkTouchPosition(float* x, float* y) override { *x = lx_; *y = ly_; return true; }

private:
    int t_ = 0;
    float lx_ = 128, ly_ = 108;
    st::DsInput in_;
};

std::string PrefPath(const char* file) {
    char* base = SDL_GetPrefPath("SoulTrails", "frontend");
    std::string p = base ? std::string(base) + file : file;
    SDL_free(base);
    return p;
}

void DrawScreen(SDL_Renderer* r, SDL_Texture* tex, const st::ScreenRect& s) {
    if (!s.visible) return;
    SDL_FRect dst{s.cx - s.w / 2, s.cy - s.h / 2, s.w, s.h};
    SDL_RenderCopyExF(r, tex, nullptr, &dst, (double)s.angle, nullptr, SDL_FLIP_NONE);
}

std::string BindingLabel(const st::Binding& b) {
    using st::BindType;
    switch (b.type) {
        case BindType::Key: return std::string("Tecla ") + SDL_GetScancodeName((SDL_Scancode)b.code);
        case BindType::PadButton: {
            const char* n = SDL_GameControllerGetStringForButton((SDL_GameControllerButton)b.code);
            return std::string("Mando ") + (n ? n : "?");
        }
        case BindType::PadAxis: {
            const char* n = SDL_GameControllerGetStringForAxis((SDL_GameControllerAxis)b.code);
            return std::string("Eje ") + (n ? n : "?") + (b.dir < 0 ? " -" : " +");
        }
        case BindType::MouseButton: return "Ratón " + std::to_string(b.code + 1);
        default: return "(vacío)";
    }
}

struct Capture {
    int action = -1;
    int slot = 0;
};

bool ScreensTab(st::LayoutConfig& c) {
    bool changed = false;
    int mode = (int)c.mode;
    const char* modes[] = {"Vertical", "Horizontal", "Grande + pequeña", "Una pantalla"};
    changed |= ImGui::Combo("Layout", &mode, modes, IM_ARRAYSIZE(modes));
    c.mode = (st::LayoutMode)mode;
    if (c.mode == st::LayoutMode::SingleScreen) {
        int which = c.singleShowsBottom;
        changed |= ImGui::RadioButton("Superior", &which, 0); ImGui::SameLine();
        changed |= ImGui::RadioButton("Inferior (táctil)", &which, 1);
        c.singleShowsBottom = which;
    } else {
        changed |= ImGui::Checkbox("Intercambiar pantallas", &c.swap);
        changed |= ImGui::SliderInt("Separación", &c.gap, 0, 96, "%d px");
    }
    if (c.mode == st::LayoutMode::LargeSmall) {
        changed |= ImGui::SliderFloat("Proporción", &c.largeRatio, 1.0f, 4.0f, "%.2fx");
        int sp = (int)c.smallPos;
        const char* pos[] = {"Arriba", "Centro", "Abajo"};
        changed |= ImGui::Combo("Pos. pequeña", &sp, pos, IM_ARRAYSIZE(pos));
        c.smallPos = (st::SmallPos)sp;
    }
    int rot = c.rotation / 90;
    const char* rots[] = {"0°", "90°", "180°", "270°"};
    changed |= ImGui::Combo("Rotación", &rot, rots, IM_ARRAYSIZE(rots));
    c.rotation = rot * 90;
    changed |= ImGui::Checkbox("Escala entera (pixel perfect)", &c.integerScale);
    int filt = (int)c.filter;
    const char* filts[] = {"Nítido (nearest)", "Suavizado (linear)"};
    changed |= ImGui::Combo("Filtro", &filt, filts, IM_ARRAYSIZE(filts));
    c.filter = (st::Filter)filt;
    changed |= ImGui::ColorEdit3("Fondo", c.bgColor);
    changed |= ImGui::Checkbox("Mostrar el menú al iniciar", &c.showMenuOnStart);
    if (ImGui::Button("Restablecer pantallas")) { c = st::LayoutConfig{}; changed = true; }
    return changed;
}

bool BindingsTable(st::InputConfig& ic, Capture& cap, int from, int to) {
    bool changed = false;
    if (ImGui::BeginTable("binds", 1 + st::kMaxBindings, ImGuiTableFlags_RowBg | ImGuiTableFlags_BordersInnerV)) {
        ImGui::TableSetupColumn("Acción", ImGuiTableColumnFlags_WidthFixed, 170);
        for (int s = 0; s < st::kMaxBindings; ++s) ImGui::TableSetupColumn(s == 0 ? "Principal" : "Alternativa");
        ImGui::TableHeadersRow();
        for (int a = from; a < to; ++a) {
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::TextUnformatted(st::ActionName((st::Action)a));
            for (int s = 0; s < st::kMaxBindings; ++s) {
                ImGui::TableNextColumn();
                ImGui::PushID(a * 8 + s);
                bool capturing = cap.action == a && cap.slot == s;
                std::string label = capturing ? "Pulsa... (Esc cancela)" : BindingLabel(ic.bind[a][s]);
                if (ImGui::Button(label.c_str(), ImVec2(-28, 0))) { cap.action = a; cap.slot = s; }
                if (ImGui::IsItemHovered()) ImGui::SetTooltip("Clic y pulsa una tecla, botón o mueve un eje");
                ImGui::SameLine();
                if (ImGui::SmallButton("x")) { ic.bind[a][s] = {}; changed = true; }
                ImGui::PopID();
            }
        }
        ImGui::EndTable();
    }
    return changed;
}

bool ControlsTab(st::InputConfig& ic, Capture& cap, bool padConnected) {
    bool changed = false;
    ImGui::TextDisabled(padConnected ? "Mando conectado" : "Sin mando (conecta uno en cualquier momento)");
    if (ImGui::BeginTabBar("ctrl")) {
        if (ImGui::BeginTabItem("Movimiento")) {
            ImGui::TextWrapped("La palanca mueve a Link tocando la pantalla a su alrededor, como mantener el lápiz: "
                               "poca inclinación = caminar, inclinación completa = correr.");
            changed |= ImGui::SliderFloat("Zona muerta", &ic.deadzone, 0.0f, 0.6f, "%.2f");
            changed |= ImGui::SliderFloat("Radio al caminar", &ic.minRadius, 4.0f, 60.0f, "%.0f px");
            changed |= ImGui::SliderFloat("Radio al correr", &ic.maxRadius, 30.0f, 128.0f, "%.0f px");
            changed |= ImGui::SliderFloat("Curva de respuesta", &ic.curve, 0.5f, 3.0f, "%.2f");
            changed |= ImGui::SliderFloat("Suavizado", &ic.stickSmoothing, 0.0f, 0.9f, "%.2f");
            changed |= ImGui::Checkbox("Seguir la posición real de Link", &ic.useGameAnchor);
            changed |= ImGui::SliderFloat("Centro X (si no se conoce Link)", &ic.anchorX, 0, 255, "%.0f");
            changed |= ImGui::SliderFloat("Centro Y (si no se conoce Link)", &ic.anchorY, 0, 191, "%.0f");
            changed |= ImGui::Checkbox("Invertir eje vertical", &ic.invertY);
            changed |= ImGui::Checkbox("Mostrar lápiz virtual", &ic.showVirtualStylus);
            ImGui::Separator();
            changed |= BindingsTable(ic, cap, (int)st::Action::MoveUp, (int)st::Action::MoveRight + 1);
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Acciones táctiles")) {
            ImGui::TextWrapped("Gestos del lápiz con un solo botón. El tajo y la voltereta usan la dirección de la palanca.");
            changed |= ImGui::SliderFloat("Longitud del tajo", &ic.slashLength, 20, 160, "%.0f px");
            changed |= ImGui::SliderFloat("Radio del giro", &ic.spinRadius, 15, 80, "%.0f px");
            changed |= ImGui::SliderInt("Duración del trazo", &ic.gestureFrames, 3, 30, "%d frames");
            changed |= ImGui::SliderFloat("Velocidad del cursor", &ic.cursorSpeed, 50, 600, "%.0f px/s");
            changed |= ImGui::Checkbox("El cursor empieza en Link", &ic.cursorReturnsToLink);
            ImGui::Separator();
            changed |= BindingsTable(ic, cap, (int)st::Action::CursorUp, (int)st::Action::Microphone + 1);
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Botones DS")) {
            changed |= BindingsTable(ic, cap, (int)st::Action::A, (int)st::Action::Right + 1);
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Atajos")) {
            changed |= BindingsTable(ic, cap, (int)st::Action::ToggleMenu, (int)st::Action::Count);
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    ImGui::Separator();
    if (ImGui::Button("Restablecer controles")) { ic.SetDefaults(); changed = true; }
    return changed;
}

void DrawCross(SDL_Renderer* r, float x, float y, float s) {
    SDL_RenderDrawLineF(r, x - s, y, x + s, y);
    SDL_RenderDrawLineF(r, x, y - s, x, y + s);
}

void DrawCircle(SDL_Renderer* r, float x, float y, float rad) {
    const int n = 24;
    for (int i = 0; i < n; ++i) {
        float a0 = 6.2831853f * i / n, a1 = 6.2831853f * (i + 1) / n;
        SDL_RenderDrawLineF(r, x + std::cos(a0) * rad, y + std::sin(a0) * rad, x + std::cos(a1) * rad,
                            y + std::sin(a1) * rad);
    }
}

}  // namespace

int main(int, char**) {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_GAMECONTROLLER) != 0) {
        std::fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    st::LayoutConfig cfg;
    const std::string layoutPath = PrefPath("layout.ini");
    st::LoadConfig(layoutPath, &cfg);
    st::InputConfig icfg;
    icfg.SetDefaults();
    const std::string controlsPath = PrefPath("controls.ini");
    st::LoadInputConfig(controlsPath, &icfg);
    st::InputMapper mapper(&icfg);

    SDL_Window* win = SDL_CreateWindow("SoulTrails", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 900, 768,
                                       SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    SDL_Renderer* ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!win || !ren) {
        std::fprintf(stderr, "SDL: %s\n", SDL_GetError());
        return 1;
    }
    SDL_SetRenderDrawBlendMode(ren, SDL_BLENDMODE_BLEND);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui::GetIO().IniFilename = nullptr;
    ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableGamepad;
    ImGui::StyleColorsDark();
    ImGui_ImplSDL2_InitForSDLRenderer(win, ren);
    ImGui_ImplSDLRenderer2_Init(ren);

    auto makeTex = [&]() {
        SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, cfg.filter == st::Filter::Linear ? "1" : "0");
        return SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING, st::kScreenW,
                                 st::kScreenH);
    };
    SDL_Texture* texTop = makeTex();
    SDL_Texture* texBot = makeTex();
    st::Filter texFilter = cfg.filter;

    std::vector<uint32_t> fbTop(st::kScreenW * st::kScreenH), fbBot(st::kScreenW * st::kScreenH);
    TestPattern game;
    ScreenSource* src = &game;

    SDL_GameController* pad = nullptr;
    bool menu = cfg.showMenuOnStart, running = true, mouseDown = false;
    Capture cap;
    Uint64 lastTicks = SDL_GetPerformanceCounter();
    st::DsInput ds;

    while (running) {
        int ww, wh, lw, lh;
        SDL_GetRendererOutputSize(ren, &ww, &wh);
        SDL_GetWindowSize(win, &lw, &lh);
        const float dpi = lw > 0 ? (float)ww / lw : 1.0f;
        st::Layout layout = st::ComputeLayout(cfg, ww, wh);
        bool cfgChanged = false, ctrlChanged = false;

        auto finishCapture = [&](st::Binding b) {
            icfg.bind[cap.action][cap.slot] = b;
            cap.action = -1;
            ctrlChanged = true;
        };

        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (cap.action >= 0) {
                // Modo "pulsa para asignar": la entrada no llega a ImGui ni al juego
                if (e.type == SDL_KEYDOWN) {
                    if (e.key.keysym.scancode == SDL_SCANCODE_ESCAPE) cap.action = -1;
                    else finishCapture({st::BindType::Key, (int)e.key.keysym.scancode, 0});
                    continue;
                }
                if (e.type == SDL_CONTROLLERBUTTONDOWN) { finishCapture({st::BindType::PadButton, e.cbutton.button, 0}); continue; }
                if (e.type == SDL_CONTROLLERAXISMOTION && std::abs(e.caxis.value) > 24000) {
                    finishCapture({st::BindType::PadAxis, e.caxis.axis, e.caxis.value < 0 ? -1 : 1});
                    continue;
                }
            }
            ImGui_ImplSDL2_ProcessEvent(&e);
            switch (e.type) {
                case SDL_QUIT: running = false; break;
                case SDL_CONTROLLERDEVICEADDED:
                    if (!pad) pad = SDL_GameControllerOpen(e.cdevice.which);
                    break;
                case SDL_CONTROLLERDEVICEREMOVED:
                    if (pad && SDL_GameControllerGetJoystick(pad) &&
                        SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(pad)) == e.cdevice.which) {
                        SDL_GameControllerClose(pad);
                        pad = nullptr;
                    }
                    break;
                case SDL_KEYDOWN:
                    if (e.key.keysym.sym == SDLK_F11) {
                        bool fs = SDL_GetWindowFlags(win) & SDL_WINDOW_FULLSCREEN_DESKTOP;
                        SDL_SetWindowFullscreen(win, fs ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP);
                    } else if (e.key.keysym.sym == SDLK_ESCAPE && !menu) {
                        menu = true;
                    }
                    break;
                case SDL_MOUSEBUTTONDOWN:
                    if (!ImGui::GetIO().WantCaptureMouse && e.button.button == SDL_BUTTON_LEFT) mouseDown = true;
                    break;
                case SDL_MOUSEBUTTONUP:
                    if (e.button.button == SDL_BUTTON_LEFT) mouseDown = false;
                    break;
            }
        }

        // Estado crudo
        Uint64 nowTicks = SDL_GetPerformanceCounter();
        st::RawInput raw;
        raw.dt = std::min(0.1f, (float)(nowTicks - lastTicks) / (float)SDL_GetPerformanceFrequency());
        lastTicks = nowTicks;
        const bool uiKeys = ImGui::GetIO().WantCaptureKeyboard || cap.action >= 0;
        if (!uiKeys) {
            int n = 0;
            const Uint8* ks = SDL_GetKeyboardState(&n);
            for (int i = 0; i < n; ++i)
                if (ks[i]) raw.keysDown.push_back(i);
        }
        if (pad && cap.action < 0 && !(menu && ImGui::GetIO().NavActive)) {
            for (int b = 0; b < SDL_CONTROLLER_BUTTON_MAX; ++b)
                if (SDL_GameControllerGetButton(pad, (SDL_GameControllerButton)b)) raw.padButtons |= 1u << b;
            for (int a = 0; a < SDL_CONTROLLER_AXIS_MAX && a < 6; ++a)
                raw.padAxes[a] = std::clamp(SDL_GameControllerGetAxis(pad, (SDL_GameControllerAxis)a) / 32767.0f, -1.f, 1.f);
        }
        if (mouseDown) {
            int mx, my;
            SDL_GetMouseState(&mx, &my);
            int tx, ty;
            if (st::WindowToTouch(layout, mx * dpi, my * dpi, &tx, &ty)) {
                raw.mouseTouch = true;
                raw.mouseTouchX = tx;
                raw.mouseTouchY = ty;
            }
        }
        raw.hasGameAnchor = src->LinkTouchPosition(&raw.gameAnchorX, &raw.gameAnchorY);

        st::FrontendEvents fe;
        ds = mapper.Update(raw, &fe);
        if (fe.toggleMenu) menu = !menu;
        if (fe.swapScreens) { cfg.swap = !cfg.swap; cfgChanged = true; }
        if (fe.nextLayout) { cfg.mode = (st::LayoutMode)(((int)cfg.mode + 1) % (int)st::LayoutMode::Count); cfgChanged = true; }
        if (fe.toggleSingle) { cfg.singleShowsBottom = !cfg.singleShowsBottom; cfgChanged = true; }
        src->Input(ds);

        ImGui_ImplSDLRenderer2_NewFrame();
        ImGui_ImplSDL2_NewFrame();
        ImGui::NewFrame();
        if (menu) {
            ImGui::SetNextWindowSize(ImVec2(620, 600), ImGuiCond_FirstUseEver);
            ImGui::SetNextWindowPos(ImVec2(20, 20), ImGuiCond_FirstUseEver);
            if (ImGui::Begin("SoulTrails", &menu)) {
                if (ImGui::BeginTabBar("main")) {
                    if (ImGui::BeginTabItem("Pantallas")) { cfgChanged |= ScreensTab(cfg); ImGui::EndTabItem(); }
                    static bool firstTab = true;
                    const char* tabEnv = SDL_getenv("ST_TAB");
                    ImGuiTabItemFlags cf = (firstTab && tabEnv && std::string(tabEnv) == "controls") ? ImGuiTabItemFlags_SetSelected : 0;
                    firstTab = false;
                    if (ImGui::BeginTabItem("Controles", nullptr, cf)) { ctrlChanged |= ControlsTab(icfg, cap, pad != nullptr); ImGui::EndTabItem(); }
                    ImGui::EndTabBar();
                }
                ImGui::TextDisabled("F1/Guide menú · F2 swap · F3 layout · Tab pantalla · F11 completa");
            }
            ImGui::End();
        }
        ImGui::Render();

        if (cfgChanged) {
            st::SaveConfig(layoutPath, cfg);
            layout = st::ComputeLayout(cfg, ww, wh);
        }
        if (ctrlChanged) st::SaveInputConfig(controlsPath, icfg);
        if (texFilter != cfg.filter) {
            SDL_DestroyTexture(texTop);
            SDL_DestroyTexture(texBot);
            texTop = makeTex();
            texBot = makeTex();
            texFilter = cfg.filter;
        }

        src->Frame(fbTop.data(), fbBot.data());
        SDL_UpdateTexture(texTop, nullptr, fbTop.data(), st::kScreenW * 4);
        SDL_UpdateTexture(texBot, nullptr, fbBot.data(), st::kScreenW * 4);

        SDL_RenderSetScale(ren, 1, 1);
        SDL_SetRenderDrawColor(ren, (Uint8)(cfg.bgColor[0] * 255), (Uint8)(cfg.bgColor[1] * 255),
                               (Uint8)(cfg.bgColor[2] * 255), 255);
        SDL_RenderClear(ren);
        DrawScreen(ren, texTop, layout.top);
        DrawScreen(ren, texBot, layout.bottom);

        // Ayudas visuales: lápiz virtual y cursor
        const float scale = layout.bottom.w / (layout.bottom.angle % 180 ? st::kScreenH : st::kScreenW);
        float wx, wy;
        if (icfg.showVirtualStylus && ds.touch && ds.virtualTouch &&
            st::TouchToWindow(layout, ds.touchX + 0.5f, ds.touchY + 0.5f, &wx, &wy)) {
            SDL_SetRenderDrawColor(ren, 255, 255, 255, 220);
            DrawCircle(ren, wx, wy, 5 * scale);
            DrawCross(ren, wx, wy, 3 * scale);
        }
        if (ds.cursorActive && st::TouchToWindow(layout, ds.cursorX, ds.cursorY, &wx, &wy)) {
            SDL_SetRenderDrawColor(ren, 255, 210, 60, 230);
            DrawCircle(ren, wx, wy, 7 * scale);
            DrawCross(ren, wx, wy, 9 * scale);
        }

        SDL_RenderSetScale(ren, dpi, dpi);
        ImGui_ImplSDLRenderer2_RenderDrawData(ImGui::GetDrawData(), ren);
        SDL_RenderPresent(ren);
    }

    st::SaveConfig(layoutPath, cfg);
    st::SaveInputConfig(controlsPath, icfg);
    if (pad) SDL_GameControllerClose(pad);
    ImGui_ImplSDLRenderer2_Shutdown();
    ImGui_ImplSDL2_Shutdown();
    ImGui::DestroyContext();
    SDL_DestroyTexture(texTop);
    SDL_DestroyTexture(texBot);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return 0;
}
