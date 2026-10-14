from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from .. import params
from ..workers import RxTableWorker
from ..state import Engine
from ..widgets import CONSOLE_STYLE


class RxAttTablePopup(QDialog):
    """Rows = attenuation 1..20 dB, columns = CH1..CH8, cells = power (-dBm, stored positive)."""

    def __init__(self, slot_id: int, engine: Engine, parent=None):
        super().__init__(parent)
        self.slot_id = slot_id
        self.engine = engine
        self._worker = None
        self.setWindowTitle(f"RX Attenuation Table - Slot-{slot_id}")
        self.resize(900, 720)

        lay = QVBoxLayout(self)
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
        for r, att in enumerate(self.atts):
            self._set(r, 0, str(att))
        lay.addWidget(self.table, 3)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(CONSOLE_STYLE)
        lay.addWidget(self.console, 1)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("Start Calibration")
        self.btn_load = QPushButton("Load from Folder")
        self.btn_close = QPushButton("Close")
        self.btn_start.clicked.connect(self.on_start_calibration)
        self.btn_load.clicked.connect(self.on_load_from_folder)
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_start, self.btn_load, self.btn_close):
            btns.addWidget(b)
        lay.addLayout(btns)

        self._show(self.engine.rx_tables.load(slot_id))

    def _set(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, col, item)

    def _show(self, table: dict):
        for ch, rows in table.items():
            for att, power in rows:
                if round(att) in self.atts:
                    self._set(self.atts.index(round(att)), ch, f"{power:.2f}")

    def log(self, text: str):
        self.console.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def on_load_from_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Folder containing CH1..CH8_Rx_calibration.txt")
        if not folder:
            return
        table = self.engine.rx_tables.load(self.slot_id, Path(folder))
        if not table:
            QMessageBox.warning(self, "RX Attenuation Table",
                                "No CHn_Rx_calibration.txt files found in that folder.")
            return
        self.engine.rx_tables.save(self.slot_id, table)
        self._show(table)
        self.log(f"Loaded channels {sorted(table)} from {folder} and saved for Slot-{self.slot_id}")

    def on_start_calibration(self):
        if self.engine.is_running(self.slot_id):
            QMessageBox.warning(self, "RX Attenuation Table",
                                f"Slot-{self.slot_id} is running a test.")
            return
        reply = QMessageBox.question(
            self, "RX Attenuation Table",
            f"Start RX table calibration for Slot-{self.slot_id}?\n\n"
            "Ensure the power meter is connected at the module RX input.\n"
            "This replaces the saved table.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.btn_start.setEnabled(False)
        self.btn_load.setEnabled(False)
        self.btn_start.setText("Calibrating...")
        self.progress.setValue(0)
        self._worker = RxTableWorker(self.slot_id, self.engine.slot_station_cfg(self.slot_id),
                                     self.engine.product)
        self._worker.log.connect(self._on_log)
        self._worker.cell.connect(self._on_cell)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.prompt.connect(self._on_prompt)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    @pyqtSlot(int, str)
    def _on_log(self, _slot_id: int, text: str):
        self.log(text)

    @pyqtSlot(int, int, float)
    def _on_cell(self, att: int, ch: int, power: float):
        self._set(self.atts.index(att), ch, f"{power:.2f}")

    @pyqtSlot(str)
    def _on_prompt(self, message: str):
        self.log(message)
        answer = QMessageBox.question(self, "RX Attenuation Table", message,
                                      QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok)
        self._worker.respond(answer == QMessageBox.Ok)

    @pyqtSlot(int, object)
    def _on_done(self, slot_id: int, table):
        self.btn_start.setText("Start Calibration")
        self.btn_start.setEnabled(True)
        self.btn_load.setEnabled(True)
        if table:
            try:
                self.engine.rx_tables.save(slot_id, table)
                self.log(f"Saved to {self.engine.rx_tables.dir(slot_id)}")
            except OSError as exc:
                self.log(f"ERROR: could not save RX table: {exc}")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        event.accept()
