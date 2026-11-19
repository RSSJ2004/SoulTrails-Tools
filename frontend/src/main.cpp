// Frontend de SoulTrails: ventana SDL2 con las dos pantallas DS y un menú
// (Dear ImGui) para ajustar el layout. El "juego" se conecta implementando
// ScreenSource; por ahora se muestra un patrón de prueba.
//
// Teclas: F1 menú · F2 intercambiar · F3 siguiente layout · Tab alternar pantalla
//         (modo una pantalla) · F11 pantalla completa · Esc salir

#include <SDL.h>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "imgui.h"
#include "imgui_impl_sdl2.h"
#include "imgui_impl_sdlrenderer2.h"
#include "layout.h"

namespace {

// Interfaz que implementará el port: entrega los framebuffers ARGB8888
// (256x192) de cada pantalla y recibe la entrada táctil.
class ScreenSource {
public:
    virtual ~ScreenSource() = default;
    virtual void Frame(uint32_t* top, uint32_t* bottom) = 0;
    virtual void Touch(bool down, int x, int y) = 0;
};

class TestPattern : public ScreenSource {
public:
    void Frame(uint32_t* top, uint32_t* bottom) override {
        ++t_;
        for (int y = 0; y < st::kScreenH; ++y) {
            for (int x = 0; x < st::kScreenW; ++x) {
                uint8_t r = (uint8_t)(x + t_), g = (uint8_t)(y * 255 / st::kScreenH), b = 160;
                bool border = x < 2 || y < 2 || x >= st::kScreenW - 2 || y >= st::kScreenH - 2;
                top[y * st::kScreenW + x] = border ? 0xFFFFFFFF : (0xFF000000u | r << 16 | g << 8 | b);
                uint8_t c = ((x / 16 + y / 16) & 1) ? 70 : 40;
                bottom[y * st::kScreenW + x] = border ? 0xFFFFD040 : (0xFF000000u | c << 16 | c << 8 | (c + 30));
            }
        }
        // "flecha" en la superior para ver la orientación
        for (int i = 0; i < 40; ++i)
            for (int j = -i / 3; j <= i / 3; ++j) top[(20 + i) * st::kScreenW + 128 + j] = 0xFFFFFFFF;
        if (touching_) {
            for (int dy = -4; dy <= 4; ++dy)
                for (int dx = -4; dx <= 4; ++dx) {
                    int px = tx_ + dx, py = ty_ + dy;
                    if (px >= 0 && py >= 0 && px < st::kScreenW && py < st::kScreenH)
                        bottom[py * st::kScreenW + px] = 0xFFFF3030;
                }
        }
    }
    void Touch(bool down, int x, int y) override { touching_ = down; tx_ = x; ty_ = y; }

private:
    int t_ = 0, tx_ = 0, ty_ = 0;
    bool touching_ = false;
};

std::string ConfigPath() {
    char* base = SDL_GetPrefPath("SoulTrails", "frontend");
    std::string p = base ? std::string(base) + "layout.ini" : "layout.ini";
    SDL_free(base);
    return p;
}

void DrawScreen(SDL_Renderer* r, SDL_Texture* tex, const st::ScreenRect& s) {
    if (!s.visible) return;
    SDL_FRect dst{s.cx - s.w / 2, s.cy - s.h / 2, s.w, s.h};
    SDL_RenderCopyExF(r, tex, nullptr, &dst, (double)s.angle, nullptr, SDL_FLIP_NONE);
}

bool LayoutMenu(st::LayoutConfig& c, bool* open) {
    bool changed = false;
    ImGui::SetNextWindowSize(ImVec2(340, 0), ImGuiCond_FirstUseEver);
    if (!ImGui::Begin("Pantallas", open)) { ImGui::End(); return false; }

    int mode = (int)c.mode;
    const char* modes[] = {"Vertical", "Horizontal", "Grande + pequeña", "Una pantalla"};
    changed |= ImGui::Combo("Layout", &mode, modes, IM_ARRAYSIZE(modes));
    c.mode = (st::LayoutMode)mode;

    if (c.mode == st::LayoutMode::SingleScreen) {
        int which = c.singleShowsBottom;
        changed |= ImGui::RadioButton("Superior", &which, 0); ImGui::SameLine();
        changed |= ImGui::RadioButton("Inferior (táctil)", &which, 1);
        c.singleShowsBottom = which;
        ImGui::TextDisabled("Tab alterna la pantalla visible");
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
    changed |= ImGui::Checkbox("Mostrar este menú al iniciar", &c.showMenuOnStart);

    ImGui::Separator();
    if (ImGui::Button("Restablecer")) { c = st::LayoutConfig{}; changed = true; }
    ImGui::SameLine();
    ImGui::TextDisabled("F1 menú · F2 swap · F3 layout");
    ImGui::End();
    return changed;
}

}  // namespace

int main(int, char**) {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS) != 0) {
        std::fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    st::LayoutConfig cfg;
    const std::string cfgPath = ConfigPath();
    st::LoadConfig(cfgPath, &cfg);

    SDL_Window* win = SDL_CreateWindow("SoulTrails", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 768, 768,
                                       SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    SDL_Renderer* ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!win || !ren) {
        std::fprintf(stderr, "SDL: %s\n", SDL_GetError());
        return 1;
    }

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGui::GetIO().IniFilename = nullptr;
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

    bool menu = cfg.showMenuOnStart, running = true, mouseDown = false;
    while (running) {
        int ww, wh;
        SDL_GetRendererOutputSize(ren, &ww, &wh);
        int lw, lh;
        SDL_GetWindowSize(win, &lw, &lh);
        const float dpi = lw > 0 ? (float)ww / lw : 1.0f;
        st::Layout layout = st::ComputeLayout(cfg, ww, wh);
        bool cfgChanged = false;

        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            ImGui_ImplSDL2_ProcessEvent(&e);
            bool uiMouse = ImGui::GetIO().WantCaptureMouse;
            switch (e.type) {
                case SDL_QUIT: running = false; break;
                case SDL_KEYDOWN:
                    if (ImGui::GetIO().WantCaptureKeyboard) break;
                    switch (e.key.keysym.sym) {
                        case SDLK_ESCAPE: running = false; break;
                        case SDLK_F1: menu = !menu; break;
                        case SDLK_F2: cfg.swap = !cfg.swap; cfgChanged = true; break;
                        case SDLK_F3:
                            cfg.mode = (st::LayoutMode)(((int)cfg.mode + 1) % (int)st::LayoutMode::Count);
                            cfgChanged = true;
                            break;
                        case SDLK_TAB: cfg.singleShowsBottom = !cfg.singleShowsBottom; cfgChanged = true; break;
                        case SDLK_F11: {
                            bool fs = SDL_GetWindowFlags(win) & SDL_WINDOW_FULLSCREEN_DESKTOP;
                            SDL_SetWindowFullscreen(win, fs ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP);
                            break;
                        }
                    }
                    break;
                case SDL_MOUSEBUTTONDOWN:
                case SDL_MOUSEBUTTONUP:
                case SDL_MOUSEMOTION: {
                    if (uiMouse && e.type == SDL_MOUSEBUTTONDOWN) break;
                    if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT) mouseDown = true;
                    if (e.type == SDL_MOUSEBUTTONUP && e.button.button == SDL_BUTTON_LEFT) mouseDown = false;
                    float mx = (e.type == SDL_MOUSEMOTION ? e.motion.x : e.button.x) * dpi;
                    float my = (e.type == SDL_MOUSEMOTION ? e.motion.y : e.button.y) * dpi;
                    int tx, ty;
                    bool inside = st::WindowToTouch(layout, mx, my, &tx, &ty);
                    src->Touch(mouseDown && inside, tx, ty);
                    break;
                }
            }
        }

        ImGui_ImplSDLRenderer2_NewFrame();
        ImGui_ImplSDL2_NewFrame();
        ImGui::NewFrame();
        if (menu) cfgChanged |= LayoutMenu(cfg, &menu);
        ImGui::Render();

        if (cfgChanged) {
            st::SaveConfig(cfgPath, cfg);
            layout = st::ComputeLayout(cfg, ww, wh);
        }
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
        SDL_RenderSetScale(ren, dpi, dpi);
        ImGui_ImplSDLRenderer2_RenderDrawData(ImGui::GetDrawData(), ren);
        SDL_RenderPresent(ren);
    }

    st::SaveConfig(cfgPath, cfg);
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
