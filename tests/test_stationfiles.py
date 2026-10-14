import shutil

import pytest

from ddmi import stationfiles
from ddmi.station import build_station
from ddmi.tools.rx_table import ATT_STEPS_DB, build_rx_table
from ddmi.mes.golden import fetch_golden_powers
from ddmi.tools.tx_loss import calibrate_tx_loss, measure_tx_loss

LOSSES = [1.5, 1.62, 1.4, 1.71, 1.33, 1.8, 1.55, 1.49]
QUIET = lambda _text: None


def test_slot_file_names():
    assert stationfiles.tx_loss_path(1).name == "system_Data.ini"
    assert stationfiles.tx_loss_path(2).name == "system_Data2.ini"
    assert stationfiles.rx_table_dir(2).name == "slot2"


def test_tx_loss_round_trip_keeps_the_rest_of_the_station_ini(tmp_path):
    path = tmp_path / "system_Data.ini"
    shutil.copy(stationfiles.TX_LOSS_FILE, path)        # the ini copied from the LabVIEW station
    before = path.read_text()
    assert stationfiles.read_tx_loss(path) is None      # the station file has no Tx Loss yet
    stationfiles.write_tx_loss(path, LOSSES)
    assert stationfiles.read_tx_loss(path) == LOSSES
    stationfiles.write_tx_loss(path, [v + 1 for v in LOSSES])       # replaces, does not duplicate
    text = path.read_text()
    assert text.count("Tx Loss") == 1
    assert 'Test_Title="800G_DDMI_Cal-Left"' in text and before.splitlines()[0] in text


@pytest.mark.parametrize("separator", ["\t", "\\t", "\\09"])
def test_tx_loss_accepts_each_tab_spelling(tmp_path, separator):
    path = tmp_path / "x.ini"
    path.write_text('[Data]\nTx Loss="' + separator.join(str(v) for v in LOSSES) + '"\n')
    assert stationfiles.read_tx_loss(path) == LOSSES


def test_tx_loss_reads_the_format_seen_on_a_station(tmp_path):
    # as in the vendor's system.ini: spaces around "=", negative values, escaped CR LF at the end
    path = tmp_path / "system.ini"
    path.write_text('[Data]\nTx Loss = "-0.384\t-0.188\t-0.097\t0.085\t0.854\t0.949\t1.141\t1.168\\0D\\0A"\n')
    assert stationfiles.read_tx_loss(path) == [-0.384, -0.188, -0.097, 0.085, 0.854, 0.949, 1.141, 1.168]


def test_tx_loss_with_wrong_count_is_ignored(tmp_path):
    path = tmp_path / "x.ini"
    path.write_text('[Data]\nTx Loss="1.0\t2.0"\n')
    assert stationfiles.read_tx_loss(path) is None
    with pytest.raises(ValueError):
        stationfiles.write_tx_loss(path, [1.0, 2.0])


def test_tx_loss_calibration_recovers_the_path_loss(station_cfg, product):
    rows = calibrate_tx_loss(station_cfg, product, slot=1, log=QUIET)
    station = build_station(station_cfg, slot=1)
    assert [row["loss_db"] for row in rows] == pytest.approx(station.sim_bench.tx_path_loss, abs=0.03)
    assert [row["wavelength_nm"] for row in rows] == [1270, 1290, 1310, 1330] * 2


# The production system.ini, byte for byte: CR LF lines, spaces around "=", real tabs,
# the literal \0D\0A suffix inside the quotes, no newline at the very end.
PRODUCTION_INI = (
    '[Data]\r\nType = "3"\r\nDevice = "4\tCOM11\t0\t\t0\\0D\\0A"\r\n'
    'SZ = "1.00\t1.00\t0.00\\0D\\0A"\r\n'
    'Tx Loss = "0.845\t0.545\t1.303\t0.618\t1.253\t1.398\t1.135\t1.310\\0D\\0A"\r\n'
    'Icc Cal = "0.001\t0.000\t0.001\\0D\\0A"'
)


def test_production_ini_is_read(tmp_path):
    path = tmp_path / "system.ini"
    path.write_bytes(PRODUCTION_INI.encode())
    assert stationfiles.read_tx_loss(path) == [0.845, 0.545, 1.303, 0.618, 1.253, 1.398, 1.135, 1.31]


def test_writing_changes_only_the_tx_loss_payload(tmp_path):
    path = tmp_path / "system.ini"
    path.write_bytes(PRODUCTION_INI.encode())
    stationfiles.write_tx_loss(path, [0.845, 0.545, 1.303, 0.618, 1.253, 1.398, 1.135, 1.310])
    assert path.read_bytes() == PRODUCTION_INI.encode()          # same values -> identical file
    stationfiles.write_tx_loss(path, [-0.384, -0.188, -0.097, 0.085, 0.854, 0.949, 1.141, 1.168])
    expected = PRODUCTION_INI.replace(
        "0.845\t0.545\t1.303\t0.618\t1.253\t1.398\t1.135\t1.310",
        "-0.384\t-0.188\t-0.097\t0.085\t0.854\t0.949\t1.141\t1.168")
    assert path.read_bytes() == expected.encode()


