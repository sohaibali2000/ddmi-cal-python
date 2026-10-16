"""Simulated module: the registers of the AWS map backed by simple physics (build map Section 8)."""
from __future__ import annotations

import math
import random

from ..hw.regmap import RegMap

LANES = 8
LASERS = 4
NORMAL_BIAS_MA = 110.0
FAULTS = ("none", "dead_tx_lane", "rx_nonlinear", "sn_mismatch", "vendor_writes_ignored", "tops_unlocked")


def _dbm(mw: float) -> float:
    return 10 * math.log10(mw) if mw > 0 else -100.0


class SimModule:
    """Implements the I2CBus interface. Reads and writes use the same addresses as the real module."""

    def __init__(self, regmap: RegMap, *, sn: str = "SIM000001", pn: str = "SIM-800G-2XFR4",
                 seed: int | None = None, fault: str = "none", fault_lane: int = 3):
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}; choose from {FAULTS}")
        self.regmap = regmap
        self.sn, self.pn = sn, pn
        self.fault, self.fault_lane = fault, fault_lane
        rng = self.rng = random.Random(seed)

        # fixed properties of this module
        self.phi0 = [rng.uniform(0, 2 * math.pi) for _ in range(LANES)]
        self.insertion = [0.8 * rng.uniform(0.9, 1.1) for _ in range(LANES)]
        self.floor = 1e-5
        self.g_tx = [0.3 * rng.uniform(0.9, 1.1) for _ in range(LANES)]   # real module: TXMON ~500 at ~1.7 mW
        self.g_mpd = [0.19 * rng.uniform(0.9, 1.1) for _ in range(LANES)]  # same ratio to g_tx as before
        self.dark = [rng.uniform(30, 60) for _ in range(LANES)]
        self.g_rx = [1.0 * rng.uniform(0.95, 1.05) for _ in range(LANES)]   # counts per uW -> slope ~10
        self.temp_c = 42.5
        if fault == "dead_tx_lane":
            self.insertion[fault_lane - 1] = 1e-4

        # state the firmware keeps
        self.v_cmd = [1.0] * LANES
        self.tops_loop = True
        # tops_unlocked: after a reset the fault lane's loop sits at the top of its fringe
        # until Vtops is written with the loop off (seen on hardware 2026-10-15).
        self.stuck = fault == "tops_unlocked"
        self.bias_loop = True
        self.bias_cmd = [NORMAL_BIAS_MA] * LASERS
        self.setpoint = [0] * LANES
        self.tops_off = [0.0] * LANES
        self.tops_room = [0.0] * LANES
        self.tx_dis = 0
        self.tx_cal = ([1.0] * LANES, [0.0] * LANES)
        self.rx_cal = ([1.0] * LANES, [0.0] * LANES)
        self.vcc_cal = (25.0, 0.0)      # deliberately off, so the VCC cal has work to do
        self.modes = {"AWS_Test_Mode": 0, "TestMode": 0, "DSP_TestMode": 0,
                      "DSP_OperatingMode": 0, "TempOffFunc": 1}
        self.resets = 0

        # set by the bench
        self.v_module = 3.3
        self.rx_in_dbm = lambda ch: -40.0

    # ---- physics ----
    def bias_ma(self, laser: int) -> float:
        return NORMAL_BIAS_MA if self.bias_loop else self.bias_cmd[laser]

    def laser_mw(self, lane: int) -> float:
        return max(0.0, 0.04 * (self.bias_ma(lane % LASERS) - 15))   # ~2 dBm out, TXMON peak ~1000 like the real module

    def transmission(self, lane: int, v: float) -> float:
        fringe = 0.5 * (1 + math.cos(self.phi0[lane] + 0.8 * v * v))
        return self.insertion[lane] * (self.floor + (1 - self.floor) * fringe)

    def _ratio(self, lane: int, v: float) -> float:
        laser_uw = self.laser_mw(lane) * 1000
        if laser_uw <= 0:
            return 0.0
        txmon = self.g_tx[lane] * laser_uw * self.transmission(lane, v) + self.dark[lane]
        return txmon / (self.g_mpd[lane] * laser_uw) * 1000

    def _locked_v(self, lane: int) -> float:
        """TOPS loop: the voltage nearest the last commanded one where TXMON/MPD_OUT*1000 = setpoint."""
        v0, target = self.v_cmd[lane], self.setpoint[lane]
        best, step = None, 0.001
        v = max(0.0, v0 - 0.4)
        prev = self._ratio(lane, v) - target
        while v < v0 + 0.4:
            cur = self._ratio(lane, v + step) - target
            if prev == 0 or prev * cur < 0:
                cross = v + step * prev / (prev - cur) if prev != cur else v
                if best is None or abs(cross - v0) < abs(best - v0):
                    best = cross
            v, prev = v + step, cur
        return v0 if best is None else best

    def lane_v(self, lane: int) -> float:
        if self.tx_dis >> lane & 1:
            return self.tops_off[lane]
        if self.stuck and self.tops_loop and lane == self.fault_lane - 1:
            return max((i / 100 for i in range(301)), key=lambda v: self.transmission(lane, v))
        if self.tops_loop and self.setpoint[lane] > 0:
            return self._locked_v(lane)
        return self.v_cmd[lane]

    def tx_out_mw(self, lane: int) -> float:
        return self.laser_mw(lane) * self.transmission(lane, self.lane_v(lane))

    def tx_out_dbm(self, ch: int) -> float:
        return _dbm(self.tx_out_mw(ch - 1))

    def _txmon(self, lane: int) -> int:
        value = self.g_tx[lane] * self.tx_out_mw(lane) * 1000 + self.dark[lane]
        return round(value + self.rng.gauss(0, 1.5))

    def _mpd_out(self, lane: int) -> int:
        return round(self.g_mpd[lane] * self.laser_mw(lane) * 1000 + self.rng.gauss(0, 2))

    def _rssi(self, lane: int) -> int:
        uw = 10 ** (self.rx_in_dbm(lane + 1) / 10) * 1000
        if self.fault == "rx_nonlinear" and lane == self.fault_lane - 1:
            uw = uw / (1 + uw / 800)
        return max(0, round(self.g_rx[lane] * uw + self.rng.gauss(0, 0.2)))

    def _vcc_adc(self) -> int:
        return round(430 * self.v_module - 25 + self.rng.gauss(0, 0.5))   # real module: ~1320 counts at 3.13 V

    # ---- I2C ----
    def read(self, page: int, byte: int, length: int) -> bytes:
        reg = self.regmap.at(page, byte)
        if reg is None or reg.length != length:
            return bytes(length)
        return reg.encode(self._read_value(reg.name))

    def _read_value(self, name: str):
        lanes = range(LANES)
        if name == "R_DTEMP":
            return self.temp_c + self.rng.gauss(0, 0.02)
        if name == "R_DVCC":
            return (self.vcc_cal[0] * self._vcc_adc() + self.vcc_cal[1]) / 10000
        if name == "R_Information":
            return self.pn.ljust(16)[:16] + "01" + self.sn.ljust(16)[:16]
        if name == "R_DTXP":
            return [self.tx_cal[0][i] * self._txmon(i) + self.tx_cal[1][i] for i in lanes]
        if name == "R_DRXP":
            return [self.rx_cal[0][i] * self._rssi(i) + self.rx_cal[1][i] for i in lanes]
        if name == "R_DBias":
            return [self.bias_ma(i % LASERS) * 100 for i in lanes]
        if name == "R_TXMON":
            return [self._txmon(i) for i in lanes]
        if name == "R_MPD_OUT":
            return [self._mpd_out(i) for i in lanes]
        if name == "R_RSSI":
            return [self._rssi(i) for i in lanes]
        if name == "R_VCCADC":
            return self._vcc_adc()
        if name == "R_Bias_setpoint":
            return [300, 300, 430, 470]
        if name == "IBias":
            return [self.bias_ma(k) for k in range(LASERS)]
        if name == "W_IBiasPID":
            return 15 if self.bias_loop else 0
        if name == "W_TOPPID":
            return 0xFF if self.tops_loop else 0
        if name == "Vtops":
            return [self.lane_v(i) for i in lanes]
        if name == "tops_setpoint":
            return list(self.setpoint)
        if name == "tops_off":
            return list(self.tops_off)
        if name == "tops_room":
            return list(self.tops_room)
        if name == "W_TXDis":
            return self.tx_dis
        if name in ("DTXPCal_A", "DTXPCal_B", "DRXPCal_A", "DRXPCal_B"):
            slopes, offsets = self.tx_cal if name.startswith("DTXP") else self.rx_cal
            half = slice(0, 4) if name.endswith("_A") else slice(4, 8)
            return offsets[half] + slopes[half]        # offsets first, as on the real module
        if name == "VccCal":
            return [self.vcc_cal[1], self.vcc_cal[0]]  # offset, slope
        if name in self.modes:
            return self.modes[name]
        return 0

    def write(self, page: int, byte: int, data: bytes) -> None:
        reg = self.regmap.at(page, byte)
        if reg is None or reg.length != len(data):
            return
        if self.fault == "vendor_writes_ignored" and page >= 0x80 and byte >= 0x80:
            return      # like the real module with only the read password entered
        name, value = reg.name, reg.decode(data)
        if name == "W_Reset":
            if value & 0x08:
                self.tops_loop = self.bias_loop = True
                self.resets += 1
                self.stuck = self.fault == "tops_unlocked"
        elif name == "W_TXDis":
            self.tx_dis = value
        elif name == "IBias":
            self.bias_cmd = list(value)
        elif name == "W_IBiasPID":
            self.bias_loop = value != 0
        elif name == "W_TOPPID":
            self.tops_loop = value != 0
        elif name == "Vtops":
            self.v_cmd = list(value)
            if not self.tops_loop:
                self.stuck = False
        elif name == "tops_setpoint":
            self.setpoint = list(value)
        elif name == "tops_off":
            self.tops_off = list(value)
        elif name == "tops_room":
            self.tops_room = list(value)
        elif name in ("DTXPCal_A", "DTXPCal_B", "DRXPCal_A", "DRXPCal_B"):
            slopes, offsets = self.tx_cal if name.startswith("DTXP") else self.rx_cal
            start = 0 if name.endswith("_A") else 4
            offsets[start:start + 4] = value[:4]
            slopes[start:start + 4] = value[4:]
        elif name == "VccCal":
            self.vcc_cal = (value[1], value[0])        # stored offset, slope
        elif name in self.modes:
            self.modes[name] = value
