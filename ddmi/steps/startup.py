"""Start-up states of 800G_TRx_Test.vi V6.8, station DDMI Cal.

The Start event queues Reini, Set_Temp, Read SN, Move_in (disabled), Read_Spec, Wait_Temp,
VCC, StartTest. The supply output is never switched on by the program (PS on-off.vi is only
in the FQC / Final-test branch, commented out): the operator powers the module.
"""
from __future__ import annotations

from ..runctx import RunContext, StopTest

REINI_ATT_DB = 1.0
# Auto Adjust Vcc.vi V6.8 constants
VCC_READING_RANGE_V = (2.8, 4.0)   # module voltage must read inside this before adjusting
VCC_SETTING_MAX_V = 4.0            # adjusting stops (fails) when the next setting would exceed it
# Python-only guards (the V6.8 loop has no count limit)
VCC_MAX_TRIES = 20
VCC_MAX_OVERDRIVE_V = 0.5     # never set the supply more than this above the target (cable drop at
                              # ~2 A is ~0.34 V on the lab station; the module voltage is sensed)


def reini(ctx: RunContext) -> None:
    """800G_TRx_Test.vi V6.8 Reini at DDMI Cal: OVA Set Chs Att.vi sets every attenuator
    channel to 1.0 dB (here: or the station minimum, if higher). No module write, no reset
    (W_TestMode / W_Reset are only for FQC / Final test) and no DCA. Python also sets the TX
    meter's wavelengths and averaging here (the LX meter needs them; V6.8's meter does not)."""
    att = ctx.bench.attenuator
    reini_db = max(REINI_ATT_DB, att.min_att_db)
    for ch in att.physical_channels:
        att.set_channel_attenuation(ch, reini_db)
    meter = ctx.product["tx_power_meter"]
    ctx.bench.power_meter.configure(meter["wavelengths_nm"], meter["avg_time_ms"])
    ctx.log(f"Reini: attenuator channels {att.physical_channels} set to {reini_db} dB; "
            f"TX meter set to {meter['wavelengths_nm']} nm, {meter['avg_time_ms']} ms averaging")


def set_temp(ctx: RunContext) -> None:
    """800G_TRx_Test.vi V6.8 Set_Temp, station DDMI CAL, AWS: W_AWS_Test_Mode = 0.
    (With 1 the module ignores IBias and freezes disabled lanes - seen on hardware.)"""
    ctx.module.write_value("AWS_Test_Mode", "aws_test_mode_ddmi")
    ctx.log("Set_Temp: W_AWS_Test_Mode = 0")


def wait_temp(ctx: RunContext) -> None:
    """800G_TRx_Test.vi V6.8 Wait_Temp, RT, AWS: wait until the case temperature is within
    30-40 C. This station has no case sensor, so the module's DDMI temperature is used."""
    cfg = ctx.product["wait_temp"]
    if not cfg.get("enabled", True):
        return
    low, high = cfg["range_c"]
    waited = 0.0
    while True:
        temp_c = ctx.module.read("R_DTEMP")
        if low <= temp_c <= high:
            ctx.log(f"Wait_Temp: DDMI temperature {temp_c:.2f} C is within {low}-{high} C"
                    + (f" (after {waited:.0f} s)" if waited else ""))
            return
        if waited >= cfg["timeout_s"]:
            raise StopTest(f"Wait_Temp: DDMI temperature {temp_c:.2f} C still outside {low}-{high} C "
                           f"after {waited:.0f} s")
        if waited == 0:
            ctx.log(f"Wait_Temp: DDMI temperature {temp_c:.2f} C, waiting for {low}-{high} C")
        ctx.sleep(cfg["poll_s"])
        waited += cfg["poll_s"]