def test_a_file_without_tx_loss_gets_the_line_under_data(tmp_path):
    path = tmp_path / "system_Data.ini"
    path.write_bytes(b'[Program]\r\nTest_VI="800G_TRx_Test.vi"\r\n')
    stationfiles.write_tx_loss(path, LOSSES)
    assert path.read_bytes().endswith(
        b'[Data]\r\nTx Loss = "1.500\t1.620\t1.400\t1.710\t1.330\t1.800\t1.550\t1.490\\0D\\0A"\r\n')
    assert stationfiles.read_tx_loss(path) == LOSSES


class FakeMeter:
    def __init__(self, readings):
        self.readings, self.configured = readings, None

    def configure(self, wavelengths_nm, avg_time_ms):
        self.configured = (list(wavelengths_nm), avg_time_ms)

    def read_all_dbm(self):
        return list(self.readings)


def test_measure_tx_loss_is_golden_minus_measured():
    meter = FakeMeter([-1.0, -2.0, -0.5, 0.0, -1.2, -1.4, -0.9, -1.1])
    golden = {ch: 0.5 for ch in range(1, 9)}
    rows = measure_tx_loss(meter, golden, [1270, 1290, 1310, 1330] * 2, 100, log=QUIET)
    assert meter.configured == ([1270, 1290, 1310, 1330] * 2, 100)
    assert [r["loss_db"] for r in rows] == [1.5, 2.5, 1.0, 0.5, 1.7, 1.9, 1.4, 1.6]


class FakeCursor:
    def __init__(self, rows_by_test):
        self.rows_by_test, self.result = rows_by_test, None

    def execute(self, sql, *params):
        if "TOP 1" in sql:
            self.result = [(max(self.rows_by_test),)] if self.rows_by_test else []
        else:
            self.result = self.rows_by_test[params[0][1]]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return self.result


class FakeConnection:
    def __init__(self, rows_by_test):
        self.cur, self.closed = FakeCursor(rows_by_test), False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def test_golden_power_comes_from_the_latest_test_number():
    rows = {7: [(f"{ch}_DDMI", -9.0) for ch in range(1, 9)],
            12: [(f"{ch}_DDMI", ch / 10) for ch in range(1, 9)] + [("ALL", 5.0), ("9_x", 1.0)]}
    conn = FakeConnection(rows)
    golden = fetch_golden_powers("SN1", {}, connect=lambda _cfg: conn)
    assert golden == {ch: ch / 10 for ch in range(1, 9)} and conn.closed


def test_golden_power_errors_are_clear():
    with pytest.raises(ValueError, match="no records"):
        fetch_golden_powers("SN1", {}, connect=lambda _cfg: FakeConnection({}))
    with pytest.raises(ValueError, match=r"missing golden power for channel\(s\) \[8\]"):
        fetch_golden_powers("SN1", {}, connect=lambda _cfg: FakeConnection(
            {3: [(f"{ch}_DDMI", 0.1) for ch in range(1, 8)]}))


def test_rx_table_build_save_and_lookup(station_cfg, product, tmp_path):
    station = build_station(station_cfg)
    seen = []
    table = build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], log=QUIET,
                           on_point=lambda att, ch, power: seen.append((att, ch)))
    assert sorted(table) == list(range(1, 9)) and len(seen) == 8 * len(ATT_STEPS_DB)
    assert [att for att, _ in table[1]] == [float(a) for a in ATT_STEPS_DB]

    stationfiles.write_rx_rows(tmp_path / "t", table)
    first = (tmp_path / "t" / "CH1_Rx_calibration.txt").read_text().splitlines()[0].split("\t")
    assert len(first) == 3 and float(first[2]) == pytest.approx(float(first[1]) - float(first[0]), abs=0.011)
    assert stationfiles.read_rx_rows(tmp_path / "t") == table

    # the attenuation the table gives for -6 dBm really puts -6 dBm at the module
    att = stationfiles.load_rx_tables(tmp_path / "t")[1].att_for_power(-6)
    station.bench.attenuator.set_attenuation(1, att)
    assert station.sim_bench.rx_input_dbm(1) == pytest.approx(-6, abs=0.03)


def test_rx_table_asks_to_move_the_meter_between_the_two_fibres(station_cfg, product):
    prompts = []
    station = build_station(station_cfg)
    table = build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], log=QUIET,
                           on_prompt=lambda msg: prompts.append(msg) or True)
    assert len(prompts) == 2
    assert "RX1" in prompts[0] and "lanes 1-4" in prompts[0]
    assert "RX2" in prompts[1] and "lanes 5-8" in prompts[1]
    assert sorted(table) == list(range(1, 9))
    assert station.sim_bench.wavelengths[:2] == [1330, 1330]     # last lane set on each fibre


def test_rx_table_can_be_cancelled_at_the_meter_prompt(station_cfg, product):
    station = build_station(station_cfg)
    answers = iter([True, False])
    assert build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], log=QUIET,
                          on_prompt=lambda _msg: next(answers)) is None
