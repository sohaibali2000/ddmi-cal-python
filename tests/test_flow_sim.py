import json
import threading
from pathlib import Path

import pytest

from ddmi.runner import run_slot
from ddmi.station import build_station

QUIET = lambda _text: None


def run(station_cfg, product, **kwargs):
    kwargs.setdefault("scanned_sn", "AWS0001")
    kwargs.setdefault("log", QUIET)
    return run_slot(station_cfg=station_cfg, product=product, **kwargs)


def values(record, item):
    return [cell["value"] for cell in record["results"][item].values()]


def test_full_run_passes_with_tight_errors(station_cfg, product):
    record = run(station_cfg, product)
    assert record["result"] == "PASS", record["failing_items"]
    assert max(abs(v) for v in values(record, "dTxP(dB)")) < 0.1
    assert max(abs(v) for v in values(record, "dRxP(dB)")) < 0.1
    assert abs(values(record, "dVcc(V)")[0]) < 0.005
    assert len(values(record, "Tops_Setpoint")) == 8
    assert all(-60 <= v <= -16 for v in values(record, "Tx_Disable(dBm)"))
    assert record["sn"] == "AWS0001" and record["simulated"] is True
    assert record["mes"] == {"checked_in": True, "saved": True, "checked_out": True}


def test_every_documented_limit_is_applied(station_cfg, product):
    record = run(station_cfg, product)
    assert record["limits"] == {
        "Rxp_Slope": [5, 30], "Tx_Disable(dBm)": [-60, -16], "dTxP(dB)": [-0.5, 0.5],
        "dRxP(dB)": [-0.5, 0.5], "dVcc(V)": [-0.033, 0.033], "DDMI_Temp(C)": [30, 60],
        # from the production CSV's MAX/MIN rows
        "Tops_Setpoint": [100, 2500], "Bias_setpoint": [100, 2500], "Tops_Mid(V)": [0, 3], "Tops_off(V)": [0, 3],
        "TxP(dBm)": [-3.3, 10000], "DDMI_TxP(dBm)": [-3.8, 10000],
        "Txp_Offset": [-10000, 10000], "Txp_Slope": [5, 150], "Rxp_Offset": [-10000, 10000],
        "Real_Vcc(V)": [3.234, 3.366], "Vcc_Offset": [-10000, 10000], "Vcc_Slope": [15, 30],
    }
    assert all(5 <= v <= 30 for v in values(record, "Rxp_Slope"))


def test_rx_slope_outside_its_limit_fails(station_cfg, product):
    product["limits"]["rx_slope"] = [0.1, 1]
    record = run(station_cfg, product)
    assert record["result"] == "FAIL"
    assert record["failing_items"] == [f"Rxp_Slope_CH{ch}" for ch in range(1, 9)]


def test_mpd_scan_curves_are_streamed_and_saved_for_the_graphs(station_cfg, product):
    events = []
    record = run(station_cfg, product, on_data=lambda kind, data: events.append(kind))
    assert events[0] == "mpd_start" and events.count("mpd_point") == 126
    assert "mpd_result" in events and events.count("mpd_tops_off") == 8
    scan = record["mpd_scan"]
    assert len(scan["volts"]) == 126 and all(len(curve) == 126 for curve in scan["curves"])
    lane = scan["lanes"][1]
    assert lane["setpoint"] == values(record, "Tops_Setpoint")[0]
    assert len(lane["points"]) == 4 and lane["mid_v"] is not None and lane["leakage_dbm"] < -16


def test_record_log_and_csv_are_saved(station_cfg, product):
    record = run(station_cfg, product)
    root = Path(station_cfg["results_dir"])
    saved = json.loads(next((root / "records").glob("AWS0001_*.json")).read_text())
    assert saved["result"] == "PASS" and saved["limits"]["dTxP(dB)"] == [-0.5, 0.5]
    assert len(next((root / "records").glob("*.csv")).read_text().splitlines()) > 100
    log = (root / record["log_file"]).read_text()
    assert "I2C W Vtops" in log and "sweep CH1 TXMON" in log     # raw values are logged


