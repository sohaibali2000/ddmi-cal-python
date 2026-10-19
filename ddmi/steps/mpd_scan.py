"""Scan_MPD and Scan_Disable (build map 6.5): 800G SIP Tx MPD Monitor.vi + Peak Search.vi."""
from __future__ import annotations

import copy

from ..calc import tops
from ..runctx import RunContext
from .tops_lock import relock


def _has_rising_edge(curve: list) -> bool:
    try:
        tops.peak_valley(curve)
        return True
    except ValueError:
        return False


def scan_mpd(ctx: RunContext) -> None:
    cfg = ctx.product["mpd_scan"]
    attempts = cfg.get("scan_attempts", 1)
    # 800G_TRx_Test.vi V6.8 Scan_MPD: the MPD Monitor call sits in a loop of 2 that repeats
    # when the scan does not pass: no rising edge, or Vtops not above tops_off after the reset
    for attempt in range(1, attempts + 1):
        curves, volts = _sweep(ctx, cfg)
        missing = [lane + 1 for lane, c in enumerate(curves) if not _has_rising_edge(c)]
        if missing and attempt < attempts:
            ctx.log(f"Scan_MPD: CH{missing} have no rising edge - scanning again")
            continue
        _analyse_and_write(ctx, cfg, curves, volts)
        wrong = _vtops_not_above_off(ctx)
        if not wrong:
            return
        if attempt < attempts:
            ctx.log(f"Scan_MPD: CH{wrong} read back Vtops <= tops_off - scanning again")
            continue
        for ch in wrong:
            ctx.results.fail(f"Scan_MPD CH{ch}: Vtops not above tops_off after the scan")


def _vtops_not_above_off(ctx: RunContext) -> list[int]:
    """Frame 5 read-back: on the rising edge the operating point (Vtops) must be above the
    valley (tops_off). Lanes without a scan result keep their values and are not judged."""
    if ctx.module.dry_run:
        return []
    vtops, off = ctx.module.read("Vtops"), ctx.module.read("tops_off")
    lanes = ctx.meta.get("mpd_scan", {}).get("lanes", {})
    wrong = [ch for ch in ctx.channels if ch in lanes and lanes[ch].get("mid_v") is not None
             and vtops[ch - 1] <= off[ch - 1]]
    ctx.detail(f"read back after the scan: Vtops {[round(v, 3) for v in vtops]}, "
               f"tops_off {[round(v, 3) for v in off]}")
    return wrong


