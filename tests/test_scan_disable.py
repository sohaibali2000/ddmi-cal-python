"""Scan_Disable against Item_Test.vi V6.8 (AWS 2xFR4)."""
import pytest

from ddmi.steps import mpd_scan


class FakeModule:
    """Leakage of a disabled lane = (tops_off - dark_v)^2 curve; tops_off applies on disable."""

    dry_run = False

    def __init__(self, start, dark_v):
        self.tops_off, self.dark_v, self.applied = list(start), dark_v, list(start)
        self.writes = []

    def read(self, name):
        assert name == "tops_off"
        return list(self.tops_off)

    def write(self, name, value):
        self.writes.append((name, list(value)))
        self.tops_off = list(value)

    def write_value(self, name, value_name):
        self.writes.append((name, value_name))
        if value_name == "txdis_all_off":
            self.applied = list(self.tops_off)


class FakeCtx:
    def __init__(self, product, module):
        self.product, self.module, self.channels = product, module, list(range(1, 9))
        self.meta, self.added, self.logs = {}, [], []
        self.results = type("R", (), {"add": lambda _s, *a: self.added.append(a)})()

    def true_tx_powers(self):
        return {ch: round(-40 + 2000 * (self.module.applied[ch - 1] - self.module.dark_v[ch - 1]) ** 2, 3)
                for ch in self.channels}

    def sleep(self, _s):
        pass

    def check_abort(self):
        pass

    def on_data(self, *_a):
        pass

    def log(self, text):
        self.logs.append(text)

    detail = log


def _run(product, start, dark_v, monkeypatch):
    monkeypatch.setattr(mpd_scan, "relock", lambda *_a, **_k: None)
    product["tx_disable"].update(min_s=0, stable_readings=1)
    module = FakeModule(start, dark_v)
    ctx = FakeCtx(product, module)
    mpd_scan.scan_disable(ctx)
    points = [v for name, v in module.writes if name == "tops_off"]
    result = {a[0]: {} for a in ctx.added}
    for item, ch, value in ctx.added:
        result[item][ch] = value
    return points, result


def test_v68_points_direction_walk_and_darkest(product, monkeypatch):
    start = [1.50] * 8
    # CH1 darkest at 1.53 (walks up), CH2 at 1.46 (walks down), CH3 at 1.50 (start is best)
    dark = [1.53, 1.46, 1.50] + [1.50] * 5
    points, result = _run(product, start, dark, monkeypatch)
    ch1 = [round(p[0], 4) for p in points]
    ch2 = [round(p[1], 4) for p in points]
    # start, +0.01, -0.01, then (start +/- 0.01) +/- k*0.01 for k = 1..4, then the darkest
    assert ch1 == [1.5, 1.51, 1.49, 1.52, 1.53, 1.54, 1.55, 1.53]
    assert ch2 == [1.5, 1.51, 1.49, 1.48, 1.47, 1.46, 1.45, 1.46]
    # V6.8 walks down when +0.01 is not darker than the start, even if the start is best
    assert [round(p[2], 4) for p in points][3:7] == [1.48, 1.47, 1.46, 1.45]
    assert result["Tops_off(V)"][1] == 1.53 and result["Tops_off(V)"][2] == 1.46
    assert result["Tops_off(V)"][3] == 1.5
    assert len(points) == 8                      # no lane above -18 dBm: no extra steps


def test_extra_five_steps_move_every_lane(product, monkeypatch):
    start = [1.50] * 8
    dark = [1.70] + [1.50] * 7                  # CH1 too far: still above -18 dBm after 4 steps
    points, result = _run(product, start, dark, monkeypatch)
    assert len(points) == 8 + 5 + 1
    extra = points[8:13]
    assert [round(p[0], 4) for p in extra] == [1.56, 1.57, 1.58, 1.59, 1.6]
    assert [round(p[1], 4) for p in extra] == [1.44, 1.43, 1.42, 1.41, 1.40]   # other lanes step too
    assert result["Tops_off(V)"][1] == 1.6 and result["Tops_off(V)"][2] == 1.5
