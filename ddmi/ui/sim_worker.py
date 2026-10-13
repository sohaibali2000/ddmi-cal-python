"""Stand-in workers that produce plausible numbers so the UI can be exercised.

These are NOT the test flow. They follow the step order and maths of the build
map (Sections 6 and 7) with made-up module data, and get replaced by the real
flow (ddmi/flow.py on top of ddmi/sim or the hardware drivers).
"""
from __future__ import annotations

import os
import random
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from ..calc.fit import two_point_fit
from ..calc.tops import tops_setpoint
from ..calc.units import dbm_to_lin as lin, lin_to_dbm as dbm
from . import params

SPEED = float(os.environ.get("DDMI_SIM_SPEED", "1.0"))


class Aborted(Exception):
    pass


class StopTest(Exception):
    pass


class _Worker(QThread):
    log = pyqtSignal(int, str)

    def __init__(self, slot_id: int):
        super().__init__()
        self.slot_id = slot_id
        self._abort = threading.Event()
        self.rng = random.Random()

    def abort(self):
        self._abort.set()

    def wait_s(self, seconds: float):
        if self._abort.wait(seconds * SPEED):
            raise Aborted()

    def say(self, text: str):
        self.log.emit(self.slot_id, text)


class SimWorker(_Worker):
    result = pyqtSignal(int, str, int, object)   # slot, param, ch (0 = module), value
    step = pyqtSignal(int, str, str)             # slot, step, running|done
    sn_read = pyqtSignal(int, str)
    done = pyqtSignal(int, str)                  # slot, "" | "ABORTED" | "FAIL: ..." | "ERROR: ..."

    def __init__(self, slot_id: int, scanned_sn: str, tx_loss: list[float], fault: str):
        super().__init__(slot_id)
        self.scanned_sn = scanned_sn
        self.tx_loss = tx_loss
        self.fault = fault

    def run(self):
        try:
            self._flow()
            self.done.emit(self.slot_id, "")
        except Aborted:
            self.done.emit(self.slot_id, "ABORTED")
        except StopTest as exc:
            self.done.emit(self.slot_id, f"FAIL: {exc}")
        except Exception as exc:  # shown in the slot log
            self.done.emit(self.slot_id, f"ERROR: {exc!r}")

    def put(self, param: str, ch: int, value):
        if value == 0:
            value = abs(value)   # show 0.0, not -0.0
        self.result.emit(self.slot_id, param, ch, value)

    def _begin(self, name: str):
        self.step.emit(self.slot_id, name, "running")
        self.say(f"=== {name} ===")

    def _end(self, name: str):
        self.step.emit(self.slot_id, name, "done")

    def _flow(self):
        rng = self.rng
        chans = range(1, params.CHANNELS + 1)
        self.say("[SIM] Simulated module and instruments - no hardware is touched.")

        self._begin("Startup")
        self.say("Reini: attenuators initialised, all 8 channels set to 1.0 dB")
        self.wait_s(0.4)
        self.say("Set_Temp: W_AWS_Test_Mode = 1")
        self.wait_s(0.3)
        vcc = 3.3 + rng.uniform(-0.004, 0.004)
        self.say(f"VCC: auto adjust to 3.300 V -> module reads {vcc:.3f} V")
        self.put("ICC(mA)", 0, round(rng.uniform(3150, 3400)))
        self.wait_s(0.4)
        module_sn = self.scanned_sn or f"SIM{rng.randint(100000, 999999)}"
        if self.fault == "SN mismatch":
            module_sn = f"SIM{rng.randint(100000, 999999)}"
        self.sn_read.emit(self.slot_id, module_sn)
        self.say(f"Read SN: module SN = {module_sn}")
        if self.scanned_sn and module_sn != self.scanned_sn:
            raise StopTest("Inconsistent internal and external SN")
        self._end("Startup")

        self._begin("Scan MPD")
        self.say("TOPS loop off. Sweep Vtops 0.50 -> 3.00 V, step 0.02 V (126 points), read TXMON")
        self.wait_s(1.5)
        for ch in chans:
            valley = rng.uniform(1.7, 2.1)
            peak = valley - rng.uniform(0.9, 1.1)
            txmon, mpd_out = rng.randint(1700, 2000), rng.randint(2850, 3050)
            setpoint = tops_setpoint(txmon, mpd_out)
            self.say(f"CH{ch}: peak {peak:.2f} V, valley {valley:.2f} V, "
                     f"TXMON {txmon} / MPD_OUT {mpd_out} -> tops_setpoint {setpoint}")
            self.put("Tops_Setpoint", ch, setpoint)
            self.wait_s(0.1)
        self.say("Scan_Disable: step tops_off +/-0.01 V toward darkest, up to 4 steps")
        for ch in chans:
            self.put("Tops_off(V)", ch, round(rng.uniform(1.7, 2.1), 2))
            self.put("Tx_Disable(dBm)", ch, round(rng.uniform(-48, -42), 2))
            self.wait_s(0.12)
        self.say("TOPS loop on")
        self._end("Scan MPD")

        self._begin("TX Cal")
        true_p = {ch: rng.uniform(0.5, 2.5) for ch in chans}
        gain = {ch: rng.uniform(0.35, 0.6) for ch in chans}
        points = {}
        for bias, delta in ((60, -3.2), (180, 2.4)):
            self.say(f"W_IBiasPID = 0, W_IBias = {bias} mA, wait 2 s")
            self.wait_s(1.0)
            for ch in chans:
                p = true_p[ch] + delta
                meter = p - self.tx_loss[ch - 1] + rng.gauss(0, 0.01)
                y = lin(meter + self.tx_loss[ch - 1])
                x = round(lin(p) * gain[ch]) + 40
                points.setdefault(ch, []).append((x, y))
                self.say(f"  CH{ch} @ {bias} mA: meter {meter:.2f} dBm + TxLoss "
                         f"{self.tx_loss[ch - 1]:.2f} -> y {y}, TXMON {x}")
        for ch in chans:
            (x1, y1), (x2, y2) = points[ch]
            slope, offset = two_point_fit((x1, y1), (x2, y2))
            points[ch] = (slope, offset)
            self.put("Txp_Slope", ch, round(slope, 4))
            self.put("Txp_Offset", ch, round(offset, 1))
        self.say("W_DTXPCal written (8 slope/offset pairs). Restore bias, W_Reset, wait 10 s")
        self.wait_s(1.5)
        self.say("TX check at normal bias")
        for ch in chans:
            slope, offset = points[ch]
            x = round(lin(true_p[ch]) * gain[ch]) + 40
            ddmi = dbm(slope * x + offset) + rng.gauss(0, 0.02)
            if self.fault == "Dead TX lane (CH3)" and ch == 3:
                ddmi -= rng.uniform(1.5, 4.0)
            real = true_p[ch] + rng.gauss(0, 0.01)
            self.put("TxP(dBm)", ch, round(real, 2))
            self.put("DDMI_TxP(dBm)", ch, round(ddmi, 2))
            self.put("dTxP(dB)", ch, round(real - ddmi, 2))
            self.wait_s(0.08)
        self._end("TX Cal")

        self._begin("RX Cal")
        g_rx = {ch: rng.uniform(3.8, 4.1) for ch in chans}
        rssi = {}
        for point in (-2, -10):
            for a, b in ((1, 5), (2, 6), (3, 7), (4, 8)):
                self.say(f"RX {point} dBm on CH{a}/CH{b}: set attenuation from RX table, read R_RSSI")
                for ch in (a, b):
                    value = round(lin(point) * g_rx[ch] + rng.gauss(0, 5))
                    rssi.setdefault(ch, []).append(value)
                    self.put(f"RSSI@{point}", ch, value)
                self.wait_s(0.3)
        for ch in chans:
            slope, offset = two_point_fit((rssi[ch][0], lin(-2)), (rssi[ch][1], lin(-10)))
            self.put("Rxp_Slope", ch, round(slope, 5))
            self.put("Rxp_Offset", ch, round(offset, 1))
        self.say("W_DRXPCal written. RX check at -6 dBm")
        self.wait_s(0.8)
        for ch in chans:
            ddmi = -6 + rng.gauss(0, 0.03)
            if self.fault == "RX offset (CH6)" and ch == 6:
                ddmi += 0.9
            self.put("DD_RxP@-6(dBm)", ch, round(ddmi, 2))
            self.put("dRxP(dB)", ch, round(ddmi + 6, 2))
            self.wait_s(0.08)
        self._end("RX Cal")

        self._begin("VCC Cal")
        self.say("Unlock: W_Debug_AWS = 1, W_Daul_AWS = 5. W_VccCal = [0, 0]")
        adc = []
        for target in (3.13, 3.47):
            got = target + rng.uniform(-0.004, 0.004)
            code = round(8000 * got + rng.gauss(0, 2))
            adc.append((code, got * 10000))
            self.say(f"VCC {target:.2f} V -> achieved {got:.3f} V, R_VCCADC {code}")
            self.wait_s(0.9)
        slope, offset = two_point_fit(adc[0], adc[1])
        self.put("Vcc_Slope", 0, round(slope, 4))
        self.put("Vcc_Offset", 0, round(offset, 1))
        self.say("W_VccCal written. Lock: W_Nomal_AWS = 0, W_Mission_AWS = 0")
        real = 3.3 + rng.uniform(-0.003, 0.003)
        ddmi = (slope * round(8000 * real) + offset) / 10000 + rng.gauss(0, 0.001)
        self.put("Real_Vcc(V)", 0, round(real, 3))
        self.put("DDMI_Vcc(V)", 0, round(ddmi, 3))
        self.put("dVcc(V)", 0, round(real - ddmi, 3))
        self.put("DDMI_Temp(C)", 0, round(42.5 + rng.gauss(0, 0.4), 2))
        self.wait_s(0.5)
        self._end("VCC Cal")

        self._begin("Final")
        self.say("ShowPF: Disable Temp Off Func, W_TestMode = 0xCC, W_Reset, check DSP mode")
        self.wait_s(0.8)
        self.say("Move_out: build result record")
        self._end("Final")