def _sweep(ctx: RunContext, cfg: dict):
    module = ctx.module
    lanes = len(ctx.channels)

    # 800G SIP Tx MPD Monitor.vi V6.8, first frame: both loops off, every lane at 1.0 V,
    # tops_off 0, 1 s after each pair of writes.
    module.write_value("W_TOPPID", "toppid_off")
    module.write("Vtops", [cfg["start_vtops"]] * lanes)
    ctx.sleep(cfg["pre_scan_wait_s"])
    module.write("tops_off", [0.0] * lanes)
    module.write_value("W_IBiasPID", "ibiaspid_off")
    ctx.sleep(cfg["pre_scan_wait_s"])
    volts = tops.sweep_points(cfg["vtops_start"], cfg["vtops_stop"], cfg["vtops_step"])
    ctx.log(f"Scan_MPD: TOPS and bias loops off, sweep Vtops {volts[0]} -> {volts[-1]} V "
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
    return curves, volts


def _analyse_and_write(ctx: RunContext, cfg: dict, curves: list, volts: list) -> None:
    module = ctx.module
    lanes = len(ctx.channels)
    scan = ctx.meta["mpd_scan"]

    # Per lane: peak, valley and the operating-point voltages between them.
    fractions = cfg["op_point_fractions"]
    points, valley_v = {}, {}
    for lane in range(lanes):
        ch = lane + 1
        ctx.detail(f"sweep CH{ch} TXMON: {curves[lane]}")
        try:
            i_peak, i_valley = tops.peak_valley(curves[lane])
        except ValueError as exc:
            ctx.log(f"CH{ch}: {exc}; keeping this lane's tops_setpoint and tops_off")
            valley_v[ch] = None
            continue
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
        ctx.sleep(cfg.get("op_point_settle_s", 0.0))
        txmon, mpd_out = module.read("R_TXMON"), module.read("R_MPD_OUT")
        ctx.detail(f"op point {key}: TXMON {txmon}, MPD_OUT {mpd_out}")
        if key != "mid":
            # TXMON / MPD_OUT x 1000 at each operating point (MPD Monitor.vi V6.8 frame 3):
            # the production CSV's TXP_MPDOut_4.5dB..2.5dB columns
            for ch in points:
                ratio = round(txmon[ch - 1] / mpd_out[ch - 1] * 1000) if mpd_out[ch - 1] else None
                scan["lanes"][ch].setdefault("mpd_out", {})[str(key)] = ratio
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
            ctx.results.add("Tops_Mid(V)", ch, round(points[ch]["mid"], 3) if ch in points else None)

    # Last frame, in the order the V6.8 error wire runs: Vtops = Tops_Mid, tops_off = valley
    # (both coerced to 0.2-3.0 V), wait 2 s, W_Reset, wait 10 s, then tops_setpoint.
    lo, hi = cfg["write_clamp_v"]
    clamp = lambda v: min(max(v, lo), hi)
    current_v, current_off = module.read("Vtops"), module.read("tops_off")
    mid_v = [clamp(points[ch]["mid"]) if ch in points else current_v[ch - 1] for ch in ctx.channels]
    off_v = [current_off[ch - 1] if valley_v[ch] is None else clamp(valley_v[ch]) for ch in ctx.channels]
    module.write("Vtops", mid_v)
    module.write("tops_off", off_v)
    ctx.sleep(cfg["end_wait_s"])
    module.write_value("W_Reset", "reset")
    ctx.sleep(cfg["after_reset_wait_s"])
    module.write("tops_setpoint", setpoints)
    ctx.log(f"Vtops = Tops_Mid, tops_off = valley; W_Reset, {cfg['after_reset_wait_s']} s; "
            f"tops_setpoint {setpoints} written")
    ctx.detail(f"after reset: Vtops {module.read('Vtops')}, tops_off {module.read('tops_off')}")
    ctx.on_data("mpd_result", {"lanes": copy.deepcopy(scan["lanes"])})
    relock(ctx, "after Scan_MPD")


BIAS_SETPOINT_REGS = ("W_Bias_setpoint", "W_Bias_setpointHT", "W_Bias_setpointLT")


def adjust_power(ctx: RunContext) -> None:
    """Item_Test.vi V6.8 "Adjust Power", AWS LC-2*FR4.

    Write the default bias setpoint to RT/HT/LT, wait 2 s, read the 8 true TX powers. Per
    laser (lanes n and n+4 share it) take the mean of the two powers in dBm: below the window
    -> setpoint + step_up, above it -> setpoint - step_down. Write it back if anything changed.
    """
    cfg, module = ctx.product["adjust_power"], ctx.module
    default = list(cfg["default_setpoint"])
    for reg in BIAS_SETPOINT_REGS:
        module.write(reg, default)
    ctx.sleep(cfg["settle_s"])
    power = ctx.true_tx_powers()
    low, high = cfg["window_dbm"]
    lasers = len(default)
    new, means = list(default), []
    for laser in range(lasers):
        lanes = [ch for ch in ctx.channels if (ch - 1) % lasers == laser]
        mean = sum(power[ch] for ch in lanes) / len(lanes)
        means.append(round(mean, 2))
        if mean < low:
            new[laser] += cfg["step_up"]
        elif mean > high:
            new[laser] -= cfg["step_down"]
    if new != default:
        for reg in BIAS_SETPOINT_REGS:
            module.write(reg, new)
    ctx.log(f"Adjust Power: lane-pair mean power {means} dBm (window {low}..{high}); "
            f"bias setpoint {default} -> {new}")


def _measure_disabled(ctx: RunContext, tops_off: list, cfg: dict) -> dict:
    """Write tops_off for every lane, enable all then disable all (the module applies
    tops_off when a lane is disabled), and return each lane's output once settled."""
    module = ctx.module
    module.write("tops_off", tops_off)
    module.write_value("W_TXDis", "txdis_all_on")
    module.write_value("W_TXDis", "txdis_all_off")
    poll, waited, hist = cfg["poll_s"], 0.0, []
    while True:
        ctx.sleep(poll)
        waited += poll
        hist.append(ctx.true_tx_powers())
        if waited >= cfg["min_s"] and len(hist) >= cfg["stable_readings"]:
            last = hist[-cfg["stable_readings"]:]
            if all(max(r[ch] for r in last) - min(r[ch] for r in last) <= cfg["tol_db"]
                   or last[-1][ch] <= cfg["dark_enough_dbm"] for ch in ctx.channels):
                break
        if waited >= cfg["max_s"]:
            break
    return hist[-1], waited


def scan_disable(ctx: RunContext) -> None:
    """Item_Test.vi V6.8 "Scan_Disable", all 8 lanes at once.

    From each lane's tops_off (the scan's valley): one step either side, then 4 steps in the
    direction that got darker, keeping the darkest voltage. Write the best and read the
    leakage. Lanes still above recheck_dbm get 5 more steps that way. All lanes are
    re-enabled at the end.
    """
    cfg, module = ctx.product["tx_disable"], ctx.module
    step = cfg["step_v"]
    chans = list(ctx.channels)
    start = list(module.read("tops_off"))
    ctx.log(f"Scan_Disable: all lanes disabled together; tops_off +/-{step} V, then "
            f"{cfg['steps']} steps toward darker; {cfg['extra_steps']} more if above {cfg['recheck_dbm']} dBm")
    readings = {ch: [] for ch in chans}
    waits = []

    def measure(values):
        p, waited = _measure_disabled(ctx, values, cfg)
        waits.append(round(waited, 1))
        for ch in chans:
            readings[ch].append((round(values[ch - 1], 3), round(p[ch], 2)))
        return p

    try:
        best_v, best_p = list(start), measure(start)
        p_plus = measure([v + step for v in start])
        p_minus = measure([v - step for v in start])
        direction = {}
        for ch in chans:
            i = ch - 1
            direction[ch] = 1 if p_plus[ch] <= p_minus[ch] else -1
            for sign, p in ((1, p_plus), (-1, p_minus)):
                if p[ch] < best_p[ch]:
                    best_v[i], best_p[ch] = start[i] + sign * step, p[ch]

        def walk(lanes, n):
            for _ in range(n):
                ctx.check_abort()
                trial = list(best_v)
                for ch in lanes:
                    trial[ch - 1] = best_v[ch - 1] + direction[ch] * step
                p = measure(trial)
                for ch in lanes:
                    if p[ch] < best_p[ch]:
                        best_v[ch - 1], best_p[ch] = trial[ch - 1], p[ch]

        walk(chans, cfg["steps"])
        leakage = measure(best_v)
        bright = [ch for ch in chans if leakage[ch] > cfg["recheck_dbm"]]
        if bright and cfg["extra_steps"]:
            ctx.log(f"Scan_Disable: CH{bright} above {cfg['recheck_dbm']} dBm - {cfg['extra_steps']} more steps")
            walk(bright, cfg["extra_steps"])
            leakage = measure(best_v)
    finally:
        module.write_value("W_TXDis", "txdis_all_on")
        ctx.sleep(cfg["enable_wait_s"])

    ctx.log(f"Scan_Disable: {len(waits)} steps, settle waits {waits} s "
            f"(total {sum(waits):.1f} s, {sum(w >= cfg['max_s'] for w in waits)} hit the {cfg['max_s']} s limit)")
    lanes_meta = ctx.meta.get("mpd_scan", {}).get("lanes", {})
    for ch in chans:
        ctx.detail(f"CH{ch} tx-disable search (tops_off V, dBm): {readings[ch]}")
        ctx.log(f"CH{ch}: tops_off {start[ch - 1]:.3f} -> {best_v[ch - 1]:.3f} V, leakage {leakage[ch]:.2f} dBm")
        ctx.results.add("Tops_off(V)", ch, round(best_v[ch - 1], 3))
        ctx.results.add("Tx_Disable(dBm)", ch, round(leakage[ch], 2))
        info = lanes_meta.get(ch)
        if info is not None:
            info.update(tops_off_v=best_v[ch - 1], leakage_dbm=leakage[ch])
            ctx.on_data("mpd_tops_off", {"ch": ch, "tops_off_v": best_v[ch - 1], "leakage_dbm": leakage[ch]})
    relock(ctx, "after Scan_Disable")