def test_module_is_left_calibrated_and_in_normal_operation(station_cfg, product):
    station = build_station(station_cfg, sim_sn="AWS0001")
    run(station_cfg, product, station=station)
    sim = station.sim_module
    assert sim.tops_loop and sim.bias_loop and sim.tx_dis == 0
    assert sim.vcc_cal[0] == pytest.approx(10000 / 430, abs=0.5)     # 0.1 mV per ADC count
    assert sim.modes["AWS_Test_Mode"] == 0 and sim.modes["TestMode"] == 0xCC   # V6.8 end state
    assert station.sim_bench.v_set == pytest.approx(3.3, abs=0.1)


def test_v68_showpf_cleanup(station_cfg, product):
    """800G_TRx_Test.vi V6.8 ShowPF, AWS DDMI CAL: Temp Off Func 59, TestMode 0xCC, reset,
    DSP modes 0. AWS_Test_Mode stays at the 0 Set_Temp wrote."""
    product["showpf"] = {"mode_cleanup": "labview"}
    station = build_station(station_cfg, sim_sn="AWS0001")
    run(station_cfg, product, station=station)
    assert station.sim_module.modes == {"AWS_Test_Mode": 0, "TestMode": 0xCC, "DSP_TestMode": 0,
                                        "DSP_OperatingMode": 0, "TempOffFunc": 59}


def test_registers_are_backed_up_before_the_run_and_can_be_restored(station_cfg, product):
    import json

    from ddmi import backup

    station = build_station(station_cfg, sim_sn="AWS0001")
    sim = station.sim_module
    original = (list(sim.tx_cal[0]), list(sim.tx_cal[1]), sim.vcc_cal, list(sim.setpoint))
    record = run(station_cfg, product, station=station)
    assert record["result"] == "PASS"
    saved = json.loads(Path(record["backup_file"]).read_text())
    assert saved["sn"] == "AWS0001" and set(saved["registers"]) == set(backup.BACKUP_REGISTERS)
    assert (list(sim.tx_cal[0]), sim.vcc_cal) != (original[0], original[2])     # the run changed them
    station.module.log = lambda _t: None
    backup.restore(station.module, saved["registers"])
    assert sim.tx_cal[0] == pytest.approx(original[0]) and sim.tx_cal[1] == pytest.approx(original[1])
    assert sim.vcc_cal == pytest.approx(original[2]) and sim.setpoint == original[3]


def test_loops_come_back_with_the_reset_after_the_scan(station_cfg, product):
    """MPD Monitor.vi V6.8 never writes TOPPID back on: the W_Reset at the end of the scan does it."""
    station = build_station(station_cfg, sim_sn="AWS0001")
    record = run(station_cfg, product, station=station)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text().splitlines()
    toppid_writes = [i for i, l in enumerate(lines) if "I2C W W_TOPPID" in l]
    assert all(lines[i].endswith("-> 00") for i in toppid_writes)
    assert any("I2C W W_Reset" in l for l in lines[toppid_writes[0]:])
    assert station.sim_module.tops_loop and station.sim_module.bias_loop


def test_dead_tx_lane_fails(station_cfg, product):
    record = run(station_cfg, product, fault="dead_tx_lane")
    assert record["result"] == "FAIL"
    assert any(item.endswith("_CH3") for item in record["failing_items"])
    assert not any(item.endswith(f"_CH{ch}") for item in record["failing_items"] for ch in (1, 2, 4))


def test_rx_nonlinear_lane_fails_only_the_rx_check(station_cfg, product):
    record = run(station_cfg, product, fault="rx_nonlinear")
    assert record["result"] == "FAIL"
    assert record["failing_items"] == ["dRxP(dB)_CH3"]