class TxLossWorker(_Worker):
    done = pyqtSignal(int, object)   # slot, list of 8 losses or None

    def run(self):
        try:
            self.say("[SIM] Tx Loss calibration (golden sample method): "
                     "TxLoss[ch] = recorded true power - station reading")
            losses = []
            for ch in range(1, params.CHANNELS + 1):
                golden = self.rng.uniform(0.8, 2.4)
                loss = self.rng.uniform(1.0, 2.2)
                self.say(f"CH{ch}: golden {golden:.2f} dBm, station reads "
                         f"{golden - loss:.2f} dBm -> Tx Loss {loss:.2f} dB")
                losses.append(round(loss, 2))
                self.wait_s(0.25)
            self.done.emit(self.slot_id, losses)
        except Aborted:
            self.say("Tx Loss calibration aborted")
            self.done.emit(self.slot_id, None)


class RxTableWorker(_Worker):
    cell = pyqtSignal(int, int, float)   # att (dB), ch, power (-dBm, positive)
    progress = pyqtSignal(int)
    done = pyqtSignal(int, object)       # slot, table or None

    ATTS = list(range(1, 21))

    def run(self):
        try:
            self.say("[SIM] RX table: per channel, others at 40 dB, step 1...20 dB in 1 dB steps")
            table, n, total = {}, 0, params.CHANNELS * len(self.ATTS)
            for ch in range(1, params.CHANNELS + 1):
                base = self.rng.uniform(1.5, 2.0)
                rows = []
                for att in self.ATTS:
                    power = round(att - base + self.rng.gauss(0, 0.01), 2)
                    rows.append((float(att), power))
                    self.cell.emit(att, ch, power)
                    n += 1
                    self.progress.emit(round(100 * n / total))
                    self.wait_s(0.02)
                table[ch] = rows
                self.say(f"CH{ch}: done ({len(rows)} points)")
            self.done.emit(self.slot_id, table)
        except Aborted:
            self.say("RX table calibration aborted")
            self.done.emit(self.slot_id, None)
