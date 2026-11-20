"""Constructores de archivos sintéticos en formatos DS para tests (sin datos del juego)."""
from __future__ import annotations

import struct


# ----------------------------------------------------------------- compresión
def _find_match(buf: bytes, i: int, min_disp: int, max_disp: int, max_len: int):
    best_len, best_disp = 0, 0
    for disp in range(min_disp, min(max_disp, i) + 1):
        ln = 0
        while ln < max_len and i + ln < len(buf) and buf[i + ln] == buf[i + ln - disp]:
            ln += 1
        if ln > best_len:
            best_len, best_disp = ln, disp
    return best_len, best_disp


def lz10_compress(data: bytes) -> bytes:
    out = bytearray([0x10]) + len(data).to_bytes(3, "little")
    i = 0
    while i < len(data):
        flag_pos = len(out)
        out.append(0)
        for bit in range(8):
            if i >= len(data):
                break
            ln, disp = _find_match(data, i, 1, 4096, 18)
            if ln >= 3:
                out[flag_pos] |= 0x80 >> bit
                out += bytes([((ln - 3) << 4) | ((disp - 1) >> 8), (disp - 1) & 0xFF])
                i += ln
            else:
                out.append(data[i])
                i += 1
    return bytes(out)


def lz11_compress(data: bytes) -> bytes:
    out = bytearray([0x11]) + len(data).to_bytes(3, "little")
    i = 0
    while i < len(data):
        flag_pos = len(out)
        out.append(0)
        for bit in range(8):
            if i >= len(data):
                break
            ln, disp = _find_match(data, i, 1, 4096, 0x110)
            if ln >= 3:
                out[flag_pos] |= 0x80 >> bit
                d = disp - 1
                if ln <= 16:
                    out += bytes([((ln - 1) << 4) | (d >> 8), d & 0xFF])
                else:
                    l2 = ln - 0x11
                    out += bytes([(l2 >> 4) & 0xF, ((l2 & 0xF) << 4) | (d >> 8), d & 0xFF])
                i += ln
            else:
                out.append(data[i])
                i += 1
    return bytes(out)


def _blz_stream(data: bytes) -> bytes:
    """Compresión BLZ voraz de 'data' entera (sin prefijo sin comprimir)."""
    rev = data[::-1]
    stream = bytearray()  # en orden de lectura del descompresor
    i = 0
    while i < len(rev):
        flag_idx = len(stream)
        stream.append(0)
        for bit in range(8):
            if i >= len(rev):
                break
            ln, disp = _find_match(rev, i, 3, 4098, 18)
            if ln >= 3:
                stream[flag_idx] |= 0x80 >> bit
                pair = ((ln - 3) << 12) | (disp - 3)
                stream += bytes([pair >> 8, pair & 0xFF])
                i += ln
            else:
                stream.append(rev[i])
                i += 1
    comp = bytes(stream[::-1])
    pad = (-len(comp)) % 4
    hdr_len = 8 + pad
    return comp + b"\xff" * pad + struct.pack("<II", (hdr_len << 24) | (len(comp) + hdr_len), 0)


def blz_inplace(buf: bytearray, end: int) -> bool:
    """Descomprime en el sitio como el SDK (MIi_UncompressBackward) y dice si la salida
    nunca pisó entrada aún sin leer."""
    hdr, inc = struct.unpack_from("<II", buf, end - 8)
    src, dst, stop = end - (hdr >> 24), end + inc, end - (hdr & 0xFFFFFF)
    while dst > stop:
        src -= 1
        flags = buf[src]
        for _ in range(8):
            if dst <= stop:
                break
            if flags & 0x80:
                src -= 2
                pr = buf[src] | buf[src + 1] << 8
                ln, disp = (pr >> 12) + 3, (pr & 0xFFF) + 3
                while ln and dst > stop:
                    dst -= 1
                    buf[dst] = buf[dst + disp]
                    ln -= 1
            else:
                dst -= 1
                src -= 1
                buf[dst] = buf[src]
            if dst < src:
                return False
            flags = (flags << 1) & 0xFF
    return True