def test_sn_mismatch_stops_the_run(station_cfg, product):
    record = run(station_cfg, product, fault="sn_mismatch")
    assert record["result"] == "FAIL"
    assert record["failing_items"] == ["Inconsistent internal and external SN"]
    assert "dTxP(dB)" not in record["results"]


def test_missing_rx_table_fails_with_a_clear_reason(station_cfg, product, tmp_path):
    station_cfg["rx_table_dir"] = str(tmp_path / "empty")
    station = build_station(station_cfg, sim_sn="AWS0001")
    mes = FakeMes()
    record = run(station_cfg, product, station=station, mes=mes)
    assert record["result"] == "NOT_TESTED"
    assert "RX table missing" in record["failing_items"][0]
    assert mes.calls == [] and station.sim_module.modes["AWS_Test_Mode"] == 0   # module untouched


def test_abort_returns_the_module_to_a_safe_state(station_cfg, product):
    station = build_station(station_cfg, sim_sn="AWS0001")
    abort = threading.Event()

    def on_step(name, state):
        if name == "TX Cal" and state == "running":
            abort.set()

    record = run(station_cfg, product, station=station, abort=abort, on_step=on_step)
    assert record["result"] == "ABORTED"
    assert station.sim_module.tops_loop and station.sim_module.bias_loop
    assert station.sim_module.tx_dis == 0
    assert record["mes"]["saved"] is False


def test_dry_run_sends_no_writes(station_cfg, product):
    station = build_station(station_cfg, sim_sn="AWS0001", dry_run=True)
    before = dict(station.sim_module.modes), list(station.sim_module.setpoint)
    record = run(station_cfg, product, station=station)
    assert record["dry_run"] is True
    assert (station.sim_module.modes, station.sim_module.setpoint) == before
    assert station.sim_module.resets == 0


def test_stage_callbacks_cover_every_stage_in_order(station_cfg, product):
    seen = []
    run(station_cfg, product, on_step=lambda name, state: seen.append((name, state)))
    started = [name for name, state in seen if state == "running"]
    assert list(dict.fromkeys(started)) == ["Startup", "Scan MPD", "TX Cal", "RX Cal", "VCC Cal", "Final"]


class FakeMes:
    def __init__(self, check_in_ok=True, save_ok=True, spec=None):
        self.check_in_ok, self.save_ok, self.spec = check_in_ok, save_ok, spec
        self.calls = []

    def check_in(self, sn):
        self.calls.append("check_in")
        return self.check_in_ok, "not routed to this station"

    def read_date(self):
        self.calls.append("read_date")
        return "2026-01-01"

    def read_spec(self, pn, station):
        self.calls.append("read_spec")
        return self.spec

    def read_test_times(self, sn):
        self.calls.append("read_test_times")
        return 0

    def save_data(self, record):
        self.calls.append("save_data")
        return self.save_ok, "server down"

    def check_out(self, sn, passed):
        self.calls.append("check_out")
        return True, ""


def test_mes_order_before_and_after(station_cfg, product):
    mes = FakeMes()
    run(station_cfg, product, mes=mes)
    assert mes.calls == ["check_in", "read_date", "read_spec", "read_test_times", "save_data", "check_out"]


def test_failed_check_in_means_no_test_and_no_saved_result(station_cfg, product):
    mes = FakeMes(check_in_ok=False)
    record = run(station_cfg, product, mes=mes)
    assert record["result"] == "NOT_TESTED" and record["results"] == {}
    assert mes.calls == ["check_in"]
    assert not (Path(station_cfg["results_dir"]) / "records").exists()


def test_check_out_is_blocked_when_save_fails(station_cfg, product):
    mes = FakeMes(save_ok=False)
    record = run(station_cfg, product, mes=mes)
    assert "check_out" not in mes.calls
    assert record["mes"] == {"checked_in": True, "saved": False, "checked_out": False}


