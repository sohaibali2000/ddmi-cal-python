"""Start-up states of 800G_TRx_Test.vi: Reini, Set_Temp, VCC, Read SN (build map 6.2-6.4)."""
from __future__ import annotations

from ..runctx import RunContext, StopTest

REINI_ATT_DB = 1.0
VCC_MAX_TRIES = 20
VCC_MAX_OVERDRIVE_V = 0.3     # never set the supply more than this above the target


def reini(ctx: RunContext) -> None:
    """Attenuators to 1.0 dB (or the station minimum, if higher), TX meter wavelengths set.
    No module reset at AWS DDMI Cal."""
    att = ctx.bench.attenuator
    reini_db = max(REINI_ATT_DB, att.min_att_db)
    for ch in att.physical_channels:
        att.set_channel_attenuation(ch, reini_db)
    meter = ctx.product["tx_power_meter"]
    ctx.bench.power_meter.configure(meter["wavelengths_nm"], meter["avg_time_ms"])
    ctx.log(f"Reini: attenuator channels {att.physical_channels} set to {reini_db} dB; "
            f"TX meter set to {meter['wavelengths_nm']} nm, {meter['avg_time_ms']} ms averaging")


def set_temp(ctx: RunContext) -> None:
    """AWS + DDMI Cal: enter test mode. No temperature controller at V6.8."""
    ctx.module.write_value("AWS_Test_Mode", "aws_test_mode_enter")
    ctx.log("Set_Temp: W_AWS_Test_Mode = 1 (no temperature control at V6.8)")


def auto_adjust_vcc(ctx: RunContext, target: float) -> float:
    """Trim the supply until the voltage at the module is within tolerance; return that voltage."""
    tol = ctx.product["vcc"]["adjust_tolerance_v"]
    supply = ctx.bench.power_supply
    setting = target
    for attempt in range(1, VCC_MAX_TRIES + 1):
        supply.set_voltage(setting)
        ctx.sleep(0.2)
        actual = supply.read_module_voltage()
        ctx.detail(f"auto_adjust_vcc try {attempt}: set {setting:.4f} V -> module {actual:.4f} V")
        if abs(target - actual) <= tol:
            return actual
        setting += target - actual
        if not target - VCC_MAX_OVERDRIVE_V <= setting <= target + VCC_MAX_OVERDRIVE_V:
            supply.set_voltage(target)
            raise StopTest(f"VCC adjust for {target} V needs an unsafe supply setting ({setting:.3f} V)")
    raise StopTest(f"VCC did not settle within {tol} V of {target} V")


def set_vcc(ctx: RunContext) -> None:
    nominal = ctx.product["vcc"]["nominal_v"]
    actual = auto_adjust_vcc(ctx, nominal)
    current_ma = round(ctx.bench.power_supply.read_current() * 1000)
    ctx.log(f"VCC: adjusted to {nominal} V, module at {actual:.3f} V, current {current_ma} mA")
    ctx.results.add("ICC(mA)", 0, current_ma)


def read_sn(ctx: RunContext) -> None:
    """R_Information: PN bytes 148-163, rev 164-165, SN 166-181. Must match the scanned SN."""
    info = ctx.module.read("R_Information")
    pn, rev, sn = info[:16].strip(), info[16:18].strip(), info[18:34].strip()
    ctx.meta.update(pn=pn, rev=rev, module_sn=sn)
    ctx.log(f"Read SN: PN {pn}, rev {rev}, SN {sn}")
    if ctx.scanned_sn and sn != ctx.scanned_sn:
        raise StopTest("Inconsistent internal and external SN")