def blz_compress(data: bytes) -> bytes:
    """Compresor BLZ para tests. Como los compresores reales, deja sin comprimir el prefijo
    mínimo (múltiplo de 4) para que la descompresión en el sitio sea segura."""
    for k in range(0, len(data), 4):
        body = _blz_stream(data[k:])
        inc = len(data) - k - len(body)
        if inc <= 0:
            break
        out = bytearray(data[:k] + body)
        struct.pack_into("<I", out, len(out) - 4, inc)
        sim = bytearray(out) + bytearray(inc)
        if blz_inplace(sim, len(out)) and bytes(sim[:len(data)]) == data:
            return bytes(out)
    raise AssertionError("datos poco comprimibles para el test")


# --------------------------------------------------------------- contenedores
def nitro_file(magic: bytes, blocks: list[bytes]) -> bytes:
    body = b"".join(blocks)
    return magic + struct.pack("<HHIHH", 0xFEFF, 0x0100, 0x10 + len(body), 0x10, len(blocks)) + body


def block(magic: bytes, payload: bytes) -> bytes:
    return magic + struct.pack("<I", 8 + len(payload)) + payload


def narc(files: list[bytes]) -> bytes:
    fat = struct.pack("<HH", len(files), 0)
    data = b""
    for f in files:
        start = len(data)
        data += f
        fat += struct.pack("<II", start, len(data))
        data += b"\xff" * ((-len(data)) % 4)
    btaf = block(b"BTAF", fat)
    btnf = block(b"BTNF", struct.pack("<IHH", 4, 0, 1))
    gmif = block(b"GMIF", data)
    return nitro_file(b"NARC", [btaf, btnf, gmif])


