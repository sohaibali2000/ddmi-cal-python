"""Tx Loss calibration, as the production DDMI TX Loss tool does it.

Golden sample in the station; set the 8-channel meter's wavelengths and averaging,
read all channels at once, TxLoss[ch] = golden power - measured power.
"""
from __future__ import annotations

from typing import Callable

from ..hw.base import PowerMeter


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


def calibrate_tx_loss(station_cfg: dict, product: dict, *, slot: int = 1, component_id: str = "",
                      golden_dbm: dict[int, float] | None = None,
                      log: Callable[[str], None] = print) -> list[dict]:
    """Full Tx Loss calibration for one slot. Does not save.

    Golden power: golden_dbm if given; on a real station otherwise from the MES database
    by the golden sample's Component ID; on the simulator the simulated module's true power.
    """
    from ..mes.golden import fetch_golden_powers, load_db_config
    from ..station import build_power_meter, build_station, is_sim

    channels = product["channels"]
    meter_cfg = product["tx_power_meter"]
    if is_sim(station_cfg):
        station = build_station(station_cfg, slot=slot)
        meter, close = station.bench.power_meter, None
        if golden_dbm is None:
            golden_dbm = {ch: station.sim_module.tx_out_dbm(ch) for ch in range(1, channels + 1)}
            log("Simulator: the simulated module's true output power stands in for the MES golden record")
    else:
        if golden_dbm is None:
            if not component_id:
                raise ValueError("enter the golden sample's Component ID / SN")
            log(f"Fetching golden power for {component_id} from MES")
            golden_dbm = fetch_golden_powers(component_id, load_db_config(), channels)
        meter = build_power_meter(station_cfg, log)
        close = meter.close
    try:
        return measure_tx_loss(meter, golden_dbm, meter_cfg["wavelengths_nm"], meter_cfg["avg_time_ms"],
                               warn_db=product["limits"]["tx_loss_warn_db"], log=log)
    finally:
        if close:
            close()
