from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..workers import TxLossWorker
from ..state import Engine
from ..widgets import CONSOLE_STYLE
from .rx_att_table_popup import RxAttTablePopup


class LossPage(QWidget):
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
        self.lbl_file = QLabel()
        self.lbl_file.setStyleSheet("color: #555;")
        top.addSpacing(15)
        top.addWidget(self.lbl_file)
        top.addStretch(1)
        lay.addLayout(top)

        self.table = QTableWidget(params.CHANNELS, 3)
        self.table.setHorizontalHeaderLabels(["Ch", "Tx Loss (dB)", "RX table (points)"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 2)

        btns = QHBoxLayout()
        self.btn_tx_cal = QPushButton("Tx Loss Calibration")
        self.btn_save = QPushButton("Save Edited Tx Loss")
        self.btn_rx_table = QPushButton("RX Attenuation Table")
        self.btn_tx_cal.clicked.connect(self.on_tx_loss_cal_click)
        self.btn_save.clicked.connect(self.on_save_click)
        self.btn_rx_table.clicked.connect(self.on_rx_table_click)
        for b in (self.btn_tx_cal, self.btn_save, self.btn_rx_table):
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

    def log(self, text: str):
        self.console.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    def on_tx_loss_cal_click(self):
        slot_id = self._slot_id()
        if self.engine.is_running(slot_id):
            QMessageBox.warning(self, "Tx Loss Calibration", f"Slot-{slot_id} is running a test.")
            return
        reply = QMessageBox.question(
            self, "Tx Loss Calibration",
            f"Start Tx Loss Calibration for Slot-{slot_id}?\n\n"
            "Ensure a Golden Unit is inserted and connected.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.btn_tx_cal.setEnabled(False)
        self.btn_tx_cal.setText("Calibrating...")
        self._worker = TxLossWorker(slot_id, self.engine.slot_station_cfg(slot_id), self.engine.product)
        self._worker.log.connect(self._on_worker_log)
        self._worker.done.connect(self._on_tx_cal_done)
        self._worker.start()

    @pyqtSlot(int, str)
    def _on_worker_log(self, _slot_id: int, text: str):
        self.log(text)

    @pyqtSlot(int, object)
    def _on_tx_cal_done(self, slot_id: int, losses):
        self.btn_tx_cal.setText("Tx Loss Calibration")
        self.btn_tx_cal.setEnabled(True)
        if losses:
            self._save(slot_id, losses)
        self.refresh_from_state()

    def on_save_click(self):
        try:
            values = [float(self.table.item(r, 1).text()) for r in range(params.CHANNELS)]
        except ValueError:
            QMessageBox.warning(self, "Tx Loss", "Every channel needs a numeric Tx Loss value.")
            return
        self._save(self._slot_id(), values)
        self.refresh_from_state()

    def _save(self, slot_id: int, values: list):
        try:
            self.engine.loss_store.set(slot_id, values)
        except OSError as exc:
            self.log(f"ERROR: could not save Tx Loss: {exc}")
            return
        self.log(f"Slot-{slot_id} Tx Loss saved to {self.engine.loss_store.path(slot_id).name}: {values}")
        high = [f"CH{i + 1}" for i, v in enumerate(values) if v > params.TX_LOSS_WARN_DB]
        if high:
            self.log(f"WARNING: Tx Loss above {params.TX_LOSS_WARN_DB} dB on {', '.join(high)}")

    def on_rx_table_click(self):
        RxAttTablePopup(self._slot_id(), self.engine, parent=self).exec_()
        self.refresh_from_state()

    def refresh_from_state(self):
        slot_id = self._slot_id()
        store = self.engine.loss_store
        self.lbl_file.setText(f"Tx Loss file: {store.path(slot_id).name}")
        tx_loss = store.get(slot_id)
        rx_table = self.engine.rx_tables.load(slot_id)
        for ch in range(1, params.CHANNELS + 1):
            row = ch - 1
            name = QTableWidgetItem(f"Ch{ch}")
            name.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, name)
            loss = QTableWidgetItem("" if tx_loss is None else f"{tx_loss[row]:.2f}")
            if tx_loss is not None and tx_loss[row] > params.TX_LOSS_WARN_DB:
                loss.setForeground(QColor(255, 0, 0))
            self.table.setItem(row, 1, loss)
            points = len(rx_table.get(ch, []))
            status = QTableWidgetItem(str(points) if points else "not calibrated")
            status.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 2, status)
