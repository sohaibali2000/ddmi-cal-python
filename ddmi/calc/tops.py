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


def peak_valley(txmon: list) -> tuple[int, int]:
    """Indexes of the highest and lowest TXMON reading of one lane's sweep."""
    if not txmon:
        raise ValueError("empty sweep")
    return max(range(len(txmon)), key=txmon.__getitem__), min(range(len(txmon)), key=txmon.__getitem__)


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
    """Vtops where TXMON crosses level, searched only between the valley and the peak."""
    i_peak, i_valley = peak_valley(txmon)
    lo, hi = sorted((i_peak, i_valley))
    for i in range(lo, hi):
        a, b = txmon[i], txmon[i + 1]
        if a != b and min(a, b) <= level <= max(a, b):
            return volts[i] + (level - a) / (b - a) * (volts[i + 1] - volts[i])
    raise ValueError(f"TXMON never crosses {level:.1f} between valley and peak")


def operating_points(volts: list, txmon: list, fractions: list, mode: str) -> dict[float, float]:
    """Vtops for each operating-point fraction of one lane."""
    i_peak, i_valley = peak_valley(txmon)
    peak, valley = txmon[i_peak], txmon[i_valley]
    return {f: voltage_at_level(volts, txmon, level_for_fraction(f, peak, valley, mode))
            for f in fractions}


def tops_mid(points: dict[float, float], mid_from: list) -> float:
    """Tops_Mid = mean of the voltages at the two mid_from fractions."""
    return sum(points[f] for f in mid_from) / len(mid_from)
