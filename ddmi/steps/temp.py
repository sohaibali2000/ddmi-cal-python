"""Temperature (build map 6.10): read only. The offset routine is not used at DDMI Cal."""
from __future__ import annotations

from ..runctx import RunContext


def read_temp(ctx: RunContext) -> None:
    temp = ctx.module.read("R_DTEMP")
    ctx.results.add("DDMI_Temp(C)", 0, round(temp, 2))
    ctx.log(f"DDMI temperature {temp:.2f} C")
