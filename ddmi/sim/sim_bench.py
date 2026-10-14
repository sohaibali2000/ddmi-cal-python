"""Simulated instruments around one SimModule: supply, power meter, switch, attenuators."""
from __future__ import annotations

import random

from ..hw.base import Bench
from .sim_module import LANES, SimModule


class SimBench:
    """One object implements every instrument interface of hw.base.

    Path losses depend only on `seed` (use the slot number), so Tx Loss and RX
    tables saved by one run stay valid for the next.
    """

    SOURCE_DBM = 3.0
    R_DROP_OHM = 0.02

    def __init__(self, module: SimModule, seed: int = 1):
        self.module = module
        paths = random.Random(f"paths-{seed}")
        self.tx_path_loss = [round(paths.uniform(1.0, 2.2), 2) for _ in range(LANES)]
        self.rx_path_loss = [round(paths.uniform(1.0, 1.5), 2) for _ in range(LANES)]
        self.rng = random.Random()
        self.atts = [40.0] * LANES
        self.wavelengths = [0.0] * LANES
        self.switch_ch = 1
        self.v_set = 3.3
        self.current_a = 3.2
        module.rx_in_dbm = self.rx_input_dbm
        self._apply_supply()

    def as_bench(self) -> Bench:
        return Bench(power_supply=self, power_meter=self, opt_switch=self, attenuator=self,
                     rx_meter=self)

    # power supply
    def _apply_supply(self):
        self.module.v_module = self.v_set - self.current_a * self.R_DROP_OHM

    def set_voltage(self, volts: float) -> None:
        self.v_set = volts
        self._apply_supply()

    def read_current(self) -> float:
        return self.current_a + self.rng.gauss(0, 0.005)

    def read_module_voltage(self) -> float:
        return self.module.v_module + self.rng.gauss(0, 0.0005)

    # optical switch + TX power meter
    def select(self, ch: int) -> None:
        self.switch_ch = ch

    def read_dbm(self) -> float:
        ch = self.switch_ch
        level = self.module.tx_out_dbm(ch) - self.tx_path_loss[ch - 1] + self.rng.gauss(0, 0.005)
        return max(level, -70.0)

    # attenuators
    def set_wavelength(self, ch: int, nm: float) -> None:
        self.wavelengths[ch - 1] = nm

    def set_attenuation(self, ch: int, db: float) -> None:
        self.atts[ch - 1] = db

    def rx_input_dbm(self, ch: int) -> float:
        return self.SOURCE_DBM - self.atts[ch - 1] - self.rx_path_loss[ch - 1]

    def read_rx_input_dbm(self, ch: int) -> float:
        """A power meter held at the module's RX input, as used to build the RX table."""
        return self.rx_input_dbm(ch) + self.rng.gauss(0, 0.005)
