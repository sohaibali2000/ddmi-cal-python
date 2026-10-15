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
    before = dict(station.sim_module.modes)
    run(station_cfg, product, station=station)
    sim = station.sim_module
    assert sim.tops_loop and sim.bias_loop and sim.tx_dis == 0
    assert sim.vcc_cal[0] == pytest.approx(1.25, abs=0.01)
    assert sim.modes == before                  # mode registers end exactly as they started
    assert station.sim_bench.v_set == pytest.approx(3.3, abs=0.1)


def test_labview_mode_cleanup_is_still_available(station_cfg, product):
    product["showpf"] = {"mode_cleanup": "labview"}
    station = build_station(station_cfg, sim_sn="AWS0001")
    run(station_cfg, product, station=station)
    assert station.sim_module.modes == {"AWS_Test_Mode": 1, "TestMode": 0xCC, "DSP_TestMode": 0,
                                        "DSP_OperatingMode": 0, "TempOffFunc": 0}


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


def test_loops_are_restored_to_the_values_read_before_the_run(station_cfg, product):
    record = run(station_cfg, product)
    lines = (Path(station_cfg["results_dir"]) / record["log_file"]).read_text().splitlines()
    toppid_writes = [l for l in lines if "I2C W W_TOPPID" in l]
    assert toppid_writes[-1].endswith("-> ff")      # back to 0xFF, as read, not to a constant 1


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
    # Caught on the first calibration read-back: the run stops instead of reporting
    # a calibration that never reached the module.
    assert record["result"] == "ERROR"
    assert after == before
    assert any("discarded the write" in f for f in record["failing_items"]), record["failing_items"]


def test_pass_is_saved_as_excel(station_cfg, product, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    station_cfg = dict(station_cfg, excel_dir=str(tmp_path / "xlsx"))
    record = run(station_cfg, product)
    assert record["result"] == "PASS"
    files = list((tmp_path / "xlsx").glob("*_PASS.xlsx"))
    assert len(files) == 1
    ws = openpyxl.load_workbook(files[0]).active
    rows = {r[0]: r for r in ws.iter_rows(values_only=True) if r and r[0]}
    assert rows["SN"][1] == record["sn"]
    assert rows["Result"][1] == "PASS"
    vcc = record["results"]["Real_Vcc(V)"]["module"]["value"]
    assert list(rows["Real_Vcc(V)"][1:9]) == [vcc] * 8          # module value in all 8 columns
    assert rows["dTxP(dB)"][1] == record["results"]["dTxP(dB)"]["ch1"]["value"]
