from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import CONSOLE_STYLE
from ..workers import TxLossWorker
from .rx_att_table_popup import RxAttTablePopup
from ...station import is_sim

COLUMNS = ["Ch", "Wavelength (nm)", "Golden (dBm)", "Measured (dBm)", "Tx Loss (dB)", "RX table (points)"]
COL_LOSS = 4


class LossPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self._worker = None
        self._last = {}   # slot -> rows of the last calibration (golden / measured shown until restart)

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Slot:"))
        self.slot = QComboBox()
        self.slot.addItems([f"Slot-{i} ({engine.settings.slot_name(i)})"
                            for i in range(1, engine.settings.slot_count + 1)])
        self.slot.currentIndexChanged.connect(self.refresh_from_state)
        top.addWidget(self.slot)
        top.addSpacing(15)
        top.addWidget(QLabel("Golden sample Component ID / SN:"))
        self.component_id = QLineEdit()
        self.component_id.setPlaceholderText(
            "optional in the simulator" if is_sim(engine.station_cfg) else "golden power is read from MES")
        self.component_id.setMinimumWidth(220)
        top.addWidget(self.component_id)
        top.addSpacing(15)
        self.lbl_file = QLabel()
        self.lbl_file.setStyleSheet("color: #555;")
        top.addWidget(self.lbl_file)
        top.addStretch(1)
        lay.addLayout(top)

        self.table = QTableWidget(params.CHANNELS, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 2)

        temp_row = QHBoxLayout()
        temp_row.addWidget(QLabel("<b>Golden sample temperature (DDMI):</b>"))
        self.temp_status = QLabel("not read yet")
        temp_row.addWidget(self.temp_status, 1)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop_click)
        temp_row.addWidget(self.btn_stop)
        lay.addLayout(temp_row)

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
        component_id = self.component_id.text().strip()
        if not component_id and not is_sim(self.engine.station_cfg):
            QMessageBox.warning(self, "Tx Loss Calibration",
                                "Enter the golden sample's Component ID / SN first.")
            return
        reply = QMessageBox.question(
            self, "Tx Loss Calibration",
            f"Start Tx Loss Calibration for Slot-{slot_id}?\n\n"
            "Ensure the golden sample is inserted, powered, and TX1 and TX2 are connected to the "
            "power meter.\n\nThe measurement starts once the module temperature is stable.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.btn_tx_cal.setEnabled(False)
        self.btn_tx_cal.setText("Calibrating...")
        self._worker = TxLossWorker(slot_id, self.engine.slot_station_cfg(slot_id), self.engine.product,
                                    component_id)
        self._worker.log.connect(self._on_worker_log)
        self._worker.temp.connect(self._on_temp)
        self._worker.done.connect(self._on_tx_cal_done)
        self.btn_stop.setEnabled(True)
        self.temp_status.setText("waiting for the first reading...")
        self._worker.start()

    def on_stop_click(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.abort()
            self.log("Stop requested")

    @pyqtSlot(object)
    def _on_temp(self, sample: dict):
        delta = "--" if sample["delta_c"] is None else f"{sample['delta_c']:.3f} °C"
        stable = sample["status"].startswith("STABLE")
        self.temp_status.setText(f"Current: {sample['temp_c']:.3f} °C   Δ: {delta}   Status: {sample['status']}")
        self.temp_status.setStyleSheet(f"font-weight: 700; color: {'#218838' if stable else '#b8860b'};")

    @pyqtSlot(int, str)
    def _on_worker_log(self, _slot_id: int, text: str):
        self.log(text)

    @pyqtSlot(int, object)
    def _on_tx_cal_done(self, slot_id: int, rows):
        self.btn_tx_cal.setText("Tx Loss Calibration")
        self.btn_tx_cal.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if not rows:
            return
        self._last[slot_id] = rows
        if self._slot_id() != slot_id:
            self.slot.setCurrentIndex(slot_id - 1)
        self.refresh_from_state(new_rows=rows)
        losses = [row["loss_db"] for row in rows]
        reply = QMessageBox.question(
            self, "Tx Loss Calibration",
            f"Save these Tx Loss values for Slot-{slot_id}?\n\n"
            + "\n".join(f"CH{row['ch']}: {row['loss_db']:.3f} dB" for row in rows),
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._save(slot_id, losses)
        else:
            self.log("Tx Loss not saved")
        self.refresh_from_state()

    def on_save_click(self):
        try:
            values = [float(self.table.item(r, COL_LOSS).text()) for r in range(params.CHANNELS)]
        except (ValueError, AttributeError):
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
        self.log(f"Slot-{slot_id} Tx Loss saved to {self.engine.loss_store.path(slot_id).name}: "
                 + ", ".join(f"{v:.3f}" for v in values))
        high = [f"CH{i + 1}" for i, v in enumerate(values) if v > params.TX_LOSS_WARN_DB]
        if high:
            self.log(f"WARNING: Tx Loss above {params.TX_LOSS_WARN_DB} dB on {', '.join(high)}")

    def on_rx_table_click(self):
        RxAttTablePopup(self._slot_id(), self.engine, parent=self).exec_()
        self.refresh_from_state()

    def _cell(self, row: int, col: int, text: str, editable: bool = False, red: bool = False):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if not editable:
            item.setFlags(Qt.ItemIsEnabled)
        if red:
            item.setForeground(QColor(255, 0, 0))
        self.table.setItem(row, col, item)

    def refresh_from_state(self, *_args, new_rows=None):
        slot_id = self._slot_id()
        store = self.engine.loss_store
        self.lbl_file.setText(f"Tx Loss file: {store.path(slot_id).name}")
        saved = store.get(slot_id)
        last = {row["ch"]: row for row in (new_rows or self._last.get(slot_id, []))}
        wavelengths = self.engine.product["tx_power_meter"]["wavelengths_nm"]
        rx_table = self.engine.rx_tables.load(slot_id)
        for ch in range(1, params.CHANNELS + 1):
            row, cal = ch - 1, last.get(ch)
            loss = cal["loss_db"] if new_rows else (saved[row] if saved is not None else None)
            self._cell(row, 0, f"Ch{ch}")
            self._cell(row, 1, str(wavelengths[row]))
            self._cell(row, 2, f"{cal['golden_dbm']:.3f}" if cal else "")
            self._cell(row, 3, f"{cal['measured_dbm']:.3f}" if cal else "")
            self._cell(row, COL_LOSS, "" if loss is None else f"{loss:.3f}", editable=True,
                       red=loss is not None and loss > params.TX_LOSS_WARN_DB)
            points = len(rx_table.get(ch, []))
            self._cell(row, 5, str(points) if points else "not calibrated")
