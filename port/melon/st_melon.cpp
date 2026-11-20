// Port híbrido: el ARM9 del juego es el código recompilado y melonDS pone todo lo demás (ARM7 con
// su código real, sonido, cartucho y chip de guardado, timers, DMA, motores 2D/3D, táctil, RTC).
// Mismo enfoque que el port de Phantom Hourglass (mismo motor que Spirit Tracks).
//
// Reparto del tiempo: melonDS::NDS::RunFrame avanza la consola; en el turno del ARM9 llama a
// ARM9ExternalExecute (parche en patch_melonds.cmake), que cambia a la fibra del juego. El
// código generado descuenta un presupuesto en cada entrada de función y vuelta de bucle; en
// st_checkpoint se suman ciclos a ARM9Timestamp y, al llegar a ARM9Target, se vuelve al
// planificador. Las IRQ las entrega el runtime (HLE del BIOS) leyendo IME/IE/IF de melonDS.
//
// Este archivo forma parte del port híbrido con melonDS (GPL-3.0, ver LICENSE).
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "Args.h"
#include "NDS.h"
#include "NDSCart.h"
#include "SPI_Firmware.h"
#include "st_melon.h"

extern "C" {
#include "st_rom.h"
#include "strecomp.h"
}

using namespace melonDS;

namespace {

std::unique_ptr<NDS> g_nds;
StCpu* g_cpu;
const StRom* g_rom;
std::string g_save_path;
bool g_halted;
uint32_t g_cycles_per_check = 24;   // ciclos ARM9 (33,5 MHz x 2) por entrada de función/vuelta

// ------------------------------------------------------------------ memoria
uint8_t* mem_ptr(uint32_t addr, uint32_t size) {
    ARMv5& a = g_nds->ARM9;
    if (addr < a.ITCMSize) return &a.ITCM[addr & (ITCMPhysicalSize - 1)];
    if ((addr & a.DTCMMask) == a.DTCMBase) {
        uint32_t o = addr & (DTCMPhysicalSize - 1);
        return o + size <= DTCMPhysicalSize ? &a.DTCM[o] : nullptr;
    }
    if ((addr >> 24) == 0x02) {
        uint32_t o = addr & g_nds->MainRAMMask;
        return o + size <= g_nds->MainRAMMask + 1 ? &g_nds->MainRAM[o] : nullptr;
    }
    return nullptr;   // E/S, VRAM, paleta, OAM, WRAM compartida...: por el bus de melonDS
}
uint32_t mem_read(uint32_t addr, int size) {
    if (size == 1) return g_nds->ARM9Read8(addr);
    if (size == 2) return g_nds->ARM9Read16(addr & ~1u);
    return g_nds->ARM9Read32(addr & ~3u);
}
void mem_write(uint32_t addr, uint32_t v, int size) {
    if (size == 1) g_nds->ARM9Write8(addr, (u8)v);
    else if (size == 2) g_nds->ARM9Write16(addr & ~1u, (u16)v);
    else g_nds->ARM9Write32(addr & ~3u, v);
}

// --------------------------------------------------------------- tiempo/IRQ
// Espera a la próxima IRQ (CP15 "esperar IRQ", Halt e IntrWait del BIOS)
int halt_until_irq(StCpu*) {
    g_halted = true;
    g_nds->ARM9Timestamp = g_nds->ARM9Target;
    st_fiber_yield_host();
    return 1;
}
uint32_t cp15_read(uint32_t id) { return g_nds->ARM9.CP15Read(id); }
void cp15_write(StCpu* c, uint32_t id, uint32_t v) {
    if (id == 0x704 || id == 0x782) { halt_until_irq(c); return; }
    g_nds->ARM9.CP15Write(id, v);
}
void irq_regs(uint32_t* ime, uint32_t* ie, uint32_t* if_) {
    *ime = g_nds->IME[0];
    *ie = g_nds->IE[0];
    *if_ = g_nds->IF[0];
}
void checkpoint(StCpu*, int32_t used) {
    g_nds->ARM9Timestamp += (u64)used * g_cycles_per_check;
    if (g_nds->ARM9Timestamp >= g_nds->ARM9Target) st_fiber_yield_host();
}

const StBackend g_backend = {mem_ptr, mem_read, mem_write, cp15_read, cp15_write, irq_regs, checkpoint};

// Turno del ARM9 en NDS::RunFrame
void arm9_execute() {
    if (g_halted) {
        if (g_nds->HaltInterrupted(0)) {
            g_halted = false;
        } else {
            g_nds->ARM9Timestamp = g_nds->ARM9Target;
            return;
        }
    }
    st_fiber_run_guest();
}

void guest_boot() {
    uint32_t entry = st_boot_prepare(g_cpu, g_rom);
    st_irq_refresh();
    st_call(g_cpu, entry);
}

bool read_file(const std::string& path, std::vector<u8>& out) {
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    out.resize(n > 0 ? (size_t)n : 0);
    bool ok = n <= 0 || std::fread(out.data(), 1, out.size(), f) == out.size();
    std::fclose(f);
    return ok;
}

}  // namespace

