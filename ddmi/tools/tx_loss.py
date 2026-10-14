"""Tx Loss calibration, golden sample method (build map 6.13).

TxLoss[ch] = recorded true power of the golden sample - station reading.
"""
from __future__ import annotations

import threading
from typing import Callable

from ..hw.base import Bench


def calibrate_tx_loss(bench: Bench, golden_dbm: list[float], *, warn_db: float = 3.5,
                      log: Callable[[str], None] = print,
                      abort: threading.Event | None = None) -> list[float] | None:
    """Returns the 8 losses (CH1 -> CH8), or None if aborted. Does not save them."""
    losses = []
    for ch, golden in enumerate(golden_dbm, start=1):
        if abort is not None and abort.is_set():
            log("Tx Loss calibration aborted")
            return None
        reading = bench.read_tx_power(ch)
        loss = round(golden - reading, 2)
        note = f"  WARNING: above {warn_db} dB" if loss > warn_db else ""
        log(f"CH{ch}: golden {golden:.2f} dBm, station reads {reading:.2f} dBm -> Tx Loss {loss:.2f} dB{note}")
        losses.append(loss)
    return losses
