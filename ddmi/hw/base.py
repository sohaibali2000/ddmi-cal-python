"""Instrument interfaces. The simulator and every real driver implement these."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class I2CBus(Protocol):
    """Module register access. Page select is the adapter's job."""

    def read(self, page: int, byte: int, length: int) -> bytes: ...
    def write(self, page: int, byte: int, data: bytes) -> None: ...


class PowerSupply(Protocol):
    def set_voltage(self, volts: float) -> None: ...
    def read_current(self) -> float: ...            # A

    def read_module_voltage(self) -> float:
        """Voltage at the module, independent of the module's own DDMI.

        Which reading LabVIEW uses for this (supply sense or `I2C Read Vcc.vi`)
        is not confirmed.
        """
        ...


class PowerMeter(Protocol):
    def read_dbm(self) -> float: ...


class OptSwitch(Protocol):
    def select(self, ch: int) -> None: ...


class Attenuator(Protocol):
    def set_wavelength(self, ch: int, nm: float) -> None: ...
    def set_attenuation(self, ch: int, db: float) -> None: ...


class RxInputMeter(Protocol):
    """Power meter at the module's RX input; only needed to build the RX table."""

    def read_rx_input_dbm(self, ch: int) -> float: ...


@dataclass
class Bench:
    power_supply: PowerSupply
    power_meter: PowerMeter
    opt_switch: OptSwitch
    attenuator: Attenuator
    rx_meter: RxInputMeter | None = None

    def read_tx_power(self, ch: int) -> float:
        """Raw meter reading for one TX lane (Tx Loss not applied)."""
        self.opt_switch.select(ch)
        return self.power_meter.read_dbm()