namespace melonDS {
namespace Platform {
// Guardado atómico: archivo temporal + renombrar, para no corromper nunca la partida
void st_melon_write_save(const u8* data, u32 len) {
    if (g_save_path.empty()) return;
    std::string tmp = g_save_path + ".tmp";
    std::FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return;
    bool ok = std::fwrite(data, 1, len, f) == len;
    ok = std::fclose(f) == 0 && ok;
    if (!ok) { std::remove(tmp.c_str()); return; }
#ifdef _WIN32
    std::remove(g_save_path.c_str());
#endif
    std::rename(tmp.c_str(), g_save_path.c_str());
}
}  // namespace Platform
}  // namespace melonDS

extern "C" int st_melon_open(StCpu* c, const StRom* rom, const char* rom_path, const char* save_path, int language,
                             char* err, size_t errlen) {
    auto fail = [&](const char* m) {
        if (err && errlen) std::snprintf(err, errlen, "%s", m);
        return -1;
    };
    g_cpu = c;
    g_rom = rom;
    g_save_path = save_path ? save_path : "";

    std::vector<u8> romdata(rom->data, rom->data + rom->size);
    NDSCart::NDSCartArgs cargs;
    std::vector<u8> save;
    if (!g_save_path.empty() && read_file(g_save_path, save) && !save.empty()) {
        cargs.SRAM = std::make_unique<u8[]>(save.size());
        std::memcpy(cargs.SRAM.get(), save.data(), save.size());
        cargs.SRAMLength = (u32)save.size();
    }
    auto cart = NDSCart::ParseROM(romdata.data(), (u32)romdata.size(), nullptr, std::move(cargs));
    if (!cart) return fail("melonDS no reconoce la ROM");

    NDSArgs args;
    args.JIT = std::nullopt;
    Firmware fw(0);
    auto& ud = fw.GetEffectiveUserData();
    ud.Settings = (u16)((ud.Settings & ~7u) | (language & 7));
    fw.UpdateChecksums();
    args.Firmware = std::move(fw);
    g_nds = std::make_unique<NDS>(std::move(args));
    g_nds->Reset();
    g_nds->SetNDSCart(std::move(cart));
    g_nds->SetupDirectBoot("soultrails.nds");
    g_nds->Start();

    st_backend = &g_backend;
    st_platform.idle = halt_until_irq;
    ARM9ExternalExecute = arm9_execute;
    if (!st_fiber_guest_start(c, guest_boot)) return fail("no se pudo crear la fibra del juego");
    return 0;
}

extern "C" void st_melon_run_frame(void) { g_nds->RunFrame(); }

extern "C" const uint32_t* st_melon_screen(int bottom) {
    return g_nds->GPU.Framebuffer[g_nds->GPU.FrontBuffer][bottom ? 1 : 0].get();
}

extern "C" void st_melon_set_keys(uint32_t pressed) {
    // Bits: A B Select Start Der Izq Arr Abj R L X Y (melonDS: 1 = suelto)
    g_nds->SetKeyMask(~pressed & 0xFFFu);
}

extern "C" void st_melon_touch(int down, int x, int y) {
    if (down) g_nds->TouchScreen((u16)x, (u16)y);
    else g_nds->ReleaseScreen();
}

extern "C" int st_melon_audio(int16_t* out, int frames) { return g_nds->SPU.ReadOutput(out, frames); }

extern "C" void st_melon_close(void) {
    ARM9ExternalExecute = nullptr;
    st_backend = nullptr;
    g_nds.reset();
}
