"""Build the RX attenuation table (build map 6.13; 0-30ATT.vi -> Att Calibration.vi).

Per channel, with the other channels at 40 dB, step the attenuator 1...20 dB and
read the power at the module input.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..hw.base import Bench

ATT_STEPS_DB = list(range(1, 21))
OTHER_CHANNELS_DB = 40.0


def build_rx_table(bench: Bench, wavelengths_nm: list[float], *, settle_s: float = 0.0,
                   log: Callable[[str], None] = print,
                   on_point: Callable[[int, int, float], None] = lambda _att, _ch, _p: None,
                   abort: threading.Event | None = None) -> dict | None:
    """Returns {ch: [(att dB, power as -dBm positive), ...]}, or None if aborted. Does not save."""
    if bench.rx_meter is None:
        raise RuntimeError("no power meter at the module RX input is configured")
    channels = range(1, len(wavelengths_nm) + 1)
    table = {}
    for ch in channels:
        for other in channels:
            bench.attenuator.set_attenuation(other, OTHER_CHANNELS_DB)
        bench.attenuator.set_wavelength(ch, wavelengths_nm[ch - 1])
        rows = []
        for att in ATT_STEPS_DB:
            if abort is not None and abort.is_set():
                log("RX table calibration aborted")
                return None
            bench.attenuator.set_attenuation(ch, att)
            if settle_s:
                time.sleep(settle_s)
            power = round(-bench.rx_meter.read_rx_input_dbm(ch), 2)
            rows.append((float(att), power))
            on_point(att, ch, power)
        table[ch] = rows
        log(f"CH{ch}: {len(rows)} points, {-rows[0][1]:.2f} dBm at {ATT_STEPS_DB[0]} dB "
            f"to {-rows[-1][1]:.2f} dBm at {ATT_STEPS_DB[-1]} dB")
    return table
