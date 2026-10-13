import math

import pytest

from ddmi.calc import tops
from ddmi.calc.txdisable import search_darkest


def test_setpoint_example():
    assert tops.tops_setpoint(1850, 2940) == 629


def test_setpoint_rejects_zero_mpd_out():
    with pytest.raises(ValueError):
        tops.tops_setpoint(1850, 0)


def test_vtops_encoding():
    assert tops.vtops_to_raw(1.47) == 1204
    assert tops.raw_to_vtops(1204) == pytest.approx(1.47, abs=0.001)
    assert tops.vtops_to_raw(1.25, full_scale=2.5) == 2048


def test_sweep_points_aws():
    points = tops.sweep_points(0.5, 3.0, 0.02)
    assert len(points) == 126
    assert points[0] == 0.5 and points[-1] == 3.0


def _txmon_at(v):
    # one MZM fringe: peak near 1.0 V, valley near 2.2 V
    return 100 + 3000 * 0.5 * (1 + math.cos(math.pi * (v - 1.0) / 1.2))


def _lane():
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    return volts, [round(_txmon_at(v)) for v in volts]


def test_peak_and_valley():
    volts, txmon = _lane()
    i_peak, i_valley = tops.peak_valley(txmon)
    assert volts[i_peak] == pytest.approx(1.0)
    assert volts[i_valley] == pytest.approx(2.2)


@pytest.mark.parametrize("mode, level", [
    ("x_of_peak", 0.44 * 3100), ("one_minus_x_of_peak", 0.56 * 3100),
    ("x_of_span", 100 + 0.44 * 3000), ("one_minus_x_of_span", 100 + 0.56 * 3000),
])
def test_level_for_fraction(mode, level):
    assert tops.level_for_fraction(0.44, 3100, 100, mode) == pytest.approx(level)


def test_operating_points_lie_between_peak_and_valley_and_hit_their_level():
    volts, txmon = _lane()
    points = tops.operating_points(volts, txmon, [0.65, 0.56, 0.50, 0.44], "x_of_peak")
    assert all(1.0 < v < 2.2 for v in points.values())
    assert points[0.65] < points[0.56] < points[0.50] < points[0.44]   # darker = nearer the valley
    assert _txmon_at(points[0.50]) == pytest.approx(0.50 * 3100, rel=0.01)
    mid = tops.tops_mid(points, [0.50, 0.44])
    assert mid == pytest.approx((points[0.50] + points[0.44]) / 2)


def _leak(valley_v):
    return lambda v: -45 + 4000 * (v - valley_v) ** 2


def test_tx_disable_steps_toward_darker_and_keeps_darkest():
    best_v, best_p, readings = search_darkest(_leak(1.83), 1.80)
    assert best_v == pytest.approx(1.83)
    assert best_p == min(p for _, p in readings)


def test_tx_disable_steps_down_when_darker_is_below():
    best_v, _, _ = search_darkest(_leak(1.78), 1.80)
    assert best_v == pytest.approx(1.78)


def test_tx_disable_is_limited_to_max_steps():
    best_v, _, _ = search_darkest(_leak(2.00), 1.80, max_steps=4)
    assert best_v == pytest.approx(1.84)


def test_tx_disable_stays_put_at_the_minimum():
    best_v, _, readings = search_darkest(_leak(1.80), 1.80)
    assert best_v == 1.80 and len(readings) == 3
