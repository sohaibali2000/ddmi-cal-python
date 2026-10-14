"""Named register access to the module. Every module write goes through Module.write."""
from __future__ import annotations

from typing import Callable

from .base import I2CBus
from .regmap import RegMap


class UnverifiedWriteError(RuntimeError):
    pass


class Module:
    def __init__(self, bus: I2CBus, regmap: RegMap, *, is_sim: bool, dry_run: bool = False,
                 allow_unverified: bool = False, log: Callable[[str], None] = lambda _t: None):
        self.bus = bus
        self.regmap = regmap
        self.is_sim = is_sim
        self.dry_run = dry_run
        self.allow_unverified = allow_unverified
        self.log = log

    def read(self, name: str):
        reg = self.regmap[name]
        data = self.bus.read(reg.page, reg.byte, reg.length)
        value = reg.decode(data)
        self.log(f"I2C R {name} [{reg.address}] = {data.hex()} -> {_show(value)}")
        return value

    def write(self, name: str, value) -> None:
        """The only way a value reaches the module.

        On real hardware a register marked UNVERIFIED is refused unless
        allow_unverified is set. With dry_run the write is logged, not sent.
        """
        reg = self.regmap[name]
        data = reg.encode(value)
        text = f"{name} [{reg.address}] = {_show(value)} -> {data.hex()}"
        if not self.is_sim and not reg.verified and not self.allow_unverified:
            raise UnverifiedWriteError(
                f"refused write to unverified register {name} [{reg.address}]. "
                "Confirm it with firmware, or pass --allow-unverified.")
        if self.dry_run:
            self.log(f"I2C W (dry-run, not sent) {text}")
            return
        self.bus.write(reg.page, reg.byte, data)
        self.log(f"I2C W {text}")

    def write_value(self, name: str, value_name: str) -> None:
        """Write one of the named constants from the register map's `values`."""
        self.write(name, self.regmap.value(value_name))

    def update_lane(self, name: str, lane: int, value) -> list:
        """Read an 8-lane register, change one lane (1-based), write it back."""
        values = self.read(name)
        values[lane - 1] = value
        self.write(name, values)
        return values

    def write_cal(self, name: str, slopes: list, offsets: list) -> None:
        """8 slope/offset pairs: CH1-4 to the _A page, CH5-8 to the _B page."""
        self.write(f"{name}_A", list(slopes[:4]) + list(offsets[:4]))
        self.write(f"{name}_B", list(slopes[4:]) + list(offsets[4:]))

    def read_cal(self, name: str) -> tuple[list, list]:
        a, b = self.read(f"{name}_A"), self.read(f"{name}_B")
        return a[:4] + b[:4], a[4:] + b[4:]


def _show(value) -> str:
    if isinstance(value, float):
        return f"{value:.5g}"
    if isinstance(value, list):
        return "[" + ", ".join(_show(v) for v in value) + "]"
    return repr(value) if isinstance(value, str) else str(value)
