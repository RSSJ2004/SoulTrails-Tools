"""Formatos sin cabecera Nitro y formatos propios del juego.

- ``.nbfp`` / ``.nbfc`` / ``.nbfs``: paleta, tiles y pantalla 2D **en crudo** (los exporta la
  herramienta NITRO-CHARACTER sin cabecera). Se combinan por carpeta y nombre como NCLR/NCGR/NSCR.
- ``.ntft`` / ``.ntfp`` / ``.ntfi``: texels, paleta e índices de paleta 4x4 de una textura 3D
  **en crudo**. El formato y el tamaño no están en el archivo: se deducen (ver ``guess_texture``)
  y se anotan como supuestos.
- Formatos por bloques (``ZOLB``, ``BPAM``, ``JNCL``...): ``parse_chunks`` busca la cadena de
  bloques ``etiqueta(4) + tamaño(4)`` que cubre el archivo y la vuelca a JSON con pistas
  (número de registros, cadenas) para poder estudiar el formato.
"""
from __future__ import annotations

import struct
from pathlib import Path

from . import formats as F
from .imaging import read_palette, write_png

RAW_2D = (".nbfp", ".nbfc", ".nbfs")
RAW_TEX = (".ntft", ".ntfp", ".ntfi")


def u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


# ------------------------------------------------------------ 2D en crudo
def raw_palette(data: bytes):
    return read_palette(data, 0, len(data) // 2)


def raw_tiles(data: bytes, bpp: int) -> dict:
    return {"tiles_x": 0xFFFF, "tiles_y": 0xFFFF, "bpp": bpp, "linear": False,
            "pixels": bytes(data), "ntiles": len(data) * 8 // bpp // 64}


def guess_bpp(chr_data: bytes, pal, scr: bytes | None) -> int:
    """4 u 8 bits por píxel: sin cabecera hay que deducirlo de la pantalla y la paleta."""
    ncol = len(pal) if pal else 16
    if scr and len(scr) >= 2:
        ents = struct.unpack_from(f"<{len(scr) // 2}H", scr)
        top = max(e & 0x3FF for e in ents) + 1
        banks = any(e >> 12 for e in ents)
        fits4, fits8 = top * 32 <= len(chr_data), top * 64 <= len(chr_data)
        if banks and fits4:
            return 4
        if fits8 and ncol > 16 and not banks:
            return 8
        return 4 if fits4 or not fits8 else 8
    return 4  # sin pantalla no hay forma fiable de saberlo: 4 bpp es lo habitual en fondos


def screen_size(scr: bytes) -> tuple[int, int]:
    n = len(scr) // 2
    tw = 32 if n >= 32 else max(1, n)
    return tw * 8, max(1, (n + tw - 1) // tw) * 8


def convert_raw_2d(items: dict[str, bytes], folder: Path) -> dict[str, tuple[list[Path], dict]]:
    """items: {"nombre.nbfx": datos} de una carpeta -> {nombre: (salidas, notas)}."""
    def stem(n): return n.rsplit(".", 1)[0]
    by = {ext: {stem(n): d for n, d in items.items() if n.lower().endswith(ext)} for ext in RAW_2D}
    pals, chrs, scrs = by[".nbfp"], by[".nbfc"], by[".nbfs"]

    def pick(d: dict, base: str):
        if base in d:
            return d[base]
        return next(iter(d.values())) if len(d) == 1 else None

    folder.mkdir(parents=True, exist_ok=True)
    res: dict[str, tuple[list[Path], dict]] = {}
    for base, d in pals.items():
        dest = folder / f"{base}.palette.png"
        F.nclr_to_png(raw_palette(d), dest)
        res[f"{base}.nbfp"] = ([dest], {"colors": len(d) // 2})
    for base, d in chrs.items():
        praw, scr = pick(pals, base), pick(scrs, base)
        pal = raw_palette(praw) if praw else None
        bpp = guess_bpp(d, pal, scr)
        g = raw_tiles(d, bpp)
        dest = folder / f"{base}.png"
        F.ncgr_to_png(g, pal, dest)
        res[f"{base}.nbfc"] = ([dest], {"bpp": bpp, "supuesto": True})
        if scr:
            w, h = screen_size(scr)
            sdest = folder / f"{base}.screen.png"
            F.screen_to_png(scr, w, h, g, pal, sdest)
            res[f"{base}.nbfs"] = ([sdest], {"size": [w, h], "bpp": bpp, "supuesto": True})
    for base in scrs:
        res.setdefault(f"{base}.nbfs", ([], {"error": "sin .nbfc en la carpeta"}))
    return res


# ------------------------------------------------------ texturas en crudo
def _pow2_dims(px: int) -> tuple[int, int] | None:
    best = None
    for wl in range(3, 11):
        w = 1 << wl
        if px % w:
            continue
        h = px // w
        if h < 8 or h > 1024 or h & (h - 1) or h > w:
            continue
        if best is None or abs(w - h) < abs(best[0] - best[1]):
            best = (w, h)
    return best


def guess_texture(tex: bytes, pal: bytes | None, idx: bytes | None) -> dict | None:
    """Formato y tamaño probables de una textura en crudo (formatos de TEX_FORMATS)."""
    if idx is not None:
        fmt, px = 5, len(tex) * 4
    elif pal is None:
        fmt, px = 7, len(tex) // 2
    else:
        ncol = len(pal) // 2
        fmt = 2 if ncol <= 4 else 6 if ncol <= 8 else 3 if ncol <= 16 else 1 if ncol <= 32 else 4
        bpp = {2: 2, 3: 4, 4: 8, 1: 8, 6: 8}[fmt]
        px = len(tex) * 8 // bpp
    dims = _pow2_dims(px)
    if dims is None:
        w = 256 if px % 256 == 0 and px >= 256 * 8 else 64 if px % 64 == 0 else 8
        dims = (w, max(1, px // w))
    w, h = dims
    if fmt == 5:
        w, h = max(4, w - w % 4), max(4, h - h % 4)
    return {"format": F.TEX_FORMATS[fmt], "fmt": fmt, "w": w, "h": h, "supuesto": True}


def convert_raw_tex(items: dict[str, bytes], folder: Path) -> dict[str, tuple[list[Path], dict]]:
    def stem(n): return n.rsplit(".", 1)[0]
    by = {ext: {stem(n): d for n, d in items.items() if n.lower().endswith(ext)} for ext in RAW_TEX}
    texs, pals, idxs = by[".ntft"], by[".ntfp"], by[".ntfi"]
    folder.mkdir(parents=True, exist_ok=True)
    res: dict[str, tuple[list[Path], dict]] = {}
    for base, tex in texs.items():
        pal = pals.get(base) if base in pals else (next(iter(pals.values())) if len(pals) == 1 else None)
        idx = idxs.get(base)
        g = guess_texture(tex, pal, idx)
        fmt, w, h = g["fmt"], g["w"], g["h"]
        if fmt == 5:
            rgba = F.decode_texture(5, w, h, tex, 0, pal or b"", 0, False, comp=tex, comp_off=0, pidx=idx, pidx_off=0)
        else:
            rgba = F.decode_texture(fmt, w, h, tex, 0, pal or b"", 0, True)
        dest = folder / f"{base}.png"
        write_png(dest, w, h, rgba)
        del g["fmt"]
        res[f"{base}.ntft"] = ([dest], g)
    for base, d in pals.items():
        dest = folder / f"{base}.palette.png"
        F.nclr_to_png(raw_palette(d), dest)
        res[f"{base}.ntfp"] = ([dest], {"colors": len(d) // 2})
    return res


# --------------------------------------------------- formatos por bloques
def _tag_ok(t: bytes) -> bool:
    if len(t) != 4:
        return False
    body = t.rstrip(b"\0") if t[0] else b""
    if len(body) < 3:
        return False
    return all(48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or b in b" _." for b in body)


def looks_chunked(data: bytes) -> bool:
    """Magia de 4 caracteres en mayúsculas/dígitos (ZOLB, BPAM, JNCL, ' APS', 'STB\\0'...).
    Las minúsculas se descartan para no tomar texto plano por un formato."""
    t = bytes(data[:4])
    return len(data) >= 8 and _tag_ok(t) and all(48 <= b <= 57 or 65 <= b <= 90 or b in b" \0" for b in t)


def _tag(t: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in t)


def _chain(data: bytes, start: int, end: int, incl: bool):
    """Bloques etiqueta+tamaño desde start; devuelve (lista, final) o None si no llega a end."""
    p, out = start, []
    while p + 8 <= end:
        t = bytes(data[p:p + 4])
        if not _tag_ok(t):
            break
        size = u32(data, p + 4)
        nxt = p + size if incl else p + 8 + size
        if (incl and size < 8) or nxt > end:
            break
        out.append((p, t, size, p + 8, nxt))
        p = nxt
        if not incl and p % 4 and p + (4 - p % 4) <= end and not any(data[p:p + (4 - p % 4)]):
            p += 4 - p % 4
    tail = data[p:end]
    if out and (p == end or (len(tail) < 32 and not any(tail))):
        return out
    return None


def _best_chain(data: bytes, lo: int, hi: int, starts):
    best = None
    for s in starts:
        if lo + s + 8 > hi:
            break
        for incl in (True, False):
            c = _chain(data, lo + s, hi, incl)
            # La que empieza antes cubre más archivo (y deja los bloques anidados como hijos)
            if c and (best is None or s < best[1] or (s == best[1] and len(c) > len(best[0]))):
                best = (c, s, incl)
    return best


def strings(data: bytes, lo: int, hi: int, n: int = 20) -> list[str]:
    out, cur = [], bytearray()
    for b in bytes(data[lo:hi]) + b"\0":
        if 32 <= b < 127:
            cur.append(b)
            continue
        if len(cur) >= 4:
            out.append(cur.decode("ascii"))
            if len(out) >= n:
                break
        cur = bytearray()
    return out


def _words(data: bytes, lo: int, hi: int, n: int = 8) -> list[str]:
    return [f"{u32(data, o):08x}" for o in range(lo, min(hi - 3, lo + 4 * n), 4)]


def _records(data: bytes, lo: int, hi: int) -> dict | None:
    """¿Empieza por un contador (u16/u32) y el resto se reparte en registros iguales?"""
    size = hi - lo
    for hdr, fmt in ((4, "<I"), (2, "<H")):
        if size < hdr + 1:
            continue
        n = struct.unpack_from(fmt, data, lo)[0]
        if 0 < n <= 10000 and (size - hdr) % n == 0 and 2 <= (size - hdr) // n <= 1024:
            return {"count": n, "record_size": (size - hdr) // n, "count_bytes": hdr}
    return None


def _describe(data: bytes, lo: int, hi: int, depth: int) -> dict:
    d: dict = {"u32": _words(data, lo, hi)}
    r = _records(data, lo, hi)
    if r:
        d["records_guess"] = r
    st = strings(data, lo, hi, 10)
    if st:
        d["strings"] = st
    if depth < 3 and hi - lo >= 16:
        sub = _best_chain(data, lo, hi, range(0, 0x24, 4))
        if sub and (len(sub[0]) >= 2 or sub[0][0][4] - sub[0][0][0] >= (hi - lo) // 2):
            d["children_at"] = sub[1]
            d["children"] = [_chunk(data, c, sub[2], depth + 1) for c in sub[0]]
    return d


def _chunk(data: bytes, c, incl: bool, depth: int) -> dict:
    p, t, size, lo, hi = c
    return {"tag": _tag(t), "tag_reversed": _tag(t[::-1]), "offset": p, "size": size,
            "size_includes_header": incl, **_describe(data, lo, hi, depth)}


def parse_chunks(data: bytes) -> dict:
    """Estructura de un formato por bloques (o pistas generales si no se encuentra)."""
    data = bytes(data)
    n = len(data)
    info: dict = {"magic": _tag(data[:4]), "magic_reversed": _tag(data[:4][::-1]), "size": n,
                  "header_u32": _words(data, 0, n)}
    sizes_at = [o for o in range(4, min(n - 3, 0x20), 4) if u32(data, o) == n]
    if sizes_at:
        info["file_size_at"] = sizes_at[0]
    best = _best_chain(data, 0, n, range(4, 0x44, 4))
    if best:
        info["chunks_at"] = best[1]
        info["chunks"] = [_chunk(data, c, best[2], 1) for c in best[0]]
    else:
        info.update(_describe(data, 0, n, 1))
    return info


def chunk_signature(info: dict) -> str:
    """Resumen corto de la estructura ('ZOLB > OBJL MPOB') para agrupar en report.txt."""
    def walk(cs):
        return " ".join(c["tag"].strip(". ") + (f"[{walk(c['children'])}]" if c.get("children") else "")
                        for c in cs)
    return f"{info['magic']} > {walk(info['chunks'])}" if info.get("chunks") else f"{info['magic']} (sin bloques)"
