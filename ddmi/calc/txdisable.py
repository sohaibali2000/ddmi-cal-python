"""TX-disable tuning: nudge tops_off toward the darkest output (build map 6.5, Scan_Disable).

The build map gives the rule (step +/-0.01 V in the direction that gets darker,
up to 4 steps, keep the darkest voltage) but not the exact order LabVIEW probes
in; that order is this module's own choice until Item_Test.vi is traced.
"""
from __future__ import annotations

from typing import Callable


def search_darkest(measure: Callable[[float], float], start_v: float,
                   step_v: float = 0.01, max_steps: int = 4,
                   first: float | None = None) -> tuple[float, float, list]:
    """Return (best voltage, leakage dBm there, every (voltage, dBm) reading taken).

    measure(v) sets tops_off to v on the disabled lane and returns the output in dBm.
    first: the reading at start_v when the caller already has it.
    """
    readings = [(start_v, measure(start_v) if first is None else first)]
    best_v, best_p = readings[0]

    direction = 0
    for sign in (1, -1):
        v = round(start_v + sign * step_v, 6)
        p = measure(v)
        readings.append((v, p))
        if p < best_p:
            best_v, best_p, direction = v, p, sign
            break
    if direction == 0:
        return best_v, best_p, readings

    for n in range(2, max_steps + 1):
        v = round(start_v + direction * n * step_v, 6)
        p = measure(v)
        readings.append((v, p))
        if p >= best_p:
            break
        best_v, best_p = v, p
    return best_v, best_p, readings
