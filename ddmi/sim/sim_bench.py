"""Simulated instruments around one SimModule: supply, 8-channel power meter, attenuators."""
from __future__ import annotations

import random

from ..hw.base import Bench, LaneAttenuator
from .sim_module import LANES, SimModule


class SimBench:
    """One object implements every instrument interface of hw.base.

    Path losses depend only on `seed` (use the slot number), so Tx Loss and RX
    tables saved by one run stay valid for the next.
    """

    SOURCE_DBM = 3.0
    R_DROP_OHM = 0.02

    def __init__(self, module: SimModule, seed: int = 1, lane_channels: list[int] | None = None,
                 min_att_db: float = 0.0):
        self.module = module
        self.lane_channels = lane_channels or list(range(1, LANES + 1))
        self.min_att_db = min_att_db
        paths = random.Random(f"paths-{seed}")
        self.tx_path_loss = [round(paths.uniform(1.0, 2.2), 2) for _ in range(LANES)]
        self.rx_path_loss = [round(paths.uniform(1.0, 1.5), 2) for _ in range(LANES)]
        self.rng = random.Random()
        self.atts = [40.0] * LANES
        self.wavelengths = [0.0] * LANES
        self.meter_wavelengths: list[int] = []
        self.meter_avg_ms = 0
        self.v_set = 3.3
        self.current_a = 3.2
        module.rx_in_dbm = self.rx_input_dbm
        self._apply_supply()

    def as_bench(self) -> Bench:
        return Bench(power_supply=self, power_meter=self, rx_meter=self,
                     attenuator=LaneAttenuator(self, self.lane_channels, self.min_att_db))

    # power supply
    def _apply_supply(self):
        self.module.v_module = self.v_set - self.current_a * self.R_DROP_OHM

    def set_voltage(self, volts: float) -> None:
        self.v_set = volts
        self._apply_supply()

    def set_current_limit(self, amps: float) -> None:
        self.current_limit_a = amps

    def read_current(self) -> float:
        return self.current_a + self.rng.gauss(0, 0.005)

    def read_output_voltage(self) -> float:
        return self.v_set

    def read_module_voltage(self) -> float:
        return self.module.v_module + self.rng.gauss(0, 0.0005)

    # 8-channel TX power meter
    def configure(self, wavelengths_nm: list[int], avg_time_ms: int) -> None:
        self.meter_wavelengths = list(wavelengths_nm)
        self.meter_avg_ms = avg_time_ms

    def read_all_dbm(self) -> list[float]:
        return [max(self.module.tx_out_dbm(ch) - self.tx_path_loss[ch - 1] + self.rng.gauss(0, 0.005), -70.0)
                for ch in range(1, LANES + 1)]

    # attenuators
    def set_wavelength(self, ch: int, nm: float) -> None:
        self.wavelengths[ch - 1] = nm

    def set_attenuation(self, ch: int, db: float) -> None:
        self.atts[ch - 1] = db

    def rx_input_dbm(self, ch: int) -> float:
        """Power at DUT lane ch: the attenuator channel feeding that lane's fibre sets it."""
        return self.SOURCE_DBM - self.atts[self.lane_channels[ch - 1] - 1] - self.rx_path_loss[ch - 1]

    def read_rx_input_dbm(self, ch: int) -> float:
        """A power meter held at the module's RX input, as used to build the RX table."""
        return self.rx_input_dbm(ch) + self.rng.gauss(0, 0.005)
