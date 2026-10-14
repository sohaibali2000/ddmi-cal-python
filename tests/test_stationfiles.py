import shutil

import pytest

from ddmi import stationfiles
from ddmi.station import build_station
from ddmi.tools.rx_table import ATT_STEPS_DB, build_rx_table
from ddmi.tools.tx_loss import calibrate_tx_loss

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


def test_tx_loss_with_wrong_count_is_ignored(tmp_path):
    path = tmp_path / "x.ini"
    path.write_text('[Data]\nTx Loss="1.0\t2.0"\n')
    assert stationfiles.read_tx_loss(path) is None
    with pytest.raises(ValueError):
        stationfiles.write_tx_loss(path, [1.0, 2.0])


def test_tx_loss_calibration_recovers_the_path_loss(station_cfg):
    station = build_station(station_cfg)
    golden = [station.sim_module.tx_out_dbm(ch) for ch in range(1, 9)]
    losses = calibrate_tx_loss(station.bench, golden, log=QUIET)
    assert losses == pytest.approx(station.sim_bench.tx_path_loss, abs=0.03)


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
