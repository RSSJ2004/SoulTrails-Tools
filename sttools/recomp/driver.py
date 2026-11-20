"""Punto de entrada del recompilador estático.

Desde la ROM (lo normal, vía `stk recomp`):
    python -m sttools.recomp.driver --rom juego.nds --out build/recomp [--ghidra ghidra_export/functions.jsonl]

Desde un binario plano (tests):
    python -m sttools.recomp.driver --bin code.bin --base 0x02000000 --seeds seeds.txt --out dir
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..nds import Rom
from .codegen import Generator
from .program import Module, Program


def modules_from_rom(rom: Rom) -> list[Module]:
    mods = []
    for seg in rom.arm9_segments():
        if seg.data and seg.name != "dtcm":  # la DTCM es solo datos
            mods.append(Module(seg.name, seg.base, seg.data))
    for ov in rom.overlays9():
        mods.append(Module(f"ov{ov.id:03d}", ov.ram, ov.code(), overlay=ov.id))
    return mods


def parse_seed_line(line: str) -> tuple[int, bool, str | None, int | None] | None:
    """Formatos: '02001234 thumb', '0x02001234', '02001234 arm ov012',
    '02200000 arm sig=1A2B3C4D' (fallo del runtime con la firma de la memoria)."""
    parts = line.split("#")[0].split()
    if not parts:
        return None
    addr = int(parts[0], 16)
    thumb = bool(addr & 1)
    mod = sig = None
    for p in parts[1:]:
        if p.lower() in ("thumb", "t"):
            thumb = True
        elif p.lower() in ("arm", "a"):
            thumb = False
        elif p.lower().startswith("sig="):
            sig = int(p[4:], 16)
        else:
            mod = p
    return addr & ~1, thumb, mod, sig


def collect_seeds(prog: Program, entry: int | None, ghidra: Path | None, seed_files: list[Path],
                  scan: bool, scan_thumb: bool) -> list:
    by_name = {m.name: m for m in prog.modules}
    seeds = []
    if entry is not None:
        seeds.append((entry & ~1, bool(entry & 1), None))
    if ghidra and ghidra.exists():
        for line in ghidra.open(encoding="utf-8"):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            addr = int(rec["addr"], 16)
            blk = rec.get("block", "")
            mod = next((m for m in prog.modules if m.overlay is not None and m.name in blk and m.contains(addr)), None)
            seeds.append((addr, bool(rec.get("thumb")), mod))
    for sf in seed_files:
        if not sf.exists():
            continue
        for line in sf.read_text(encoding="utf-8").splitlines():
            s = parse_seed_line(line)
            if not s:
                continue
            addr, thumb, mod, sig = s
            if mod:
                seeds.append((addr, thumb, by_name.get(mod)))
            else:  # puede estar en uno o varios overlays
                seeds += [(addr, thumb, m) for m in prog.candidates(addr, thumb, sig)]
    if scan:
        for m in prog.modules:
            for a, t, mm in prog.scan_prologues(m):
                if t and not scan_thumb:
                    continue
                seeds.append((a, t, mm))
    return seeds


def run(mods: list[Module], out: Path, entry: int | None, ghidra: Path | None = None,
        seed_files: list[Path] = (), scan: bool = True, scan_thumb: bool = False,
        hooks: set[int] | None = None, comments: bool = True) -> dict:
    prog = Program(mods)
    seeds = collect_seeds(prog, entry, ghidra, list(seed_files), scan, scan_thumb)
    prog.discover(seeds)
    res = Generator(prog, hooks=hooks, comments=comments).write(out)
    res["seeds"] = len(seeds)
    res["modules"] = [{"name": m.name, "base": hex(m.base), "size": len(m.data), "overlay": m.overlay} for m in mods]
    (out / "recomp_report.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Recompilador estático ARM/Thumb -> C")
    ap.add_argument("--rom")
    ap.add_argument("--bin")
    ap.add_argument("--base", default="0x02000000")
    ap.add_argument("--entry")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ghidra")
    ap.add_argument("--seeds", action="append", default=[])
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--scan-thumb", action="store_true")
    ap.add_argument("--hook", action="append", default=[], help="dirección que siempre pasa por st_call (para reemplazos)")
    ap.add_argument("--no-comments", action="store_true")
    a = ap.parse_args()
    if a.rom:
        rom = Rom.load(a.rom)
        mods = modules_from_rom(rom)
        entry = int(a.entry, 16) if a.entry else rom.header["arm9_entry"]
    elif a.bin:
        mods = [Module("main", int(a.base, 16), Path(a.bin).read_bytes())]
        entry = int(a.entry, 16) if a.entry else None
    else:
        ap.error("usa --rom o --bin")
    res = run(mods, Path(a.out), entry, Path(a.ghidra) if a.ghidra else None, [Path(s) for s in a.seeds],
              scan=not a.no_scan, scan_thumb=a.scan_thumb, hooks={int(x, 16) for x in a.hook},
              comments=not a.no_comments)
    print(f"{res['functions']} funciones, {res['instructions']} instrucciones, {res['undefined']} no soportadas")
    for wmsg in res["warnings"][:20]:
        print("  aviso:", wmsg)


if __name__ == "__main__":
    main()
