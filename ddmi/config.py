"""Load and validate the product and station YAML files."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

OP_POINT_MODES = ("x_of_peak", "one_minus_x_of_peak", "x_of_span", "one_minus_x_of_span")
DDMI_CAL_PARTS = ("tx", "rx", "vcc", "temp")
INSTRUMENTS = ("i2c", "power_supply", "power_meter", "attenuator")


class ConfigError(ValueError):
    pass


def _get(cfg: dict, path: str):
    node = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"missing setting: {path}")
        node = node[key]
    return node


def _number(cfg: dict, path: str, low=None, high=None) -> float:
    value = _get(cfg, path)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number, got {value!r}")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError(f"{path} = {value} is outside {low} to {high}")
    return value


def _numbers(cfg: dict, path: str, count: int | None = None) -> list:
    value = _get(cfg, path)
    if (not isinstance(value, list) or not value
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        raise ConfigError(f"{path} must be a list of numbers, got {value!r}")
    if count is not None and len(value) != count:
        raise ConfigError(f"{path} needs {count} values, got {len(value)}")
    return value


def _range(cfg: dict, path: str) -> tuple:
    low, high = _numbers(cfg, path, 2)
    if low >= high:
        raise ConfigError(f"{path} must be [low, high] with low < high, got {[low, high]}")
    return low, high


def _load(path) -> dict:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping")
    return data


def validate_product(cfg: dict) -> dict:
    channels = _number(cfg, "channels", 1)
    lasers = _number(cfg, "lasers", 1)
    if channels % lasers:
        raise ConfigError(f"channels ({channels}) must be a multiple of lasers ({lasers})")

    start = _number(cfg, "mpd_scan.vtops_start", 0)
    stop = _number(cfg, "mpd_scan.vtops_stop", 0)
    _number(cfg, "mpd_scan.vtops_step", 1e-6)
    if start >= stop:
        raise ConfigError("mpd_scan.vtops_start must be below mpd_scan.vtops_stop")
    fractions = _numbers(cfg, "mpd_scan.op_point_fractions")
    if any(not 0 < f < 1 for f in fractions):
        raise ConfigError("mpd_scan.op_point_fractions must all be between 0 and 1")
    if any(f not in fractions for f in _numbers(cfg, "mpd_scan.tops_mid_from", 2)):
        raise ConfigError("mpd_scan.tops_mid_from must be two of mpd_scan.op_point_fractions")
    mode = _get(cfg, "mpd_scan.op_point_mode")
    if mode not in OP_POINT_MODES:
        raise ConfigError(f"mpd_scan.op_point_mode must be one of {OP_POINT_MODES}, got {mode!r}")
    _number(cfg, "mpd_scan.setpoint_scale", 1)
    _number(cfg, "mpd_scan.scan_attempts", 1)

    order = _get(cfg, "flow.ddmi_cal_order")
    if not isinstance(order, list) or sorted(order) != sorted(DDMI_CAL_PARTS):
        raise ConfigError(f"flow.ddmi_cal_order must list each of {DDMI_CAL_PARTS} once, got {order!r}")

    # Adjust Power: one setpoint per laser. Item_Test.vi V6.8 has two AWS branches: LC-2*FR4
    # [280, 280, 410, 450] / 0.7-2.3 dBm (this product, 4 lasers) and MPO-DR8 [300, 350] /
    # 1.0-2.6 dBm (2 lasers) - the DR8 values must never end up here.
    setpoint = _numbers(cfg, "adjust_power.default_setpoint", int(cfg["lasers"]))
    if any(not 100 <= v <= 2500 for v in setpoint):
        raise ConfigError("adjust_power.default_setpoint values must be within 100-2500")
    _range(cfg, "adjust_power.window_dbm")
    _number(cfg, "adjust_power.step_up", 0)
    _number(cfg, "adjust_power.step_down", 0)
    _number(cfg, "adjust_power.settle_s", 0)
    _number(cfg, "tx_disable.step_v", 1e-6)
    _number(cfg, "tx_disable.steps", 0)
    _number(cfg, "tx_disable.recheck_dbm")
    _number(cfg, "tx_disable.extra_steps", 0)
    _number(cfg, "tx_disable.enable_wait_s", 0)
    _number(cfg, "tx_disable.poll_s", 1e-3)
    _number(cfg, "tx_disable.min_s", 0)
    _number(cfg, "tx_disable.max_s", 1e-3)
    _number(cfg, "tx_disable.stable_readings", 1)
    _number(cfg, "tx_disable.tol_db", 0)
    _number(cfg, "tx_disable.dark_enough_dbm")
    _range(cfg, "tx_disable.limit_dbm")

    for nm in _numbers(cfg, "tx_power_meter.wavelengths_nm", int(channels)):
        if nm not in (1270, 1290, 1310, 1330):
            raise ConfigError(f"tx_power_meter.wavelengths_nm: the meter supports 1270/1290/1310/1330, not {nm}")
    if _get(cfg, "tx_power_meter.avg_time_ms") not in (100, 200, 500):
        raise ConfigError("tx_power_meter.avg_time_ms must be 100, 200 or 500")

    if not isinstance(_get(cfg, "tx_loss.stabilize"), bool):
        raise ConfigError("tx_loss.stabilize must be true or false")
    _number(cfg, "tx_loss.interval_s", 0)
    _number(cfg, "tx_loss.threshold_c", 0.001)
    _number(cfg, "tx_loss.consecutive", 1)
    _number(cfg, "tx_loss.timeout_s", 1)

    low, high = _numbers(cfg, "tx_cal.bias_ma", 2)
    if low == high:
        raise ConfigError("tx_cal.bias_ma needs two different bias points")
    _number(cfg, "tx_cal.settle_s", 0)
    _number(cfg, "tx_cal.after_reset_wait_s", 0)

    p1, p2 = _numbers(cfg, "rx_cal.points_dbm", 2)
    if p1 == p2:
        raise ConfigError("rx_cal.points_dbm needs two different powers")
    _number(cfg, "rx_cal.check_dbm")
    _numbers(cfg, "rx_cal.wavelengths_nm", int(channels))
    _number(cfg, "rx_cal.settle_s", 0)

    _number(cfg, "vcc.nominal_v", 0)
    v1, v2 = _numbers(cfg, "vcc.cal_points_v", 2)
    if v1 == v2:
        raise ConfigError("vcc.cal_points_v needs two different voltages")
    _number(cfg, "vcc.settle_s", 0)
    _number(cfg, "vcc.adjust_tolerance_v", 1e-6)

    for name in ("tx_disable_dbm", "dtxp_db", "rx_check_error_db", "rx_slope",
                 "dvcc_v", "ddmi_temp_c"):
        _range(cfg, f"limits.{name}")
    _number(cfg, "limits.tx_loss_warn_db", 0)
    return cfg


def validate_station(cfg: dict) -> dict:
    if not isinstance(_get(cfg, "station_name"), str):
        raise ConfigError("station_name must be text")
    for flag in ("dry_run", "allow_unverified_writes"):
        if not isinstance(_get(cfg, flag), bool):
            raise ConfigError(f"{flag} must be true or false")
    for name in INSTRUMENTS:
        entry = _get(cfg, name)
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            raise ConfigError(f"{name} must be a mapping with a 'type'")
    ps = cfg["power_supply"]
    if "max_voltage_v" in ps:
        _number(cfg, "power_supply.max_voltage_v", 3.3, 4.5)
    att = cfg["attenuator"]
    if "lane_channels" in att:
        lanes = att["lane_channels"]
        if (not isinstance(lanes, list) or len(lanes) != 8
                or any(isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in lanes)):
            raise ConfigError("attenuator.lane_channels must list the attenuator channel for each of the 8 lanes")
    if "min_att_db" in att:
        _number(cfg, "attenuator.min_att_db", 0, 60)
    if "time_scale" in cfg:
        _number(cfg, "time_scale", 0)
    for name in ("tx_loss_file", "rx_table_dir", "results_dir"):
        if not isinstance(_get(cfg, name), str):
            raise ConfigError(f"{name} must be a path")
    return cfg


def load_product(path=CONFIG_DIR / "product_aws_2xfr4.yaml") -> dict:
    return validate_product(_load(path))


def load_station(path=CONFIG_DIR / "station_sim.yaml") -> dict:
    return validate_station(_load(path))
