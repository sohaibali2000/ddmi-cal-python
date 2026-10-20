"""Start-up states against 800G_TRx_Test.vi / Auto Adjust Vcc.vi V6.8."""
import pytest

from ddmi.runctx import StopTest
from ddmi.steps import startup


class FakeSupply:
    """Module voltage = setting - drop; records what was sent."""

    def __init__(self, setting=3.30, drop=0.30, current_a=2.0):
        self.setting, self.drop, self.current_a = setting, drop, current_a
        self.settings, self.limits = [], []

    def set_voltage(self, volts):
        self.setting = volts
        self.settings.append(round(volts, 4))

    def set_current_limit(self, amps):
        self.limits.append(amps)

    def read_output_voltage(self):
        return self.setting

    def read_module_voltage(self):
        return self.setting - self.drop

    def read_current(self):
        return self.current_a


class FakeModule:
    dry_run = False
    is_sim = False


class FakeCtx:
    def __init__(self, product, supply):
        self.product, self.module = product, FakeModule()
        self.bench = type("Bench", (), {"power_supply": supply})()
        self.sleeps, self.logs, self.added = [], [], []
        self.results = type("R", (), {"add": lambda _s, *a: self.added.append(a)})()

    def sleep(self, s):
        self.sleeps.append(s)

    def log(self, text):
        self.logs.append(text)

    detail = log


def test_auto_adjust_vcc_takes_half_steps_like_v68(product):
    supply = FakeSupply(setting=3.30, drop=0.30)        # module at 3.00 V
    ctx = FakeCtx(product, supply)
    actual = startup.auto_adjust_vcc(ctx, 3.3)
    assert abs(actual - 3.3) < 0.01
    # first setting = supply output + (3.3 - 3.0) / 2 = 3.45; then +half the error each time
    assert supply.settings[:3] == [3.45, 3.525, 3.5625]
    assert supply.limits == [6.0]                       # Limit Curr (mA) 6000
    assert ctx.sleeps == [2.0] * len(supply.settings)   # 2000 ms after every supply change


def test_auto_adjust_vcc_refuses_an_implausible_module_reading(product):
    supply = FakeSupply(setting=0.0, drop=0.0)          # supply output off
    with pytest.raises(StopTest, match="Please check your Multimeter"):
        startup.auto_adjust_vcc(FakeCtx(product, supply), 3.3)
    assert supply.settings == []


def test_auto_adjust_vcc_stops_before_a_setting_above_4v(product):
    supply = FakeSupply(setting=3.85, drop=0.9)         # module 2.95 V: needs far more than 4 V
    with pytest.raises(StopTest, match="VCC voltage regulation failed"):
        startup.auto_adjust_vcc(FakeCtx(product, supply), 3.3)
    assert all(v <= 4.0 for v in supply.settings)


def test_icc_must_be_above_the_spec(product):
    ctx = FakeCtx(product, FakeSupply(current_a=1.8))   # exactly 1.8 A: V6.8 uses Greater?
    with pytest.raises(StopTest, match=r"Current 1\.80A failed"):
        startup.set_vcc(ctx)


def test_startup_runs_in_v68_order(station_cfg, product):
    from pathlib import Path

    from ddmi.runner import run_slot
    record = run_slot(station_cfg=station_cfg, product=product, scanned_sn="AWS0001", log=lambda _t: None)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text()
    order = [lines.index(marker) for marker in ("Reini:", "Set_Temp:", "Read SN:", "VCC: adjusted")]
    assert order == sorted(order)
