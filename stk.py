#!/usr/bin/env python3
"""stk - Spirit Tracks toolkit.

Automatiza las partes repetitivas del trabajo de decompilación/port y guarda
todo el estado en disco (.stk/) para que nunca se pierda el contexto entre
sesiones.

Uso rápido:
    python stk.py init --decomp ../SoulTrails --rom ruta/a/tu.nds
    python stk.py doctor
    python stk.py setup            # descarga Ghidra, NTRGhidra y objdiff-cli
    python stk.py rom info
    python stk.py rom extract      # saca NitroFS a assets/
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
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".stk"
CONFIG_FILE = STATE_DIR / "config.json"
STATE_FILE = STATE_DIR / "state.json"
JOURNAL_FILE = STATE_DIR / "journal.md"
HISTORY_FILE = STATE_DIR / "progress_history.jsonl"
TOOLS_DIR = ROOT / "tools_bin"
WORK_DIR = ROOT / "work"
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
    rc = subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)
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
    decomp = Path(a.decomp).resolve()
    rom = Path(a.rom).resolve()
    if not (decomp / "tools" / "configure.py").exists():
        sys.exit(f"{decomp} no parece el repo de la decomp (falta tools/configure.py)")
    if not rom.exists():
        sys.exit(f"No existe la ROM: {rom}")
    digest = sha1_of(rom)
    version = KNOWN_ROMS.get(digest)
    if version is None:
        print(f"AVISO: SHA1 {digest} no coincide con ninguna ROM soportada.")
        version = a.version or "eur"
    cfg = load_json(CONFIG_FILE, {})
    cfg.update({"decomp": str(decomp), "rom": str(rom), "rom_sha1": digest, "version": a.version or version})
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
        d = Path(cfg["decomp"])
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
def read_header(f) -> dict:
    f.seek(0)
    h = f.read(0x200)
    u32 = lambda o: struct.unpack_from("<I", h, o)[0]
    return {
        "title": h[0:12].rstrip(b"\0").decode("ascii", "replace"),
        "gamecode": h[12:16].decode("ascii", "replace"),
        "arm9_off": u32(0x20), "arm9_entry": u32(0x24), "arm9_ram": u32(0x28), "arm9_size": u32(0x2C),
        "arm7_off": u32(0x30), "arm7_entry": u32(0x34), "arm7_ram": u32(0x38), "arm7_size": u32(0x3C),
        "fnt_off": u32(0x40), "fnt_size": u32(0x44), "fat_off": u32(0x48), "fat_size": u32(0x4C),
        "ovt9_off": u32(0x50), "ovt9_size": u32(0x54), "ovt7_off": u32(0x58), "ovt7_size": u32(0x5C),
    }


def walk_fnt(f, fnt_off: int):
    """Devuelve [(ruta, file_id)] del sistema de archivos NitroFS."""
    def entry(dir_id):
        f.seek(fnt_off + (dir_id & 0xFFF) * 8)
        sub_off, first_id, _ = struct.unpack("<IHH", f.read(8))
        return sub_off, first_id

    out = []

    def walk(dir_id, prefix):
        sub_off, fid = entry(dir_id)
        pos = fnt_off + sub_off
        while True:
            f.seek(pos)
            t = f.read(1)[0]
            if t == 0:
                break
            ln = t & 0x7F
            name = f.read(ln).decode("latin-1")
            pos += 1 + ln
            if t & 0x80:
                f.seek(pos)
                sub = struct.unpack("<H", f.read(2))[0]
                pos += 2
                walk(sub, f"{prefix}{name}/")
            else:
                out.append((prefix + name, fid))
                fid += 1

    walk(0xF000, "")
    return out


def cmd_rom(a) -> None:
    cfg = config()
    rom = Path(cfg["rom"])
    with rom.open("rb") as f:
        h = read_header(f)
        if a.action == "info":
            for k, v in h.items():
                print(f"  {k:12} {hex(v) if isinstance(v, int) else v}")
            print(f"  overlays ARM9: {h['ovt9_size'] // 32}")
            print(f"  archivos NitroFS: {len(walk_fnt(f, h['fnt_off']))}")
            return
        out = Path(a.out or ROOT / "assets").resolve()
        files = walk_fnt(f, h["fnt_off"])
        n = 0
        for path, fid in files:
            f.seek(h["fat_off"] + fid * 8)
            start, end = struct.unpack("<II", f.read(8))
            dest = out / "data" / path
            if dest.exists() and dest.stat().st_size == end - start and not a.force:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.seek(start)
            dest.write_bytes(f.read(end - start))
            n += 1
        # binarios y overlays
        for name, off, size in (("arm9.bin", h["arm9_off"], h["arm9_size"]), ("arm7.bin", h["arm7_off"], h["arm7_size"])):
            f.seek(off)
            (out / name).write_bytes(f.read(size))
        ovdir = out / "overlays"
        ovdir.mkdir(parents=True, exist_ok=True)
        ovt = []
        for i in range(h["ovt9_size"] // 32):
            f.seek(h["ovt9_off"] + i * 32)
            ov_id, ram, size, bss, sinit_s, sinit_e, file_id, flags = struct.unpack("<8I", f.read(32))
            f.seek(h["fat_off"] + file_id * 8)
            start, end = struct.unpack("<II", f.read(8))
            f.seek(start)
            (ovdir / f"ov{ov_id:03d}.bin").write_bytes(f.read(end - start))
            ovt.append({"id": ov_id, "ram": hex(ram), "size": size, "bss": bss, "compressed": bool(flags & 0x01000000)})
        save_json(out / "overlays.json", ovt)
    print(f"Extraídos {n} archivos nuevos de NitroFS + arm9/arm7 + {len(ovt)} overlays en {out}")
    print("Nota: arm9 y los overlays marcados 'compressed' están en BLZ; Ghidra/NTRGhidra los descomprime.")
    journal(f"rom extract -> {out} ({n} archivos)")


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
    out = ROOT / "ghidra_export"
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
    idx = ROOT / "ghidra_export" / "functions.jsonl"
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
        st["tasks"][key] = {"status": "in_progress", "file": str(path.relative_to(ROOT)), "created": now(),
                            "size": size, "match": pct}
        print(f"  ficha: {path.relative_to(ROOT)}  ({size} B, {pct:.1f}%)")
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
    s.add_argument("--decomp", required=True)
    s.add_argument("--rom", required=True)
    s.add_argument("--version", choices=["jp", "eur", "eur1", "usa"])
    s.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="comprueba que todo esté instalado").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("setup", help="descarga Ghidra, NTRGhidra y objdiff-cli")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("rom", help="info / extract de la ROM")
    s.add_argument("action", choices=["info", "extract"])
    s.add_argument("--out")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_rom)

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
    s.add_argument("--closest", action="store_true", help="prioriza las de mayor % (casi terminadas)")
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
