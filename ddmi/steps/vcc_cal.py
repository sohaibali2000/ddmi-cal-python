"""VCC calibration and check (build map 6.9): DDMI_Cal.vi frame 1."""
from __future__ import annotations

from ..calc.fit import two_point_fit
from ..calc.units import volts_to_lin
from ..runctx import RunContext
from .startup import auto_adjust_vcc


def vcc_cal(ctx: RunContext) -> None:
    cfg, module = ctx.product["vcc"], ctx.module
    module.write_value("DSP_TestMode", "dsp_debug")           # W_Debug_AWS = 1
    module.write_value("DSP_OperatingMode", "dsp_dual")       # W_Daul_AWS = 5
    try:
        module.write("VccCal", [0.0, 0.0])
        ctx.log("VCC cal: unlocked, old calibration cleared")
        points = []
        for target in cfg["cal_points_v"]:
            ctx.check_abort()
            actual = auto_adjust_vcc(ctx, target)
            ctx.sleep(cfg["settle_s"])
            adc = module.read("R_VCCADC")
            points.append((adc, volts_to_lin(actual)))
            ctx.log(f"VCC {target} V: achieved {actual:.4f} V, R_VCCADC {adc}")
        try:
            slope, offset = two_point_fit(*points)
        except ValueError as exc:
            ctx.log(f"VCC fit failed ({exc})")
            ctx.results.add("Vcc_Slope", 0, None)
            ctx.results.add("Vcc_Offset", 0, None)
        else:
            module.write("VccCal", [slope, offset])
            ctx.results.add("Vcc_Slope", 0, round(slope, 4))
            ctx.results.add("Vcc_Offset", 0, round(offset, 1))
            ctx.log(f"W_VccCal written: slope {slope:.4f}, offset {offset:.1f}")
    finally:
        module.write_value("DSP_TestMode", "dsp_normal")          # W_Nomal_AWS = 0
        module.write_value("DSP_OperatingMode", "dsp_mission")    # W_Mission_AWS = 0


def vcc_check(ctx: RunContext) -> None:
    """Back at nominal: error = real V - DDMI V."""
    module = ctx.module
    actual = auto_adjust_vcc(ctx, ctx.product["vcc"]["nominal_v"])
    ddmi = module.read("R_DVCC")
    ctx.detail(f"R_VccCal read back: {module.read('VccCal')}")
    ctx.results.add("Real_Vcc(V)", 0, round(actual, 3))
    ctx.results.add("DDMI_Vcc(V)", 0, round(ddmi, 3))
    ctx.results.add("dVcc(V)", 0, round(actual - ddmi, 3))
    ctx.log(f"VCC check: real {actual:.3f} V, DDMI {ddmi:.3f} V")
