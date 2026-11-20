#include "layout.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>

namespace st {
namespace {

struct R {
    float x, y, w, h;
};

// Rota un rectángulo dentro de un lienzo W x H, en sentido horario.
R RotateRect(R r, float W, float H, int rot) {
    switch (rot) {
        case 90: return {H - r.y - r.h, r.x, r.h, r.w};
        case 180: return {W - r.x - r.w, H - r.y - r.h, r.w, r.h};
        case 270: return {r.y, W - r.x - r.w, r.h, r.w};
        default: return r;
    }
}

int NormRot(int r) {
    r = ((r % 360) + 360) % 360;
    return (r / 90) * 90;
}

}  // namespace

const char* LayoutModeName(LayoutMode m) {
    switch (m) {
        case LayoutMode::Vertical: return "Vertical";
        case LayoutMode::Horizontal: return "Horizontal";
        case LayoutMode::LargeSmall: return "Grande + pequeña";
        case LayoutMode::SingleScreen: return "Una pantalla";
        default: return "?";
    }
}

Layout ComputeLayout(const LayoutConfig& cfg, int windowW, int windowH) {
    const float sw = kScreenW, sh = kScreenH;
    const float gap = static_cast<float>(std::max(0, cfg.gap));
    // 'a' es la pantalla principal (superior salvo swap), 'b' la otra.
    R a{}, b{};
    bool aVis = true, bVis = true;
    float W = 0, H = 0;

    switch (cfg.mode) {
        case LayoutMode::Vertical:
            a = {0, 0, sw, sh};
            b = {0, sh + gap, sw, sh};
            W = sw; H = sh * 2 + gap;
            break;
        case LayoutMode::Horizontal:
            a = {0, 0, sw, sh};
            b = {sw + gap, 0, sw, sh};
            W = sw * 2 + gap; H = sh;
            break;
        case LayoutMode::LargeSmall: {
            float k = std::clamp(cfg.largeRatio, 1.0f, 4.0f);
            float bw = sw / k * 1.0f, bh = sh / k;
            // la grande ocupa sw*1 en unidades del lienzo; la pequeña 1/k
            a = {0, 0, sw, sh};
            float by = 0;
            if (cfg.smallPos == SmallPos::Center) by = (sh - bh) / 2;
            if (cfg.smallPos == SmallPos::Bottom) by = sh - bh;
            b = {sw + gap, by, bw, bh};
            W = sw + gap + bw; H = sh;
            break;
        }
        case LayoutMode::SingleScreen:
        default:
            a = {0, 0, sw, sh};
            b = a;
            W = sw; H = sh;
            aVis = true; bVis = false;
            break;
    }

    // ¿Qué pantalla física va en cada hueco?
    bool topIsA = !cfg.swap;
    if (cfg.mode == LayoutMode::SingleScreen) topIsA = !cfg.singleShowsBottom;

    const int rot = NormRot(cfg.rotation);
    R ra = RotateRect(a, W, H, rot), rb = RotateRect(b, W, H, rot);
    float CW = (rot % 180) ? H : W, CH = (rot % 180) ? W : H;

    float scale = std::min(windowW / CW, windowH / CH);
    if (cfg.integerScale && scale >= 1.0f) scale = std::floor(scale);
    float ox = (windowW - CW * scale) / 2, oy = (windowH - CH * scale) / 2;

    auto toScreen = [&](R r, bool vis) {
        ScreenRect s;
        s.visible = vis;
        s.cx = ox + (r.x + r.w / 2) * scale;
        s.cy = oy + (r.y + r.h / 2) * scale;
        // tamaño sin rotar
        float w = r.w * scale, h = r.h * scale;
        if (rot % 180) std::swap(w, h);
        s.w = w; s.h = h;
        s.angle = rot;
        return s;
    };

    Layout out;
    ScreenRect sa = toScreen(ra, aVis), sb = toScreen(rb, bVis);
    out.top = topIsA ? sa : sb;
    out.bottom = topIsA ? sb : sa;
    if (cfg.mode == LayoutMode::SingleScreen) {
        out.top.visible = topIsA;
        out.bottom.visible = !topIsA;
    }
    return out;
}

bool WindowToTouch(const Layout& l, float wx, float wy, int* tx, int* ty) {
    const ScreenRect& s = l.bottom;
    if (!s.visible || s.w <= 0 || s.h <= 0) return false;
    float dx = wx - s.cx, dy = wy - s.cy;
    // deshacer la rotación (horaria) => rotar -angle
    float ux = dx, uy = dy;
    switch (s.angle) {
        case 90: ux = dy; uy = -dx; break;
        case 180: ux = -dx; uy = -dy; break;
        case 270: ux = -dy; uy = dx; break;
        default: break;
    }
    float nx = (ux + s.w / 2) / s.w, ny = (uy + s.h / 2) / s.h;
    if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1) return false;
    *tx = static_cast<int>(nx * kScreenW);
    *ty = static_cast<int>(ny * kScreenH);
    return true;
}