def auto_adjust_vcc(ctx: RunContext, target: float) -> float:
    """Auto Adjust Vcc.vi V6.8 (automatic mode); returns the voltage at the module.

    1. Read the module voltage (I2C Read Vcc.vi); it must be 2.8-4.0 V, otherwise
       "Please check your Multimeter".
    2. First setting = present supply output (PS Read Volt.vi) + (target - module) / 2,
       which must be below 4.0 V.
    3. Loop: set the supply (voltage, current limit 6000 mA), wait 2000 ms, read the module
       voltage. Done when |target - module| < 0.01 V; otherwise setting += (target - module) / 2,
       and stop with "VCC voltage regulation failed" if that is above 4.0 V.
    """
    cfg = ctx.product["vcc"]
    tol = cfg["adjust_tolerance_v"]
    supply = ctx.bench.power_supply
    if ctx.module.dry_run and not ctx.module.is_sim:
        # The supply is not changed in a real dry run; report what it is at now.
        supply.set_voltage(target)
        actual = supply.read_module_voltage()
        ctx.log(f"VCC (dry-run): would adjust to {target} V; supply reads {actual:.4f} V and is left as it is")
        return actual
    # The 2000 ms wait also lets the module's VCC monitor catch up (with 0.2 s it still read
    # the previous point - seen on hardware).
    wait = cfg.get("adjust_wait_s", 2.0)
    lo, hi = VCC_READING_RANGE_V
    module_v = supply.read_module_voltage()
    if not lo <= module_v <= hi:
        raise StopTest(f"Please check your Multimeter: module voltage reads {module_v:.3f} V "
                       f"(expected {lo}-{hi} V) - is the supply output on?")
    setting = supply.read_output_voltage() + (target - module_v) / 2
    if setting >= VCC_SETTING_MAX_V:
        raise StopTest(f"VCC voltage regulation failed: first setting {setting:.3f} V for {target} V "
                       f"is not below {VCC_SETTING_MAX_V} V")
    # PS Set V&A sends the current limit with every voltage; it does not change, so send it once
    supply.set_current_limit(cfg.get("current_limit_ma", 6000) / 1000)
    for attempt in range(1, VCC_MAX_TRIES + 1):
        if setting > target + VCC_MAX_OVERDRIVE_V:
            supply.set_voltage(target)
            raise StopTest(f"VCC voltage regulation failed: {target} V needs an unsafe supply setting "
                           f"({setting:.3f} V)")
        supply.set_voltage(setting)
        ctx.sleep(wait)
        module_v = supply.read_module_voltage()
        diff = target - module_v
        ctx.detail(f"auto_adjust_vcc try {attempt}: set {setting:.4f} V -> module {module_v:.4f} V")
        if abs(diff) < tol:
            return module_v
        setting += diff / 2
        if setting > VCC_SETTING_MAX_V:
            raise StopTest(f"VCC voltage regulation failed: next setting {setting:.3f} V for {target} V "
                           f"is above {VCC_SETTING_MAX_V} V")
    raise StopTest(f"VCC voltage regulation failed: not within {tol} V of {target} V "
                   f"after {VCC_MAX_TRIES} tries")


def set_vcc(ctx: RunContext) -> None:
    nominal = ctx.product["vcc"]["nominal_v"]
    actual = auto_adjust_vcc(ctx, nominal)
    current_ma = round(ctx.bench.power_supply.read_current() * 1000)
    ctx.log(f"VCC: adjusted to {nominal} V, module at {actual:.3f} V, current {current_ma} mA")
    ctx.results.add("ICC(mA)", 0, current_ma)
    icc_min = ctx.product["vcc"].get("icc_min_ma")
    if icc_min and not current_ma > icc_min and not ctx.module.dry_run:
        # V6.8 VCC state: PS Read Current.vi after adjusting, must be greater than the
        # "ICC Spec.txt" value (1.8 A)
        raise StopTest(f"Current {current_ma / 1000:.2f}A failed (must be above {icc_min / 1000:.2f} A)")


def read_sn(ctx: RunContext) -> None:
    """R_Information: PN bytes 148-163, rev 164-165, SN 166-181. Must match the scanned SN."""
    info = ctx.module.read("R_Information")
    pn, rev, sn = info[:16].strip(), info[16:18].strip(), info[18:34].strip()
    ctx.meta.update(pn=pn, rev=rev, module_sn=sn)
    ctx.log(f"Read SN: PN {pn}, rev {rev}, SN {sn}")
    if ctx.scanned_sn and sn != ctx.scanned_sn:
        raise StopTest("Inconsistent internal and external SN")
