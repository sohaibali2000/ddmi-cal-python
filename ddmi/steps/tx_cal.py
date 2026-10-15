"""TX calibration and check (build map 6.6, 6.7): DDMI_Cal.vi frame 2."""
from __future__ import annotations

from ..calc.fit import two_point_fit
from ..calc.units import dbm_to_lin, lin_to_dbm
from ..runctx import RunContext


def tx_cal(ctx: RunContext) -> None:
    cfg, module = ctx.product["tx_cal"], ctx.module
    saved_bias = module.read("IBias")
    ctx.log(f"TX cal: saved bias {[round(b, 1) for b in saved_bias]} mA")

    points = {ch: [] for ch in ctx.channels}
    try:
        for bias in cfg["bias_ma"]:
            ctx.check_abort()
            module.write_value("W_IBiasPID", "ibiaspid_off")
            module.write("IBias", [bias] * ctx.product["lasers"])
            ctx.sleep(cfg["settle_s"])
            power = ctx.true_tx_powers()
            txmon = module.read("R_TXMON")
            for ch in ctx.channels:
                points[ch].append((txmon[ch - 1], dbm_to_lin(power[ch])))
            ctx.log(f"bias {bias} mA: power {[round(power[ch], 2) for ch in ctx.channels]} dBm, TXMON {txmon}")

        old_slopes, old_offsets = module.read_cal("DTXPCal")
        min_step = cfg.get("min_txmon_change", 0.2)
        slopes, offsets = [], []
        for ch in ctx.channels:
            (x1, _), (x2, _) = points[ch]
            if abs(x2 - x1) < min_step * max(abs(x1), 1):
                # The bias change hardly moved TXMON: a fit would be noise. Keep this lane's
                # current calibration and fail it.
                ctx.log(f"CH{ch}: TXMON {x1} -> {x2} between the two bias points - bias control had no "
                        f"effect; keeping this lane's current TX calibration")
                slopes.append(old_slopes[ch - 1])
                offsets.append(old_offsets[ch - 1])
                ctx.results.add("Txp_Slope", ch, None)
                ctx.results.add("Txp_Offset", ch, None)
                continue
            try:
                slope, offset = two_point_fit(*points[ch])
            except ValueError as exc:
                ctx.log(f"CH{ch}: TX fit failed ({exc}); keeping this lane's current TX calibration")
                slopes.append(old_slopes[ch - 1])
                offsets.append(old_offsets[ch - 1])
                ctx.results.add("Txp_Slope", ch, None)
                ctx.results.add("Txp_Offset", ch, None)
                continue
            ctx.detail(f"CH{ch} TX fit: points {points[ch]} -> slope {slope:.6f}, offset {offset:.3f}")
            slopes.append(slope)
            offsets.append(offset)
            ctx.results.add("Txp_Slope", ch, round(slope, 4))
            ctx.results.add("Txp_Offset", ch, round(offset, 1))
        module.write_cal("DTXPCal", slopes, offsets)
        ctx.log("W_DTXPCal written (8 slope/offset pairs)")
    finally:
        module.write("IBias", saved_bias)
        module.write("W_IBiasPID", ctx.pre_run("W_IBiasPID", "ibiaspid_restore"))
        module.write_value("W_Reset", "reset")
        ctx.log(f"bias restored, W_Reset, wait {cfg['after_reset_wait_s']} s")
    ctx.sleep(cfg["after_reset_wait_s"])


def tx_check(ctx: RunContext) -> None:
    """At normal bias: dTxP = true_dBm - DDMI_dBm."""
    module = ctx.module
    power = ctx.true_tx_powers()
    ddmi = module.read("R_DTXP")
    slopes, offsets = module.read_cal("DTXPCal")
    ctx.detail(f"R_DTXPCal read back: slopes {slopes}, offsets {offsets}")
    for ch in ctx.channels:
        ddmi_dbm = lin_to_dbm(ddmi[ch - 1])
        ctx.results.add("TxP(dBm)", ch, round(power[ch], 2))
        ctx.results.add("DDMI_TxP(dBm)", ch, round(ddmi_dbm, 2))
        ctx.results.add("dTxP(dB)", ch, round(power[ch] - ddmi_dbm, 2))
    ctx.log(f"TX check: dTxP {[ctx.results.value('dTxP(dB)', ch) for ch in ctx.channels]} dB")
