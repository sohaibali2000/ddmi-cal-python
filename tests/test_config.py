import copy
import re

import pytest

from ddmi import config


def test_shipped_configs_load():
    product = config.load_product()
    assert product["customer"] == "AWS" and product["channels"] == 8
    assert product["tx_cal"]["bias_ma"] == [60, 180]
    assert config.load_station()["i2c"]["type"] == "sim"
    lab = config.load_station(config.CONFIG_DIR / "station_lab.yaml")
    assert lab["allow_unverified_writes"] is False


@pytest.mark.parametrize("path, value", [
    ("mpd_scan.vtops_step", 0),
    ("mpd_scan.op_point_mode", "guess"),
    ("mpd_scan.op_point_fractions", [0.5, 1.2]),
    ("tx_cal.bias_ma", [60, 60]),
    ("rx_cal.points_dbm", [-2]),
    ("limits.dtxp_db", [0.5, -0.5]),
    ("vcc.nominal_v", "3.3"),
])
def test_bad_values_are_rejected(path, value):
    product = copy.deepcopy(config.load_product())
    node = product
    *parents, leaf = path.split(".")
    for key in parents:
        node = node[key]
    node[leaf] = value
    with pytest.raises(config.ConfigError, match=re.escape(path)):
        config.validate_product(product)


def test_missing_setting_is_named():
    product = copy.deepcopy(config.load_product())
    del product["rx_cal"]["check_dbm"]
    with pytest.raises(config.ConfigError, match="rx_cal.check_dbm"):
        config.validate_product(product)


def test_station_flags_must_be_booleans():
    station = copy.deepcopy(config.load_station())
    station["allow_unverified_writes"] = "no"
    with pytest.raises(config.ConfigError):
        config.validate_station(station)
