import math

import pytest

from ddmi.calc import tops


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
    # one MZM fringe, rising edge: valley near 1.0 V, peak near 2.2 V
    return 100 + 3000 * 0.5 * (1 + math.cos(math.pi * (v - 2.2) / 1.2))


def _lane():
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    return volts, [round(_txmon_at(v)) for v in volts]


def test_peak_and_valley():
    volts, txmon = _lane()
    i_peak, i_valley = tops.peak_valley(txmon)
    assert volts[i_valley] == pytest.approx(1.0)
    assert volts[i_peak] == pytest.approx(2.2)


def test_first_rising_edge_is_used_not_the_global_peak_and_valley():
    # peak at the sweep start, valley ~1.3 V, peak ~2.3 V, valley ~2.9 V (as CH1 of
    # P185262004341): the falling edge 0.5-1.3 V must not be used
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    txmon = [round(500 + 500 * math.cos(math.pi * (v - 0.5) / 0.85)) for v in volts]
    i_peak, i_valley = tops.peak_valley(txmon)
    assert volts[i_valley] == pytest.approx(1.36, abs=0.03)
    assert volts[i_peak] == pytest.approx(2.2, abs=0.03)
    assert volts[i_valley] < volts[i_peak]


def test_a_sweep_with_only_a_falling_edge_is_refused():
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    txmon = [round(100 + 3000 * 0.5 * (1 + math.cos(math.pi * (v - 1.0) / 1.2))) for v in volts]
    with pytest.raises(ValueError):
        tops.peak_valley(txmon)


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
    # x_of_peak: 0.44 is the darkest level; on a rising edge darker = lower voltage
    assert points[0.44] < points[0.50] < points[0.56] < points[0.65]
    assert _txmon_at(points[0.50]) == pytest.approx(0.50 * 3100, rel=0.01)
    mid = tops.tops_mid(points, [0.50, 0.44])
    assert mid == pytest.approx((points[0.50] + points[0.44]) / 2)


def test_shipped_config_gives_the_documented_operating_points():
    # 35 / 44 / 50 / 56 % of peak (4.5 / 3.5 / 3.0 / 2.5 dB below it); Tops_Mid from the
    # 3.5 dB and 3.0 dB points, about 3.25 dB below the peak (about 47 %).
    from ddmi.config import load_product
    cfg = load_product()["mpd_scan"]
    levels = sorted(tops.level_for_fraction(f, 1000, 0, cfg["op_point_mode"])
                    for f in cfg["op_point_fractions"])
    assert levels == pytest.approx([350, 440, 500, 560])
    mid_levels = sorted(tops.level_for_fraction(f, 1000, 0, cfg["op_point_mode"])
                        for f in cfg["tops_mid_from"])
    assert mid_levels == pytest.approx([440, 500])

    volts, txmon = _lane()
    points = tops.operating_points(volts, txmon, cfg["op_point_fractions"], cfg["op_point_mode"])
    mid = tops.tops_mid(points, cfg["tops_mid_from"])
    below_peak_db = 10 * math.log10(_txmon_at(mid) / max(txmon))
    assert below_peak_db == pytest.approx(-3.25, abs=0.1)