"""TOPS maths: setpoint, Vtops register encoding and the MPD scan analysis (build map 6.5, 7.4)."""
from __future__ import annotations

VTOPS_FULL_SCALE_AWS = 5.0      # raw = V / 5.0 * 4095 (other customers use 2.5)


def tops_setpoint(txmon: float, mpd_out: float, scale: int = 1000) -> int:
    if mpd_out == 0:
        raise ValueError("MPD_OUT is 0, cannot compute tops_setpoint")
    return round(txmon / mpd_out * scale)


def vtops_to_raw(volts: float, full_scale: float = VTOPS_FULL_SCALE_AWS) -> int:
    return round(volts / full_scale * 4095)


def raw_to_vtops(raw: int, full_scale: float = VTOPS_FULL_SCALE_AWS) -> float:
    return raw / 4095 * full_scale


def sweep_points(start: float, stop: float, step: float) -> list[float]:
    count = round((stop - start) / step)
    return [round(start + i * step, 6) for i in range(count + 1)]


# 800G SIP Tx MPD Monitor.vi V6.8 pairing loop: a valley is only used from this sweep point on
# (AWS 5, Acacia 100, other customers 20).
MIN_VALLEY_INDEX = 5


def peak_search(data: list, valleys: bool) -> tuple[list[float], list[int]]:
    """Peak Search.vi V6.8: (amplitudes, locations).

    Threshold = mean of the whole sweep. Every run of points at or above it (peaks) or at or
    below it (valleys) gives one entry: its highest / lowest point (the first one on a tie).
    A run still open at the end of the sweep counts too. A peak run that starts at the first
    sweep point is not reported (the VI's run flag starts FALSE for peaks, TRUE for valleys):
    with it, the same-index pairing below matches the operating point LabVIEW left on all 8
    lanes of P172262504894; without it, 6 lanes would have no pair.
    """
    if not data:
        return [], []
    mean = sum(data) / len(data)
    amps, locs, run = [], [], []
    for i, v in enumerate(data):
        if (v <= mean) if valleys else (v >= mean):
            run.append(i)
        if run and (run[-1] != i or i == len(data) - 1):
            if valleys or run[0] > 0:
                best = (min if valleys else max)(run, key=data.__getitem__)
                amps.append(data[best])
                locs.append(best)
            run = []
    return amps, locs


def peak_valley(txmon: list, min_valley_index: int = MIN_VALLEY_INDEX) -> tuple[int, int]:
    """(peak index, valley index) of the edge 800G SIP Tx MPD Monitor.vi V6.8 uses.

    Peak Search gives the peaks and the valleys; peak k is paired with valley k. A pair is
    kept when peak index - valley index > 1 (the peak comes after the valley: a rising edge),
    valley index >= min_valley_index (AWS 5) and peak index < (Vtops_Max - Vtops_Min) / step
    (not the last sweep point). Of the kept pairs, AWS (the default case) takes the one with
    the lowest valley (Array Max & Min, min index, of the valley amplitudes).
    """
    if not txmon:
        raise ValueError("empty sweep")
    p_amp, p_loc = peak_search(txmon, valleys=False)
    v_amp, v_loc = peak_search(txmon, valleys=True)
    last = len(txmon) - 1
    kept = [k for k in range(min(len(p_loc), len(v_loc)))
            if p_loc[k] - v_loc[k] > 1 and v_loc[k] >= min_valley_index and p_loc[k] < last]
    if not kept:
        raise ValueError(f"no rising edge: Peak Search peaks at {p_loc}, valleys at {v_loc} "
                         f"give no valley-then-peak pair")
    k = min(kept, key=lambda j: v_amp[j])
    return p_loc[k], v_loc[k]


def level_for_fraction(fraction: float, peak: float, valley: float, mode: str) -> float:
    """TXMON level an operating-point fraction stands for.

    How Peak Search.vi uses the fraction array is not confirmed (build map open
    question 4), so the interpretation is a config setting, not a fixed rule.
    """
    if mode == "x_of_peak":
        return fraction * peak
    if mode == "one_minus_x_of_peak":
        return (1 - fraction) * peak
    if mode == "x_of_span":
        return valley + fraction * (peak - valley)
    if mode == "one_minus_x_of_span":
        return valley + (1 - fraction) * (peak - valley)
    raise ValueError(f"unknown op_point_mode {mode!r}")


def voltage_at_level(volts: list, txmon: list, level: float) -> float:
    """Vtops of the sweep step whose TXMON is closest to level, searched only between the
    valley and the peak (MPD Monitor.vi V6.8: closest step, no interpolation, 0.02 V
    resolution)."""
    i_peak, i_valley = peak_valley(txmon)
    lo, hi = sorted((i_peak, i_valley))
    return volts[min(range(lo, hi + 1), key=lambda i: abs(txmon[i] - level))]


def operating_points(volts: list, txmon: list, fractions: list, mode: str) -> dict[float, float]:
    """Vtops for each operating-point fraction of one lane.

    MPD Monitor.vi V6.8: when the second and third points (3.5 dB and 3.0 dB) land on the same
    sweep step, the second is moved one step lower, so Tops_Mid lies between two steps.
    """
    i_peak, i_valley = peak_valley(txmon)
    peak, valley = txmon[i_peak], txmon[i_valley]
    points = {f: voltage_at_level(volts, txmon, level_for_fraction(f, peak, valley, mode))
              for f in fractions}
    if len(fractions) >= 3 and len(volts) >= 2 and points[fractions[1]] == points[fractions[2]]:
        points[fractions[1]] = round(points[fractions[2]] - (volts[1] - volts[0]), 6)
    return points


def tops_mid(points: dict[float, float], mid_from: list) -> float:
    """Tops_Mid = mean of the voltages at the two mid_from fractions."""
    return sum(points[f] for f in mid_from) / len(mid_from)
