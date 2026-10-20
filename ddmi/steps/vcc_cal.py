"""VCC calibration and check: DDMI_Cal.vi V6.8 "Vcc" (Cal / Check), Item_Test.vi "DDMI_VCC"."""
from __future__ import annotations

from ..calc.fit import two_point_fit
from ..calc.units import volts_to_lin
from ..runctx import RunContext
from .startup import auto_adjust_vcc


def vcc_cal(ctx: RunContext) -> None:
    cfg, module = ctx.product["vcc"], ctx.module
    # DDMI_Cal.vi V6.8 Vcc / Cal (AWS): W_Debug_AWS, W_Daul_AWS. IIC.vi: CFh:81h = 63;
    # CFh:86h = 64, then CFh:80h = 63
    module.write_value("DSP_TestMode", "dsp_debug")
    module.write_value("DSP_OperatingMode", "dsp_dual")
    module.write_value("DSP_Command", "dsp_apply")
    old_slope, old_offset = module.read_vcc_cal()
    try:
        module.write_vcc_cal(0.0, 0.0)
        # "Time Delay2" after the clear: its time is not wired (LabVIEW's default is 1 s)
        ctx.sleep(cfg.get("clear_wait_s", 1.0))
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
            if abs(points[1][0] - points[0][0]) < 20:
                raise ValueError(f"VCC ADC {points[0][0]} -> {points[1][0]} hardly changed between "
                                 f"{cfg['cal_points_v']} V")
            slope, offset = two_point_fit(*points)
        except ValueError as exc:
            ctx.log(f"VCC fit failed ({exc}); restoring the previous VCC calibration")
            module.write_vcc_cal(old_slope, old_offset)
            ctx.results.add("Vcc_Slope", 0, None)
            ctx.results.add("Vcc_Offset", 0, None)
        else:
            module.write_vcc_cal(slope, offset)
            ctx.results.add("Vcc_Slope", 0, round(slope, 4))
            ctx.results.add("Vcc_Offset", 0, round(offset, 1))
            ctx.log(f"W_VccCal written: slope {slope:.4f}, offset {offset:.1f}")
    finally:
        # Lock again: W_Nomal_AWS / W_Mission_AWS, i.e. the values read before the run (0 / 0)
        # W_Nomal_AWS, W_Mission_AWS: CFh:81h = 0; CFh:86h = 0, then CFh:80h = 63
        module.write("DSP_TestMode", ctx.pre_run("DSP_TestMode", "dsp_normal"))
        module.write("DSP_OperatingMode", ctx.pre_run("DSP_OperatingMode", "dsp_mission"))
        module.write_value("DSP_Command", "dsp_apply")


def vcc_check(ctx: RunContext) -> None:
    """DDMI_Cal.vi V6.8 "Vcc" / Check (AWS): Auto Adjust Vcc to 3.3 V, wait ("Time Delay10",
    time not wired: LabVIEW's default 1 s), read R_DVCC, read the module voltage again
    (I2C Read Vcc), read R_VccCal. dVcc = module V - DDMI V, in volts for AWS (other
    customers: percent of the module voltage)."""
    module, cfg = ctx.module, ctx.product["vcc"]
    auto_adjust_vcc(ctx, cfg["nominal_v"])
    ctx.sleep(cfg.get("check_wait_s", 1.0))
    ddmi = module.read("R_DVCC")
    actual = ctx.bench.power_supply.read_module_voltage()
    ctx.detail(f"R_VccCal read back (slope, offset): {module.read_vcc_cal()}")
    ctx.results.add("Real_Vcc(V)", 0, round(actual, 3))
    ctx.results.add("DDMI_Vcc(V)", 0, round(ddmi, 3))
    ctx.results.add("dVcc(V)", 0, round(actual - ddmi, 3))
    ctx.log(f"VCC check: real {actual:.3f} V, DDMI {ddmi:.3f} V")


def needs_rerun(ctx: RunContext) -> list[str]:
    """Item_Test.vi V6.8 "DDMI_VCC": Cal + Check run again only when |dVcc| >= 1.0 (V for
    AWS). The slope and the dVcc limit are judged at the end, not here."""
    d = ctx.results.value("dVcc(V)", 0)
    return [f"|dVcc| {abs(d):.3f} V >= 1.0"] if d is not None and abs(d) >= RERUN_DVCC_V else []


RERUN_DVCC_V = 1.0
