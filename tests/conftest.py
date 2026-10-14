import pytest

from ddmi import config, stationfiles
from ddmi.station import build_station
from ddmi.tools.rx_table import build_rx_table
from ddmi.tools.tx_loss import calibrate_tx_loss


@pytest.fixture
def product():
    return config.load_product()


@pytest.fixture
def station_cfg(tmp_path, product):
    """Simulator station with its own data folders, already Tx Loss and RX table calibrated."""
    cfg = config.load_station()
    cfg.update(tx_loss_file=str(tmp_path / "system_Data.ini"),
               rx_table_dir=str(tmp_path / "rx_tables"),
               results_dir=str(tmp_path / "results"), time_scale=0.0)
    station = build_station(cfg, slot=1)
    quiet = lambda _text: None
    rows = calibrate_tx_loss(cfg, product, slot=1, log=quiet)
    stationfiles.write_tx_loss(tmp_path / "system_Data.ini", [row["loss_db"] for row in rows])
    stationfiles.write_rx_rows(
        stationfiles.rx_table_dir(1, tmp_path / "rx_tables"),
        build_rx_table(station.bench, product["rx_cal"]["wavelengths_nm"], log=quiet))
    return cfg
