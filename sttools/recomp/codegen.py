"""Generación de C a partir de las funciones analizadas."""
from __future__ import annotations

import re
import time
from pathlib import Path

from .insn import BRANCH, CALL, COND_C, TABLE, UNDEF, h
from .program import SIG_BYTES, Function, Program, signature_at

HEADER = """/* Generado por stk recomp. NO EDITAR y NO SUBIR al repositorio:
 * contiene código traducido de tu ROM. */
#include "strecomp.h"
"""


def signature(f: Function) -> int:
    """Firma de la función recortada al final del módulo (como lookup() en el runtime)."""
    return signature_at(f.module, f.addr, min(SIG_BYTES, f.module.end - f.addr))


FN_NAME = re.compile(r"\bf_(?:[A-Za-z]+[0-9]*_)?[0-9A-F]{8}(?:_t)?\b")


def label(addr: int) -> str:
    return f"L_{addr:08X}"


def _retry(op, path: Path, what: str):
    """En Windows un archivo puede estar abierto un momento (MSBuild, el antivirus, un editor)."""
    for i in range(6):
        try:
            return op()
        except PermissionError:
            if i == 5:
                raise SystemExit(f"No puedo {what} {path}: otro proceso lo tiene abierto. Cierra Visual Studio "
                                 "o el editor que lo use (o espera a que termine la compilación) y repite.")
            time.sleep(0.5 * (i + 1))


def write_if_changed(path: Path, text: str) -> bool:
    """Escribe solo si cambia: así la compilación incremental no recompila lo que no cambió."""
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    _retry(lambda: path.write_bytes(data), path, "escribir")
    return True


