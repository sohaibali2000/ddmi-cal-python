"""Scan_MPD and Scan_Disable (build map 6.5): 800G SIP Tx MPD Monitor.vi + Peak Search.vi."""
from __future__ import annotations

import copy

from ..calc import tops
from ..runctx import RunContext, StopTest
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

    # 800G SIP Tx MPD Monitor.vi V6.8, frame 0 (error-wire order): bias loop off, TOPS loop
    # off, 1 s; every lane's Vtops 1.0 V and tops_off 0, 1 s.
    module.write_value("W_IBiasPID", "ibiaspid_off")
    module.write_value("W_TOPPID", "toppid_off")
    ctx.sleep(cfg["pre_scan_wait_s"])
    module.write("Vtops", [cfg["start_vtops"]] * lanes)
    module.write("tops_off", [0.0] * lanes)
    ctx.sleep(cfg["pre_scan_wait_s"])
    volts = tops.sweep_points(cfg["vtops_start"], cfg["vtops_stop"], cfg["vtops_step"])
    ctx.log(f"Scan_MPD: TOPS and bias loops off, sweep Vtops {volts[0]} -> {volts[-1]} V "
            f"step {cfg['vtops_step']} V ({len(volts)} points)")
    curves = [[] for _ in range(lanes)]
    scan = ctx.meta["mpd_scan"] = {"volts": [], "curves": curves, "lanes": {}}
    ctx.on_data("mpd_start", {"volts": volts, "lanes": lanes})
    for i, v in enumerate(volts):
        ctx.check_abort()
        with module.no_page_wait():         # MPD Monitor.vi sweep: IIC.vi Delay = FALSE
            module.write("Vtops", [v] * lanes)
        ctx.sleep(cfg.get("start_settle_s", 0.0) if i == 0 else cfg.get("step_settle_s", 0.0))
        with module.no_page_wait():
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
    # Never rewrite the bias on a reading that is not the module's light (TX fibre off,
    # meter not connected): on 2026-10-19 -79 dBm on every lane still bumped the setpoints.
    before = ctx.true_tx_powers()
    dark = [ch for ch in ctx.channels if before[ch] < cfg["min_light_dbm"]]
    if dark:
        raise StopTest(f"Adjust Power: TX power below {cfg['min_light_dbm']} dBm on CH{dark} "
                       f"({[round(before[ch], 1) for ch in dark]} dBm) - is the TX fibre connected? "
                       "Bias setpoints not changed")
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


def _wait_dark_reading(ctx: RunContext, cfg: dict) -> tuple[dict, float]:
    """Read the disabled lanes once they are steady. V6.8 reads straight away (0 s; 0.1 s
    for the final reading); this module needs seconds after a disable, so the meter is
    polled until stable_readings agree within tol_db (or the lane is dark_enough_dbm)."""
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


def _measure_disabled(ctx: RunContext, tops_off: list, cfg: dict, final: bool = False) -> tuple[dict, float]:
    """One Scan_Disable step, in Item_Test.vi V6.8 order: write tops_off for every lane,
    disable all lanes (the module applies tops_off as a lane goes into disable), read the
    leakage, enable all lanes, wait 0.01 s. The final reading (frame 3 / 5) leaves the lanes
    enabled and waits enable_wait_s (1 s) instead."""
    module = ctx.module
    module.write("tops_off", tops_off)
    module.write_value("W_TXDis", "txdis_all_off")
    try:
        power, waited = _wait_dark_reading(ctx, cfg)
    finally:
        module.write_value("W_TXDis", "txdis_all_on")
    ctx.sleep(cfg["enable_wait_s"] if final else 0.01)
    return power, waited


def scan_disable(ctx: RunContext) -> None:
    """Item_Test.vi V6.8 "Scan_Disable", all 8 lanes at once.

    Frame 0: from each lane's tops_off (the scan's valley) measure start, start + step and
    start - step; a lane walks up if start + step was darker than start, otherwise down.
    Frame 1: 4 steps at (start +/- step) +/- k*step, k = 1..4, taken whatever they read.
    Frame 2: per lane the darkest of all measured points (first one on a tie).
    Frame 3: write it, disable, read the leakage (the result), enable, 1 s.
    Frames 4-5: if any lane is above recheck_dbm (-18), every lane takes 5 more steps
    (k = 5..9), the darkest of all points is written and read again.
    """
    cfg, module = ctx.product["tx_disable"], ctx.module
    step = cfg["step_v"]
    chans = list(ctx.channels)
    start = list(module.read("tops_off"))
    ctx.log(f"Scan_Disable: all lanes disabled together; tops_off +/-{step} V, then "
            f"{cfg['steps']} steps; {cfg['extra_steps']} more if a lane is above {cfg['recheck_dbm']} dBm")
    readings = {ch: [] for ch in chans}       # (tops_off V, dBm) of every measured point
    waits = []

    def measure(values, final=False):
        p, waited = _measure_disabled(ctx, values, cfg, final)
        waits.append(round(waited, 1))
        if not final:
            for ch in chans:
                readings[ch].append((round(values[ch - 1], 4), round(p[ch], 2)))
        return p

    def best():
        """Frame 2 / 5: per lane the voltage of the lowest reading (Array Max & Min, min index)."""
        return [min(readings[ch], key=lambda r: r[1])[0] for ch in chans]

    try:
        p0 = measure(start)
        p_up = measure([v + step for v in start])
        measure([v - step for v in start])
        direction = {ch: 1 if p_up[ch] < p0[ch] else -1 for ch in chans}
        base = [start[ch - 1] + direction[ch] * step for ch in chans]

        def walk(ks):
            for k in ks:
                ctx.check_abort()
                measure([base[ch - 1] + direction[ch] * k * step for ch in chans])

        walk(range(1, cfg["steps"] + 1))
        best_v = best()
        leakage = measure(best_v, final=True)
        bright = [ch for ch in chans if leakage[ch] > cfg["recheck_dbm"]]
        if bright and cfg["extra_steps"]:
            ctx.log(f"Scan_Disable: CH{bright} above {cfg['recheck_dbm']} dBm - "
                    f"{cfg['extra_steps']} more steps on every lane")
            walk(range(cfg["steps"] + 1, cfg["steps"] + cfg["extra_steps"] + 1))
            best_v = best()
            leakage = measure(best_v, final=True)
    finally:
        module.write_value("W_TXDis", "txdis_all_on")

    ctx.log(f"Scan_Disable: {len(waits)} readings, settle waits {waits} s "
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
