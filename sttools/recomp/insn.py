"""Representación común de una instrucción traducida a C."""
from __future__ import annotations

from dataclasses import dataclass, field

# Tipos de flujo de control
NEXT = "next"          # sigue con la siguiente instrucción
BRANCH = "branch"      # salto directo (goto o llamada de cola)
CALL = "call"          # BL/BLX: llama y continúa
RET = "ret"            # retorno de función (el código ya contiene ST_RETURN)
INDIRECT = "indirect"  # salto a dirección calculada (el código contiene ST_INDIRECT)
TABLE = "table"        # tabla de saltos detectada (switch)
UNDEF = "undef"        # instrucción no soportada (llama a st_unimpl)

COND_C = [
    "c->z", "!c->z", "c->c", "!c->c", "c->n", "!c->n", "c->v", "!c->v",
    "(c->c && !c->z)", "(!c->c || c->z)", "(c->n == c->v)", "(c->n != c->v)",
    "(!c->z && c->n == c->v)", "(c->z || c->n != c->v)", "1", "0",
]
COND_NAMES = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "", "nv"]


@dataclass
class Ins:
    addr: int
    size: int
    thumb: bool
    raw: int
    asm: str = ""
    cond: int = 14
    code: str = ""
    kind: str = NEXT
    target: int | None = None        # destino de BRANCH/CALL (bit0 = Thumb)
    table: list[int] = field(default_factory=list)  # destinos de TABLE
    table_reg: str = ""

    @property
    def next(self) -> int:
        return self.addr + self.size

    @property
    def ends_block(self) -> bool:
        """True si no hay caída a la siguiente instrucción."""
        if self.cond != 14:
            return False
        return self.kind in (BRANCH, RET, INDIRECT, TABLE, UNDEF)


def h(v: int) -> str:
    return f"0x{v & 0xFFFFFFFF:08X}u"


def sx(v: int, bits: int) -> int:
    m = 1 << (bits - 1)
    return (v & (m - 1)) - (v & m)
