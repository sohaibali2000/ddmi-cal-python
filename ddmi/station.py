"""Build the module + instruments for one slot from the station config."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import INSTRUMENTS
from .hw.base import Bench, LaneAttenuator
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
    closers: list = field(default_factory=list)

    def close(self) -> None:
        """Release every instrument (serial ports, the attenuator host process)."""
        while self.closers:
            try:
                self.closers.pop()()
            except Exception:
                pass


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
        sim = SimModule(regmap, sn=sim_sn or "SIM000001", fault=fault, seed=slot)   # repeatable runs
        att = station_cfg["attenuator"]
        sim_bench = SimBench(sim, seed=slot, lane_channels=att.get("lane_channels"),
                             min_att_db=att.get("min_att_db", 0.0))
        module = Module(sim, regmap, is_sim=True, dry_run=dry_run,
                        allow_unverified=allow_unverified, log=log)
        return Station(module, sim_bench.as_bench(), sim, sim_bench)

    return _build_real_station(station_cfg, regmap, dry_run, allow_unverified, log)


def _build_real_station(station_cfg: dict, regmap, dry_run: bool, allow_unverified: bool,
                        log: Callable[[str], None]) -> Station:
    from .hw.attenuator_at4308 import AttenuatorAT4308
    from .hw.base import DryRunAttenuator, DryRunSupply, LaneAttenuator, SensedSupply
    from .hw.i2c_usbiss import UsbIssBus
    from .hw.ports import resolve_port
    from .hw.power_supply_gpp import GppSupply

    kinds = {name: station_cfg[name]["type"] for name in INSTRUMENTS}
    supported = {"i2c": "usb_iss", "power_supply": "gw_gpp", "power_meter": "lx_mpo", "attenuator": "at4308"}
    unknown = [f"{name}: {kind}" for name, kind in kinds.items() if kind != supported[name]]
    if unknown:
        raise NotImplementedError("no driver for " + ", ".join(unknown)
                                  + f" (supported: {', '.join(f'{k}: {v}' for k, v in supported.items())})")

    closers = []
    try:
        i2c = station_cfg["i2c"]
        bus = UsbIssBus(resolve_port(i2c["resource"]), address=i2c.get("address", 0x50), log=log).open()
        def release_bus(bus=bus):
            try:
                bus.write(0, 0x7F, bytes([0]))      # leave the module on page 00
            finally:
                bus.close()

        closers.append(release_bus)
        if not bus.present():
            raise RuntimeError(f"no module answers on {bus.port} - is the DUT inserted and powered?")
        module = Module(bus, regmap, is_sim=False, dry_run=dry_run, allow_unverified=allow_unverified, log=log)

        ps = station_cfg["power_supply"]
        supply = GppSupply(resolve_port(ps["resource"]), channel=ps.get("channel", 1),
                           max_voltage_v=ps.get("max_voltage_v", 3.9), log=log).open()
        closers.append(supply.close)
        if not supply.output_on():
            raise RuntimeError(f"the DUT supply output (CH{supply.channel}) is off - switch it on first")
        monitor = ps.get("vcc_monitor")
        if monitor:
            if monitor["type"] != "usb_iss_adc":
                raise NotImplementedError(f"no VCC monitor of type {monitor['type']!r} (supported: usb_iss_adc)")
            scale = monitor.get("full_scale_v", 5.0) / monitor.get("counts", 1024)
            pin = monitor.get("pin", 1)
            supply = SensedSupply(supply, lambda: bus.read_adc(pin) * scale)
            log(f"VCC at the module: USB-ISS analogue pin {pin} reads {supply.read_module_voltage():.3f} V")

        # Meter and attenuator can serve several slots: opened once per process, every call
        # locked, closed when the last slot releases them.
        from .hw import shared

        meter, release_meter = shared.acquire(f"meter:{station_cfg['power_meter']['resource']}",
                                              lambda: build_power_meter(station_cfg, log))
        closers.append(release_meter)

        att_cfg = station_cfg["attenuator"]

        def open_attenuator():
            dev = AttenuatorAT4308(resolve_port(att_cfg["resource"]), att_cfg["dll_dir"], log=log)
            dev.connect()
            return dev

        att, release_att = shared.acquire(f"attenuator:{att_cfg['resource']}", open_attenuator)
        closers.append(release_att)
        attenuator = LaneAttenuator(att, att_cfg.get("lane_channels", list(range(1, 9))),
                                    att_cfg.get("min_att_db", 0.0))
    except Exception:
        for close in reversed(closers):
            try:
                close()
            except Exception:
                pass
        raise
    if dry_run:
        # Nothing is set on any instrument either: the supply's measured voltage is at its
        # terminals, so even "adjusting to 3.3 V" could drop the module out.
        supply = DryRunSupply(supply, log)
        attenuator = LaneAttenuator(DryRunAttenuator(log), attenuator.lane_channels, attenuator.min_att_db)
    # No RX input meter on this station yet: the RX table tool says so instead of guessing.
    bench = Bench(power_supply=supply, power_meter=meter, attenuator=attenuator, rx_meter=None)
    return Station(module, bench, closers=closers)


def build_power_meter(station_cfg: dict, log: Callable[[str], None] = print):
    """The real TX power meter on its own, connected. Enough for Tx Loss calibration,
    which needs no module register access (as in the production Tx Loss tool)."""
    cfg = station_cfg["power_meter"]
    if cfg["type"] == "lx_mpo":
        from .hw.ports import resolve_port
        from .hw.power_meter_lx import PowerMeterLX

        meter = PowerMeterLX(resolve_port(cfg["resource"]), cfg["dll"], log=log)
        meter.connect()
        return meter
    raise NotImplementedError(f"no driver for power meter type {cfg['type']!r} (supported: lx_mpo)")
