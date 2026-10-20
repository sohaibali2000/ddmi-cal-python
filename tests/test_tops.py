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
    # V6.8 takes the closest sweep step (0.02 V), so the level is hit to within one step
    assert all(v in volts for v in points.values())
    assert _txmon_at(points[0.50]) == pytest.approx(0.50 * 3100, rel=0.05)
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

def test_valley_in_the_first_five_sweep_points_is_skipped():
    """MPD Monitor.vi V6.8 (AWS): valley index >= 5. LabVIEW-finished P172262504894 CH5 used the
    1.94 V valley, not the one at 0.56 V (sweep point 3)."""
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    txmon = [round(410 + 400 * math.cos(math.pi * (v - 0.56) / 0.69 + math.pi)) for v in volts]
    i_peak, i_valley = tops.peak_valley(txmon)
    assert volts[i_valley] > 1.0 and volts[i_peak] > volts[i_valley]


def test_peak_search_reports_one_entry_per_run_around_the_mean():
    data = [5, 1, 0, 1, 5, 9, 10, 9, 5, 1, 2, 1, 5, 8]          # mean 4.43
    assert tops.peak_search(data, valleys=True) == ([0, 1], [2, 9])
    # the run starting at point 0 is not reported for peaks; the open run at the end is
    assert tops.peak_search(data, valleys=False) == ([10, 8], [6, 13])


def test_the_pair_with_the_deepest_valley_is_used():
    # two rising edges; the second valley is darker -> V6.8 (AWS) uses the second pair
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    txmon = [round(1000 + 900 * math.cos(math.pi * (v - 0.7) / 0.5) - (300 if v > 1.95 else 0)) for v in volts]
    i_peak, i_valley = tops.peak_valley(txmon)              # valleys 1.2 V and 2.2 V (darker)
    assert volts[i_valley] == pytest.approx(2.2, abs=0.03)
    assert volts[i_peak] == pytest.approx(2.7, abs=0.03)


def test_a_peak_on_the_last_sweep_point_is_not_used():
    volts = tops.sweep_points(0.5, 3.0, 0.02)
    txmon = [round(100 + 1000 * (v - 1.5) ** 2) if v > 1.5 else round(100 + 1000 * (1.5 - v)) for v in volts]
    with pytest.raises(ValueError):                 # only valley 1.5 V -> peak at 3.0 V (last point)
        tops.peak_valley(txmon)


def test_equal_3_5_and_3_db_points_are_split_by_one_step():
    # 44 % and 50 % of the peak are both closest to the same step (TXMON 470)
    txmon = [600] * 6 + [10, 20, 470, 900, 1000, 990] + [600] * 2
    volts = [round(0.88 + 0.02 * i, 2) for i in range(len(txmon))]
    points = tops.operating_points(volts, txmon, [0.65, 0.56, 0.50, 0.44], "one_minus_x_of_peak")
    assert points[0.50] - points[0.56] == pytest.approx(0.02)


def test_edge_choice_matches_the_labview_station_on_a_real_module():
    """Sweeps of P172262504894 against the tops_off the LabVIEW V6.8 station left in the
    module (Scan_Disable moves it a few 0.01 V steps). CH3 and CH6 have two valleys of equal
    depth (13 vs 14 and 10 vs 10 counts), so the deepest-valley pick depends on sweep noise."""
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "data" / "p172262504894_mpd_sweep.json").read_text())
    volts = data["volts"]
    for ch in (1, 2, 4, 5, 7, 8):
        _peak, valley = tops.peak_valley(data["curves"][ch - 1])
        assert volts[valley] == pytest.approx(data["labview_tops_off"][ch - 1], abs=0.05), ch
