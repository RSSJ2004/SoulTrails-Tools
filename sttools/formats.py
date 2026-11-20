"""Conversores de formatos de Nintendo DS (Nitro) a formatos abiertos.

- NSBTX / NSBMD (bloque TEX0) -> PNG por textura
- NCLR / NCGR / NSCR / NCER  -> PNG (paleta, hoja de tiles, pantalla)
- BMG                        -> JSON (mensajes)
- SDAT                       -> archivos internos con nombre (SSEQ/SBNK/SWAR/STRM...)
- SWAR / STRM / SWAV         -> WAV
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

from .imaging import read_palette, rgb555, write_png


# ------------------------------------------------------------- utilidades
def u8(d, o): return d[o]
def u16(d, o): return struct.unpack_from("<H", d, o)[0]
def u32(d, o): return struct.unpack_from("<I", d, o)[0]


def nitro_blocks(data: bytes) -> dict[bytes, int]:
    """Bloques de un archivo Nitro estándar (cabecera de 0x10 con nº de bloques)."""
    hdr = u16(data, 0xC)
    n = u16(data, 0xE)
    blocks = {}
    p = hdr
    for _ in range(n):
        if p + 8 > len(data):
            break
        magic = bytes(data[p:p + 4])
        size = u32(data, p + 4)
        blocks.setdefault(magic, p)
        if size < 8:
            break
        p += size
    return blocks


def safe_name(s: str) -> str:
    s = "".join(c if c.isalnum() or c in "._-" else "_" for c in s).strip("._")
    return s or "unnamed"


# ----------------------------------------------------------------- 3D dict
def read_dict(data: bytes, off: int, entry_hint: int | None = None) -> list[tuple[str, int]]:
    """Diccionario 3D de Nitro: devuelve [(nombre, offset_de_datos)]."""
    # 0x0 u8 0 | 0x1 u8 n | 0x2 u16 tamaño | 0x4 árbol (cabecera 8 + n*4) | 0xC+4n datos
    count = data[off + 1]
    p = off + 0xC + 4 * count
    entry_size = u16(data, p)
    p += 4
    data_off = p
    names_off = p + count * entry_size
    out = []
    for i in range(count):
        raw = data[names_off + i * 16: names_off + i * 16 + 16]
        name = raw.split(b"\0")[0].decode("latin-1")
        out.append((name, data_off + i * entry_size))
    return out


# ------------------------------------------------------------- texturas 3D
TEX_FORMATS = {1: "a3i5", 2: "pal4", 3: "pal16", 4: "pal256", 5: "tex4x4", 6: "a5i3", 7: "direct"}


def decode_texture(fmt, w, h, tex, tex_off, pal, pal_off, color0_transparent,
                   comp=None, comp_off=0, pidx=None, pidx_off=0) -> bytearray:
    out = bytearray(w * h * 4)

    def put(i, rgba):
        out[i * 4:i * 4 + 4] = bytes(rgba)

    def color(idx):
        o = pal_off + idx * 2
        if o + 2 > len(pal):
            return (255, 0, 255, 255)
        return rgb555(u16(pal, o))

    if fmt == 7:
        for i in range(w * h):
            c = u16(tex, tex_off + i * 2) if tex_off + i * 2 + 2 <= len(tex) else 0
            put(i, rgb555(c, 255 if c & 0x8000 else 0))
    elif fmt in (2, 3, 4):
        bpp = {2: 2, 3: 4, 4: 8}[fmt]
        per = 8 // bpp
        mask = (1 << bpp) - 1
        for i in range(w * h):
            o = tex_off + i // per
            b = tex[o] if o < len(tex) else 0
            idx = (b >> ((i % per) * bpp)) & mask
            r, g, bb, _ = color(idx)
            put(i, (r, g, bb, 0 if (idx == 0 and color0_transparent) else 255))
    elif fmt in (1, 6):
        ib, ab = (5, 3) if fmt == 1 else (3, 5)
        for i in range(w * h):
            o = tex_off + i
            b = tex[o] if o < len(tex) else 0
            idx = b & ((1 << ib) - 1)
            a = b >> ib
            a8 = (a * 255) // ((1 << ab) - 1)
            r, g, bb, _ = color(idx)
            put(i, (r, g, bb, a8))
    elif fmt == 5 and comp is not None:
        bw = w // 4
        for by in range(h // 4):
            for bx in range(bw):
                blk = by * bw + bx
                o = comp_off + blk * 4
                if o + 4 > len(comp):
                    continue
                texels = u32(comp, o)
                po = pidx_off + blk * 2
                pi = u16(pidx, po) if po + 2 <= len(pidx) else 0
                mode = pi >> 14
                base = pal_off + (pi & 0x3FFF) * 4

                def c(n):
                    oo = base + n * 2
                    return rgb555(u16(pal, oo)) if oo + 2 <= len(pal) else (255, 0, 255, 255)

                c0, c1 = c(0), c(1)
                if mode == 0:
                    cols = [c0, c1, c(2), (0, 0, 0, 0)]
                elif mode == 1:
                    mid = tuple((a + b) // 2 for a, b in zip(c0[:3], c1[:3])) + (255,)
                    cols = [c0, c1, mid, (0, 0, 0, 0)]
                elif mode == 2:
                    cols = [c0, c1, c(2), c(3)]
                else:
                    m1 = tuple((5 * a + 3 * b) // 8 for a, b in zip(c0[:3], c1[:3])) + (255,)
                    m2 = tuple((3 * a + 5 * b) // 8 for a, b in zip(c0[:3], c1[:3])) + (255,)
                    cols = [c0, c1, m1, m2]
                for ty in range(4):
                    for tx in range(4):
                        t = (texels >> ((ty * 4 + tx) * 2)) & 3
                        put((by * 4 + ty) * w + bx * 4 + tx, cols[t])
    return out


def pair_palette(tex_name: str, i: int, pals: list[tuple[str, int, int]], ntex: int):
    for cand in (tex_name + "_pl", tex_name + "_p", tex_name):
        for p in pals:
            if p[0] == cand:
                return p
    if len(pals) == ntex and i < len(pals):
        return pals[i]
    return pals[0] if pals else None


def convert_tex0(data: bytes, out_dir: Path) -> list[str]:
    """Extrae todas las texturas de un BTX0/BMD0 a PNG. Devuelve las rutas creadas."""
    blocks = nitro_blocks(data)
    if b"TEX0" not in blocks:
        return []
    t = blocks[b"TEX0"]
    tex_info = t + u16(data, t + 0x0E)
    tex_data = t + u32(data, t + 0x14)
    comp_data = t + u32(data, t + 0x24)
    comp_pidx = t + u32(data, t + 0x28)
    pal_dict = t + u32(data, t + 0x34)
    pal_data = t + u32(data, t + 0x38)

    texs = []
    for name, eo in read_dict(data, tex_info):
        param = u32(data, eo)
        texs.append((name, param))
    pals = []
    for name, eo in read_dict(data, pal_dict):
        pals.append((name, u16(data, eo) << 3, u16(data, eo + 2)))

    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, (name, p) in enumerate(texs):
        off = (p & 0xFFFF) << 3
        w = 8 << ((p >> 20) & 7)
        h = 8 << ((p >> 23) & 7)
        fmt = (p >> 26) & 7
        transp = bool((p >> 29) & 1)
        if fmt == 0:
            continue
        pal = pair_palette(name, i, pals, len(texs))
        pal_off = pal_data + (pal[1] if pal else 0)
        if fmt == 5:
            rgba = decode_texture(fmt, w, h, data, 0, data, pal_off, transp,
                                  comp=data, comp_off=comp_data + off,
                                  pidx=data, pidx_off=comp_pidx + off // 2)
        else:
            rgba = decode_texture(fmt, w, h, data, tex_data + off, data, pal_off, transp)
        dest = out_dir / f"{safe_name(name)}.png"
        write_png(dest, w, h, rgba)
        made.append(str(dest))
    return made


# ------------------------------------------------------------------- 2D
def parse_nclr(data: bytes):
    b = nitro_blocks(data)
    p = b.get(b"TTLP")
    if p is None:
        raise ValueError("NCLR sin TTLP")
    depth = u32(data, p + 8)
    size = u32(data, p + 0x10)
    off = p + 8 + u32(data, p + 0x14)
    size = min(size, len(data) - off)
    return depth, read_palette(data, off, size // 2)


def parse_ncgr(data: bytes):
    b = nitro_blocks(data)
    p = b.get(b"RAHC")
    if p is None:
        raise ValueError("NCGR sin RAHC")
    ty = u16(data, p + 8)
    tx = u16(data, p + 0xA)
    depth = u32(data, p + 0xC)
    linear = u32(data, p + 0x14) & 0xFF
    size = u32(data, p + 0x18)
    off = p + 8 + u32(data, p + 0x1C)
    pix = data[off: off + size]
    bpp = 4 if depth == 3 else 8
    ntiles = len(pix) * 8 // bpp // 64
    return {"tiles_x": tx, "tiles_y": ty, "bpp": bpp, "linear": bool(linear), "pixels": pix, "ntiles": ntiles}


def tile_pixels(g, idx: int) -> list[int]:
    bpp = g["bpp"]
    tb = 64 * bpp // 8
    raw = g["pixels"][idx * tb:(idx + 1) * tb]
    if len(raw) < tb:
        return [0] * 64
    if bpp == 8:
        return list(raw)
    out = []
    for b in raw:
        out += [b & 0xF, b >> 4]
    return out


def ncgr_to_png(g, pal, dest: Path, pal_bank: int = 0) -> None:
    tx = g["tiles_x"] if g["tiles_x"] not in (0, 0xFFFF) else 32
    tx = max(1, min(tx, g["ntiles"])) if g["ntiles"] else 1
    ty = max(1, (g["ntiles"] + tx - 1) // tx)
    w, h = tx * 8, ty * 8
    rgba = bytearray(w * h * 4)
    if g["linear"] and g["tiles_x"] not in (0, 0xFFFF):
        bpp = g["bpp"]
        for i in range(min(w * h, len(g["pixels"]) * 8 // bpp)):
            idx = g["pixels"][i] if bpp == 8 else (g["pixels"][i // 2] >> ((i & 1) * 4)) & 0xF
            c = pal[(pal_bank * 16 + idx) % max(1, len(pal))] if pal else (idx * 17,) * 3 + (255,)
            rgba[i * 4:i * 4 + 4] = bytes(c[:3] + ((0,) if idx == 0 else (255,)))
    else:
        for t in range(g["ntiles"]):
            px = tile_pixels(g, t)
            ox, oy = (t % tx) * 8, (t // tx) * 8
            for k, idx in enumerate(px):
                c = pal[(pal_bank * 16 + idx) % max(1, len(pal))] if pal else (idx * 17 & 255,) * 3 + (255,)
                o = ((oy + k // 8) * w + ox + k % 8) * 4
                rgba[o:o + 4] = bytes(c[:3] + ((0,) if idx == 0 else (255,)))
    write_png(dest, w, h, rgba)


def nscr_to_png(data: bytes, g, pal, dest: Path) -> None:
    b = nitro_blocks(data)
    p = b.get(b"NRCS")
    if p is None:
        raise ValueError("NSCR sin NRCS")
    w = u16(data, p + 8)
    h = u16(data, p + 0xA)
    size = u32(data, p + 0x10)
    entries = data[p + 0x14: p + 0x14 + size]
    tw = w // 8
    rgba = bytearray(w * h * 4)
    for i in range(min(len(entries) // 2, tw * (h // 8))):
        e = u16(entries, i * 2)
        tile = e & 0x3FF
        hf, vf = (e >> 10) & 1, (e >> 11) & 1
        bank = e >> 12 if g["bpp"] == 4 else 0
        px = tile_pixels(g, tile)
        ox, oy = (i % tw) * 8, (i // tw) * 8
        for k, idx in enumerate(px):
            x, y = k % 8, k // 8
            if hf:
                x = 7 - x
            if vf:
                y = 7 - y
            c = pal[(bank * 16 + idx) % max(1, len(pal))] if pal else (idx * 17 & 255,) * 3 + (255,)
            o = ((oy + y) * w + ox + x) * 4
            rgba[o:o + 4] = bytes(c[:3] + ((0,) if idx == 0 else (255,)))
    write_png(dest, w, h, rgba)


def nclr_to_png(pal, dest: Path) -> None:
    n = len(pal)
    cols = 16
    rows = max(1, (n + cols - 1) // cols)
    s = 8
    rgba = bytearray(cols * s * rows * s * 4)
    for i, c in enumerate(pal):
        cx, cy = (i % cols) * s, (i // cols) * s
        for y in range(s):
            for x in range(s):
                o = ((cy + y) * cols * s + cx + x) * 4
                rgba[o:o + 4] = bytes(c)
    write_png(dest, cols * s, rows * s, rgba)


# ------------------------------------------------------------------- BMG
BMG_ENCODINGS = {0: "cp1252", 1: "cp1252", 2: "utf-16-le", 3: "shift_jis", 4: "utf-8"}


def parse_bmg(data: bytes) -> dict:
    if data[:8] != b"MESGbmg1":
        raise ValueError("no es BMG")
    nsec = u32(data, 0xC)
    enc = BMG_ENCODINGS.get(data[0x10], "utf-16-le")
    p = 0x20
    inf = dat = mid = None
    for _ in range(nsec):
        if p + 8 > len(data):
            break
        magic = data[p:p + 4]
        size = u32(data, p + 4)
        if magic == b"INF1":
            inf = p
        elif magic == b"DAT1":
            dat = p
        elif magic == b"MID1":
            mid = p
        if size < 8:
            break
        p += size
    if inf is None or dat is None:
        raise ValueError("BMG sin INF1/DAT1")
    count = u16(data, inf + 8)
    esize = u16(data, inf + 0xA)
    group = u16(data, inf + 0xC)
    ids = []
    if mid is not None:
        mcount = u16(data, mid + 8)
        ids = [u32(data, mid + 0x10 + i * 4) for i in range(mcount)]
    msgs = []
    for i in range(count):
        e = inf + 0x10 + i * esize
        off = u32(data, e)
        attrs = data[e + 4: e + esize].hex()
        msgs.append({"index": i, "id": ids[i] if i < len(ids) else None, "attributes": attrs,
                     "text": decode_bmg_string(data, dat + 8 + off, enc)})
    return {"encoding": enc, "group": group, "messages": msgs}


def decode_bmg_string(data: bytes, p: int, enc: str) -> str:
    """Decodifica hasta el terminador. Las secuencias de control (0x1A) se guardan como {hex}."""
    out = []
    wide = enc == "utf-16-le"
    while p < len(data):
        if wide:
            if p + 2 > len(data):
                break
            ch = u16(data, p)
            if ch == 0:
                break
            if ch == 0x1A:
                ln = data[p + 2]
                if ln < 3:
                    ln = 3
                out.append("{" + data[p + 3:p + ln].hex() + "}")
                p += ln
                continue
            out.append(chr(ch) if ch < 0xD800 or ch > 0xDFFF else "�")
            p += 2
        else:
            ch = data[p]
            if ch == 0:
                break
            if ch == 0x1A:
                ln = max(data[p + 1], 2)
                out.append("{" + data[p + 2:p + ln].hex() + "}")
                p += ln
                continue
            end = p + 1
            # carácter multibyte en SJIS / UTF-8
            if enc == "shift_jis" and (0x81 <= ch <= 0x9F or 0xE0 <= ch <= 0xFC):
                end = p + 2
            elif enc == "utf-8" and ch >= 0xC0:
                end = p + (2 if ch < 0xE0 else 3 if ch < 0xF0 else 4)
            out.append(data[p:end].decode(enc, "replace"))
            p = end
    return "".join(out)


# ---------------------------------------------------------------- audio
IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]
IMA_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767]


def ima_adpcm(block: bytes) -> list[int]:
    """IMA-ADPCM estilo DS: cabecera de 4 bytes (muestra inicial s16, índice) + nibbles (bajo primero)."""
    if len(block) < 4:
        return []
    pred = struct.unpack_from("<h", block, 0)[0]
    idx = min(max(block[2], 0), 88)
    out = []
    for b in block[4:]:
        for nib in (b & 0xF, b >> 4):
            step = IMA_STEP[idx]
            diff = step >> 3
            if nib & 1: diff += step >> 2
            if nib & 2: diff += step >> 1
            if nib & 4: diff += step
            pred = pred - diff if nib & 8 else pred + diff
            pred = max(-32768, min(32767, pred))
            idx = min(max(idx + IMA_INDEX[nib & 7], 0), 88)
            out.append(pred)
    return out


def decode_pcm(kind: int, raw: bytes) -> list[int]:
    if kind == 0:
        return [(struct.unpack_from("<b", raw, i)[0]) << 8 for i in range(len(raw))]
    if kind == 1:
        n = len(raw) // 2
        return list(struct.unpack_from(f"<{n}h", raw, 0))
    return ima_adpcm(raw)


def write_wav(path: Path, rate: int, channels: list[list[int]]) -> None:
    n = min(len(c) for c in channels) if channels else 0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(max(1, rate))
        frames = bytearray()
        for i in range(n):
            for c in channels:
                frames += struct.pack("<h", c[i])
        w.writeframes(bytes(frames))


def convert_swav_body(data: bytes, p: int, end: int, dest: Path) -> None:
    kind, loop, rate = data[p], data[p + 1], u16(data, p + 2)
    write_wav(dest, rate, [decode_pcm(kind, data[p + 12:end])])


def convert_swar(data: bytes, out_dir: Path) -> list[str]:
    b = nitro_blocks(data)
    p = b.get(b"DATA")
    if p is None:
        return []
    count = u32(data, p + 0x28)
    offs = [u32(data, p + 0x2C + i * 4) for i in range(count)]
    ends = offs[1:] + [len(data)]
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for i, (s, e) in enumerate(zip(offs, ends)):
        dest = out_dir / f"{i:03d}.wav"
        convert_swav_body(data, s, e, dest)
        made.append(str(dest))
    return made


def convert_swav(data: bytes, dest: Path) -> None:
    b = nitro_blocks(data)
    p = b[b"DATA"]
    convert_swav_body(data, p + 8, p + u32(data, p + 4), dest)


def convert_strm(data: bytes, dest: Path) -> None:
    b = nitro_blocks(data)
    h = b[b"HEAD"] + 8
    kind, loop, nch = data[h], data[h + 1], data[h + 2]
    rate = u16(data, h + 4)
    data_off = u32(data, h + 0x10)
    nblocks = u32(data, h + 0x14)
    blen = u32(data, h + 0x18)
    last_len = u32(data, h + 0x20)
    chans: list[list[int]] = [[] for _ in range(nch)]
    p = data_off
    for bi in range(nblocks):
        ln = last_len if bi == nblocks - 1 else blen
        for c in range(nch):
            chans[c] += decode_pcm(kind, data[p:p + ln])
            p += ln if bi != nblocks - 1 else ln
            if bi == nblocks - 1:
                # el último bloque puede tener relleno hasta 4 bytes
                p += (-ln) % 4
    write_wav(dest, rate, chans)


# ----------------------------------------------------------------- SDAT
SDAT_KINDS = ["seq", "seqarc", "bank", "wavearc", "player", "group", "player2", "strm"]
FILE_KINDS = {"seq": ".sseq", "seqarc": ".ssar", "bank": ".sbnk", "wavearc": ".swar", "strm": ".strm"}


def unpack_sdat(data: bytes) -> list[tuple[str, bytes]]:
    """SDAT -> [(nombre, bytes)] usando los nombres de SYMB si existen."""
    symb_off, symb_size = u32(data, 0x10), u32(data, 0x14)
    info_off = u32(data, 0x18)
    fat_off = u32(data, 0x20)
    nfiles = u32(data, fat_off + 8)
    fat = [(u32(data, fat_off + 0xC + i * 16), u32(data, fat_off + 0x10 + i * 16)) for i in range(nfiles)]

    def symb_names(kind_i):
        if not symb_off or not symb_size:
            return []
        rec = symb_off + u32(data, symb_off + 8 + kind_i * 4)
        n = u32(data, rec)
        names = []
        for j in range(n):
            o = u32(data, rec + 4 + j * 4)
            if o == 0:
                names.append(None)
            else:
                e = data.find(b"\0", symb_off + o)
                names.append(data[symb_off + o:e].decode("latin-1"))
        return names

    out = []
    used = set()
    for ki, kind in enumerate(SDAT_KINDS):
        if kind not in FILE_KINDS:
            continue
        rec = info_off + u32(data, info_off + 8 + ki * 4)
        n = u32(data, rec)
        names = symb_names(ki)
        for j in range(n):
            o = u32(data, rec + 4 + j * 4)
            if o == 0:
                continue
            fid = u16(data, info_off + o)
            if fid >= nfiles:
                continue
            name = names[j] if j < len(names) and names[j] else f"{kind}_{j:04d}"
            s, size = fat[fid]
            out.append((f"{kind}/{safe_name(name)}{FILE_KINDS[kind]}", data[s:s + size]))
            used.add(fid)
    for fid, (s, size) in enumerate(fat):
        if fid not in used:
            out.append((f"unreferenced/{fid:04d}.bin", data[s:s + size]))
    return out
