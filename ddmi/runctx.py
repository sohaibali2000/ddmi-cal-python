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

    def true_tx_power(self, ch: int) -> float:
        """true_dBm = meter + TxLoss[ch]."""
        meter = self.bench.read_tx_power(ch)
        true = meter + self.tx_loss[ch - 1]
        self.detail(f"TX power CH{ch}: meter {meter:.3f} dBm + TxLoss {self.tx_loss[ch - 1]:.2f} = {true:.3f} dBm")
        return true