def test_mes_spec_overrides_the_config_limits(station_cfg, product):
    record = run(station_cfg, product, mes=FakeMes(spec={"ddmi_temp_c": [50, 60]}))
    assert record["result"] == "FAIL" and record["failing_items"] == ["DDMI_Temp(C)"]
    assert record["limits"]["DDMI_Temp(C)"] == [50, 60]


def test_lanes_share_attenuator_channels_like_the_lab_station(station_cfg):
    att = build_station(station_cfg).bench.attenuator
    assert att.physical_channels == [1, 2]
    assert [att.channel(lane) for lane in range(1, 9)] == [1, 1, 1, 1, 2, 2, 2, 2]


def test_minimum_attenuation_protects_the_receiver(station_cfg, product):
    from ddmi.hw.base import LaneAttenuator

    class Recorder:
        def __init__(self):
            self.calls = []

        def set_wavelength(self, ch, nm):
            self.calls.append(("wl", ch, nm))

        def set_attenuation(self, ch, db):
            self.calls.append(("att", ch, db))

    rec = Recorder()
    att = LaneAttenuator(rec, [1, 1, 1, 1, 2, 2, 2, 2], min_att_db=3.0)
    att.set_attenuation(6, 7.5)
    assert rec.calls == [("att", 2, 7.5)]
    with pytest.raises(ValueError, match="below the station minimum"):
        att.set_attenuation(1, 2.0)
    assert rec.calls == [("att", 2, 7.5)]       # nothing sent

    # a run that would need less than the minimum stops instead of overdriving the DUT
    station_cfg["attenuator"]["min_att_db"] = 5.0
    record = run(station_cfg, product)
    assert record["result"] == "FAIL"
    assert "below the station minimum" in record["failing_items"][0]


def test_dry_run_never_touches_mes(station_cfg, product):
    station = build_station(station_cfg, sim_sn="AWS0001", dry_run=True)
    mes = FakeMes()
    record = run(station_cfg, product, station=station, mes=mes)
    assert mes.calls == []
    assert record["mes"] == {"checked_in": False, "saved": False, "checked_out": False}
    assert (Path(station_cfg["results_dir"]) / "records").exists()      # still recorded locally


def test_ignored_vendor_writes_leave_every_calibration_untouched(station_cfg, product):
    """The 2026-10-15 lab run: the module discarded every vendor-page write."""
    station = build_station(station_cfg, sim_sn="AWS0001", fault="vendor_writes_ignored")
    sim = station.sim_module
    before = (list(sim.tx_cal[0]), list(sim.tx_cal[1]), list(sim.rx_cal[0]), sim.vcc_cal,
              list(sim.setpoint), list(sim.tops_off))
    record = run(station_cfg, product, station=station)
    after = (list(sim.tx_cal[0]), list(sim.tx_cal[1]), list(sim.rx_cal[0]), sim.vcc_cal,
             list(sim.setpoint), list(sim.tops_off))
    # The run stops instead of reporting a calibration that never reached the module: at the
    # first calibration read-back, or earlier when the module gives no light for Adjust Power.
    assert record["result"] in ("ERROR", "FAIL")
    assert after == before
    assert any("discarded the write" in f or "Bias setpoints not changed" in f
               for f in record["failing_items"]), record["failing_items"]



def test_production_csv_layout(station_cfg, product, tmp_path):
    from ddmi.prod_csv import HEADER, append_run
    record = run(station_cfg, product)
    assert record["result"] == "PASS"
    path = append_run(record, tmp_path)
    append_run(record, tmp_path)                       # second module: no second header
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.name.endswith("_DDMI2XFR4.csv")
    assert lines[0].split(",") == HEADER
    assert len(lines) == 1 + 2 * 10
    rows = [line.split(",") for line in lines[1:11]]
    assert [r[0] for r in rows] == ["MAX", "MIN"] + [f"CH{c}" for c in range(1, 9)]
    assert all(len(r) == len(HEADER) for r in rows)
    col = {name: i for i, name in enumerate(HEADER)}
    assert rows[0][col["Tx_Disable(dBm)"]] == "-16" and rows[1][col["Tx_Disable(dBm)"]] == "-60"
    assert rows[0][col["DDMI_Vcc"]] == "/"
    ch1 = rows[2]
    assert ch1[col["Result"]] == "Pass" and ch1[col["error item"]] == "0"
    assert ch1[col["RxP1"]] == "-6"
    assert ch1[col["TXP_MPDOut_3dB"]] != ""
    assert rows[2][col["I2C_Vcc"]] == rows[9][col["I2C_Vcc"]]   # module value on every channel


