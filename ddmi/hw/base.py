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
    def set_current_limit(self, amps: float) -> None: ...
    def read_current(self) -> float: ...            # A
    def read_output_voltage(self) -> float: ...     # V at the supply terminals (PS Read Volt.vi)

    def read_module_voltage(self) -> float:
        """Voltage at the module, independent of the module's own DDMI.

        V6.8 Auto Adjust Vcc.vi reads it with I2C Read Vcc.vi (USB-ISS ADC on the DUT board).
        """
        ...


class PowerMeter(Protocol):
    """Multi-channel TX power meter (the production station uses an 8-channel MPO meter)."""

    def configure(self, wavelengths_nm: list[int], avg_time_ms: int) -> None:
        """Set each channel's wavelength (list index 0 = CH1) and the averaging time."""
        ...

    def read_all_dbm(self) -> list[float]:
        """One reading per channel, CH1 first, in dBm."""
        ...


class Attenuator(Protocol):
    def set_wavelength(self, ch: int, nm: float) -> None: ...
    def set_attenuation(self, ch: int, db: float) -> None: ...


class LaneAttenuator:
    """The attenuator as the steps see it: addressed by DUT lane, not by attenuator channel.

    On a 2xFR4 station one attenuator channel feeds one RX fibre carrying 4 lanes, so
    several lanes map to the same channel. Every setting is checked against a minimum
    attenuation that protects the DUT receiver.
    """

    def __init__(self, attenuator: Attenuator, lane_channels: list[int], min_att_db: float = 0.0):
        self.attenuator = attenuator
        self.lane_channels = list(lane_channels)
        self.min_att_db = min_att_db

    @property
    def physical_channels(self) -> list[int]:
        return sorted(set(self.lane_channels))

    def channel(self, lane: int) -> int:
        return self.lane_channels[lane - 1]

    def _check(self, db: float) -> None:
        if db < self.min_att_db:
            raise ValueError(f"attenuation {db:.2f} dB is below the station minimum of "
                             f"{self.min_att_db} dB (protects the DUT receiver)")

    def set_wavelength(self, lane: int, nm: float) -> None:
        self.attenuator.set_wavelength(self.channel(lane), nm)

    def set_attenuation(self, lane: int, db: float) -> None:
        self._check(db)
        self.attenuator.set_attenuation(self.channel(lane), db)

    def set_channel_attenuation(self, channel: int, db: float) -> None:
        """Set one attenuator channel directly (all lanes on that fibre)."""
        self._check(db)
        self.attenuator.set_attenuation(channel, db)


class SensedSupply:
    """A supply whose 'voltage at the module' comes from a monitor on the DUT board
    (on the lab station: the DUT's USB-ISS analogue pin 1, as LabVIEW's I2C Read Vcc.vi)."""

    def __init__(self, supply, read_module_volts):
        self.supply, self.read_module_volts = supply, read_module_volts

    def set_voltage(self, volts: float) -> None:
        self.supply.set_voltage(volts)

    def set_current_limit(self, amps: float) -> None:
        self.supply.set_current_limit(amps)

    def read_current(self) -> float:
        return self.supply.read_current()

    def read_output_voltage(self) -> float:
        return self.supply.read_output_voltage()

    def read_module_voltage(self) -> float:
        return self.read_module_volts()


class DryRunSupply:
    """Dry run on real hardware: the supply is read, never set (each intended setting is logged)."""

    def __init__(self, supply, log):
        self.supply, self.log = supply, log

    def set_voltage(self, volts: float) -> None:
        self.log(f"SUPPLY (dry-run, not sent) set {volts:.3f} V")

    def set_current_limit(self, amps: float) -> None:
        self.log(f"SUPPLY (dry-run, not sent) current limit {amps:.3f} A")

    def read_current(self) -> float:
        return self.supply.read_current()

    def read_output_voltage(self) -> float:
        return self.supply.read_output_voltage()

    def read_module_voltage(self) -> float:
        return self.supply.read_module_voltage()


class DryRunAttenuator:
    """Dry run on real hardware: the attenuator is never changed (each intended setting is logged)."""

    def __init__(self, log):
        self.log = log

    def set_wavelength(self, ch: int, nm: float) -> None:
        self.log(f"ATTENUATOR (dry-run, not sent) CH{ch} wavelength {nm} nm")

    def set_attenuation(self, ch: int, db: float) -> None:
        self.log(f"ATTENUATOR (dry-run, not sent) CH{ch} {db:.2f} dB")


class RxInputMeter(Protocol):
    """Power meter at the module's RX input; only needed to build the RX table."""

    def read_rx_input_dbm(self, ch: int) -> float: ...


@dataclass
class Bench:
    power_supply: PowerSupply
    power_meter: PowerMeter
    attenuator: LaneAttenuator
    rx_meter: RxInputMeter | None = None

    def read_tx_powers(self) -> list[float]:
        """Raw meter readings for all TX lanes (Tx Loss not applied), CH1 first."""
        return list(self.power_meter.read_all_dbm())

    def read_tx_power(self, ch: int) -> float:
        return self.read_tx_powers()[ch - 1]
