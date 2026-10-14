"""Background threads that run the engine for the UI and report back through Qt signals."""
from __future__ import annotations

import threading

from PyQt5.QtCore import QThread, pyqtSignal

from ..runner import run_slot
from ..station import build_station
from ..tools.rx_table import ATT_STEPS_DB, build_rx_table
from ..tools.tx_loss import calibrate_tx_loss


class _Worker(QThread):
    log = pyqtSignal(int, str)

    def __init__(self, slot_id: int, station_cfg: dict, product: dict):
        super().__init__()
        self.slot_id = slot_id
        self.station_cfg = station_cfg
        self.product = product
        self._abort = threading.Event()

    def abort(self):
        self._abort.set()

    def say(self, text: str):
        self.log.emit(self.slot_id, text)


class FlowWorker(_Worker):
    result = pyqtSignal(int, str, int, object, bool)   # slot, item, ch (0 = module), value, ok
    step = pyqtSignal(int, str, str)                   # slot, stage, running|done
    sn_read = pyqtSignal(int, str)
    done = pyqtSignal(int, object)                     # slot, record

    def __init__(self, slot_id: int, station_cfg: dict, product: dict, *, scanned_sn: str,
                 user: str, plan: str, fault: str, dry_run: bool, allow_unverified: bool):
        super().__init__(slot_id, station_cfg, product)
        self.kwargs = dict(scanned_sn=scanned_sn, user=user, plan=plan, fault=fault,
                           dry_run=dry_run, allow_unverified=allow_unverified)

    def run(self):
        slot = self.slot_id
        try:
            record = run_slot(
                station_cfg=self.station_cfg, product=self.product, slot=slot, abort=self._abort,
                log=self.say,
                on_result=lambda item, ch, value, ok: self.result.emit(slot, item, ch, value, ok),
                on_step=lambda name, state: self.step.emit(slot, name, state),
                on_sn=lambda sn: self.sn_read.emit(slot, sn),
                **self.kwargs,
            )
        except Exception as exc:   # e.g. no hardware driver; shown in the slot log
            reason = f"{type(exc).__name__}: {exc}"
            self.say(f"ERROR: {reason}")
            record = {"result": "ERROR", "failing_items": [reason], "sn": self.kwargs["scanned_sn"]}
        self.done.emit(slot, record)


class TxLossWorker(_Worker):
    done = pyqtSignal(int, object)   # slot, list of 8 losses or None

    def run(self):
        losses = None
        try:
            station = build_station(self.station_cfg, slot=self.slot_id)
            if station.sim_module is None:
                raise NotImplementedError("the golden sample's recorded power is needed on real hardware")
            channels = range(1, self.product["channels"] + 1)
            golden = [station.sim_module.tx_out_dbm(ch) for ch in channels]
            self.say("Simulator: the simulated module's true output power is the golden record")
            losses = calibrate_tx_loss(station.bench, golden, log=self.say, abort=self._abort,
                                       warn_db=self.product["limits"]["tx_loss_warn_db"])
        except Exception as exc:
            self.say(f"ERROR: {type(exc).__name__}: {exc}")
        self.done.emit(self.slot_id, losses)


class RxTableWorker(_Worker):
    cell = pyqtSignal(int, int, float)   # att (dB), ch, power (-dBm, positive)
    progress = pyqtSignal(int)
    done = pyqtSignal(int, object)       # slot, table or None

    ATTS = ATT_STEPS_DB

    def run(self):
        table = None
        total = self.product["channels"] * len(self.ATTS)
        count = 0

        def on_point(att, ch, power):
            nonlocal count
            count += 1
            self.cell.emit(att, ch, power)
            self.progress.emit(round(100 * count / total))

        try:
            station = build_station(self.station_cfg, slot=self.slot_id)
            table = build_rx_table(station.bench, self.product["rx_cal"]["wavelengths_nm"],
                                   settle_s=0.01 if station.sim_module else 0.5,
                                   log=self.say, on_point=on_point, abort=self._abort)
        except Exception as exc:
            self.say(f"ERROR: {type(exc).__name__}: {exc}")
        self.done.emit(self.slot_id, table)
