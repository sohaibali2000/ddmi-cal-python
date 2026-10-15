"""Scan_MPD and Scan_Disable (build map 6.5): 800G SIP Tx MPD Monitor.vi + Peak Search.vi."""
from __future__ import annotations

import copy

from ..calc import tops
from ..calc.txdisable import search_darkest
from ..runctx import RunContext


def scan_mpd(ctx: RunContext) -> None:
    cfg, module = ctx.product["mpd_scan"], ctx.module
    lanes = len(ctx.channels)

    module.write_value("W_TOPPID", "toppid_off")
    volts = tops.sweep_points(cfg["vtops_start"], cfg["vtops_stop"], cfg["vtops_step"])
    ctx.log(f"Scan_MPD: TOPS loop off, sweep Vtops {volts[0]} -> {volts[-1]} V "
            f"step {cfg['vtops_step']} V ({len(volts)} points)")
    curves = [[] for _ in range(lanes)]
    scan = ctx.meta["mpd_scan"] = {"volts": [], "curves": curves, "lanes": {}}
    ctx.on_data("mpd_start", {"volts": volts, "lanes": lanes})
    for v in volts:
        ctx.check_abort()
        module.write("Vtops", [v] * lanes)
        txmon = module.read("R_TXMON")
        for lane, value in enumerate(txmon):
            curves[lane].append(value)
        scan["volts"].append(v)
        ctx.on_data("mpd_point", {"v": v, "txmon": list(txmon)})

    # Per lane: peak, valley and the operating-point voltages between them.
    fractions = cfg["op_point_fractions"]
    points, valley_v = {}, {}
    for lane in range(lanes):
        ch = lane + 1
        ctx.detail(f"sweep CH{ch} TXMON: {curves[lane]}")
        i_peak, i_valley = tops.peak_valley(curves[lane])
        valley_v[ch] = volts[i_valley]
        peak, valley = curves[lane][i_peak], curves[lane][i_valley]
        if peak < max(2 * valley, valley + cfg.get("min_contrast", 50)):
            # A flat curve: Vtops did not move the modulator. Keep this lane's TOPS settings.
            ctx.log(f"CH{ch}: sweep is flat (TXMON {valley}..{peak}) - Vtops had no effect; "
                    f"keeping this lane's tops_setpoint and tops_off")
            valley_v[ch] = None
            continue
        lane_info = scan["lanes"][ch] = {
            "peak_v": volts[i_peak], "peak": curves[lane][i_peak],
            "valley_v": volts[i_valley], "valley": curves[lane][i_valley],
            "points": {}, "mid_v": None, "setpoint": None, "tops_off_v": volts[i_valley],
        }
        try:
            points[ch] = tops.operating_points(volts, curves[lane], fractions, cfg["op_point_mode"])
            points[ch]["mid"] = tops.tops_mid(points[ch], cfg["tops_mid_from"])
        except ValueError as exc:
            ctx.log(f"CH{ch}: scan failed ({exc})")
            continue
        lane_info["points"] = {str(f): points[ch][f] for f in fractions}
        lane_info["mid_v"] = points[ch]["mid"]
        ctx.log(f"CH{ch}: peak {curves[lane][i_peak]} @ {volts[i_peak]:.2f} V, valley "
                f"{curves[lane][i_valley]} @ {volts[i_valley]:.2f} V, Tops_Mid {points[ch]['mid']:.3f} V")

    # Visit every operating point, then Tops_Mid last so Vtops is left there.
    setpoints = module.read("tops_setpoint")
    for key in list(fractions) + ["mid"]:
        ctx.check_abort()
        current_v = module.read("Vtops")
        module.write("Vtops", [points[ch][key] if ch in points
                               else (valley_v[ch] if valley_v[ch] is not None else current_v[ch - 1])
                               for ch in ctx.channels])
        txmon, mpd_out = module.read("R_TXMON"), module.read("R_MPD_OUT")
        ctx.detail(f"op point {key}: TXMON {txmon}, MPD_OUT {mpd_out}")
        if key != "mid":
            continue
        for ch in ctx.channels:
            value = None
            if ch in points:
                try:
                    value = tops.tops_setpoint(txmon[ch - 1], mpd_out[ch - 1], cfg["setpoint_scale"])
                    setpoints[ch - 1] = value
                    scan["lanes"][ch]["setpoint"] = value
                except ValueError as exc:
                    ctx.log(f"CH{ch}: {exc}")
            ctx.results.add("Tops_Setpoint", ch, value)

    module.write("tops_setpoint", setpoints)
    current_off = module.read("tops_off")
    module.write("tops_off", [current_off[ch - 1] if valley_v[ch] is None else valley_v[ch]
                              for ch in ctx.channels])
    toppid = ctx.pre_run("W_TOPPID", "toppid_on")
    module.write("W_TOPPID", toppid)
    ctx.log(f"tops_setpoint written: {setpoints}; tops_off = valley voltage; TOPS loop on (TOPPID 0x{toppid:02X})")
    ctx.on_data("mpd_result", {"lanes": copy.deepcopy(scan["lanes"])})


def scan_disable(ctx: RunContext) -> None:
    """Per lane: disable it, then nudge tops_off toward the darkest output."""
    cfg, module = ctx.product["tx_disable"], ctx.module
    ctx.log(f"Scan_Disable: step {cfg['step_v']} V toward darker, up to {cfg['max_steps']} steps")
    try:
        for ch in ctx.channels:
            ctx.check_abort()
            start = module.read("tops_off")[ch - 1]
            module.write("W_TXDis", 1 << (ch - 1))

            def measure(v, ch=ch):
                module.update_lane("tops_off", ch, v)
                ctx.sleep(cfg["settle_s"])
                return ctx.true_tx_power(ch)

            best_v, _, readings = search_darkest(measure, start, cfg["step_v"], cfg["max_steps"])
            leakage = measure(best_v)
            ctx.detail(f"CH{ch} tx-disable search: {[(round(v, 3), round(p, 2)) for v, p in readings]}")
            ctx.log(f"CH{ch}: tops_off {start:.3f} -> {best_v:.3f} V, leakage {leakage:.2f} dBm")
            ctx.results.add("Tops_off(V)", ch, round(best_v, 3))
            ctx.results.add("Tx_Disable(dBm)", ch, round(leakage, 2))
            lane_info = ctx.meta.get("mpd_scan", {}).get("lanes", {}).get(ch)
            if lane_info is not None:
                lane_info.update(tops_off_v=best_v, leakage_dbm=leakage)
                ctx.on_data("mpd_tops_off", {"ch": ch, "tops_off_v": best_v, "leakage_dbm": leakage})
    finally:
        module.write("W_TXDis", 0x00)
