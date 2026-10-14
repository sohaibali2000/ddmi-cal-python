"""RX calibration and check (build map 6.8): DDMI_Cal.vi frame 3 + CH RxP To Att.vi."""
from __future__ import annotations

from ..calc.fit import two_point_fit
from ..calc.units import dbm_to_lin, lin_to_dbm
from ..runctx import RunContext, StopTest


def _set_power(ctx: RunContext, ch: int, power_dbm: float) -> None:
    try:
        att = ctx.rx_tables[ch].att_for_power(power_dbm)
    except ValueError as exc:
        raise StopTest(f"RX table CH{ch}: {exc}") from exc
    attenuator = ctx.bench.attenuator
    attenuator.set_wavelength(ch, ctx.product["rx_cal"]["wavelengths_nm"][ch - 1])
    try:
        attenuator.set_attenuation(ch, att)
    except ValueError as exc:
        raise StopTest(f"RX CH{ch} at {power_dbm} dBm: {exc}") from exc
    ctx.detail(f"RX CH{ch}: {power_dbm} dBm -> attenuator CH{attenuator.channel(ch)} {att:.2f} dB")


def _pairs(ctx: RunContext) -> list[tuple[int, int]]:
    half = len(ctx.channels) // 2
    return [(n, n + half) for n in range(1, half + 1)]     # (1,5), (2,6), (3,7), (4,8)


def rx_cal(ctx: RunContext) -> None:
    cfg, module = ctx.product["rx_cal"], ctx.module
    missing = [ch for ch in ctx.channels if ch not in ctx.rx_tables]
    if missing:
        raise StopTest(f"RX table missing for CH{missing} - run the RX Attenuation Table calibration")

    rssi = {ch: [] for ch in ctx.channels}
    for point in cfg["points_dbm"]:
        for pair in _pairs(ctx):
            ctx.check_abort()
            for ch in pair:
                _set_power(ctx, ch, point)
            ctx.sleep(cfg["settle_s"])
            reading = module.read("R_RSSI")
            for ch in pair:
                rssi[ch].append(reading[ch - 1])
                ctx.results.add(f"RSSI@{point:g}", ch, reading[ch - 1])
            ctx.log(f"RX {point} dBm on CH{pair[0]}/CH{pair[1]}: RSSI "
                    f"{reading[pair[0] - 1]} / {reading[pair[1] - 1]}")

    y = [dbm_to_lin(p) for p in cfg["points_dbm"]]
    slopes, offsets = [], []
    for ch in ctx.channels:
        try:
            slope, offset = two_point_fit((rssi[ch][0], y[0]), (rssi[ch][1], y[1]))
        except ValueError as exc:
            ctx.log(f"CH{ch}: RX fit failed ({exc})")
            slopes.append(0.0)
            offsets.append(0.0)
            ctx.results.add("Rxp_Slope", ch, None)
            ctx.results.add("Rxp_Offset", ch, None)
            continue
        ctx.detail(f"CH{ch} RX fit: RSSI {rssi[ch]} vs {y} -> slope {slope:.6f}, offset {offset:.3f}")
        slopes.append(slope)
        offsets.append(offset)
        ctx.results.add("Rxp_Slope", ch, round(slope, 5))
        ctx.results.add("Rxp_Offset", ch, round(offset, 1))
    module.write_cal("DRXPCal", slopes, offsets)
    ctx.log("W_DRXPCal written (8 slope/offset pairs)")


def rx_check(ctx: RunContext) -> None:
    """Set the check power, read R_DRXP: error = DDMI - check.

    In the same lane pairs as the calibration: lanes on one RX fibre share an attenuator
    channel, so each lane's own setting only holds while its pair is being read.
    """
    cfg, module = ctx.product["rx_cal"], ctx.module
    check = cfg["check_dbm"]
    for pair in _pairs(ctx):
        ctx.check_abort()
        for ch in pair:
            _set_power(ctx, ch, check)
        ctx.sleep(cfg["settle_s"])
        ddmi = module.read("R_DRXP")
        for ch in pair:
            ddmi_dbm = lin_to_dbm(ddmi[ch - 1])
            ctx.results.add(f"DD_RxP@{check:g}(dBm)", ch, round(ddmi_dbm, 2))
            ctx.results.add("dRxP(dB)", ch, round(ddmi_dbm - check, 2))
    ctx.log(f"RX check at {check} dBm: error {[ctx.results.value('dRxP(dB)', ch) for ch in ctx.channels]} dB")
