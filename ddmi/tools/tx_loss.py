"""Tx Loss calibration, as the production DDMI TX Loss tool does it.

Golden sample in the station; set the 8-channel meter's wavelengths and averaging,
read all channels at once, TxLoss[ch] = golden power - measured power.
"""
from __future__ import annotations

import threading
from typing import Callable

from ..hw.base import PowerMeter
from .temp_stabilize import wait_for_stable_temperature


def measure_tx_loss(meter: PowerMeter, golden_dbm: dict[int, float], wavelengths_nm: list[int],
                    avg_time_ms: int = 100, *, warn_db: float = 3.5,
                    log: Callable[[str], None] = print) -> list[dict]:
    """One row per channel: ch, wavelength_nm, golden_dbm, measured_dbm, loss_db. Does not save."""
    meter.configure(wavelengths_nm, avg_time_ms)
    measured = meter.read_all_dbm()
    rows = []
    for ch in range(1, len(wavelengths_nm) + 1):
        loss = round(golden_dbm[ch] - measured[ch - 1], 3)
        note = f"  WARNING: above {warn_db} dB" if loss > warn_db else ""
        log(f"CH{ch} ({wavelengths_nm[ch - 1]} nm): golden {golden_dbm[ch]:.3f} dBm, "
            f"measured {measured[ch - 1]:.3f} dBm -> Tx Loss {loss:.3f} dB{note}")
        rows.append({"ch": ch, "wavelength_nm": wavelengths_nm[ch - 1], "golden_dbm": golden_dbm[ch],
                     "measured_dbm": measured[ch - 1], "loss_db": loss})
    return rows


def stabilize_golden_temperature(station_cfg: dict, product: dict, *, slot: int = 1,
                                 log: Callable[[str], None] = print,
                                 on_temp: Callable[[dict], None] = lambda _s: None,
                                 abort: threading.Event | None = None) -> bool:
    """Only the temperature wait ("Start Stabilization"): True once stable, False if stopped.
    Raises if it does not settle within tx_loss.timeout_s."""
    stab = product["tx_loss"]
    abort = abort or threading.Event()
    if _wait_for_temperature(station_cfg, slot, stab, log, on_temp, abort):
        return True
    if abort.is_set():
        return False
    raise RuntimeError(f"the golden sample's temperature did not settle within {stab['timeout_s']:g} s")


def calibrate_tx_loss(station_cfg: dict, product: dict, *, slot: int = 1, component_id: str = "",
                      golden_dbm: dict[int, float] | None = None, stabilize: bool | None = None,
                      meter=None, log: Callable[[str], None] = print,
                      on_temp: Callable[[dict], None] = lambda _s: None,
                      abort: threading.Event | None = None) -> list[dict] | None:
    """Full Tx Loss calibration for one slot. Does not save. None if stopped.

    1. Wait for the golden sample's DDMI temperature to settle (product tx_loss settings).
    2. Golden power: golden_dbm if given; on a real station otherwise from the MES database
       by the golden sample's Component ID; on the simulator the simulated module's true power.
    3. Read the 8-channel meter (meter if given, e.g. connected on the Tx Loss tab; it is
       left open); TxLoss = golden - measured.
    """
    from ..station import acquire_power_meter, build_station, is_sim

    channels = product["channels"]
    meter_cfg = product["tx_power_meter"]
    stab = product["tx_loss"]
    stabilize = stab["stabilize"] if stabilize is None else stabilize
    abort = abort or threading.Event()
    if stabilize and not stabilize_golden_temperature(station_cfg, product, slot=slot, log=log,
                                                      on_temp=on_temp, abort=abort):
        return None
    close = None
    if is_sim(station_cfg):
        station = build_station(station_cfg, slot=slot)
        meter = meter or station.bench.power_meter
        if golden_dbm is None:
            golden_dbm = {ch: station.sim_module.tx_out_dbm(ch) for ch in range(1, channels + 1)}
            log("Simulator: the simulated module's true output power stands in for the MES golden record")
    else:
        if golden_dbm is None:
            if not component_id:
                raise ValueError("enter the golden sample's Component ID / SN")
            log(f"Fetching golden power for {component_id} from MES")
            golden_dbm = fetch_golden(component_id, product)
        if meter is None:
            meter, close = acquire_power_meter(station_cfg, log)
    try:
        return measure_tx_loss(meter, golden_dbm, meter_cfg["wavelengths_nm"], meter_cfg["avg_time_ms"],
                               warn_db=product["limits"]["tx_loss_warn_db"], log=log)
    finally:
        if close:
            close()


def fetch_golden(component_id: str, product: dict) -> dict[int, float]:
    """"Fetch Golden Power from DB": {ch: dBm} from the MES database."""
    from ..mes.golden import fetch_golden_powers, load_db_config

    if not component_id:
        raise ValueError("enter the golden sample's Component ID / SN")
    return fetch_golden_powers(component_id, load_db_config(), product["channels"])


def read_golden_temperature(station_cfg: dict, slot: int = 1) -> float:
    """"Read Once": one DDMI temperature reading of the module in the DUT position."""
    read, close, _scale = _temperature_reader(station_cfg, slot)
    try:
        return read()
    finally:
        if close:
            close()


def _temperature_reader(station_cfg: dict, slot: int):
    """(read, close, time scale) for the golden sample's DDMI temperature.
    Lower-page reads only: nothing is written to the module."""
    from ..hw.module import Module
    from ..hw.regmap import load_regmap
    from ..station import build_station, is_sim

    if is_sim(station_cfg):
        station = build_station(station_cfg, slot=slot)
        return (lambda: station.module.read("R_DTEMP")), None, station_cfg.get("time_scale", 1.0)
    from ..hw.i2c_usbiss import UsbIssBus
    from ..hw.ports import resolve_port

    bus = UsbIssBus(resolve_port(station_cfg["i2c"]["resource"])).open()
    if not bus.present():
        bus.close()
        raise RuntimeError("no module answers in the DUT position - insert and power the golden sample")
    module = Module(bus, load_regmap(), is_sim=False)
    return (lambda: module.read("R_DTEMP")), bus.close, 1.0


def _wait_for_temperature(station_cfg: dict, slot: int, stab: dict, log, on_temp, abort) -> bool:
    """Read the golden sample's DDMI temperature from the DUT position until it settles."""
    read, close, scale = _temperature_reader(station_cfg, slot)
    try:
        return wait_for_stable_temperature(
            read, interval_s=stab["interval_s"], threshold_c=stab["threshold_c"],
            consecutive=int(stab["consecutive"]), timeout_s=stab["timeout_s"], log=log,
            on_sample=on_temp, abort=abort, sleep=lambda s: abort.wait(s * scale))
    finally:
        if close:
            close()
