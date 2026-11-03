"""Rx Loss sub-tab: the RX attenuation table per slot (0-30ATT.vi / Att Calibration.vi V6.8)."""
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..workers import RxTableWorker
from ..state import Engine
from ..widgets import CONSOLE_STYLE


class RxLossPage(QWidget):
    """Rows = attenuation 1..20 dB, columns = CH1..CH8, cells = power (-dBm, stored positive)."""

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self._worker = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Slot:"))
        self.slot = QComboBox()
        self.slot.addItems([f"Slot-{i} ({engine.settings.slot_name(i)})"
                            for i in range(1, engine.settings.slot_count + 1)])
        self.slot.currentIndexChanged.connect(self.refresh_from_state)
        top.addWidget(self.slot)
        top.addSpacing(15)
        self.lbl_folder = QLabel()
        self.lbl_folder.setStyleSheet("color: #555;")
        top.addWidget(self.lbl_folder, 1)
        lay.addLayout(top)
        lay.addWidget(QLabel(
            "Power at the module input for each attenuator setting (-dBm, stored positive). "
            "Saved as CHn_Rx_calibration.txt."))

        self.atts = RxTableWorker.ATTS
        self.table = QTableWidget(len(self.atts), params.CHANNELS + 1)
        self.table.setHorizontalHeaderLabels(
            ["Att (dB)"] + [f"CH{c}" for c in range(1, params.CHANNELS + 1)])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 3)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("Start Calibration")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_load = QPushButton("Load from Folder")
        self.btn_start.clicked.connect(self.on_start_calibration)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_load.clicked.connect(self.on_load_from_folder)
        for b in (self.btn_start, self.btn_stop, self.btn_load):
            b.setMinimumHeight(34)
            btns.addWidget(b)
        lay.addLayout(btns)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(CONSOLE_STYLE)
        lay.addWidget(self.console, 1)

        self.refresh_from_state()

    def _slot_id(self) -> int:
        return self.slot.currentIndex() + 1

    def _set(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, col, item)

    def refresh_from_state(self, *_args):
        slot_id = self._slot_id()
        self.lbl_folder.setText(f"RX table folder: {self.engine.rx_tables.dir(slot_id)}")
        for r, att in enumerate(self.atts):
            self._set(r, 0, str(att))
            for ch in range(1, params.CHANNELS + 1):
                self._set(r, ch, "")
        self._show(self.engine.rx_tables.load(slot_id))

    def _show(self, table: dict):
        for ch, rows in table.items():
            for att, power in rows:
                if round(att) in self.atts:
                    self._set(self.atts.index(round(att)), ch, f"{power:.2f}")

    def log(self, text: str):
        self.console.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def _running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def on_load_from_folder(self):
        slot_id = self._slot_id()
        folder = QFileDialog.getExistingDirectory(
            self, "Folder containing CH1..CH8_Rx_calibration.txt")
        if not folder:
            return
        table = self.engine.rx_tables.load(slot_id, Path(folder))
        if not table:
            QMessageBox.warning(self, "Rx Loss", "No CHn_Rx_calibration.txt files found in that folder.")
            return
        self.engine.rx_tables.save(slot_id, table)
        self.refresh_from_state()
        self.log(f"Loaded channels {sorted(table)} from {folder} and saved for Slot-{slot_id}")

    def on_start_calibration(self):
        slot_id = self._slot_id()
        if self.engine.is_running(slot_id):
            QMessageBox.warning(self, "Rx Loss", f"Slot-{slot_id} is running a test.")
            return
        reply = QMessageBox.question(
            self, "Rx Loss",
            f"Start RX table calibration for Slot-{slot_id}?\n\n"
            "Ensure the power meter is connected at the module RX input.\n"
            "This replaces the saved table.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.slot.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.btn_load.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_start.setText("Calibrating...")
        self.progress.setValue(0)
        self._worker = RxTableWorker(slot_id, self.engine.slot_station_cfg(slot_id), self.engine.product)
        self._worker.log.connect(self._on_log)
        self._worker.cell.connect(self._on_cell)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.prompt.connect(self._on_prompt)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def on_stop(self):
        if self._running():
            self._worker.abort()
            self.log("Stop requested")

    @pyqtSlot(int, str)
    def _on_log(self, _slot_id: int, text: str):
        self.log(text)

    @pyqtSlot(int, int, float)
    def _on_cell(self, att: int, ch: int, power: float):
        self._set(self.atts.index(att), ch, f"{power:.2f}")

    @pyqtSlot(str)
    def _on_prompt(self, message: str):
        self.log(message)
        answer = QMessageBox.question(self, "Rx Loss", message,
                                      QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        self._worker.respond(answer == QMessageBox.Ok)

    @pyqtSlot(int, object)
    def _on_done(self, slot_id: int, table):
        self.slot.setEnabled(True)
        self.btn_start.setText("Start Calibration")
        self.btn_start.setEnabled(True)
        self.btn_load.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if table:
            try:
                self.engine.rx_tables.save(slot_id, table)
                self.log(f"Saved to {self.engine.rx_tables.dir(slot_id)}")
            except OSError as exc:
                self.log(f"ERROR: could not save RX table: {exc}")

    def shutdown(self):
        if self._running():
            self._worker.abort()
            self._worker.wait(3000)