def test_unlocked_tops_loop_is_relocked(station_cfg, product):
    """2026-10-15: a lane's loop sat at the fringe top after a reset until Vtops was rewritten."""
    station = build_station(station_cfg, sim_sn="AWS0001", fault="tops_unlocked")
    record = run(station_cfg, product, station=station)
    log = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text(encoding="utf-8")
    assert record["result"] == "PASS", record["failing_items"]
    assert "TOPS lock after the final reset: relocked" in log
    assert not station.sim_module.stuck            # the module leaves the station locked


def test_without_the_lock_check_a_stuck_lane_would_go_unnoticed(station_cfg, product, monkeypatch):
    """Why the final check exists: the TX check compares DDMI with the meter, and a lane
    stuck at full power reads high on both, so nothing else flags it."""
    from ddmi.steps import mpd_scan, showpf, tx_cal
    for mod in (mpd_scan, showpf, tx_cal):
        monkeypatch.setattr(mod, "relock", lambda *a, **k: None)
    station = build_station(station_cfg, sim_sn="AWS0001", fault="tops_unlocked")
    run(station_cfg, product, station=station)
    assert station.sim_module.stuck


def test_lane_that_never_locks_fails_the_run(station_cfg, product, monkeypatch):
    from ddmi.steps import tops_lock
    monkeypatch.setattr(tops_lock, "unlocked_lanes", lambda ctx: {1: "ratio 29000 vs setpoint 587"})
    record = run(station_cfg, product)
    assert record["result"] == "FAIL"
    assert any(f.startswith("TOPS loop not locked CH1") for f in record["failing_items"])


def test_bias_setpoint_is_read_per_laser_into_the_csv(station_cfg, product, tmp_path):
    from ddmi.prod_csv import HEADER, append_run
    record = run(station_cfg, product)
    bias = values(record, "Bias_setpoint")
    # Adjust Power: default [280, 280, 410, 450], +20 / -30 per laser; lanes n and n+4 share one
    assert bias[:4] == bias[4:]
    assert all(b - d in (20, 0, -30) for b, d in zip(bias, [280, 280, 410, 450]))
    rows = [r.split(",") for r in append_run(record, tmp_path).read_text().splitlines()]
    col = HEADER.index("Bias_setpoint")
    assert [r[col] for r in rows[3:11]] == [str(b) for b in bias]
    assert rows[1][col] == "2500" and rows[2][col] == "100"


# ---- V6.8 logic read from the VIs (2026-10-16) ----

class _FakeCtx:
    """Just enough RunContext for one step: powers per lane, writes recorded."""

    def __init__(self, product, powers):
        self.product, self._powers, self.writes, self.logs = product, powers, [], []
        self.channels = list(range(1, 9))
        outer = self

        class _Module:
            def write(self, name, value):
                outer.writes.append((name, list(value) if isinstance(value, list) else value))

        self.module = _Module()

    def sleep(self, _s):
        pass

    def true_tx_powers(self):
        return dict(self._powers)

    def log(self, text):
        self.logs.append(text)

    detail = log