bool TouchToWindow(const Layout& l, float tx, float ty, float* wx, float* wy) {
    const ScreenRect& s = l.bottom;
    if (!s.visible) return false;
    float ux = tx / kScreenW * s.w - s.w / 2, uy = ty / kScreenH * s.h - s.h / 2;
    float dx = ux, dy = uy;
    switch (s.angle) {  // rotación horaria
        case 90: dx = -uy; dy = ux; break;
        case 180: dx = -ux; dy = -uy; break;
        case 270: dx = uy; dy = -ux; break;
        default: break;
    }
    *wx = s.cx + dx;
    *wy = s.cy + dy;
    return true;
}

bool SaveConfig(const std::string& path, const LayoutConfig& c) {
    std::ofstream f(path);
    if (!f) return false;
    f << "[layout]\n"
      << "mode=" << static_cast<int>(c.mode) << "\n"
      << "swap=" << c.swap << "\n"
      << "rotation=" << c.rotation << "\n"
      << "gap=" << c.gap << "\n"
      << "integer_scale=" << c.integerScale << "\n"
      << "large_ratio=" << c.largeRatio << "\n"
      << "small_pos=" << static_cast<int>(c.smallPos) << "\n"
      << "single_shows_bottom=" << c.singleShowsBottom << "\n"
      << "filter=" << static_cast<int>(c.filter) << "\n"
      << "bg=" << c.bgColor[0] << "," << c.bgColor[1] << "," << c.bgColor[2] << "\n"
      << "show_menu_on_start=" << c.showMenuOnStart << "\n";
    return static_cast<bool>(f);
}

bool LoadConfig(const std::string& path, LayoutConfig* c) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    auto clampEnum = [](int v, int count) { return std::clamp(v, 0, count - 1); };
    while (std::getline(f, line)) {
        auto eq = line.find('=');
        if (eq == std::string::npos || line[0] == '[' || line[0] == '#') continue;
        std::string k = line.substr(0, eq), v = line.substr(eq + 1);
        int i = std::atoi(v.c_str());
        if (k == "mode") c->mode = static_cast<LayoutMode>(clampEnum(i, (int)LayoutMode::Count));
        else if (k == "swap") c->swap = i != 0;
        else if (k == "rotation") c->rotation = NormRot(i);
        else if (k == "gap") c->gap = std::clamp(i, 0, 256);
        else if (k == "integer_scale") c->integerScale = i != 0;
        else if (k == "large_ratio") c->largeRatio = std::clamp((float)std::atof(v.c_str()), 1.0f, 4.0f);
        else if (k == "small_pos") c->smallPos = static_cast<SmallPos>(clampEnum(i, (int)SmallPos::Count));
        else if (k == "single_shows_bottom") c->singleShowsBottom = i != 0;
        else if (k == "filter") c->filter = static_cast<Filter>(clampEnum(i, (int)Filter::Count));
        else if (k == "show_menu_on_start") c->showMenuOnStart = i != 0;
        else if (k == "bg") std::sscanf(v.c_str(), "%f,%f,%f", &c->bgColor[0], &c->bgColor[1], &c->bgColor[2]);
    }
    return true;
}

}  // namespace st
