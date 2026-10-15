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
        self.unlocked = False

    VENDOR_PAGES = 0x80     # pages 80h-FFh are vendor pages: locked until the password is entered

    def unlock(self) -> None:
        """Enter the vendor password (CMIS bytes 122-125). Needed again after a module reset.

        Sent even in a dry run: it changes no module setting, and without it the vendor
        pages cannot be read at all.
        """
        if "vendor_password" not in self.regmap.values:
            return
        reg = self.regmap["W_Password"]
        self.bus.write(reg.page, reg.byte, reg.encode(self.regmap.value("vendor_password")))
        self.log(f"I2C W W_Password [{reg.address}] (vendor password entry)")
        if hasattr(self.bus, "forget_page"):
            self.bus.forget_page()
        self.unlocked = True

    def _ensure_unlocked(self, reg) -> None:
        if reg.page >= self.VENDOR_PAGES and reg.byte >= 0x80 and not self.unlocked:
            self.unlock()

    def read(self, name: str):
        reg = self.regmap[name]
        self._ensure_unlocked(reg)
        data = self.bus.read(reg.page, reg.byte, reg.length)
        value = reg.decode(data)
        self.log(f"I2C R {name} [{reg.address}] = {data.hex()} -> {_show(value)}")
        return value

    def write(self, name: str, value) -> None:
        """The only way a value reaches the module.

        With dry_run the write is only logged (marked when the register is unverified).
        Otherwise, on real hardware, a register marked UNVERIFIED is refused unless
        allow_unverified is set.
        """
        reg = self.regmap[name]
        data = reg.encode(value)
        text = f"{name} [{reg.address}] = {_show(value)} -> {data.hex()}"
        if self.dry_run:
            flag = "" if reg.verified else " [UNVERIFIED register]"
            self.log(f"I2C W (dry-run, not sent){flag} {text}")
            return
        if not self.is_sim and not reg.verified and not self.allow_unverified:
            raise UnverifiedWriteError(
                f"refused write to unverified register {name} [{reg.address}]. "
                "Confirm it with firmware, or pass --allow-unverified.")
        self._ensure_unlocked(reg)
        self.bus.write(reg.page, reg.byte, data)
        self.log(f"I2C W {text}")
        if name == "W_Reset":
            self.unlocked = False       # a reset locks the vendor pages again
            if hasattr(self.bus, "forget_page"):
                self.bus.forget_page()

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
        """8 slope/offset pairs: CH1-4 to the _A page, CH5-8 to the _B page.
        Each page holds the 4 offsets first, then the 4 slopes (read from a real module)."""
        self.write(f"{name}_A", list(offsets[:4]) + list(slopes[:4]))
        self.write(f"{name}_B", list(offsets[4:]) + list(slopes[4:]))

    def read_cal(self, name: str) -> tuple[list, list]:
        """Returns (slopes, offsets), CH1 first."""
        a, b = self.read(f"{name}_A"), self.read(f"{name}_B")
        return a[4:] + b[4:], a[:4] + b[:4]

    def write_vcc_cal(self, slope: float, offset: float) -> None:
        """VccCal holds the offset first, then the slope."""
        self.write("VccCal", [offset, slope])

    def read_vcc_cal(self) -> tuple[float, float]:
        """Returns (slope, offset)."""
        offset, slope = self.read("VccCal")
        return slope, offset


def _show(value) -> str:
    if isinstance(value, float):
        return f"{value:.5g}"
    if isinstance(value, list):
        return "[" + ", ".join(_show(v) for v in value) + "]"
    return repr(value) if isinstance(value, str) else str(value)