def test_adjust_power_steps_each_laser_by_its_lane_pair_mean(product):
    from ddmi.steps.mpd_scan import adjust_power
    # laser 1 (CH1/5) low, laser 2 (CH2/6) in window, laser 3 (CH3/7) high, laser 4 (CH4/8) in window
    powers = {1: 0.2, 5: 0.6, 2: 1.5, 6: 1.6, 3: 2.6, 7: 2.5, 4: 2.4, 8: 2.0}
    ctx = _FakeCtx(product, powers)
    adjust_power(ctx)
    written = [w for w in ctx.writes if w[0].startswith("W_Bias_setpoint")]
    assert [w[1] for w in written[:3]] == [[280, 280, 410, 450]] * 3        # default to RT, HT, LT
    assert [w[1] for w in written[3:]] == [[300, 280, 380, 450]] * 3        # +20 low, -30 high
    assert [w[0] for w in written[3:]] == ["W_Bias_setpoint", "W_Bias_setpointHT", "W_Bias_setpointLT"]


def test_scan_disable_takes_v68_steps(station_cfg, product):
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if "Scan_Disable: all lanes" in l)
    j = next(n for n, l in enumerate(lines) if n > i and "CH1: tops_off" in l)
    toggles = [l for l in lines[i:j] if "I2C W W_TXDis" in l and l.endswith("-> ff")]
    # start, +step, -step, 4 toward darker, final = 8 disables (no lane above -18 dBm)
    assert len(toggles) == 8
    assert all(v <= -16 for v in values(record, "Tx_Disable(dBm)"))


def test_set_temp_writes_aws_test_mode_0(station_cfg, product):
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text()
    assert "I2C W AWS_Test_Mode [EEh/0x80/1] = 0 -> 00" in lines
    assert "= 1 -> 01" not in "".join(l for l in lines.splitlines() if "AWS_Test_Mode" in l)


def test_wait_temp_stops_a_module_that_stays_cold(station_cfg, product):
    station = build_station(station_cfg, sim_sn="AWS0001")
    station.sim_module.temp_c = 20.0
    product["wait_temp"].update(enabled=True, timeout_s=10)     # skipped at DDMI Cal by default
    record = run(station_cfg, product, station=station)
    assert record["result"] == "FAIL"
    assert any(f.startswith("Wait_Temp") for f in record["failing_items"])


def test_failed_ddmi_item_is_run_once_more(station_cfg, product):
    product["limits"]["rx_slope"] = [0.1, 1]            # every lane fails the RX slope
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text()
    assert lines.count("RX Cal: Rxp_Slope_CH1") == 1    # one retry, not a loop
    assert lines.count("W_DRXPCal written") == 2


def test_tops_setpoint_is_written_after_the_scan_reset(station_cfg, product):
    """MPD Monitor.vi V6.8 last frame (error wire): Vtops, tops_off, 2 s, W_Reset, 10 s, setpoint."""
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text().splitlines()
    first_reset = next(i for i, l in enumerate(lines) if "I2C W W_Reset" in l)
    setpoint = next(i for i, l in enumerate(lines) if "I2C W tops_setpoint" in l)
    assert setpoint > first_reset
    assert not any("Wait_Temp" in l for l in lines)          # skipped at DDMI Cal


def test_dr8_adjust_power_values_are_refused(product):
    """Item_Test.vi V6.8 also has the MPO-DR8 branch ([300, 350], 1.0-2.6 dBm, 2 lasers)."""
    import copy
    from ddmi.config import ConfigError, validate_product
    bad = copy.deepcopy(product)
    bad["adjust_power"]["default_setpoint"] = [300, 350]
    with pytest.raises(ConfigError):
        validate_product(bad)
    assert product["adjust_power"]["default_setpoint"] == [280, 280, 410, 450]
    assert product["adjust_power"]["window_dbm"] == [0.7, 2.3]


