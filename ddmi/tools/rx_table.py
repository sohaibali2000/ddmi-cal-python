"""Build the RX attenuation table (build map 6.13; 0-30ATT.vi -> Att Calibration.vi).

Per lane, with the other fibre at 40 dB, set the attenuator to the lane's wavelength,
step it as Att Calibration.vi V6.8 does (Start 0 to Stop 20 in 1 dB steps, a 0 replaced
by 1: 1, 1, 2 ... 20), wait "ATT Delay(s)" + 0.5 s, and read the external power meter at
the module's RX input. At the end every attenuator channel goes back to 1 dB.

As on the production station, one external meter is used: it sits on the RX1 fibre for
lanes 1-4, then the operator moves it to RX2 for lanes 5-8 (on_prompt asks for that).
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from ..hw.base import Bench

ATT_STEPS_DB = list(range(1, 21))                          # the table's distinct steps (display)
ATT_SEQUENCE_DB = [max(1, att) for att in range(0, 21)]     # Att Calibration.vi: 0 -> 1, then 1..20
OTHER_CHANNELS_DB = 40.0
END_ATT_DB = 1.0
POINT_WAIT_EXTRA_S = 0.5                                    # wait = "ATT Delay(s)" + 0.5 s


def point_wait_s(rx_cal_cfg: dict) -> float:
    return rx_cal_cfg.get("att_delay_s", 0.0) + POINT_WAIT_EXTRA_S


def build_rx_table(bench: Bench, wavelengths_nm: list[float], *, settle_s: float = 0.0,
                   log: Callable[[str], None] = print,
                   on_point: Callable[[int, int, float], None] = lambda _att, _ch, _p: None,
                   on_prompt: Callable[[str], bool] = lambda _msg: True,
                   abort: threading.Event | None = None) -> dict | None:
    """Returns {ch: [(att dB, power as -dBm positive), ...]}, or None if aborted. Does not save."""
    if bench.rx_meter is None:
        raise RuntimeError("no power meter at the module RX input is configured")
    channels = range(1, len(wavelengths_nm) + 1)
    table = {}
    attenuator = bench.attenuator
    fibre = None
    for ch in channels:
        if attenuator.channel(ch) != fibre:
            fibre = attenuator.channel(ch)
            lanes = [lane for lane in channels if attenuator.channel(lane) == fibre]
            index = attenuator.physical_channels.index(fibre) + 1
            if not on_prompt(f"Connect the external power meter to the DUT position's RX{index} fibre "
                             f"(attenuator CH{fibre}) for lanes {lanes[0]}-{lanes[-1]}, then continue."):
                log("RX table calibration cancelled")
                return None
            log(f"Measuring lanes {lanes[0]}-{lanes[-1]} on RX{index} (attenuator CH{fibre})")
        # Darken the other fibres; this lane's own fibre is stepped below.
        for other in attenuator.physical_channels:
            if other != attenuator.channel(ch):
                attenuator.set_channel_attenuation(other, OTHER_CHANNELS_DB)
        attenuator.set_wavelength(ch, wavelengths_nm[ch - 1])
        rows = []
        for att in ATT_SEQUENCE_DB:
            if att < attenuator.min_att_db:
                continue            # never below the station minimum
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
        log(f"CH{ch}: {len(rows)} points, {-rows[0][1]:.2f} dBm at {rows[0][0]:g} dB "
            f"to {-rows[-1][1]:.2f} dBm at {rows[-1][0]:g} dB")
    for fibre in attenuator.physical_channels:                # Att Calibration.vi: all back to 1 dB
        attenuator.set_channel_attenuation(fibre, max(END_ATT_DB, attenuator.min_att_db))
    return table
