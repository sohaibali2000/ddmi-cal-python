"""What every step function receives."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from .calc.rxtable import RxTable
from .hw.base import Bench
from .hw.module import Module
from .results import Results


class Aborted(Exception):
    """The operator pressed Abort."""


class StopTest(Exception):
    """The run cannot continue; the message is the failure reason."""


@dataclass
class RunContext:
    product: dict
    module: Module
    bench: Bench
    results: Results
    tx_loss: list[float]
    rx_tables: dict[int, RxTable]
    scanned_sn: str = ""
    time_scale: float = 1.0
    log: Callable[[str], None] = print          # operator-level lines
    detail: Callable[[str], None] = lambda _t: None   # raw values; log file only
    on_step: Callable[[str, str], None] = lambda _name, _state: None
    on_data: Callable[[str, dict], None] = lambda _kind, _data: None   # live data for graphs
    abort: threading.Event = field(default_factory=threading.Event)
    meta: dict = field(default_factory=dict)

    @property
    def channels(self) -> range:
        return range(1, self.product["channels"] + 1)

    def check_abort(self) -> None:
        if self.abort.is_set():
            raise Aborted()

    def sleep(self, seconds: float) -> None:
        if self.abort.wait(seconds * self.time_scale):
            raise Aborted()

    def pre_run(self, name: str, fallback: str):
        """A register's value as read before the run (restored instead of a guessed constant);
        the register map's named value when no backup was taken."""
        values = self.meta.get("pre_run")
        if values and name in values:
            return values[name]
        return self.module.regmap.value(fallback)

    def true_tx_powers(self) -> dict[int, float]:
        """true_dBm = meter + TxLoss[ch], all channels from one read of the 8-channel meter."""
        meter = self.bench.read_tx_powers()
        true = {ch: meter[ch - 1] + self.tx_loss[ch - 1] for ch in self.channels}
        self.detail("TX power: " + ", ".join(
            f"CH{ch} {meter[ch - 1]:.3f}+{self.tx_loss[ch - 1]:.3f}={true[ch]:.3f}" for ch in self.channels))
        return true

    def true_tx_power(self, ch: int) -> float:
        return self.true_tx_powers()[ch]
