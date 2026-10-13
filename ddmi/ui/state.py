"""Run state shared by the pages, and the engine that owns the slot workers."""
from __future__ import annotations

import time
from datetime import datetime

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from . import params
from .sim_worker import SimWorker
from .stores import LossStore, RecordStore, RxTableStore, SettingsStore


class RunData:
    def __init__(self):
        self.meta = {"state": "IDLE"}
        self.logs: list[str] = []
        self.results: dict[str, dict[int, dict]] = {}   # param -> ch -> {"value", "ok"}
        self.steps = {s: "idle" for s in params.STEPS}
        self.step_failed: set[str] = set()
        self.current_step = ""

    def failing_items(self) -> list[str]:
        return [params.item_name(p, ch)
                for p, chans in self.results.items()
                for ch, cell in sorted(chans.items()) if not cell["ok"]]


class Engine(QObject):
    slot_updated = pyqtSignal(int)
    log_line = pyqtSignal(int, str)
    record_saved = pyqtSignal()

    def __init__(self, settings: SettingsStore):
        super().__init__()
        self.settings = settings
        self.loss_store = LossStore()
        self.rx_tables = RxTableStore()
        self.records = RecordStore()
        self.run_data = {s: RunData() for s in range(1, params.MAX_SLOTS + 1)}
        self.workers: dict[int, SimWorker] = {}
        self.user_id = ""

    def is_running(self, slot_id: int) -> bool:
        # Stays true until _on_done has run, not just until the thread exits.
        return self.run_data[slot_id].meta.get("state") == "RUNNING"

    def any_running(self) -> bool:
        return any(self.is_running(s) for s in self.run_data)

    def start_slot(self, slot_id: int, plan: str, scanned_sn: str):
        if self.is_running(slot_id):
            return
        rd = RunData()
        rd.meta.update(
            state="RUNNING", plan=plan, sn=scanned_sn, user=self.user_id or "UNKNOWN",
            start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), start_ts=time.time(),
            station=f"{self.settings.data['station']['station_id']}-{self.settings.slot_name(slot_id)}",
        )
        self.run_data[slot_id] = rd

        tx_loss = self.loss_store.get(slot_id)
        worker = SimWorker(slot_id, scanned_sn, tx_loss or [0.0] * params.CHANNELS,
                           self.settings.data["run"].get("sim_fault", "None"))
        worker.log.connect(self._on_log)
        worker.result.connect(self._on_result)
        worker.step.connect(self._on_step)
        worker.sn_read.connect(self._on_sn)
        worker.done.connect(self._on_done)
        self.workers[slot_id] = worker

        self._on_log(slot_id, f"Start: {plan} | station {rd.meta['station']} | user {rd.meta['user']}")
        if tx_loss is None:
            self._on_log(slot_id, "WARNING: no Tx Loss saved for this slot - using 0 dB. "
                                  "Run Tx Loss Calibration on the Loss tab.")
        elif max(tx_loss) > params.TX_LOSS_WARN_DB:
            self._on_log(slot_id, f"WARNING: Tx Loss above {params.TX_LOSS_WARN_DB} dB: {tx_loss}")
        worker.start()
        self.slot_updated.emit(slot_id)

    def abort_slot(self, slot_id: int):
        worker = self.workers.get(slot_id)
        if worker and worker.isRunning():
            self._on_log(slot_id, "Abort requested...")
            worker.abort()

    def shutdown(self):
        for worker in self.workers.values():
            worker.abort()
        for worker in self.workers.values():
            worker.wait(3000)
        for rd in self.run_data.values():
            if rd.meta.get("state") == "RUNNING":
                rd.meta["state"] = "ABORTED"

    @pyqtSlot(int, str)
    def _on_log(self, slot_id: int, text: str):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        self.run_data[slot_id].logs.append(line)
        self.log_line.emit(slot_id, line)

    @pyqtSlot(int, str, int, object)
    def _on_result(self, slot_id: int, param: str, ch: int, value):
        rd = self.run_data[slot_id]
        ok = params.check(param, value)
        rd.results.setdefault(param, {})[ch] = {"value": value, "ok": ok}
        if not ok:
            rd.step_failed.add(rd.current_step)
            lo, hi = params.LIMITS[param]
            self._on_log(slot_id, f"FAIL {params.item_name(param, ch)} = {value} (limit {lo} to {hi})")
        self.slot_updated.emit(slot_id)

    @pyqtSlot(int, str, str)
    def _on_step(self, slot_id: int, step: str, state: str):
        rd = self.run_data[slot_id]
        if state == "running":
            rd.current_step = step
            rd.steps[step] = "running"
        else:
            rd.steps[step] = "fail" if step in rd.step_failed else "pass"
        self.slot_updated.emit(slot_id)

    @pyqtSlot(int, str)
    def _on_sn(self, slot_id: int, sn: str):
        rd = self.run_data[slot_id]
        rd.meta["module_sn"] = sn
        rd.meta["sn"] = rd.meta.get("sn") or sn
        self.slot_updated.emit(slot_id)

    @pyqtSlot(int, str)
    def _on_done(self, slot_id: int, reason: str):
        rd = self.run_data[slot_id]
        fails = rd.failing_items()
        if reason == "ABORTED":
            state = "ABORTED"
        elif reason.startswith("ERROR"):
            state = "ERROR"
        elif reason or fails:
            state = "FAIL"
        else:
            state = "PASS"
        for step, s in rd.steps.items():
            if s == "running":
                rd.steps[step] = "idle" if state == "ABORTED" else "fail"
        if reason and reason != "ABORTED":
            self._on_log(slot_id, reason)
            fails = fails or [reason.split(": ", 1)[-1]]

        secs = int(time.time() - rd.meta["start_ts"])
        rd.meta.update(
            state=state, end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            elapsed=f"{secs // 60:02d}:{secs % 60:02d}", failing_items=fails,
        )
        self._on_log(slot_id, f"Result: {state}" + (f" - failing: {', '.join(fails)}" if fails and state == "FAIL" else ""))

        record = {k: v for k, v in rd.meta.items() if k != "start_ts"}
        record.update(
            slot=slot_id, result=state, simulated=True, steps=rd.steps,
            results={p: {("module" if ch == 0 else f"ch{ch}"): cell for ch, cell in chans.items()}
                     for p, chans in rd.results.items()},
        )
        try:
            path = self.records.save(record, rd.logs)
            self._on_log(slot_id, f"Record saved: {path.name}")
        except OSError as exc:
            self._on_log(slot_id, f"ERROR: could not save record: {exc}")
        self.record_saved.emit()
        self.slot_updated.emit(slot_id)