def test_txp_mpdout_is_the_monitor_ratio_rising_toward_the_peak(station_cfg, product):
    """V6.8: TXP_MPDOut = TXMON / MPD_OUT x 1000 at 4.5 / 3.5 / 3.0 / 2.5 dB below the peak."""
    record = run(station_cfg, product)
    for lane in record["mpd_scan"]["lanes"].values():
        r = lane["mpd_out"]
        # brighter -> higher ratio (neighbouring points can share a 0.02 V step: allow noise)
        assert r["0.65"] < r["0.44"]
        assert r["0.65"] <= r["0.56"] + 5 and r["0.56"] <= r["0.5"] + 5 and r["0.5"] <= r["0.44"] + 5
        assert min(r["0.56"], r["0.5"]) - 5 <= lane["setpoint"] <= max(r["0.56"], r["0.5"]) + 5


def test_adjust_power_runs_on_the_first_scan_pass_only(station_cfg, product):
    product["limits"]["tx_disable_dbm"] = [-60, -99]     # every lane fails -> second pass
    record = run(station_cfg, product)
    log = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text()
    assert log.count("Scan_MPD: TOPS and bias loops off") == 2
    assert log.count("Adjust Power:") == 1


def test_missing_tx_loss_stops_before_check_in(station_cfg, product):
    Path(station_cfg["tx_loss_file"]).unlink()
    record = run(station_cfg, product)
    assert record["result"] != "PASS"
    assert any("Tx Loss missing" in f for f in record["failing_items"])
    assert "results" not in record or not record["results"]


def test_vtops_not_above_tops_off_rescans_then_fails(station_cfg, product, monkeypatch):
    from ddmi.steps import mpd_scan
    calls = []
    monkeypatch.setattr(mpd_scan, "_vtops_not_above_off", lambda ctx: calls.append(1) or [3])
    record = run(station_cfg, product)
    assert len(calls) == 2                                # scanned twice (V6.8 loop of 2)
    assert "Scan_MPD CH3: Vtops not above tops_off after the scan" in record["failing_items"]


def test_mes_gets_the_module_pn_for_read_spec(station_cfg, product, tmp_path):
    from ddmi.mes.offline import OfflineMes

    class _Mes(OfflineMes):
        pns = []

        def read_spec(self, pn, station):
            self.pns.append(pn)
            return super().read_spec(pn, station)

    mes = _Mes(tmp_path / "mes")
    record = run(station_cfg, product, mes=mes)
    assert mes.pns and mes.pns[0] and mes.pns[0] == record["pn"]


def test_adjust_power_leaves_the_bias_alone_without_light(product):
    """2026-10-19 15:41: TX fibre off (-79 dBm on every lane) still bumped the setpoints."""
    from ddmi.runctx import StopTest
    from ddmi.steps.mpd_scan import adjust_power
    ctx = _FakeCtx(product, {ch: -79.5 for ch in range(1, 9)})
    with pytest.raises(StopTest, match="TX fibre"):
        adjust_power(ctx)
    assert not [w for w in ctx.writes if w[0].startswith("W_Bias_setpoint")]


def test_mpd_scan_frame_0_runs_in_v68_order(station_cfg, product):
    """800G SIP Tx MPD Monitor.vi V6.8 frame 0: IBiasPID 0, TOPPID 0, 1 s, Vtops 1.0 V, tops_off 0."""
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text().splitlines()
    start = next(n for n, l in enumerate(lines) if "=== Scan MPD ===" in l)
    writes = [l.split("I2C W ")[1].split(" ")[0] for l in lines[start:] if "I2C W " in l][:4]
    assert writes == ["W_IBiasPID", "W_TOPPID", "Vtops", "tops_off"]


def test_rx_pairs_wait_att_delay_plus_2_s(station_cfg, product):
    """DDMI_Cal.vi / Item_Test.vi V6.8: after setting a lane pair, wait "ATT Delay(s)" + 2 s."""
    from ddmi.steps import rx_cal
    product["rx_cal"]["att_delay_s"] = 0.5
    assert rx_cal._pair_wait_s(product["rx_cal"]) == 2.5
    assert rx_cal._pairs(type("C", (), {"channels": list(range(1, 9))})()) == [(1, 5), (2, 6), (3, 7), (4, 8)]
