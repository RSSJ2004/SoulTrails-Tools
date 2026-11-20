#!/usr/bin/env python3
"""stk - Spirit Tracks toolkit.

Automatiza las partes repetitivas del trabajo de decompilación/port y guarda
todo el estado en disco (.stk/) para que nunca se pierda el contexto entre
sesiones.

Uso rápido:
    python stk.py init --decomp ../SoulTrails --rom ruta/a/tu.nds
    python stk.py doctor
    python stk.py setup            # descarga Ghidra, NTRGhidra y objdiff-cli
    python stk.py rom              # información de la ROM
    python stk.py assets           # extrae y convierte assets a assets/
    python stk.py recomp           # recompilación estática a C (build/recomp)
    python stk.py ghidra import    # importa y analiza la ROM (una vez)
    python stk.py ghidra export    # exporta pseudo-C de todas las funciones
    python stk.py build            # configure.py + ninja en el repo decomp
    python stk.py progress         # informe objdiff + histórico
    python stk.py next -n 5        # crea fichas de trabajo de las siguientes funciones
    python stk.py loop             # build -> progress -> next, en bucle (Ctrl+C para parar)
    python stk.py journal "nota"   # bitácora persistente
    python stk.py status           # resumen de dónde te quedaste

Solo usa la biblioteca estándar de Python (3.10+).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Carpeta de trabajo (estado, assets, build...). Por defecto, junto a stk.py.
HOME = Path(os.environ.get("STK_HOME", ROOT)).resolve()
STATE_DIR = HOME / ".stk"
CONFIG_FILE = STATE_DIR / "config.json"
STATE_FILE = STATE_DIR / "state.json"
JOURNAL_FILE = STATE_DIR / "journal.md"
HISTORY_FILE = STATE_DIR / "progress_history.jsonl"
TOOLS_DIR = HOME / "tools_bin"
WORK_DIR = HOME / "work"
GHIDRA_SCRIPTS = ROOT / "ghidra_scripts"

# SHA1 de las ROMs soportadas por la decomp (públicos en su README).
KNOWN_ROMS = {
    "eaee3602b8a2235211b2e20cdcd4cb357956a264": "jp",
    "9e99cc803a14ce038eb908db585431f8254f09ee": "eur",
    "dcb221a89acb894503a461c2c7d4cfdad9a06b68": "eur1",
    "b9096f46e9163750a9e3f6c2fc0758e6094b6135": "eur1",
    "4efbff9a263fe622f3f7e7cfb7bea8046b86e57d": "eur1",
    "1f0921b5e99db9d92d24710f1b004e4f3c997b73": "usa",
}

GITHUB_RELEASES = {
    "ghidra": "NationalSecurityAgency/ghidra",
    "ntrghidra": "pedro-javierf/NTRGhidra",
    "objdiff": "encounter/objdiff",
}


# ----------------------------------------------------------------- utilidades
def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def config() -> dict:
    cfg = load_json(CONFIG_FILE, None)
    if cfg is None:
        sys.exit("No hay configuración. Ejecuta primero: python stk.py init --decomp <repo> --rom <rom.nds>")
    return cfg


def state() -> dict:
    return load_json(STATE_FILE, {"tasks": {}, "last_command": None})


def save_state(st: dict) -> None:
    save_json(STATE_FILE, st)


def journal(text: str) -> None:
    JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_FILE.open("a", encoding="utf-8") as f:
        f.write(f"- **{now()}** {text}\n")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    try:
        rc = subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        journal(f"FALTA el programa `{cmd[0]}`")
        sys.exit(f"No encuentro el programa '{cmd[0]}'. Instálalo y asegúrate de que está en el PATH "
                 f"(python stk.py doctor dice qué falta y cómo instalarlo).")
    if check and rc != 0:
        journal(f"FALLO ({rc}): `{' '.join(str(c) for c in cmd)}`")
        sys.exit(rc)
    return rc


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def http_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "stk"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  ya descargado: {dest.name}")
        return dest
    print(f"  descargando {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "stk"})
    with urllib.request.urlopen(req, timeout=600) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


# ----------------------------------------------------------------------- init
def cmd_init(a) -> None:
    decomp = Path(a.decomp).resolve() if a.decomp else None
    rom = Path(a.rom).resolve()
    if decomp and not (decomp / "tools" / "configure.py").exists():
        sys.exit(f"{decomp} no parece el repo de la decomp (falta tools/configure.py)")
    if not rom.exists():
        sys.exit(f"No existe la ROM: {rom}")
    digest = sha1_of(rom)
    version = KNOWN_ROMS.get(digest)
    if version is None:
        print(f"AVISO: SHA1 {digest} no coincide con ninguna ROM soportada.")
        version = a.version or "eur"
    cfg = load_json(CONFIG_FILE, {})
    cfg.update({"decomp": str(decomp) if decomp else None, "rom": str(rom), "rom_sha1": digest, "version": a.version or version})
    save_json(CONFIG_FILE, cfg)
    journal(f"init: decomp={decomp} rom={rom.name} version={cfg['version']}")
    print(json.dumps(cfg, indent=2))


# --------------------------------------------------------------------- doctor
def find_ghidra() -> Path | None:
    cfg = load_json(CONFIG_FILE, {})
    for cand in [cfg.get("ghidra_home"), os.environ.get("GHIDRA_INSTALL_DIR")]:
        if cand and Path(cand, "support").exists():
            return Path(cand)
    for p in sorted(TOOLS_DIR.glob("ghidra_*"), reverse=True):
        if (p / "support").exists():
            return p
    return None


def find_objdiff() -> str | None:
    cfg = load_json(CONFIG_FILE, {})
    if cfg.get("objdiff_cli") and Path(cfg["objdiff_cli"]).exists():
        return cfg["objdiff_cli"]
    local = TOOLS_DIR / exe("objdiff-cli")
    if local.exists():
        return str(local)
    return shutil.which("objdiff-cli")


def cmd_doctor(a) -> None:
    ok = True

    def check(label, cond, hint=""):
        nonlocal ok
        print(f"  [{'OK' if cond else '--'}] {label}" + ("" if cond else f"  -> {hint}"))
        ok &= bool(cond)

    print("Herramientas:")
    check("Python >= 3.10", sys.version_info >= (3, 10), "actualiza Python")
    check("ninja", shutil.which("ninja"), "instala Ninja")
    check("cmake (recomp --compile/--learn, frontend)", shutil.which("cmake"),
          "Windows: winget install Kitware.CMake  (o https://cmake.org/download/)")
    cc = c_compiler()
    check(f"compilador de C/C++ ({cc or 'ninguno'})", cc,
          "Windows: Visual Studio Build Tools con 'Desarrollo para el escritorio con C++' "
          "(winget install Microsoft.VisualStudio.2022.BuildTools) o MinGW-w64")
    check("java (JDK 21+ para Ghidra)", shutil.which("java"), "instala un JDK 21")
    check("Ghidra", find_ghidra(), "python stk.py setup")
    g = find_ghidra()
    check("NTRGhidra instalado", g and any((g / "Ghidra" / "Extensions").glob("NTRGhidra*")), "python stk.py setup")
    check("objdiff-cli", find_objdiff(), "python stk.py setup")
    cfg = load_json(CONFIG_FILE, None)
    print("Proyecto:")
    check("configuración (.stk/config.json)", cfg, "python stk.py init ...")
    if cfg:
        check("ROM existe", Path(cfg["rom"]).exists(), "revisa la ruta de la ROM")
        check(f"ROM reconocida ({cfg['version']})", cfg["rom_sha1"] in KNOWN_ROMS, "usa una ROM limpia")
        d = Path(cfg["decomp"] or ".")
        check("baserom copiada en extract/", any((d / "extract").glob("*.nds")), "python stk.py build la copia")
        check("objdiff.json generado", (d / "objdiff.json").exists(), "python stk.py build")
    print("\nTodo listo." if ok else "\nFaltan cosas (ver arriba).")


# ---------------------------------------------------------------------- setup
def pick_asset(assets, *needles, exclude=()):
    for asset in assets:
        n = asset["name"].lower()
        if all(x in n for x in needles) and not any(x in n for x in exclude):
            return asset
    return None


def objdiff_asset_name() -> tuple[str, ...]:
    sysname = platform.system().lower()
    arch = platform.machine().lower()
    arch = "arm64" if arch in ("arm64", "aarch64") else "x86_64"
    osname = {"windows": "windows", "darwin": "macos"}.get(sysname, "linux")
    return ("objdiff-cli", osname, arch)


def cmd_setup(a) -> None:
    TOOLS_DIR.mkdir(exist_ok=True)
    cfg = load_json(CONFIG_FILE, {})

    if not find_ghidra() or a.force:
        print("Ghidra:")
        rel = http_json(f"https://api.github.com/repos/{GITHUB_RELEASES['ghidra']}/releases/latest")
        asset = pick_asset(rel["assets"], ".zip")
        z = download(asset["browser_download_url"], TOOLS_DIR / asset["name"])
        with zipfile.ZipFile(z) as zf:
            zf.extractall(TOOLS_DIR)
        cfg["ghidra_home"] = str(find_ghidra())
        if os.name != "nt":
            for f in Path(cfg["ghidra_home"]).rglob("*"):
                if f.is_file() and (f.suffix in ("", ".sh") and f.parent.name in ("support", "os", "linux_x86_64", "mac_x86_64", "mac_arm_64", "linux_arm_64")):
                    f.chmod(f.stat().st_mode | 0o111)

    ghidra = find_ghidra()
    ext_dir = ghidra / "Ghidra" / "Extensions"
    if ghidra and (not any(ext_dir.glob("NTRGhidra*")) or a.force):
        print("NTRGhidra (loader de ROMs NDS):")
        rel = http_json(f"https://api.github.com/repos/{GITHUB_RELEASES['ntrghidra']}/releases/latest")
        asset = pick_asset(rel["assets"], ".zip")
        if asset is None:
            print("  No encontré un .zip en el release; instálalo a mano desde File > Install Extensions.")
        else:
            z = download(asset["browser_download_url"], TOOLS_DIR / asset["name"])
            with zipfile.ZipFile(z) as zf:
                zf.extractall(ext_dir)
            print("  Nota: el zip debe corresponder a tu versión de Ghidra; si Ghidra lo rechaza,")
            print("  compílalo con gradle según el README de NTRGhidra.")

    if not find_objdiff() or a.force:
        print("objdiff-cli:")
        rel = http_json(f"https://api.github.com/repos/{GITHUB_RELEASES['objdiff']}/releases/latest")
        asset = pick_asset(rel["assets"], *objdiff_asset_name())
        if asset is None:
            print("  No hay binario para tu plataforma; instala con: cargo install objdiff-cli")
        else:
            dest = TOOLS_DIR / exe("objdiff-cli")
            tmp = download(asset["browser_download_url"], TOOLS_DIR / asset["name"])
            shutil.copyfile(tmp, dest)
            dest.chmod(0o755)
            cfg["objdiff_cli"] = str(dest)

    if cfg.get("decomp"):
        req = Path(cfg["decomp"]) / "tools" / "requirements.txt"
        if req.exists():
            print("Dependencias Python de la decomp:")
            run([sys.executable, "-m", "pip", "install", "-r", req], check=False)

    save_json(CONFIG_FILE, cfg)
    journal("setup completado")
    cmd_doctor(a)


# ------------------------------------------------------------------------ rom
sys.path.insert(0, str(ROOT))


def cmd_rom(a) -> None:
    """Información de la ROM."""
    from sttools.nds import Rom
    cfg = config()
    rom = Rom.load(cfg["rom"])
    for k, v in rom.header.items():
        print(f"  {k:12} {hex(v) if isinstance(v, int) else v}")
    stored, calc = rom.header_crc()
    print(f"  CRC cabecera {stored:#06x} {'OK' if stored == calc else f'NO COINCIDE (calculado {calc:#06x}): ROM dañada o modificada'}")
    print(f"  overlays ARM9: {len(rom.overlays9())}")
    print(f"  archivos NitroFS: {len(rom.files())}")
    mp = rom.arm9_module_params()
    if mp:
        print(f"  ARM9 comprimido: {'sí' if mp['compressed_static_end'] else 'no'}  SDK: {mp['sdk_version']:#x}")
        for s in rom.arm9_segments():
            print(f"  segmento {s.name:8} {s.base:#010x}  {len(s.data):8} bytes")


def cmd_assets(a) -> None:
    """Extrae y convierte todos los assets (ver sttools/assets.py)."""
    from sttools.assets import extract
    cfg = config()
    out = Path(a.out or HOME / "assets").resolve()
    print(f"Extrayendo assets de {Path(cfg['rom']).name} a {out} ...")
    t0 = time.time()
    s = extract(cfg["rom"], out, convert=not a.raw_only, verbose=a.verbose)
    print(f"Listo en {time.time() - t0:.0f}s: {s['files_in_rom']} archivos en la ROM")
    for k, v in s["types"].items():
        print(f"  {k:10} {v}")
    print(f"Errores: {s['errors']}  (detalle en {out / 'report.txt'})")
    journal(f"assets -> {out}: {s['types']} errores={s['errors']}")


# -------------------------------------------------------------------- recomp
MISSES = STATE_DIR / "recomp_misses.txt"
WARNINGS = STATE_DIR / "recomp_warnings.txt"
DECOMP_SYMBOLS = STATE_DIR / "decomp_symbols.txt"
FRAMES_DIR = STATE_DIR / "frames"


def recomp_once(a, cfg, rom, out: Path) -> dict:
    from sttools.recomp import driver
    mods = driver.modules_from_rom(rom)
    seeds = [MISSES] + [Path(s) for s in a.seeds]
    if not getattr(a, "no_decomp", False) and decomp_symbols(cfg):
        seeds.append(DECOMP_SYMBOLS)
    ghidra = HOME / "ghidra_export" / "functions.jsonl"
    nmiss = sum(1 for _ in MISSES.open()) if MISSES.exists() else 0
    print(f"Módulos: {len(mods)} (estático, ITCM/DTCM y overlays)")
    print(f"Semillas: punto de entrada, {'Ghidra, ' if ghidra.exists() else ''}prólogos, punteros, {nmiss} fallos aprendidos")
    t0 = time.time()
    res = driver.run(mods, out, rom.header["arm9_entry"], ghidra if ghidra.exists() else None, seeds,
                     scan=not a.no_scan, scan_thumb=a.scan_thumb, hooks={int(x, 16) for x in a.hook},
                     comments=not a.no_comments)
    if res.get("named"):
        print(f"  {res['named']} funciones con nombre del decomp (objdiff)")
    print(f"{res['functions']} funciones, {res['instructions']} instrucciones, "
          f"{res['undefined']} no soportadas, {len(res['files'])} archivos C en {out} ({time.time() - t0:.0f}s)")
    WARNINGS.write_text("\n".join(res["warnings"]) + "\n", encoding="utf-8")
    if res["warnings"]:
        kinds = {}
        for w in res["warnings"]:
            k = w.split(": ", 1)[-1] if ": " in w else w
            k = "llamada en ARM y Thumb a la vez" if "ARM y Thumb" in w else k
            kinds[k] = kinds.get(k, 0) + 1
        print(f"  {len(res['warnings'])} avisos (todos en {WARNINGS}):")
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:5]:
            print(f"    {n:6}  {k}")
    for u in res.get("undefined_list", [])[:20]:
        print(f"  no soportada: {u}")
    return res


def c_compiler() -> str | None:
    """Compilador de C que CMake podrá usar (en Windows, Visual Studio aunque no esté en el PATH)."""
    for c in ("cl", "gcc", "clang", "cc"):
        if shutil.which(c):
            return c
    if os.name == "nt":
        vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if vswhere.exists():
            r = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                               capture_output=True, text=True)
            if r.stdout.strip():
                return "Visual Studio"
    return None


def port_build(out: Path, opt: str = "release") -> Path:
    if not shutil.which("cmake"):
        sys.exit("Falta CMake para compilar el código recompilado.\n"
                 "  Windows: winget install Kitware.CMake  (o https://cmake.org/download/, marca 'añadir al PATH')\n"
                 "  y un compilador: Visual Studio Build Tools con C++ (winget install Microsoft.VisualStudio.2022.BuildTools).\n"
                 "Cierra y vuelve a abrir la consola tras instalar, y comprueba con: python stk.py doctor")
    cc = c_compiler()
    if not cc:
        sys.exit("Falta un compilador de C. Windows: Visual Studio Build Tools con 'Desarrollo para el escritorio con C++' "
                 "(winget install Microsoft.VisualStudio.2022.BuildTools) o MinGW-w64. Comprueba con: python stk.py doctor")
    # Una carpeta por nivel: cambiar de nivel no obliga a recompilarlo todo
    bdir = out.parent / ("recomp-build" if opt == "release" else f"recomp-build-{opt}")
    # Ninja solo si hay un compilador en el PATH; en Windows sin él, CMake elige Visual Studio
    use_ninja = shutil.which("ninja") and cc != "Visual Studio"
    gen = ["-G", "Ninja"] if use_ninja and not (bdir / "CMakeCache.txt").exists() else []
    run(["cmake", "-S", ROOT / "port", "-B", bdir, f"-DST_RECOMP_DIR={out}", f"-DST_OPT={opt}", "-DCMAKE_BUILD_TYPE=Release", *gen])
    build = ["cmake", "--build", bdir, "--parallel", "--config", "Release"]
    cache = bdir / "CMakeCache.txt"
    if cache.exists() and "CMAKE_GENERATOR:INTERNAL=Visual Studio" in cache.read_text(encoding="utf-8", errors="replace"):
        # Que MSBuild no deje procesos vivos con archivos de build/recomp abiertos
        build += ["--", "/nodeReuse:false"]
    t0 = time.time()
    run(build)
    print(f"Compilado con --opt {opt} en {(time.time() - t0) / 60:.1f} min ({bdir})")
    name = "soultrails_port.exe" if os.name == "nt" else "soultrails_port"
    for cand in (bdir / name, bdir / "Release" / name):
        if cand.exists():
            return cand
    return bdir / name


PORT_EXIT = {0: "terminó sin fallos", 2: "error de uso o de ROM", 3: "función no traducida",
             4: "instrucción no soportada", 5: "detenido (bkpt, espera abortada o falta la capa de plataforma)",
             6: "sin terminar (vigilante): el juego gira en algún bucle; abajo, las últimas funciones"}


def miss_lines() -> list[str]:
    return [ln.strip() for ln in MISSES.read_text(encoding="utf-8").splitlines() if ln.strip()] if MISSES.exists() else []


def cmd_recomp(a) -> None:
    """Recompilación estática ARM/Thumb -> C de todo el código del juego.
    Con --learn repite: recompilar -> compilar -> ejecutar el port -> aprender el fallo."""
    from sttools.nds import Rom
    cfg = config()
    out = Path(a.out or HOME / "build" / "recomp").resolve()
    rom = Rom.load(cfg["rom"])
    MISSES.parent.mkdir(parents=True, exist_ok=True)
    a.opt = a.opt or ("fast" if a.learn else "release")
    res = recomp_once(a, cfg, rom, out)
    print(f"El runtime registra direcciones desconocidas en {MISSES}; vuelve a ejecutar `stk recomp` para incluirlas.")
    exe = None
    if a.compile or a.learn:
        exe = port_build(out, a.opt)
        print(f"El código recompilado compila correctamente ({exe.parent}).")
        print(f"Ejecutable: {exe} --rom {cfg['rom']} --misses {MISSES}")
    learned = 0
    if a.learn:
        for it in range(1, a.max_iter + 1):
            before = set(miss_lines())
            cmd = [str(exe), "--rom", cfg["rom"], "--misses", str(MISSES), "--max-frames", str(a.max_frames),
                   "--watchdog", str(max(5, a.timeout - 10))]
            for spec in a.dump_mem:
                cmd += ["--dump-mem", spec]
            if a.dump_frames:
                FRAMES_DIR.mkdir(parents=True, exist_ok=True)
                cmd += ["--dump-frames", str(a.dump_frames), "--frames-dir", str(FRAMES_DIR)]
            print(f"[learn {it}] $ {' '.join(cmd)}", flush=True)
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=a.timeout)
                rc, log = p.returncode, (p.stdout + p.stderr)
            except subprocess.TimeoutExpired as e:
                dec = (lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or ""))
                rc, log = -1, dec(e.stdout) + dec(e.stderr)
            (STATE_DIR / "last_run.txt").write_text(log, encoding="utf-8")
            tail = "\n".join(log.strip().splitlines()[-(100 if rc in (3, 5, 6) else 12):])
            if rc == 0:
                (STATE_DIR / "last_run.txt").write_text(log, encoding="utf-8")
                print(f"[learn {it}] {PORT_EXIT[0]}")
                # Resumen: mensajes con el ARM7, hilos y funciones más ejecutadas
                keep = [ln for ln in log.splitlines() if ln.startswith(("[arm7]", "[st] PXI", "[hilos]"))]
                hot = log.split("[port] funciones más ejecutadas", 1)
                for ln in keep[-30:]:
                    print(ln)
                if len(hot) > 1:
                    print("[port] funciones más ejecutadas" + hot[1].rstrip())
                print(f"(salida completa en {STATE_DIR / 'last_run.txt'})")
                break
            new = [ln for ln in miss_lines() if ln not in before]
            if rc != 3 or not new:
                why = PORT_EXIT.get(rc, "tiempo agotado" if rc == -1 else f"código {rc}")
                if rc == 3:
                    why += " ya conocida (p. ej. overlay no cargado: falta la capa de plataforma)"
                print(f"[learn {it}] se para: {why}\n{tail}\n(salida completa en {STATE_DIR / 'last_run.txt'})")
                break
            learned += len(new)
            print(f"[learn {it}] aprendido: {', '.join(new)}")
            res = recomp_once(a, cfg, rom, out)
            port_build(out, a.opt)
        else:
            print(f"[learn] se alcanzó --max-iter {a.max_iter}")
        print(f"Aprendizaje: {learned} direcciones nuevas, {res['functions']} funciones")
    st = state()
    st["recomp"] = {"when": now(), "functions": res["functions"], "out": str(out), "learned": learned}
    save_state(st)
    journal(f"recomp: {res['functions']} funciones, {res['undefined']} no soportadas"
            + (f", {learned} aprendidas" if a.learn else ""))


# --------------------------------------------------------------------- ghidra
def ghidra_headless() -> Path:
    g = find_ghidra()
    if not g:
        sys.exit("Ghidra no encontrado: python stk.py setup")
    return g / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")


def cmd_ghidra(a) -> None:
    cfg = config()
    proj_dir = STATE_DIR / "ghidra_project"
    proj_dir.mkdir(parents=True, exist_ok=True)
    head = ghidra_headless()
    name = f"st_{cfg['version']}"
    exists = (proj_dir / f"{name}.gpr").exists()
    out = HOME / "ghidra_export"
    out.mkdir(exist_ok=True)

    if a.action == "import":
        if exists and not a.force:
            print("El proyecto Ghidra ya existe (usa --force para reimportar).")
            return
        cmd = [head, proj_dir, name, "-import", cfg["rom"], "-overwrite", "-scriptPath", GHIDRA_SCRIPTS]
        run(cmd)
        journal("ghidra import completado")
    elif a.action == "export":
        if not exists:
            sys.exit("Primero: python stk.py ghidra import")
        cmd = [head, proj_dir, name, "-process", "-noanalysis", "-readOnly",
               "-scriptPath", GHIDRA_SCRIPTS, "-postScript", "ExportFunctions.java", out]
        run(cmd)
        idx = out / "functions.jsonl"
        count = sum(1 for _ in idx.open(encoding="utf-8")) if idx.exists() else 0
        st = state()
        st["ghidra_export"] = {"when": now(), "functions": count}
        save_state(st)
        journal(f"ghidra export: {count} funciones -> {out}")
    elif a.action == "open":
        g = find_ghidra()
        run([g / ("ghidraRun.bat" if os.name == "nt" else "ghidraRun")], check=False)


def ghidra_index() -> dict[int, dict]:
    idx = HOME / "ghidra_export" / "functions.jsonl"
    out = {}
    if idx.exists():
        for line in idx.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
                out[int(rec["addr"], 16)] = rec
            except (ValueError, KeyError):
                pass
    return out


# ---------------------------------------------------------------------- build
def cmd_build(a) -> None:
    cfg = config()
    d = Path(cfg["decomp"])
    extract = d / "extract"
    extract.mkdir(exist_ok=True)
    if not any(extract.glob("*.nds")):
        dest = extract / f"baserom_{cfg['version']}.nds"
        print(f"Copiando ROM a {dest}  (revisa extract/README.md si el nombre esperado es otro)")
        shutil.copyfile(cfg["rom"], dest)
    if a.configure or not (d / "build.ninja").exists():
        run([sys.executable, "tools/configure.py", "-v", cfg["version"]], cwd=d)
    rc = run(["ninja"] + (["-k", "0"] if a.keep_going else []), cwd=d, check=False)
    st = state()
    st["last_build"] = {"when": now(), "rc": rc}
    save_state(st)
    journal(f"build rc={rc}")
    if rc and not a.keep_going:
        sys.exit(rc)


# ------------------------------------------------------------------- progress
def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def generate_report(cfg) -> dict:
    d = Path(cfg["decomp"])
    od = find_objdiff()
    if not od:
        sys.exit("objdiff-cli no encontrado: python stk.py setup")
    if not (d / "objdiff.json").exists():
        sys.exit("Falta objdiff.json en el repo decomp: python stk.py build")
    report = STATE_DIR / "report.json"
    run([od, "report", "generate", "-p", d, "-o", report])
    return load_json(report, {})


def unit_module(unit: dict) -> str | None:
    """Módulo del recompilador (ovNNN, itcm o None = residente/a deducir) de una unidad de objdiff."""
    meta = unit.get("metadata") or {}
    for txt in (str(meta.get("module_name") or ""), str(unit.get("name") or "")):
        m = re.search(r"(?:^|[^a-z])ov(?:erlay)?_?(\d+)", txt, re.I)
        if m:
            return f"ov{int(m.group(1)):03d}"
        if re.search(r"itcm", txt, re.I):
            return "itcm"
    return None


def decomp_symbols(cfg) -> bool:
    """Semillas con nombre a partir del informe de objdiff del repo decomp (solo metadatos:
    nombre, dirección, tamaño y % de match; no se lee el código del decomp).
    Escribe .stk/decomp_symbols.txt; devuelve False si no hay decomp u objdiff."""
    d = cfg.get("decomp")
    od = find_objdiff()
    if not d or not od or not (Path(d) / "objdiff.json").exists():
        return DECOMP_SYMBOLS.exists()  # se reutiliza el último si lo hay
    report = STATE_DIR / "report.json"
    if run([od, "report", "generate", "-p", d, "-o", report], check=False) != 0:
        print("  aviso: objdiff-cli report falló; uso los símbolos anteriores si los hay")
        return DECOMP_SYMBOLS.exists()
    rep = load_json(report, {})
    lines, matched = [], 0
    for unit in rep.get("units", []):
        mod = unit_module(unit)
        for fn in unit.get("functions", []) or []:
            addr = fn_addr(fn)
            if addr is None or not fn.get("name"):
                continue
            pct = num(fn.get("fuzzy_match_percent"))
            matched += pct >= 100
            name = "".join(ch if ch.isalnum() or ch in "_:$.@" else "_" for ch in fn["name"])
            lines.append(f"{addr:08X} auto {mod or ''} # {name} size={int(num(fn.get('size')))} match={pct:.0f}")
    DECOMP_SYMBOLS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  decomp (objdiff): {len(lines)} funciones con dirección, {matched} emparejadas al 100 %")
    return bool(lines)


def cmd_progress(a) -> None:
    cfg = config()
    rep = generate_report(cfg)
    m = rep.get("measures", {})
    entry = {
        "when": now(),
        "matched_code_percent": round(num(m.get("matched_code_percent")), 3),
        "fuzzy_match_percent": round(num(m.get("fuzzy_match_percent")), 3),
        "matched_functions": int(num(m.get("matched_functions"))),
        "total_functions": int(num(m.get("total_functions"))),
    }
    hist = [json.loads(l) for l in HISTORY_FILE.open(encoding="utf-8")] if HISTORY_FILE.exists() else []
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    prev = hist[-1] if hist else None
    print(f"Código matching: {entry['matched_code_percent']:.2f}%   fuzzy: {entry['fuzzy_match_percent']:.2f}%")
    print(f"Funciones: {entry['matched_functions']}/{entry['total_functions']}")
    if prev:
        df = entry["matched_functions"] - prev["matched_functions"]
        print(f"Desde {prev['when']}: {df:+d} funciones")
    # Marca como terminadas las tareas que ya llegaron al 100 %
    st = state()
    done = []
    for unit, fn in iter_functions(rep):
        key = f"{unit}::{fn['name']}"
        t = st["tasks"].get(key)
        if t and t["status"] != "done" and num(fn.get("fuzzy_match_percent")) >= 100:
            t.update(status="done", finished=now())
            done.append(key)
    save_state(st)
    for k in done:
        print(f"  ✔ completada: {k}")
        journal(f"MATCH 100%: {k}")


def iter_functions(rep):
    for unit in rep.get("units", []):
        for fn in unit.get("functions", []) or []:
            yield unit.get("name", "?"), fn


def fn_addr(fn) -> int | None:
    meta = fn.get("metadata") or {}
    va = meta.get("virtual_address", fn.get("address"))
    try:
        return int(va, 0) if isinstance(va, str) else (int(va) if va is not None else None)
    except ValueError:
        return None


# ----------------------------------------------------------------------- next
def cmd_next(a) -> None:
    cfg = config()
    rep = generate_report(cfg)
    st = state()
    gidx = ghidra_index()
    cands = []
    for unit, fn in iter_functions(rep):
        pct = num(fn.get("fuzzy_match_percent"))
        key = f"{unit}::{fn['name']}"
        if pct >= 100 or st["tasks"].get(key, {}).get("status") in ("done", "skipped"):
            continue
        if a.unit and a.unit not in unit:
            continue
        size = int(num(fn.get("size")))
        if a.max_size and size > a.max_size:
            continue
        cands.append((-(pct) if a.closest else size, key, unit, fn, pct, size))
    cands.sort(key=lambda c: c[0])
    WORK_DIR.mkdir(exist_ok=True)
    made = 0
    for _, key, unit, fn, pct, size in cands:
        if made >= a.n:
            break
        if key in st["tasks"] and st["tasks"][key]["status"] == "in_progress" and not a.force:
            continue
        addr = fn_addr(fn)
        g = gidx.get(addr) if addr is not None else None
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in fn["name"])[:120]
        path = WORK_DIR / f"{safe}.md"
        demangled = (fn.get("metadata") or {}).get("demangled_name") or fn["name"]
        lines = [
            f"# {demangled}",
            "",
            f"- Símbolo: `{fn['name']}`",
            f"- Unidad: `{unit}`",
            f"- Dirección: `{hex(addr) if addr is not None else '?'}`  Tamaño: {size} bytes",
            f"- Match actual: {pct:.1f}%",
            f"- Creada: {now()}",
            "",
            "## Pasos",
            "1. Abre la unidad en objdiff (GUI) y localiza la función.",
            "2. Escribe tu implementación en el .c/.cpp de la unidad.",
            "3. `python stk.py loop --once` para recompilar y medir.",
            "4. Si se atasca en 90-99 %, prueba decomp.me o el permuter.",
            "",
            "## Pseudo-C de Ghidra (solo referencia, reescríbelo con los tipos del proyecto)",
            "```c",
            (g or {}).get("c", "// Sin exportación de Ghidra para esta dirección: python stk.py ghidra export"),
            "```",
            "",
            "## Notas",
            "",
        ]
        if not path.exists() or a.force:
            path.write_text("\n".join(lines), encoding="utf-8")
        st["tasks"][key] = {"status": "in_progress", "file": str(path.relative_to(HOME)), "created": now(),
                            "size": size, "match": pct}
        print(f"  ficha: {path.relative_to(HOME)}  ({size} B, {pct:.1f}%)")
        made += 1
    save_state(st)
    journal(f"next: {made} fichas nuevas")
    if not made:
        print("No quedan candidatas con esos filtros.")


# ---------------------------------------------------------------- loop/status
def cmd_loop(a) -> None:
    while True:
        cmd_build(argparse.Namespace(configure=False, keep_going=True))
        cmd_progress(a)
        open_tasks = [k for k, t in state()["tasks"].items() if t["status"] == "in_progress"]
        if len(open_tasks) < a.keep:
            cmd_next(argparse.Namespace(n=a.keep - len(open_tasks), unit=None, max_size=a.max_size,
                                        closest=False, force=False))
        if a.once:
            return
        print(f"Esperando cambios en src/ ... (Ctrl+C para salir)")
        wait_for_change(Path(config()["decomp"]) / "src", a.interval)


def snapshot(src: Path) -> dict:
    return {str(p): p.stat().st_mtime for p in src.rglob("*") if p.suffix in (".c", ".cpp", ".h", ".hpp", ".inc")}


def wait_for_change(src: Path, interval: float) -> None:
    before = snapshot(src)
    while True:
        time.sleep(interval)
        if snapshot(src) != before:
            return


def cmd_disasm(a) -> None:
    """Desensambla una función de TU ROM con el decodificador del recompilador (para
    diagnosticar dónde gira o falla el port). Muestra también la traducción a C."""
    from sttools.nds import Rom
    from sttools.recomp import driver
    from sttools.recomp.program import Program
    cfg = config()
    rom = Rom.load(cfg["rom"])
    prog = Program(driver.modules_from_rom(rom))
    for spec in a.addr:
        addr = int(spec, 16)
        thumb = bool(addr & 1) if a.mode is None else a.mode == "thumb"
        addr &= ~1
        mods = [m for m in prog.modules if m.name == a.module] if a.module else (
            [prog.module_for(addr)] if prog.module_for(addr) else prog.overlays_at(addr))
        if not mods:
            print(f"{addr:08X}: no está en ningún módulo")
            continue
        for m in mods[:1] if not a.module and prog.module_for(addr) else mods:
            f = prog.trace_free(m, addr, thumb)
            print(f"== {m.name}:{addr:08X} {'Thumb' if thumb else 'ARM'}, {len(f.insns)} instrucciones")
            for ia in sorted(f.insns):
                ins = f.insns[ia]
                raw = f"{ins.raw:0{4 if thumb else 8}X}"
                code = " ".join(ins.code.split()) if a.c else ""
                asm = ins.asm or "?"
                lit = re.search(r"=(0x[0-9a-fA-F]+)$", asm)
                if lit and asm.startswith("ldr"):   # valor del literal en la ROM
                    v = prog.read(m, int(lit.group(1), 16), 4)
                    asm += f"  ; [{v:08X}]" if v is not None else ""
                print(f"  {ia:08X}  {raw:8}  {asm:44} {code[:110]}")
            words = [prog.read(m, x, 4) for x in range(max(f.insns) + 4, max(f.insns) + 4 + 4 * a.pool, 4)]
            if a.pool:
                print("  literales tras la función: " + " ".join(f"{w:08X}" for w in words if w is not None))


def cmd_status(a) -> None:
    cfg = load_json(CONFIG_FILE, {})
    st = state()
    print(f"Repo decomp: {cfg.get('decomp', '-')}   versión: {cfg.get('version', '-')}")
    if HISTORY_FILE.exists():
        last = json.loads(HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()[-1])
        print(f"Último progreso ({last['when']}): {last['matched_code_percent']:.2f}%  "
              f"{last['matched_functions']}/{last['total_functions']} funciones")
    tasks = st["tasks"]
    for status in ("in_progress", "done", "skipped"):
        ks = [k for k, t in tasks.items() if t["status"] == status]
        print(f"{status}: {len(ks)}")
        if status == "in_progress":
            for k in ks[:20]:
                print(f"   - {k}  -> {tasks[k]['file']}")
    if JOURNAL_FILE.exists():
        print("\nÚltimas entradas de la bitácora:")
        for l in JOURNAL_FILE.read_text(encoding="utf-8").splitlines()[-8:]:
            print("  " + l)


def cmd_journal(a) -> None:
    journal(" ".join(a.text))
    print("Anotado.")


def cmd_task(a) -> None:
    st = state()
    matches = [k for k in st["tasks"] if a.name in k]
    if not matches:
        sys.exit("No encontrada.")
    for k in matches:
        st["tasks"][k]["status"] = a.status
        print(f"{k} -> {a.status}")
    save_state(st)


# ----------------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser(prog="stk", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="configura rutas del repo decomp y la ROM")
    s.add_argument("--decomp", help="repo de la decomp (opcional: solo para build/progress/next/loop)")
    s.add_argument("--rom", required=True)
    s.add_argument("--version", choices=["jp", "eur", "eur1", "usa"])
    s.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="comprueba que todo esté instalado").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("setup", help="descarga Ghidra, NTRGhidra y objdiff-cli")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_setup)

    sub.add_parser("rom", help="información de la ROM").set_defaults(fn=cmd_rom)

    s = sub.add_parser("assets", help="extrae y convierte todos los assets")
    s.add_argument("--out", help="carpeta de salida (por defecto assets/)")
    s.add_argument("--raw-only", action="store_true", help="solo extraer, sin convertir")
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_assets)

    s = sub.add_parser("recomp", help="recompilación estática del código a C")
    s.add_argument("--out", help="carpeta de salida (por defecto build/recomp)")
    s.add_argument("--seeds", action="append", default=[], help="archivo extra de direcciones")
    s.add_argument("--no-scan", action="store_true", help="no buscar prólogos de función")
    s.add_argument("--scan-thumb", action="store_true", help="buscar también prólogos Thumb")
    s.add_argument("--hook", action="append", default=[], help="función a reemplazar en tiempo de ejecución (hex)")
    s.add_argument("--no-comments", action="store_true")
    s.add_argument("--no-decomp", action="store_true",
                   help="no usar los símbolos del informe de objdiff del repo decomp")
    s.add_argument("--compile", action="store_true", help="compila el resultado para verificarlo (necesita cmake)")
    s.add_argument("--opt", choices=["fast", "medium", "release"], default=None,
                   help="optimización del C: fast (sin optimizar, compila mucho antes; por defecto con --learn), "
                        "medium o release (/O2, la más lenta de compilar; por defecto con --compile)")
    s.add_argument("--learn", action="store_true",
                   help="repite recompilar -> ejecutar el port -> aprender fallos hasta que no haya más")
    s.add_argument("--max-iter", type=int, default=50, help="máximo de vueltas de --learn")
    s.add_argument("--max-frames", type=int, default=600, help="cuadros que se deja correr el port en cada vuelta")
    s.add_argument("--timeout", type=int, default=120, help="segundos máximos por ejecución del port")
    s.add_argument("--dump-mem", action="append", default=[],
                   help="con --learn, vuelca memoria del juego al terminar: DIR:LEN en hex (repetible)")
    s.add_argument("--dump-frames", type=int, default=60,
                   help="con --learn, guarda las dos pantallas cada N cuadros en .stk/frames (0 = no)")
    s.set_defaults(fn=cmd_recomp)

    s = sub.add_parser("disasm", help="desensambla funciones de tu ROM (diagnóstico)")
    s.add_argument("addr", nargs="+", help="dirección en hex (bit 0 = Thumb)")
    s.add_argument("--mode", choices=["arm", "thumb"], help="fuerza ARM o Thumb")
    s.add_argument("--module", help="módulo (arm9, itcm, ov031...) si la dirección es de un overlay")
    s.add_argument("--c", action="store_true", help="muestra también la traducción a C")
    s.add_argument("--pool", type=int, default=8, help="palabras de literales a mostrar tras la función")
    s.set_defaults(fn=cmd_disasm)

    s = sub.add_parser("ghidra", help="import / export / open")
    s.add_argument("action", choices=["import", "export", "open"])
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_ghidra)

    s = sub.add_parser("build", help="configure + ninja en el repo decomp")
    s.add_argument("--configure", action="store_true", help="fuerza configure.py")
    s.add_argument("--keep-going", action="store_true")
    s.set_defaults(fn=cmd_build)

    sub.add_parser("progress", help="informe objdiff + histórico").set_defaults(fn=cmd_progress)

    s = sub.add_parser("next", help="genera fichas de las siguientes funciones")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--unit", help="filtra por nombre de unidad (p.ej. ov024)")
    s.add_argument("--max-size", type=int, default=0)
    s.add_argument("--closest", action="store_true", help="prioriza las de mayor %% (casi terminadas)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_next)

    s = sub.add_parser("loop", help="build -> progress -> next, repetido al guardar cambios")
    s.add_argument("--once", action="store_true")
    s.add_argument("--keep", type=int, default=5, help="fichas abiertas a mantener")
    s.add_argument("--max-size", type=int, default=0)
    s.add_argument("--interval", type=float, default=2.0)
    s.set_defaults(fn=cmd_loop)

    sub.add_parser("status", help="dónde te quedaste").set_defaults(fn=cmd_status)

    s = sub.add_parser("journal", help="añade una nota a la bitácora")
    s.add_argument("text", nargs="+")
    s.set_defaults(fn=cmd_journal)

    s = sub.add_parser("task", help="cambia el estado de una ficha")
    s.add_argument("name")
    s.add_argument("status", choices=["in_progress", "done", "skipped"])
    s.set_defaults(fn=cmd_task)

    a = p.parse_args()
    st = state()
    st["last_command"] = {"when": now(), "argv": sys.argv[1:]}
    save_state(st)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        print("\nInterrumpido.")


if __name__ == "__main__":
    main()
