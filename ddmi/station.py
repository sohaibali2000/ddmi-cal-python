"""Build the module + instruments for one slot from the station config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import INSTRUMENTS
from .hw.base import Bench
from .hw.module import Module
from .hw.regmap import load_regmap
from .sim.sim_bench import SimBench
from .sim.sim_module import SimModule


@dataclass
class Station:
    module: Module
    bench: Bench
    sim_module: SimModule | None = None
    sim_bench: SimBench | None = None


def is_sim(station_cfg: dict) -> bool:
    return all(station_cfg[name]["type"] == "sim" for name in INSTRUMENTS)


def build_station(station_cfg: dict, *, slot: int = 1, sim_sn: str = "", fault: str = "none",
                  dry_run: bool | None = None, allow_unverified: bool | None = None,
                  log: Callable[[str], None] = lambda _t: None) -> Station:
    """sim_sn / fault only apply to the simulator."""
    dry_run = station_cfg["dry_run"] if dry_run is None else dry_run
    if allow_unverified is None:
        allow_unverified = station_cfg["allow_unverified_writes"]
    regmap = load_regmap()

    if is_sim(station_cfg):
        sim = SimModule(regmap, sn=sim_sn or "SIM000001", fault=fault)
        att = station_cfg["attenuator"]
        sim_bench = SimBench(sim, seed=slot, lane_channels=att.get("lane_channels"),
                             min_att_db=att.get("min_att_db", 0.0))
        module = Module(sim, regmap, is_sim=True, dry_run=dry_run,
                        allow_unverified=allow_unverified, log=log)
        return Station(module, sim_bench.as_bench(), sim, sim_bench)

    real = [f"{name}: {station_cfg[name]['type']}" for name in INSTRUMENTS
            if station_cfg[name]["type"] != "sim"]
    raise NotImplementedError(
        "No hardware drivers yet (build map phase 14). Station config asks for " + ", ".join(real))


def build_power_meter(station_cfg: dict, log: Callable[[str], None] = print):
    """The real TX power meter on its own, connected. Enough for Tx Loss calibration,
    which needs no module register access (as in the production Tx Loss tool)."""
    cfg = station_cfg["power_meter"]
    if cfg["type"] == "lx_mpo":
        from .hw.power_meter_lx import PowerMeterLX

        meter = PowerMeterLX(cfg["resource"], cfg["dll"], log=log)
        meter.connect()
        return meter
    raise NotImplementedError(f"no driver for power meter type {cfg['type']!r} (supported: lx_mpo)")
