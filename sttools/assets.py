"""Pipeline de extracción de assets.

    assets/
      raw/          NitroFS tal cual sale de la ROM
      unpacked/     descomprimido (LZ), NARC/SDAT expandidos (recursivo)
      converted/    PNG / JSON / WAV
      code/         arm9 (descomprimido + autoloads), arm7 y overlays descomprimidos
      manifest.json qué es cada archivo, qué salió de él y errores
      report.txt    resumen
"""
from __future__ import annotations

import hashlib
import json
import traceback
from collections import Counter
from pathlib import Path

from . import formats as F
from .nds import Rom, looks_lz, lz_decompress, parse_narc

MAGICS = {
    b"NARC": "narc", b"BTX0": "nsbtx", b"BMD0": "nsbmd", b"BCA0": "nsbca", b"BTA0": "nsbta",
    b"BTP0": "nsbtp", b"BMA0": "nsbma", b"BVA0": "nsbva",
    b"RLCN": "nclr", b"RGCN": "ncgr", b"RCSN": "nscr", b"RECN": "ncer", b"RNAN": "nanr",
    b"SDAT": "sdat", b"SSEQ": "sseq", b"SSAR": "ssar", b"SBNK": "sbnk", b"SWAR": "swar",
    b"SWAV": "swav", b"STRM": "strm", b"MESG": "bmg", b"NFTR": "nftr", b"RTFN": "nftr",
    b"RNCL": "nclr", b"RGCN": "ncgr", b"BMG ": "bmg",
}


def identify(data: bytes) -> str:
    if len(data) >= 4:
        m = bytes(data[:4])
        if m in MAGICS:
            return MAGICS[m]
    if len(data) >= 8 and data[:8] == b"MESGbmg1":
        return "bmg"
    return "bin"


