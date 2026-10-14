"""Run state shared by the pages, and the engine that owns the slot workers."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .. import config
from . import params
from .stores import ROOT, LossStore, RecordStore, RxTableStore, SettingsStore
from .workers import FlowWorker


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


class RunData:
    def __init__(self):
        self.meta = {"state": "IDLE"}
        self.results: dict[str, dict[int, dict]] = {}   # item -> ch -> {"value", "ok"}
        self.steps = {s: "idle" for s in params.STEPS}
        self.step_failed: set[str] = set()
        self.current_step = ""
        self.mpd: dict | None = None    # live MPD scan: volts, curves (per lane), lanes (analysis)


class Engine(QObject):
    slot_updated = pyqtSignal(int)
    log_line = pyqtSignal(int, str)
    record_saved = pyqtSignal()
    mpd_updated = pyqtSignal(int)

    def __init__(self, settings: SettingsStore, station_cfg: dict | None = None):
        super().__init__()
        self.settings = settings
        self.product = config.load_product()
        self.station_cfg = station_cfg or config.load_station()
        self.loss_store = LossStore(_path(self.station_cfg["tx_loss_file"]))
        self.rx_tables = RxTableStore(_path(self.station_cfg["rx_table_dir"]))
        self.records = RecordStore(_path(self.station_cfg["results_dir"]))
        self.run_data = {s: RunData() for s in range(1, params.MAX_SLOTS + 1)}
        self.workers: dict[int, FlowWorker] = {}
        self.user_id = ""

    def slot_station_cfg(self, slot_id: int) -> dict:
        """The station config for one slot, named <station id>-<slot name> as LabVIEW does."""
        cfg = dict(self.station_cfg)
        cfg["station_name"] = (f"{self.settings.data['station']['station_id']}-"
                               f"{self.settings.slot_name(slot_id)}")
        return cfg

    def is_running(self, slot_id: int) -> bool:
        # Stays true until _on_done has run, not just until the thread exits.
        return self.run_data[slot_id].meta.get("state") == "RUNNING"

    def any_running(self) -> bool:
        return any(self.is_running(s) for s in self.run_data)

    def start_slot(self, slot_id: int, plan: str, scanned_sn: str):
        if self.is_running(slot_id):
            return
        rd = RunData()
        rd.meta.update(state="RUNNING", plan=plan, sn=scanned_sn, start_ts=time.time(),
                       start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.run_data[slot_id] = rd

        run = self.settings.data["run"]
        worker = FlowWorker(
            slot_id, self.slot_station_cfg(slot_id), self.product, scanned_sn=scanned_sn,
            user=self.user_id or "UNKNOWN", plan=plan,
            fault=params.SIM_FAULTS.get(run.get("sim_fault"), "none"),
            dry_run=bool(run.get("dry_run")),
            allow_unverified=bool(run.get("allow_unverified_writes")),
        )
        worker.log.connect(self._on_log)
        worker.result.connect(self._on_result)
        worker.step.connect(self._on_step)
        worker.sn_read.connect(self._on_sn)
        worker.data.connect(self._on_data)
        worker.done.connect(self._on_done)
        self.workers[slot_id] = worker
        worker.start()
        self.slot_updated.emit(slot_id)

    def abort_slot(self, slot_id: int):
        worker = self.workers.get(slot_id)
        if worker and self.is_running(slot_id):
            self.log_line.emit(slot_id, "Abort requested...")
            worker.abort()

    def shutdown(self):
        for worker in self.workers.values():
            worker.abort()
        for worker in self.workers.values():
            worker.wait(5000)

    @pyqtSlot(int, str)
    def _on_log(self, slot_id: int, line: str):
        self.log_line.emit(slot_id, line)

    @pyqtSlot(int, str, int, object, bool)
    def _on_result(self, slot_id: int, item: str, ch: int, value, ok: bool):
        rd = self.run_data[slot_id]
        rd.results.setdefault(item, {})[ch] = {"value": value, "ok": ok}
        if not ok:
            rd.step_failed.add(rd.current_step)
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

    @pyqtSlot(int, str, object)
    def _on_data(self, slot_id: int, kind: str, data: dict):
        rd = self.run_data[slot_id]
        if kind == "mpd_start":
            rd.mpd = {"volts": [], "curves": [[] for _ in range(data["lanes"])], "lanes": {},
                      "planned": len(data["volts"])}
        elif rd.mpd is None:
            return
        elif kind == "mpd_point":
            rd.mpd["volts"].append(data["v"])
            for lane, value in enumerate(data["txmon"]):
                rd.mpd["curves"][lane].append(value)
        elif kind == "mpd_result":
            rd.mpd["lanes"] = data["lanes"]
        elif kind == "mpd_tops_off":
            lane = rd.mpd["lanes"].get(data["ch"])
            if lane is not None:
                lane.update(tops_off_v=data["tops_off_v"], leakage_dbm=data["leakage_dbm"])
        self.mpd_updated.emit(slot_id)

    @pyqtSlot(int, str)
    def _on_sn(self, slot_id: int, sn: str):
        self.run_data[slot_id].meta["sn"] = sn
        self.slot_updated.emit(slot_id)

    @pyqtSlot(int, object)
    def _on_done(self, slot_id: int, record: dict):
        rd = self.run_data[slot_id]
        state = record.get("result", "ERROR")
        for step, s in rd.steps.items():
            if s == "running":
                rd.steps[step] = "idle" if state == "ABORTED" else "fail"
        secs = int(time.time() - rd.meta["start_ts"])
        rd.meta.update(
            state=state, sn=record.get("sn") or rd.meta.get("sn", ""),
            end_time=record.get("end_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            elapsed=record.get("elapsed", f"{secs // 60:02d}:{secs % 60:02d}"),
            failing_items=record.get("failing_items", []),
        )
        self.record_saved.emit()
        self.slot_updated.emit(slot_id)
