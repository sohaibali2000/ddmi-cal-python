"""GW Instek GPP-3060 / GPP-X323 power supply, one channel, over its USB virtual COM port.

Confirmed on the lab unit (GPP-3060, firmware V1.08): 9600 baud, newline-terminated
commands, `*IDN?`, `VSET1?`, `ISET1?`, `VOUT1?`, `IOUT1?`, `:OUTPut1:STATe?`.
Setting a voltage uses `VSET<ch>:<volts>` from the GPP programming manual.

The driver never switches the output on or off: the operator does that. A run refuses
to start if the output is off, and no setting above max_voltage_v is ever sent.
"""
from __future__ import annotations

from typing import Callable


class SupplyError(RuntimeError):
    pass


class GppSupply:
    def __init__(self, port: str, channel: int = 1, max_voltage_v: float = 3.9,
                 log: Callable[[str], None] = lambda _t: None, serial_factory=None):
        self.port = port
        self.channel = channel
        self.max_voltage_v = max_voltage_v
        self.log = log
        self._serial_factory = serial_factory
        self.serial = None

    def open(self) -> "GppSupply":
        if self._serial_factory is None:
            import serial   # pyserial

            self._serial_factory = lambda port: serial.Serial(port, 9600, timeout=1)
        self.serial = self._serial_factory(self.port)
        ident = self.query("*IDN?")
        if "GPP" not in ident:
            self.close()
            raise SupplyError(f"{self.port} is not a GW Instek GPP supply (*IDN? -> {ident!r})")
        self.log(f"Power supply on {self.port}: {ident}, channel {self.channel}")
        return self

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None

    def query(self, command: str) -> str:
        if self.serial is None:
            raise SupplyError(f"supply on {self.port} is not open")
        self.serial.write((command + "\n").encode())
        reply = self.serial.readline().decode(errors="replace").strip()
        if not reply:
            raise SupplyError(f"supply on {self.port}: no reply to {command!r}")
        return reply

    def _number(self, command: str) -> float:
        text = self.query(command).rstrip("VAWvaw ")
        try:
            return float(text)
        except ValueError:
            raise SupplyError(f"supply on {self.port}: {command!r} returned {text!r}") from None

    def output_on(self) -> bool:
        return self.query(f":OUTPut{self.channel}:STATe?").upper() == "ON"

    # PowerSupply protocol (hw.base)
    def set_voltage(self, volts: float) -> None:
        if not 0 <= volts <= self.max_voltage_v:
            raise SupplyError(f"refusing to set {volts:.3f} V: the station limit is {self.max_voltage_v} V")
        if self.serial is None:
            raise SupplyError(f"supply on {self.port} is not open")
        self.serial.write(f"VSET{self.channel}:{volts:.3f}\n".encode())
        readback = self._number(f"VSET{self.channel}?")
        if abs(readback - volts) > 0.002:
            raise SupplyError(f"supply on {self.port}: set {volts:.3f} V but it reports {readback:.3f} V")

    def read_current(self) -> float:
        return self._number(f"IOUT{self.channel}?")

    def read_module_voltage(self) -> float:
        """The supply's output voltage. Without remote sense this is not the voltage at the
        module (the lab station showed about 0.26 V of drop at 2 A); see hw.base.PowerSupply."""
        return self._number(f"VOUT{self.channel}?")
