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


PEAK_MIN_OF_MAX = 0.8     # the edge's peak must reach this share of the sweep's maximum
# MPD Monitor.vi V6.8 pairing loop: a valley is only used from this sweep point on
# (AWS 5, Acacia 100, other customers 20) - AWS skips valleys below 0.60 V.
MIN_VALLEY_INDEX = 5


def peak_valley(txmon: list) -> tuple[int, int]:
    """(peak index, valley index) of the first RISING edge of one lane's sweep: the lowest-
    voltage valley that has a full peak after it, and that peak.

    800G SIP Tx MPD Monitor.vi V6.8 pairs each Peak Search valley with the peak above it, and
    for AWS requires Vtops_Mid > Vtops_min (rising edge, change list 2025-10-09). Every row of
    the production CSV has Tops_Mid > Tops_off. On a falling edge the module's TOPS loop
    does not hold the lane after a reset (it sits at the top of the fringe).
    """
    if not txmon:
        raise ValueError("empty sweep")
    n, top, bottom = len(txmon), max(txmon), min(txmon)
    dark = bottom + 0.1 * (top - bottom)       # a real valley, not the slope at the sweep start
    # valleys: runs of dark points (robust to noise), each at its darkest point
    runs, start = [], None
    for i, v in enumerate(txmon + [dark + 1]):
        if v <= dark and start is None:
            start = i
        elif v > dark and start is not None:
            runs.append((start, i))
            start = None
    for k, (a, b) in enumerate(runs):
        valley = min(range(a, b), key=txmon.__getitem__)
        if valley < MIN_VALLEY_INDEX:
            continue
        end = runs[k + 1][0] if k + 1 < len(runs) else n
        if valley + 1 >= end:
            continue
        peak = max(range(valley + 1, end), key=txmon.__getitem__)
        if txmon[peak] >= PEAK_MIN_OF_MAX * top:
            return peak, valley
    raise ValueError("no rising edge (valley followed by a full peak) in the sweep")


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
    """Vtops for each operating-point fraction of one lane."""
    i_peak, i_valley = peak_valley(txmon)
    peak, valley = txmon[i_peak], txmon[i_valley]
    return {f: voltage_at_level(volts, txmon, level_for_fraction(f, peak, valley, mode))
            for f in fractions}


def tops_mid(points: dict[float, float], mid_from: list) -> float:
    """Tops_Mid = mean of the voltages at the two mid_from fractions."""
    return sum(points[f] for f in mid_from) / len(mid_from)
