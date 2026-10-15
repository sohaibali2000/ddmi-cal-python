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
    for i, v in enumerate(volts):
        ctx.check_abort()
        module.write("Vtops", [v] * lanes)
        ctx.sleep(cfg.get("start_settle_s", 0.0) if i == 0 else cfg.get("step_settle_s", 0.0))
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


def settled_power(ctx: RunContext, ch: int, cfg: dict) -> float:
    """A disabled lane's output once it has settled.

    On the real module every TX disable and every tops_off write starts with a bright
    spike (about +4 dBm) that then drifts down for about 10 s. Read every poll_s until the
    last readings agree within tol_db, or the lane is safely dark, or max_s has passed.
    """
    poll, waited, hist = cfg["poll_s"], 0.0, []
    dark_enough = cfg["limit_dbm"][1] - cfg["dark_margin_db"]
    while True:
        ctx.sleep(poll)
        waited += poll
        hist.append(ctx.true_tx_power(ch))
        if waited >= cfg["min_s"]:
            last = hist[-cfg["stable_readings"]:]
            if hist[-1] <= dark_enough or (len(last) == cfg["stable_readings"]
                                           and max(last) - min(last) <= cfg["tol_db"]):
                break
        if waited >= cfg["max_s"]:
            break
    ctx.detail(f"CH{ch} disabled output over {waited:.1f} s: {[round(p, 2) for p in hist]}")
    return hist[-1]


def scan_disable(ctx: RunContext) -> None:
    """Per lane: disable it at tops_off = the scan's valley and read the settled leakage.
    Only a lane that fails is tuned, in tune_step_v steps, each one settled."""
    cfg, module = ctx.product["tx_disable"], ctx.module
    ctx.log(f"Scan_Disable: settle up to {cfg['max_s']} s per lane; tune by {cfg['tune_step_v']} V "
            f"(up to {cfg['tune_max_steps']} steps) only if above {cfg['limit_dbm'][1]} dBm")
    # With AWS_Test_Mode = 1 a disabled lane is frozen where it was and tops_off is never
    # applied (seen on hardware 2026-10-15: CH8 stayed at +5.0 dBm; with 0 it went dark).
    test_mode = module.read("AWS_Test_Mode")
    if test_mode:
        module.write("AWS_Test_Mode", 0)
        ctx.log(f"Scan_Disable: AWS_Test_Mode {test_mode} -> 0 while the lanes are disabled")
    try:
        for ch in ctx.channels:
            ctx.check_abort()
            start = module.read("tops_off")[ch - 1]
            module.write("W_TXDis", 1 << (ch - 1))

            def measure(v, ch=ch):
                if abs(module.read("tops_off")[ch - 1] - v) > 1e-3:
                    module.update_lane("tops_off", ch, v)
                return settled_power(ctx, ch, cfg)

            best_v, leakage = start, measure(start)
            readings = [(start, leakage)]
            if leakage > cfg["limit_dbm"][1] and cfg["tune_max_steps"]:
                ctx.log(f"CH{ch}: {leakage:.2f} dBm at the valley {start:.3f} V - tuning tops_off")
                best_v, leakage, more = search_darkest(measure, start, cfg["tune_step_v"],
                                                       cfg["tune_max_steps"], first=leakage)
                readings = more
                if abs(module.read("tops_off")[ch - 1] - best_v) > 1e-3:
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
        if test_mode:
            module.write("AWS_Test_Mode", test_mode)
