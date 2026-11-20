"""Lectura de ROMs de Nintendo DS y formatos de compresión/contenedores.

Todo está escrito a partir de la documentación pública del formato (GBATEK y
similares). Sin dependencias externas.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

NITROCODE = bytes.fromhex("2106C0DE") + bytes.fromhex("DEC00621")  # BE seguido de LE


# --------------------------------------------------------------- compresión
def blz_decompress(data: bytes) -> bytes:
    """Descomprime 'bottom LZ' (código ARM9 y overlays). Si no está comprimido devuelve igual."""
    if len(data) < 8:
        return bytes(data)
    enc_and_hdr, inc_len = struct.unpack_from("<II", data, len(data) - 8)
    hdr_len = enc_and_hdr >> 24
    enc_len = enc_and_hdr & 0xFFFFFF
    if hdr_len < 8 or enc_len > len(data) or hdr_len > enc_len:
        raise ValueError("cabecera BLZ inválida")
    dec_len = len(data) + inc_len
    out = bytearray(data) + bytearray(inc_len)
    src = len(data) - hdr_len
    dst = dec_len
    end = len(data) - enc_len
    inp = bytes(data)  # el flujo comprimido se lee de una copia: no importa si la salida lo pisa
    while dst > end and src > end:
        src -= 1
        flags = inp[src]
        for _ in range(8):
            if dst <= end or src <= end:
                break
            if flags & 0x80:
                src -= 2
                pair = inp[src] | (inp[src + 1] << 8)
                ln = (pair >> 12) + 3
                disp = (pair & 0xFFF) + 3
                for _ in range(ln):
                    if dst <= end:
                        break
                    dst -= 1
                    out[dst] = out[dst + disp]
            else:
                src -= 1
                dst -= 1
                out[dst] = inp[src]
            flags = (flags << 1) & 0xFF
    return bytes(out[:dec_len])


def lz_decompress(data: bytes, offset: int = 0) -> bytes:
    """LZ10 (0x10) y LZ11 (0x11), los usados por las funciones de la BIOS y NitroSDK."""
    kind = data[offset]
    size = data[offset + 1] | (data[offset + 2] << 8) | (data[offset + 3] << 16)
    p = offset + 4
    if size == 0:
        size = struct.unpack_from("<I", data, p)[0]
        p += 4
    out = bytearray()
    while len(out) < size:
        flags = data[p]
        p += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                if kind == 0x10:
                    b1, b2 = data[p], data[p + 1]
                    p += 2
                    ln = (b1 >> 4) + 3
                    disp = (((b1 & 0xF) << 8) | b2) + 1
                else:
                    b1 = data[p]
                    ind = b1 >> 4
                    if ind == 0:
                        b2, b3 = data[p + 1], data[p + 2]
                        ln = (((b1 & 0xF) << 4) | (b2 >> 4)) + 0x11
                        disp = (((b2 & 0xF) << 8) | b3) + 1
                        p += 3
                    elif ind == 1:
                        b2, b3, b4 = data[p + 1], data[p + 2], data[p + 3]
                        ln = (((b1 & 0xF) << 12) | (b2 << 4) | (b3 >> 4)) + 0x111
                        disp = (((b3 & 0xF) << 8) | b4) + 1
                        p += 4
                    else:
                        b2 = data[p + 1]
                        ln = ind + 1
                        disp = (((b1 & 0xF) << 8) | b2) + 1
                        p += 2
                if disp > len(out):
                    raise ValueError("LZ: referencia fuera de rango")
                for _ in range(ln):
                    out.append(out[-disp])
            else:
                out.append(data[p])
                p += 1
    return bytes(out[:size])


def looks_lz(data: bytes) -> bool:
    """Heurística: ¿parece un bloque LZ10/LZ11 válido?"""
    if len(data) < 5 or data[0] not in (0x10, 0x11):
        return False
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    if size == 0 and len(data) >= 8:
        size = struct.unpack_from("<I", data, 4)[0]
    # Ratio razonable y tamaño limitado
    if size < len(data) // 2 or size > 64 * len(data) + 64 or size > 64 << 20:
        return False
    try:
        lz_decompress(data)
        return True
    except (ValueError, IndexError):
        return False


# ---------------------------------------------------------------- contenedores
def parse_narc(data: bytes) -> list[tuple[str, bytes]]:
    """NARC -> [(ruta, bytes)]. Si no tiene nombres usa el índice."""
    if data[:4] != b"NARC":
        raise ValueError("no es NARC")
    hdr_size = struct.unpack_from("<H", data, 0xC)[0]
    p = hdr_size
    blocks = {}
    while p + 8 <= len(data):
        magic = data[p:p + 4]
        size = struct.unpack_from("<I", data, p + 4)[0]
        if size < 8:
            break
        blocks[magic] = (p, size)
        p += size
    btaf, _ = blocks[b"BTAF"]
    count = struct.unpack_from("<H", data, btaf + 8)[0]
    entries = [struct.unpack_from("<II", data, btaf + 12 + i * 8) for i in range(count)]
    gmif, _ = blocks[b"GMIF"]
    base = gmif + 8
    names = {}
    if b"BTNF" in blocks:
        btnf, bsize = blocks[b"BTNF"]
        fnt = data[btnf + 8: btnf + bsize]
        try:
            for path, fid in walk_fnt_bytes(fnt):
                names[fid] = path
        except (IndexError, struct.error):
            names = {}
    out = []
    for i, (s, e) in enumerate(entries):
        out.append((names.get(i, f"{i:04d}"), data[base + s: base + e]))
    return out


def walk_fnt_bytes(fnt: bytes) -> list[tuple[str, int]]:
    """Recorre una tabla de nombres NitroFS (FNT) en memoria."""
    out: list[tuple[str, int]] = []
    if len(fnt) < 8:
        return out
    ndirs = struct.unpack_from("<H", fnt, 6)[0]

    def walk(dir_id: int, prefix: str, depth: int) -> None:
        if depth > 32:
            return
        idx = dir_id & 0xFFF
        if idx >= max(ndirs, 1):
            return
        sub_off, fid, _ = struct.unpack_from("<IHH", fnt, idx * 8)
        pos = sub_off
        while pos < len(fnt):
            t = fnt[pos]
            if t == 0:
                break
            ln = t & 0x7F
            name = fnt[pos + 1: pos + 1 + ln].decode("latin-1")
            pos += 1 + ln
            if t & 0x80:
                sub = struct.unpack_from("<H", fnt, pos)[0]
                pos += 2
                walk(sub, f"{prefix}{name}/", depth + 1)
            else:
                out.append((prefix + name, fid))
                fid += 1

    walk(0xF000, "", 0)
    return out


# ------------------------------------------------------------------------ ROM
@dataclass
class Overlay:
    id: int
    ram: int
    ram_size: int
    bss_size: int
    sinit_start: int
    sinit_end: int
    file_id: int
    flags: int
    data: bytes = b""

    @property
    def compressed(self) -> bool:
        return bool(self.flags & 0x01000000)

    def code(self) -> bytes:
        """Código del overlay descomprimido si hace falta."""
        if self.compressed:
            return blz_decompress(self.data)
        return self.data


@dataclass
class Segment:
    """Rango de memoria con código/datos: el binario estático, autoloads (ITCM/DTCM) u overlays."""
    name: str
    base: int
    data: bytes
    bss: int = 0
    overlay: int | None = None

    @property
    def end(self) -> int:
        return self.base + len(self.data)


@dataclass
class Rom:
    path: Path
    raw: bytes
    header: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "Rom":
        raw = Path(path).read_bytes()
        r = cls(Path(path), raw)
        h = raw[:0x200]
        u32 = lambda o: struct.unpack_from("<I", h, o)[0]
        r.header = {
            "title": h[0:12].rstrip(b"\0").decode("ascii", "replace"),
            "gamecode": h[12:16].decode("ascii", "replace"),
            "arm9_off": u32(0x20), "arm9_entry": u32(0x24), "arm9_ram": u32(0x28), "arm9_size": u32(0x2C),
            "arm7_off": u32(0x30), "arm7_entry": u32(0x34), "arm7_ram": u32(0x38), "arm7_size": u32(0x3C),
            "fnt_off": u32(0x40), "fnt_size": u32(0x44), "fat_off": u32(0x48), "fat_size": u32(0x4C),
            "ovt9_off": u32(0x50), "ovt9_size": u32(0x54), "ovt7_off": u32(0x58), "ovt7_size": u32(0x5C),
            "banner_off": u32(0x68),
        }
        return r

    # --- sistema de archivos
    def file_range(self, fid: int) -> tuple[int, int]:
        return struct.unpack_from("<II", self.raw, self.header["fat_off"] + fid * 8)

    def file(self, fid: int) -> bytes:
        s, e = self.file_range(fid)
        return self.raw[s:e]

    def files(self) -> list[tuple[str, int]]:
        h = self.header
        return walk_fnt_bytes(self.raw[h["fnt_off"]: h["fnt_off"] + h["fnt_size"]])

    # --- código
    def arm9_raw(self) -> bytes:
        h = self.header
        return self.raw[h["arm9_off"]: h["arm9_off"] + h["arm9_size"]]

    def arm7_raw(self) -> bytes:
        h = self.header
        return self.raw[h["arm7_off"]: h["arm7_off"] + h["arm7_size"]]

    def overlays9(self) -> list[Overlay]:
        h = self.header
        out = []
        for i in range(h["ovt9_size"] // 32):
            fields = struct.unpack_from("<8I", self.raw, h["ovt9_off"] + i * 32)
            ov = Overlay(*fields)
            ov.data = self.file(ov.file_id)
            out.append(ov)
        return out

    def arm9_module_params(self, arm9: bytes | None = None) -> dict | None:
        """Busca la estructura _start_ModuleParams a partir de la marca 'nitrocode'."""
        arm9 = arm9 if arm9 is not None else self.arm9_raw()
        pos = arm9.find(NITROCODE)
        if pos < 0x1C:
            return None
        base = pos - 0x1C
        f = struct.unpack_from("<7I", arm9, base)
        return {
            "offset": base,
            "autoload_list_start": f[0], "autoload_list_end": f[1], "autoload_start": f[2],
            "bss_start": f[3], "bss_end": f[4], "compressed_static_end": f[5], "sdk_version": f[6],
        }

    def arm9_decompressed(self) -> bytes:
        """Binario ARM9 descomprimido (si el juego lo distribuye comprimido)."""
        arm9 = self.arm9_raw()
        mp = self.arm9_module_params(arm9)
        if not mp or not mp["compressed_static_end"]:
            return arm9
        end = mp["compressed_static_end"] - self.header["arm9_ram"]
        dec = blz_decompress(arm9[:end]) + arm9[end:]
        return dec

    def arm9_segments(self) -> list[Segment]:
        """Divide el ARM9 en: estático + autoloads (ITCM, DTCM...)."""
        ram = self.header["arm9_ram"]
        arm9 = self.arm9_decompressed()
        mp = self.arm9_module_params(arm9)
        if not mp:
            return [Segment("arm9", ram, arm9)]
        segs = []
        auto_start = mp["autoload_start"] - ram
        list_s = mp["autoload_list_start"] - ram
        list_e = mp["autoload_list_end"] - ram
        segs.append(Segment("arm9", ram, arm9[:auto_start], bss=max(0, mp["bss_end"] - mp["bss_start"])))
        p = auto_start
        for i, o in enumerate(range(list_s, list_e, 12)):
            addr, size, bss = struct.unpack_from("<III", arm9, o)
            name = "itcm" if 0x01000000 <= addr < 0x02000000 else ("dtcm" if 0x027E0000 <= addr < 0x02800000 else f"autoload{i}")
            segs.append(Segment(name, addr, arm9[p: p + size], bss=bss))
            p += size
        return segs