class Generator:
    def __init__(self, prog: Program, hooks: set[int] | None = None, comments: bool = True):
        self.prog = prog
        self.hooks = hooks or set()
        self.comments = comments

    # Destino de llamada: directa si el compilador puede resolverla, si no, por el despachador
    def call_target(self, f: Function, target: int) -> str | None:
        if (target & ~1) in self.hooks:
            return None
        g = self.prog.find_function(f.module, target)
        if g is None or g.thumb != bool(target & 1):
            return None
        if any(i.kind == UNDEF for i in g.insns.values()):
            return None  # no parece código de este módulo (p. ej. datos): que decida el despachador
        if g.module.overlay is not None and g.module is not f.module:
            return None  # otro overlay: depende de qué esté cargado
        return g.cname

    def emit_function(self, f: Function) -> str:
        addrs = sorted(f.insns)
        uses_dispatch = any("ST_INDIRECT" in f.insns[a].code or f.insns[a].kind == CALL for a in addrs)
        has_indirect = any("ST_INDIRECT" in f.insns[a].code for a in addrs)
        targets = {f.addr}
        for a in addrs:
            ins = f.insns[a]
            if (ins.kind == BRANCH and ins.target is not None and (ins.target & ~1) in f.insns
                    and bool(ins.target & 1) == f.thumb):
                targets.add(ins.target & ~1)
            if ins.kind == TABLE:
                targets.update(t for t in ins.table if t in f.insns)
        # Caída a la siguiente instrucción cuando otra se decodificó en medio (p. ej. un salto a la
        # segunda mitad de un BL Thumb de 32 bits): el goto necesita su etiqueta.
        for i, a in enumerate(addrs):
            ins = f.insns[a]
            nxt = addrs[i + 1] if i + 1 < len(addrs) else None
            if not ins.ends_block and nxt != ins.next and ins.next in f.insns:
                targets.add(ins.next)
        if has_indirect:
            targets.update(addrs)
        resume = self.resume_points(f)
        targets.update(resume)
        tbit = 1 if f.thumb else 0
        sym = self.prog.names.get((f.module.name, f.addr))
        sym = f" {sym.replace('*/', '')}" if sym else ""
        out = [f"/* {f.module.name} {f.addr:#010x} {'Thumb' if f.thumb else 'ARM'}, {len(addrs)} instr.{sym} */",
               f"void {f.cname}(StCpu* c) {{"]
        if has_indirect:
            out.append("    uint32_t jt;")
        if resume:
            out.append("    uint32_t jr, my_ret = c->r[14];   /* vuelta normal de esta función */")
        out.append(f"    ST_TRACE({h(f.addr | tbit)});")
        out.append(f"    goto {label(f.addr)};")
        for i, a in enumerate(addrs):
            ins = f.insns[a]
            if a in targets:
                out.append(f"{label(a)}:;")
            body = self.emit_insn(f, ins)
            cmt = f"/* {a:08X}: {ins.asm} */ " if self.comments else ""
            if ins.cond != 14 and ins.kind != UNDEF:
                out.append(f"    {cmt}if ({COND_C[ins.cond]}) {{ {body} }}")
            else:
                out.append(f"    {cmt}{body}")
            nxt = addrs[i + 1] if i + 1 < len(addrs) else None
            if not ins.ends_block and nxt != ins.next:
                if ins.next in f.insns:
                    out.append(f"    goto {label(ins.next)};")
                else:
                    out.append(f"    ST_TAIL_DYN({h(ins.next | tbit)});")
        if has_indirect:
            out.append("st_dispatch:")
            out.append("    switch (jt) {")
            for a in addrs:
                out.append(f"    case {h(a | tbit)}: goto {label(a)};")
            out.append("    default: ST_TAIL_DYN(jt);")
            out.append("    }")
        if resume:
            # Un hilo reanudado tras SaveContext vuelve a la instrucción siguiente a esa llamada
            out.append("st_resume:")
            out.append("    switch (jr) {")
            for a in resume:
                out.append(f"    case {h(a | tbit)}: goto {label(a)};")
            # Otra dirección (p. ej. el punto de reanudación dentro de SaveContext): se ejecuta y
            # se vuelve a mirar a dónde quiere volver, hasta que sea un punto de esta función o
            # su propia vuelta
            out.append("    default: st_call(c, jr); jr = c->next_pc; if (jr == my_ret) return; goto st_resume;")
            out.append("    }")
        out.append("}")
        del uses_dispatch
        return "\n".join(out)

    @staticmethod
    def resume_points(f: Function) -> list[int]:
        """Vueltas de llamada dentro de la función (destinos de st_resume)."""
        pts = []
        for a in sorted(f.insns):
            ins = f.insns[a]
            if ins.next not in f.insns:
                continue
            if ins.kind == CALL or "ST_CALL_DYN_EXPR(" in ins.code:
                pts.append(ins.next)
        return pts

    def emit_insn(self, f: Function, ins) -> str:
        tbit = 1 if f.thumb else 0
        if ins.kind == BRANCH:
            tgt = ins.target & ~1
            if tgt in f.insns and bool(ins.target & 1) == f.thumb:
                # Salto hacia atrás = bucle: punto donde entregar IRQ (una espera activa sobre un
                # flag que pone el manejador de una IRQ no llama a nadie)
                return f"{{ ST_POLL(); goto {label(tgt)}; }}" if tgt <= ins.addr else f"goto {label(tgt)};"
            name = self.call_target(f, ins.target)
            return f"ST_TAIL({name});" if name else f"ST_TAIL_DYN({h(ins.target)});"
        resumable = ins.next in f.insns
        r = "R" if resumable else ""
        if ins.kind == CALL:
            ret = h(ins.next | tbit)
            if ins.target is None:
                return f"{{ {ins.code} ST_CALL{r}_DYN({ins.table_reg}, {ret}); }}"
            name = self.call_target(f, ins.target)
            call = f"ST_CALL{r}({name}, {ret});" if name else f"ST_CALL{r}_DYN({h(ins.target)}, {ret});"
            return f"{{ {ins.code} {call} }}"
        if resumable and "ST_CALL_DYN_EXPR(" in ins.code:
            return ins.code.replace("ST_CALL_DYN_EXPR(", "ST_CALLR_DYN_EXPR(", 1)
        if ins.kind == TABLE:
            cases = " ".join(f"case {k}: goto {label(a)};" if a in f.insns else f"case {k}: ST_TAIL_DYN({h(a | tbit)});"
                             for k, a in enumerate(ins.table))
            fallback = f"ST_TAIL_DYN({h(ins.addr + 8)} + ({ins.table_reg} << 2));"
            return f"switch ({ins.table_reg}) {{ {cases} default: {fallback} }}"
        return ins.code

    # ------------------------------------------------------------- archivos
    def write(self, out_dir: Path, bucket: int = 0x8000) -> dict:
        """Un archivo por módulo y rango fijo de direcciones (32 KB) con sus propios prototipos:
        aprender una función nueva solo cambia el archivo de su rango y los que la llaman, y la
        compilación incremental de --learn no rehace todo."""
        out_dir.mkdir(parents=True, exist_ok=True)
        funcs = sorted(self.prog.functions.values(), key=lambda f: (f.module.name, f.addr))
        groups: dict[tuple[str, int], list[Function]] = {}
        for f in funcs:
            groups.setdefault((f.module.name, f.addr // bucket), []).append(f)
        files = []
        for (mname, b), chunk in sorted(groups.items()):
            path = out_dir / f"rc_{mname}_{b * bucket:08X}.c"
            code = [self.emit_function(f) + "\n" for f in chunk]
            # Toda referencia a una función generada (también como argumento de ST_CALL/ST_TAIL)
            used = sorted(set(FN_NAME.findall("".join(code))))
            body = [HEADER] + [f"void {n}(StCpu* c);" for n in used] + [""] + code
            write_if_changed(path, "\n".join(body))
            files.append(path.name)
        # Prototipos
        protos = ["#pragma once", '#include "strecomp.h"', ""]
        protos += [f"void {f.cname}(StCpu* c);" for f in funcs]
        write_if_changed(out_dir / "recomp_funcs.h", "\n".join(protos) + "\n")
        # Tablas por módulo
        tbl = [HEADER, '#include "recomp_funcs.h"', ""]
        mods = []
        for m in self.prog.modules:
            fs = [f for f in funcs if f.module is m]
            if not fs:
                continue
            tbl.append(f"static const StFnEntry fns_{m.name}[] = {{")
            for f in fs:
                sig = h(signature(f)) if m.overlay is not None else "0"
                tbl.append(f"    {{ {h(f.addr)}, {int(f.thumb)}, {f.cname}, {sig} }},")
            tbl.append("};")
            mods.append(f"    {{ \"{m.name}\", {m.overlay if m.overlay is not None else -1}, {h(m.base)}, {h(m.end)},"
                        f" fns_{m.name}, {len(fs)} }},")
        tbl.append("const StModule st_modules[] = {")
        tbl += mods
        tbl.append("};")
        tbl.append(f"const unsigned st_module_count = {len(mods)};")
        write_if_changed(out_dir / "recomp_table.c", "\n".join(tbl) + "\n")
        files.append("recomp_table.c")
        for pattern in ("recomp_*.c", "rc_*.c"):
            for old in out_dir.glob(pattern):
                if old.name not in files:  # sobran de una ejecución anterior
                    _retry(old.unlink, old, "borrar")
        write_if_changed(out_dir / "CMakeLists.txt",
            "# Generado por stk recomp\n"
            "file(GLOB ST_RECOMP_SOURCES ${CMAKE_CURRENT_LIST_DIR}/*.c)\n"
            "add_library(st_recomp_generated OBJECT ${ST_RECOMP_SOURCES})\n"
            "target_include_directories(st_recomp_generated PUBLIC ${CMAKE_CURRENT_LIST_DIR})\n"
            "target_link_libraries(st_recomp_generated PUBLIC st_runtime)\n"
            "if(MSVC AND CMAKE_C_COMPILER_ID STREQUAL \"Clang\")\n"
            "  # clang-cl (port híbrido con melonDS): sin /MP (Ninja ya compila en paralelo)\n"
            "  target_compile_options(st_recomp_generated PRIVATE -Wno-unused-label -Wno-unused-variable\n"
            "                         -Werror=implicit-function-declaration)\n"
            "elseif(MSVC)\n"
            "  # /MP: compila los .c en paralelo, limitado por la RAM (cada cl.exe con /O2 sobre estos\n"
            "  # archivos puede usar ~1,5 GB; sin límite, 16 hilos con 16 GB acaban en el archivo de paginación).\n"
            "  # /bigobj: muchas funciones por archivo; C4102: etiqueta sin usar (normal aquí).\n"
            "  cmake_host_system_information(RESULT st_mem QUERY TOTAL_PHYSICAL_MEMORY)\n"
            "  cmake_host_system_information(RESULT st_cores QUERY NUMBER_OF_LOGICAL_CORES)\n"
            "  math(EXPR st_jobs \"${st_mem} / 1500\")\n"
            "  if(st_jobs GREATER st_cores)\n    set(st_jobs ${st_cores})\n  endif()\n"
            "  if(st_jobs LESS 1)\n    set(st_jobs 1)\n  endif()\n"
            "  target_compile_options(st_recomp_generated PRIVATE /MP${st_jobs} /bigobj /wd4102)\n"
            "else()\n  target_compile_options(st_recomp_generated PRIVATE -Wno-unused-label -fno-strict-aliasing\n"
            "                         -Werror=implicit-function-declaration)\nendif()\n")
        ninsn = sum(len(f.insns) for f in funcs)
        undef = sorted({(f.module.name, a, i.raw, f.thumb) for f in funcs for a, i in f.insns.items() if i.kind == UNDEF})
        nundef = len(undef)
        ulist = [f"{mn}:{a:08X} {'Thumb' if t else 'ARM'} {raw:0{4 if t else 8}X}" for mn, a, raw, t in undef]
        return {"functions": len(funcs), "instructions": ninsn, "undefined": nundef, "undefined_list": ulist, "files": files,
                "warnings": self.prog.warnings}
