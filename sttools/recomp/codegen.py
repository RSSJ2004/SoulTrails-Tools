"""Generación de C a partir de las funciones analizadas."""
from __future__ import annotations

from pathlib import Path

from .insn import BRANCH, CALL, COND_C, TABLE, UNDEF, h
from .program import Function, Program

HEADER = """/* Generado por stk recomp. NO EDITAR y NO SUBIR al repositorio:
 * contiene código traducido de tu ROM. */
#include "strecomp.h"
"""


def label(addr: int) -> str:
    return f"L_{addr:08X}"


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
                targets.update(ins.table)
        if has_indirect:
            targets.update(addrs)
        tbit = 1 if f.thumb else 0
        out = [f"/* {f.module.name} {f.addr:#010x} {'Thumb' if f.thumb else 'ARM'}, {len(addrs)} instr. */",
               f"void {f.cname}(StCpu* c) {{"]
        if has_indirect:
            out.append("    uint32_t jt;")
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
        out.append("}")
        del uses_dispatch
        return "\n".join(out)

    def emit_insn(self, f: Function, ins) -> str:
        tbit = 1 if f.thumb else 0
        if ins.kind == BRANCH:
            tgt = ins.target & ~1
            if tgt in f.insns and bool(ins.target & 1) == f.thumb:
                return f"goto {label(tgt)};"
            name = self.call_target(f, ins.target)
            return f"ST_TAIL({name});" if name else f"ST_TAIL_DYN({h(ins.target)});"
        if ins.kind == CALL:
            ret = h(ins.next | tbit)
            if ins.target is None:
                return f"{{ {ins.code} ST_CALL_DYN({ins.table_reg}, {ret}); }}"
            name = self.call_target(f, ins.target)
            call = f"ST_CALL({name}, {ret});" if name else f"ST_CALL_DYN({h(ins.target)}, {ret});"
            return f"{{ {ins.code} {call} }}"
        if ins.kind == TABLE:
            cases = " ".join(f"case {k}: goto {label(a)};" for k, a in enumerate(ins.table))
            fallback = f"ST_TAIL_DYN({h(ins.addr + 8)} + ({ins.table_reg} << 2));"
            return f"switch ({ins.table_reg}) {{ {cases} default: {fallback} }}"
        return ins.code

    # ------------------------------------------------------------- archivos
    def write(self, out_dir: Path, per_file: int = 400) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("recomp_*.c"):
            old.unlink()
        funcs = sorted(self.prog.functions.values(), key=lambda f: (f.module.name, f.addr))
        files = []
        for i in range(0, len(funcs), per_file):
            chunk = funcs[i:i + per_file]
            path = out_dir / f"recomp_{i // per_file:04d}.c"
            body = [HEADER, '#include "recomp_funcs.h"', ""]
            body += [self.emit_function(f) + "\n" for f in chunk]
            path.write_text("\n".join(body), encoding="utf-8")
            files.append(path.name)
        # Prototipos
        protos = ["#pragma once", '#include "strecomp.h"', ""]
        protos += [f"void {f.cname}(StCpu* c);" for f in funcs]
        (out_dir / "recomp_funcs.h").write_text("\n".join(protos) + "\n", encoding="utf-8")
        # Tablas por módulo
        tbl = [HEADER, '#include "recomp_funcs.h"', ""]
        mods = []
        for m in self.prog.modules:
            fs = [f for f in funcs if f.module is m]
            if not fs:
                continue
            tbl.append(f"static const StFnEntry fns_{m.name}[] = {{")
            for f in fs:
                tbl.append(f"    {{ {h(f.addr)}, {int(f.thumb)}, {f.cname} }},")
            tbl.append("};")
            mods.append(f"    {{ \"{m.name}\", {m.overlay if m.overlay is not None else -1}, {h(m.base)}, {h(m.end)},"
                        f" fns_{m.name}, {len(fs)} }},")
        tbl.append("const StModule st_modules[] = {")
        tbl += mods
        tbl.append("};")
        tbl.append(f"const unsigned st_module_count = {len(mods)};")
        (out_dir / "recomp_table.c").write_text("\n".join(tbl) + "\n", encoding="utf-8")
        files.append("recomp_table.c")
        (out_dir / "CMakeLists.txt").write_text(
            "# Generado por stk recomp\n"
            "file(GLOB ST_RECOMP_SOURCES ${CMAKE_CURRENT_LIST_DIR}/*.c)\n"
            "add_library(st_recomp_generated STATIC ${ST_RECOMP_SOURCES})\n"
            "target_include_directories(st_recomp_generated PUBLIC ${CMAKE_CURRENT_LIST_DIR})\n"
            "target_link_libraries(st_recomp_generated PUBLIC st_runtime)\n"
            "if(NOT MSVC)\n  target_compile_options(st_recomp_generated PRIVATE -Wno-unused-label -fno-strict-aliasing)\nendif()\n",
            encoding="utf-8")
        ninsn = sum(len(f.insns) for f in funcs)
        nundef = sum(1 for f in funcs for i in f.insns.values() if i.kind == UNDEF)
        return {"functions": len(funcs), "instructions": ninsn, "undefined": nundef, "files": files,
                "warnings": self.prog.warnings}