# ---------------------------------------------------------------- 3D (TEX0)
def nitro_dict(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    esize = len(entries[0][1]) if entries else 4
    tree = struct.pack("<HHI", 8, 0xC + 4 * n, 0x17F) + b"\0" * (4 * n)
    data = struct.pack("<HH", esize, 4 + esize * n) + b"".join(e for _, e in entries)
    names = b"".join(nm.encode()[:16].ljust(16, b"\0") for nm, _ in entries)
    body = tree + data + names
    return struct.pack("<BBH", 0, n, 4 + len(body)) + body


def btx0(textures: list[tuple[str, int, int, int, bytes, bool]], palettes: list[tuple[str, bytes]]) -> bytes:
    """textures: (nombre, fmt, w, h, datos, color0_transparente)."""
    tex_data = b""
    tex_entries = []
    for name, fmt, w, h, data, transp in textures:
        off = len(tex_data) >> 3
        tex_data += data + b"\0" * ((-len(data)) % 8)
        wl = {8: 0, 16: 1, 32: 2, 64: 3, 128: 4, 256: 5, 512: 6, 1024: 7}
        param = off | (wl[w] << 20) | (wl[h] << 23) | (fmt << 26) | (int(transp) << 29)
        tex_entries.append((name, struct.pack("<II", param, 0)))
    pal_data = b""
    pal_entries = []
    for name, data in palettes:
        pal_entries.append((name, struct.pack("<HH", len(pal_data) >> 3, 0)))
        pal_data += data + b"\0" * ((-len(data)) % 8)
    hdr_size = 0x3C
    tex_dict = nitro_dict(tex_entries)
    pal_dict = nitro_dict(pal_entries)
    tex_info_off = hdr_size
    pal_dict_off = tex_info_off + len(tex_dict)
    tex_data_off = pal_dict_off + len(pal_dict)
    tex_data_off += (-tex_data_off) % 8
    pal_data_off = tex_data_off + len(tex_data)
    hdr = bytearray(hdr_size - 8)
    struct.pack_into("<H", hdr, 0x0E - 8, tex_info_off)
    struct.pack_into("<I", hdr, 0x14 - 8, tex_data_off)
    struct.pack_into("<I", hdr, 0x24 - 8, tex_data_off)
    struct.pack_into("<I", hdr, 0x28 - 8, tex_data_off)
    struct.pack_into("<I", hdr, 0x34 - 8, pal_dict_off)
    struct.pack_into("<I", hdr, 0x38 - 8, pal_data_off)
    payload = bytes(hdr) + tex_dict + pal_dict
    payload += b"\0" * (tex_data_off - 8 - len(payload))
    payload += tex_data + pal_data
    return nitro_file(b"BTX0", [block(b"TEX0", payload)])


# ------------------------------------------------------------------------ 2D
def nclr(colors: list[int], depth: int = 3) -> bytes:
    data = b"".join(struct.pack("<H", c) for c in colors)
    payload = struct.pack("<IIII", depth, 0, len(data), 0x10) + data
    return nitro_file(b"RLCN", [block(b"TTLP", payload)])


def ncgr(pixels4bpp: bytes, tiles_x: int, tiles_y: int) -> bytes:
    payload = struct.pack("<HHIIIII", tiles_y, tiles_x, 3, 0, 0, len(pixels4bpp), 0x18) + pixels4bpp
    return nitro_file(b"RGCN", [block(b"RAHC", payload)])


def nscr(w: int, h: int, entries: list[int]) -> bytes:
    data = b"".join(struct.pack("<H", e) for e in entries)
    payload = struct.pack("<HHII", w, h, 0, len(data)) + data
    return nitro_file(b"RCSN", [block(b"NRCS", payload)])


# ----------------------------------------------------------------------- BMG
def bmg(messages: list[str]) -> bytes:
    dat = b"\0\0"
    inf_entries = b""
    for m in messages:
        inf_entries += struct.pack("<II", len(dat), 0)
        dat += m.encode("utf-16-le") + b"\0\0"
    inf = b"INF1" + struct.pack("<IHHHH", 0x10 + len(inf_entries), len(messages), 8, 0, 0) + inf_entries
    inf += b"\0" * ((-len(inf)) % 4)
    inf = inf[:4] + struct.pack("<I", len(inf)) + inf[8:]
    dat1 = b"DAT1" + struct.pack("<I", 8 + len(dat)) + dat
    body = inf + dat1
    hdr = b"MESGbmg1" + struct.pack("<II", 0x20 + len(body), 2) + bytes([2]) + b"\0" * 15
    return hdr + body


# --------------------------------------------------------------------- audio
def swar(waves: list[tuple[int, int, bytes]]) -> bytes:
    """waves: (tipo, rate, datos)."""
    hdr_len = 0x10 + 8 + 32 + 4 + 4 * len(waves)
    offs, body = [], b""
    for kind, rate, data in waves:
        offs.append(hdr_len + len(body))
        body += struct.pack("<BBHHHI", kind, 0, rate, 0, 0, len(data) // 4) + data
    payload = b"\0" * 32 + struct.pack("<I", len(waves)) + b"".join(struct.pack("<I", o) for o in offs) + body
    return nitro_file(b"SWAR", [block(b"DATA", payload)])


# ----------------------------------------------------------------------- ROM
def rom(files: dict[str, bytes], arm9: bytes = b"\0" * 16, arm9_ram: int = 0x02000000,
        overlays: list[tuple[int, bytes, bool]] = (), arm7: bytes | None = None,
        arm7_ram: int = 0x02380000, arm9_entry: int | None = None) -> bytes:
    """ROM mínima con NitroFS de un solo directorio + overlays (y binario ARM7 si se da)."""
    names = list(files)
    blobs = [files[n] for n in names] + [o[1] for o in overlays]
    out = bytearray(0x4000)
    out[0:12] = b"SYNTHETIC\0\0\0"
    out[12:16] = b"TEST"
    arm9_off = 0x4000
    out += arm9 + b"\0" * ((-len(arm9)) % 4)
    struct.pack_into("<IIII", out, 0x20, arm9_off, arm9_ram if arm9_entry is None else arm9_entry, arm9_ram, len(arm9))
    if arm7 is not None:
        while len(out) % 0x200:
            out += b"\0"
        arm7_off = len(out)
        out += arm7 + b"\0" * ((-len(arm7)) % 4)
        struct.pack_into("<IIII", out, 0x30, arm7_off, arm7_ram, arm7_ram, len(arm7))
    # FNT: directorio raíz con ficheros
    sub = b"".join(bytes([len(n)]) + n.encode() for n in names) + b"\0"
    fnt = struct.pack("<IHH", 8, len(overlays), 1) + sub
    fnt_off = len(out)
    out += fnt + b"\0" * ((-len(fnt)) % 4)
    fat_off = len(out)
    out += b"\0" * (8 * len(blobs))
    ranges = []
    for b in blobs:
        s = len(out)
        out += b + b"\0" * ((-len(b)) % 4)
        ranges.append((s, s + len(b)))
    # Los overlays ocupan los primeros file IDs en NitroFS real; aquí los nombrados empiezan en len(overlays)
    order = list(range(len(names), len(blobs))) + list(range(len(names)))
    for fid, bi in enumerate(order):
        struct.pack_into("<II", out, fat_off + fid * 8, *ranges[bi])
    ovt_off = len(out)
    for i, (ram, data, comp) in enumerate(overlays):
        flags = (len(data) & 0xFFFFFF) | (0x01000000 if comp else 0)
        out += struct.pack("<8I", i, ram, 0x1000, 0, 0, 0, i, flags)
    struct.pack_into("<II", out, 0x40, fnt_off, len(fnt))
    struct.pack_into("<II", out, 0x48, fat_off, 8 * len(blobs))
    struct.pack_into("<II", out, 0x50, ovt_off, 32 * len(overlays))
    crc = 0xFFFF   # CRC-16/MODBUS de la cabecera, como en una ROM real
    for b in out[:0x15E]:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    struct.pack_into("<H", out, 0x15E, crc)
    return bytes(out)


# --------------------------------------------------------------- código ARM a mano
# Palabras ARM fijas para probar el descubrimiento de funciones sin compilador.
ARM_PUSH = 0xE92D4010      # stmdb sp!, {r4, lr}
ARM_POP_PC = 0xE8BD8010    # ldmia sp!, {r4, pc}
ARM_BX_LR = 0xE12FFF1E     # bx lr
ARM_UNDEF = 0xE7F000F0     # instrucción permanentemente indefinida


def arm_mov_r0(imm: int) -> int:
    return 0xE3A00000 | (imm & 0xFF)


def arm_bl(src: int, dst: int) -> int:
    return 0xEB000000 | (((dst - src - 8) >> 2) & 0xFFFFFF)


def code_with_data(base: int, n_funcs: int, n_leaf: int, n_data: int, seed: int = 1):
    """Módulo sintético: n_funcs funciones con prólogo que se llaman en cadena, n_leaf
    funciones hoja sin prólogo a las que solo se llega por una tabla de punteros, y una zona
    de datos con punteros a datos, enteros pequeños y ruido. Devuelve (bytes, info)."""
    import random
    rnd = random.Random(seed)
    words: list[int] = []
    funcs, leaves = [], []
    for i in range(n_funcs):
        a = base + 4 * len(words)
        funcs.append(a)
        words += [ARM_PUSH, arm_mov_r0(i), 0, ARM_POP_PC]   # el 0 se parchea con un BL
    for i, a in enumerate(funcs):
        nxt = funcs[i + 1] if i + 1 < len(funcs) else funcs[0]
        words[(a - base) // 4 + 2] = arm_bl(a + 8, nxt)
    for i in range(n_leaf):
        leaves.append(base + 4 * len(words))
        words += [arm_mov_r0(i), ARM_BX_LR]
    bad_undef = base + 4 * len(words)
    words += [arm_mov_r0(1), ARM_UNDEF]
    table = base + 4 * len(words)
    words += leaves + [bad_undef]
    dstart = base + 4 * len(words)
    for _ in range(n_data):
        r = rnd.random()
        if r < 0.4:
            words.append(dstart + 4 * rnd.randrange(n_data))
        elif r < 0.7:
            words.append(rnd.randrange(256))
        else:
            words.append(rnd.getrandbits(32))
    # Al final: código que se sale del módulo (sin retorno) y un puntero a él
    words.append(dstart + 4 * (n_data + 1))
    off_end = base + 4 * len(words)
    words += [arm_mov_r0(2), arm_mov_r0(3)]
    info = {"funcs": funcs, "leaves": leaves, "table": table, "bad_undef": bad_undef,
            "off_end": off_end, "data": dstart}
    return struct.pack(f"<{len(words)}I", *words), info


# ------------------------------------------------- formatos de las SWI del BIOS (GBATEK)
def rl_compress(data: bytes) -> bytes:
    """RLUnComp (SWI 14h/15h): flag bit7=1 -> (flag&7F)+3 copias de un byte; si no, flag+1 literales."""
    out = bytearray([0x30]) + len(data).to_bytes(3, "little")
    i = 0
    while i < len(data):
        run = 1
        while i + run < len(data) and data[i + run] == data[i] and run < 130:
            run += 1
        if run >= 3:
            out += bytes([0x80 | (run - 3), data[i]])
            i += run
            continue
        j = i
        while j < len(data) and j - i < 128 and not (j + 2 < len(data) and data[j] == data[j + 1] == data[j + 2]):
            j += 1
        out.append(j - i - 1)
        out += data[i:j]
        i = j
    return bytes(out + bytes(-len(out) % 4))


def huff_compress(data: bytes, bits: int) -> bytes:
    """HuffUnComp (SWI 13h) con unidades de 4 u 8 bits. Árbol en anchura para que los
    desplazamientos de 6 bits alcancen (vale para alfabetos pequeños como los de los tests)."""
    import heapq
    units = [u for b in data for u in ((b & 0xF, b >> 4) if bits == 4 else (b,))]
    freq: dict[int, int] = {}
    for u in units:
        freq[u] = freq.get(u, 0) + 1
    if len(freq) == 1:
        freq[(units[0] + 1) % (1 << bits)] = 0
    heap = [(n, k, ("leaf", s)) for k, (s, n) in enumerate(sorted(freq.items()))]
    heapq.heapify(heap)
    k = len(heap)
    while len(heap) > 1:
        a, b = heapq.heappop(heap), heapq.heappop(heap)
        heapq.heappush(heap, (a[0] + b[0], k, ("node", a[2], b[2])))
        k += 1
    root = heap[0][2]
    codes: dict[int, str] = {}

    def walk(n, pre):
        if n[0] == "leaf":
            codes[n[1]] = pre
        else:
            walk(n[1], pre + "0")
            walk(n[2], pre + "1")
    walk(root, "")
    # Tabla: T[0] = tamaño, T[1] = raíz, par k en T[2+2k], T[3+2k]
    table = [0, None]
    pairs = []          # (índice del nodo padre en la tabla, nodo)
    queue = [(1, root)]
    while queue:
        idx, n = queue.pop(0)
        if n[0] == "leaf":
            table[idx] = n[1]
            continue
        kpair = len(pairs)
        pairs.append(n)
        off = kpair - (idx >> 1)
        assert 0 <= off < 64, "árbol demasiado ancho para el test"
        table[idx] = off | (0x80 if n[1][0] == "leaf" else 0) | (0x40 if n[2][0] == "leaf" else 0)
        table += [None, None]
        queue += [(2 + 2 * kpair, n[1]), (3 + 2 * kpair, n[2])]
    table = [0 if v is None else v for v in table]
    while len(table) % 4:
        table.append(0)
    table[0] = len(table) // 2 - 1
    bitstr = "".join(codes[u] for u in units)
    bitstr += "0" * (-len(bitstr) % 32)
    words = b"".join(struct.pack("<I", int(bitstr[i:i + 32], 2)) for i in range(0, len(bitstr), 32))
    hdr = bytes([0x20 | bits]) + len(data).to_bytes(3, "little")
    return hdr + bytes(table) + words


def diff_filter(data: bytes, sixteen: bool) -> bytes:
    """Diff8bitUnFilter / Diff16bitUnFilter (SWI 16h/18h): se guardan las diferencias."""
    hdr = bytes([0x82 if sixteen else 0x81]) + len(data).to_bytes(3, "little")
    if sixteen:
        vals = struct.unpack(f"<{len(data) // 2}H", data)
        diffs = [vals[0]] + [(vals[i] - vals[i - 1]) & 0xFFFF for i in range(1, len(vals))]
        return hdr + struct.pack(f"<{len(diffs)}H", *diffs)
    return hdr + bytes([data[0]] + [(data[i] - data[i - 1]) & 0xFF for i in range(1, len(data))])