class Extractor:
    def __init__(self, rom: Rom, out: Path, convert: bool = True, verbose: bool = False):
        self.rom = rom
        self.out = out
        self.convert = convert
        self.verbose = verbose
        self.manifest: list[dict] = []
        self.counts: Counter = Counter()
        self.errors: list[dict] = []
        self.pending_2d: dict[Path, dict[str, bytes]] = {}

    # -------------------------------------------------------------- general
    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def run(self) -> dict:
        (self.out / "raw").mkdir(parents=True, exist_ok=True)
        self.extract_code()
        files = self.rom.files()
        for i, (path, fid) in enumerate(files):
            data = self.rom.file(fid)
            raw_dest = self.out / "raw" / path
            raw_dest.parent.mkdir(parents=True, exist_ok=True)
            raw_dest.write_bytes(data)
            self.process(path, data, depth=0)
            if not self.verbose and i % 200 == 0:
                print(f"  {i}/{len(files)} archivos...", flush=True)
        self.flush_2d()
        summary = {
            "rom": {"title": self.rom.header["title"], "gamecode": self.rom.header["gamecode"]},
            "files_in_rom": len(files),
            "types": dict(self.counts.most_common()),
            "errors": len(self.errors),
        }
        (self.out / "manifest.json").write_text(json.dumps(
            {"summary": summary, "entries": self.manifest, "errors": self.errors}, indent=1, ensure_ascii=False),
            encoding="utf-8")
        self.write_report(summary)
        return summary

    def write_report(self, s: dict) -> None:
        lines = [f"ROM: {s['rom']['title']} ({s['rom']['gamecode']})",
                 f"Archivos en NitroFS: {s['files_in_rom']}", "", "Tipos encontrados:"]
        for k, v in s["types"].items():
            lines.append(f"  {k:10} {v}")
        lines.append("")
        lines.append(f"Errores: {s['errors']}")
        for e in self.errors[:200]:
            lines.append(f"  {e['path']}: {e['error']}")
        (self.out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------------------------------------------------------------- código
    def extract_code(self) -> None:
        code = self.out / "code"
        code.mkdir(parents=True, exist_ok=True)
        try:
            segs = self.rom.arm9_segments()
            seg_info = []
            for s in segs:
                (code / f"{s.name}.bin").write_bytes(s.data)
                seg_info.append({"name": s.name, "base": hex(s.base), "size": len(s.data), "bss": s.bss})
            (code / "arm7.bin").write_bytes(self.rom.arm7_raw())
            ovs = []
            (code / "overlays").mkdir(exist_ok=True)
            for ov in self.rom.overlays9():
                try:
                    data = ov.code()
                    err = None
                except ValueError as e:
                    data, err = ov.data, str(e)
                (code / "overlays" / f"ov{ov.id:03d}.bin").write_bytes(data)
                ovs.append({"id": ov.id, "ram": hex(ov.ram), "ram_size": ov.ram_size, "bss": ov.bss_size,
                            "compressed": ov.compressed, "decompressed_size": len(data), "error": err})
            mp = self.rom.arm9_module_params(self.rom.arm9_decompressed())
            (code / "layout.json").write_text(json.dumps({
                "arm9_entry": hex(self.rom.header["arm9_entry"]), "module_params": mp,
                "segments": seg_info, "overlays": ovs}, indent=2), encoding="utf-8")
            self.counts["code"] += 1 + len(ovs)
        except Exception as e:  # noqa: BLE001 - se registra y se sigue
            self.errors.append({"path": "<code>", "error": f"{type(e).__name__}: {e}"})

    # -------------------------------------------------------------- archivos
    def process(self, path: str, data: bytes, depth: int) -> None:
        entry = {"path": path, "size": len(data), "sha1": hashlib.sha1(data).hexdigest()}
        kind = identify(data)
        if kind == "bin" and looks_lz(data):
            try:
                data = lz_decompress(data)
                entry["lz"] = True
                kind = identify(data)
            except (ValueError, IndexError):
                pass
        entry["type"] = kind
        self.counts[kind] += 1
        self.manifest.append(entry)
        if depth > 6:
            return

        un = self.out / "unpacked" / path
        try:
            if kind == "narc":
                for name, sub in parse_narc(data):
                    self.process(f"{path}/{name}", sub, depth + 1)
                return
            if kind == "sdat":
                for name, sub in F.unpack_sdat(data):
                    self.process(f"{path}/{name}", sub, depth + 1)
                return
            if entry.get("lz") or depth > 0:
                un.parent.mkdir(parents=True, exist_ok=True)
                ext = "" if un.suffix[1:].lower() == kind else f".{kind}" if kind != "bin" else ""
                un = un.with_name(un.name + ext)
                un.write_bytes(data)
                entry["unpacked"] = str(un.relative_to(self.out))
            if self.convert:
                entry["outputs"] = self.convert_one(path, kind, data)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            entry["error"] = err
            self.errors.append({"path": path, "type": kind, "error": err})
            self.log(traceback.format_exc())

    def convert_one(self, path: str, kind: str, data: bytes) -> list[str]:
        conv = self.out / "converted" / path
        if kind in ("nsbtx", "nsbmd"):
            made = F.convert_tex0(data, conv)
            if kind == "nsbmd":
                # copia del modelo para el futuro conversor a glTF
                conv.mkdir(parents=True, exist_ok=True)
            return [self.rel(m) for m in made]
        if kind == "bmg":
            conv.parent.mkdir(parents=True, exist_ok=True)
            dest = conv.with_suffix(".json")
            dest.write_text(json.dumps(F.parse_bmg(data), indent=1, ensure_ascii=False), encoding="utf-8")
            return [self.rel(dest)]
        if kind == "swar":
            return [self.rel(m) for m in F.convert_swar(data, conv)]
        if kind == "swav":
            conv.parent.mkdir(parents=True, exist_ok=True)
            dest = conv.with_suffix(".wav")
            F.convert_swav(data, dest)
            return [self.rel(dest)]
        if kind == "strm":
            conv.parent.mkdir(parents=True, exist_ok=True)
            dest = conv.with_suffix(".wav")
            F.convert_strm(data, dest)
            return [self.rel(dest)]
        if kind in ("nclr", "ncgr", "nscr"):
            # Se combinan al final por carpeta y nombre base
            folder = conv.parent
            self.pending_2d.setdefault(folder, {})[f"{Path(path).stem}|{kind}"] = data
            return []
        return []

    def rel(self, p) -> str:
        return str(Path(p).relative_to(self.out))

    def flush_2d(self) -> None:
        """Empareja NCLR/NCGR/NSCR de la misma carpeta (mismo nombre base, o el único disponible)."""
        for folder, items in self.pending_2d.items():
            pals = {k.split("|")[0]: v for k, v in items.items() if k.endswith("|nclr")}
            gfx = {k.split("|")[0]: v for k, v in items.items() if k.endswith("|ncgr")}
            scr = {k.split("|")[0]: v for k, v in items.items() if k.endswith("|nscr")}
            folder.mkdir(parents=True, exist_ok=True)

            def pick(d: dict, base: str):
                if base in d:
                    return d[base]
                if len(d) == 1:
                    return next(iter(d.values()))
                # nombre más parecido (prefijo común más largo)
                best = max(d, key=lambda k: len(_common_prefix(k, base)), default=None)
                return d[best] if best is not None else None

            parsed_pals = {}
            for base, data in pals.items():
                try:
                    _, pal = F.parse_nclr(data)
                    parsed_pals[base] = pal
                    F.nclr_to_png(pal, folder / f"{base}.palette.png")
                except Exception as e:  # noqa: BLE001
                    self.errors.append({"path": str(folder / base), "type": "nclr", "error": str(e)})
            parsed_gfx = {}
            for base, data in gfx.items():
                try:
                    g = F.parse_ncgr(data)
                    parsed_gfx[base] = g
                    F.ncgr_to_png(g, pick(parsed_pals, base), folder / f"{base}.png")
                except Exception as e:  # noqa: BLE001
                    self.errors.append({"path": str(folder / base), "type": "ncgr", "error": str(e)})
            for base, data in scr.items():
                try:
                    g = pick(parsed_gfx, base)
                    if g is None:
                        continue
                    F.nscr_to_png(data, g, pick(parsed_pals, base), folder / f"{base}.screen.png")
                except Exception as e:  # noqa: BLE001
                    self.errors.append({"path": str(folder / base), "type": "nscr", "error": str(e)})


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def extract(rom_path: str | Path, out: str | Path, convert: bool = True, verbose: bool = False) -> dict:
    rom = Rom.load(rom_path)
    return Extractor(rom, Path(out), convert=convert, verbose=verbose).run()
