"""Loss tab: Tx Loss calibration laid out and stepped like the production DDMI TX Loss tool
(Power Meter, Module Temperature, Golden Power, table, Read / Calculate, Save to system.ini),
plus this program's slot choice and RX attenuation table."""
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import CONSOLE_STYLE
from ..workers import CallWorker, StabilizeWorker
from .rx_att_table_popup import RxAttTablePopup
from ... import stationfiles
from ...station import acquire_power_meter, build_station, is_sim
from ...tools.tx_loss import fetch_golden, read_golden_temperature

COLUMNS = ["Channel", "Wavelength (nm)", "Golden (dBm)", "Measured (dBm)", "TX Loss (dB)", "RX table (points)"]
COL_GOLDEN, COL_MEASURED, COL_LOSS, COL_RX = 2, 3, 4, 5


class LossPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self._stabilizer = None      # StabilizeWorker while Start Stabilization runs
        self._calls = []             # CallWorkers still running (kept referenced until done)
        self._meter = None           # connected TX power meter (shared with test runs)
        self._release_meter = None
        self._sim_station = None     # simulator: the station whose meter is connected
        self._golden = {}            # slot -> {ch: dBm}
        self._measured = {}          # slot -> {ch: dBm}

        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Slot:"))
        self.slot = QComboBox()
        self.slot.addItems([f"Slot-{i} ({engine.settings.slot_name(i)})"
                            for i in range(1, engine.settings.slot_count + 1)])
        self.slot.currentIndexChanged.connect(self._on_slot_changed)
        top.addWidget(self.slot)
        top.addStretch(1)
        self.btn_rx_table = QPushButton("RX Attenuation Table")
        self.btn_rx_table.clicked.connect(self.on_rx_table_click)
        top.addWidget(self.btn_rx_table)
        lay.addLayout(top)

        # ---- Power Meter
        pm_group = QGroupBox("Power Meter")
        pm = QHBoxLayout(pm_group)
        pm.addWidget(QLabel("COM Port:"))
        self.combo_com = QComboBox()
        self.combo_com.setEditable(True)
        pm.addWidget(self.combo_com, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_com_ports)
        pm.addWidget(self.btn_refresh)
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.on_connect_click)
        pm.addWidget(self.btn_connect)
        self.lbl_pm_status = QLabel("Not connected")
        pm.addWidget(self.lbl_pm_status)
        lay.addWidget(pm_group)

        # ---- Module Temperature (DDMI)
        temp_group = QGroupBox("Module Temperature (DDMI)")
        temp = QVBoxLayout(temp_group)
        temp_btns = QHBoxLayout()
        self.btn_stabilize = QPushButton("Start Stabilization")
        self.btn_stabilize.clicked.connect(self.on_stabilize_click)
        self.btn_read_once = QPushButton("Read Once")
        self.btn_read_once.clicked.connect(self.on_read_once_click)
        self.btn_clear = QPushButton("Clear History")
        for b in (self.btn_stabilize, self.btn_read_once, self.btn_clear):
            temp_btns.addWidget(b)
        self.temp_status = QLabel("Current: --   Δ: --   Status: Idle")
        temp_btns.addWidget(self.temp_status, 1)
        temp.addLayout(temp_btns)
        self.temp_history = QTextEdit()
        self.temp_history.setReadOnly(True)
        self.temp_history.setStyleSheet(CONSOLE_STYLE)
        self.temp_history.setPlaceholderText("Temperature history will appear here (one line per reading).")
        self.temp_history.setMinimumHeight(110)
        self.btn_clear.clicked.connect(self.temp_history.clear)
        temp.addWidget(self.temp_history)
        lay.addWidget(temp_group, 1)

        # ---- Golden Power (MES Database)
        db_group = QGroupBox("Golden Power (MES Database)")
        db = QFormLayout(db_group)
        self.component_id = QLineEdit()
        self.component_id.setPlaceholderText(
            "simulator: the simulated module stands in" if self._sim() else "e.g. P172253901328")
        db.addRow("Component ID / Serial Number:", self.component_id)
        self.btn_fetch_golden = QPushButton("Fetch Golden Power from DB")
        self.btn_fetch_golden.clicked.connect(self.on_fetch_golden_click)
        db.addRow(self.btn_fetch_golden)
        lay.addWidget(db_group)

        # ---- table
        self.table = QTableWidget(params.CHANNELS, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 2)

        actions = QHBoxLayout()
        self.btn_read_power = QPushButton("Read Power from Meter")
        self.btn_read_power.clicked.connect(self.on_read_power_click)
        self.btn_calculate = QPushButton("Calculate TX Loss")
        self.btn_calculate.clicked.connect(self.on_calculate_click)
        actions.addWidget(self.btn_read_power)
        actions.addWidget(self.btn_calculate)
        lay.addLayout(actions)

        # ---- Save to system.ini
        save_group = QGroupBox("Save to system.ini")
        save = QHBoxLayout(save_group)
        self.edit_ini_path = QLineEdit()
        save.addWidget(self.edit_ini_path, 1)
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self.on_browse_click)
        save.addWidget(self.btn_browse)
        self.btn_save = QPushButton("Save TX Loss to system.ini")
        self.btn_save.clicked.connect(self.on_save_click)
        save.addWidget(self.btn_save)
        lay.addWidget(save_group)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(CONSOLE_STYLE)
        self.console.setMaximumHeight(110)
        lay.addWidget(self.console)

        self.refresh_com_ports()
        self._on_slot_changed()

    # ---- helpers --------------------------------------------------------------------------
    def _slot_id(self) -> int:
        return self.slot.currentIndex() + 1

    def _sim(self) -> bool:
        return is_sim(self.engine.station_cfg)

    def log(self, text: str):
        self.console.append(f"[{datetime.now():%H:%M:%S}] {text}")

    def _call(self, fn, on_done):
        worker = CallWorker(fn)
        self._calls.append(worker)

        def finished(ok, result, worker=worker):
            self._calls.remove(worker)
            on_done(ok, result)

        worker.done.connect(finished)
        worker.start()

    def _module_free(self, title: str) -> bool:
        """The golden sample's I2C is free: no test on this slot, no stabilization running."""
        slot_id = self._slot_id()
        if self.engine.is_running(slot_id):
            QMessageBox.warning(self, title, f"Slot-{slot_id} is running a test.")
            return False
        if self._stabilizer is not None and self._stabilizer.isRunning():
            QMessageBox.warning(self, title, "Stabilization is running - stop it first.")
            return False
        return True

    def _cell(self, row: int, col: int, text: str, editable: bool = False, red: bool = False):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if not editable:
            item.setFlags(Qt.ItemIsEnabled)
        if red:
            item.setForeground(QColor(255, 0, 0))
        self.table.setItem(row, col, item)

    def _loss_cell(self, row: int, loss):
        self._cell(row, COL_LOSS, "" if loss is None else f"{loss:.3f}", editable=True,
                   red=loss is not None and loss > params.TX_LOSS_WARN_DB)

    # ---- slot -----------------------------------------------------------------------------
    @pyqtSlot()
    def _on_slot_changed(self, *_args):
        slot_id = self._slot_id()
        self.edit_ini_path.setText(str(self.engine.loss_store.path(slot_id)))
        self.refresh_from_state()

    def refresh_from_state(self, *_args):
        """Table for the selected slot: golden / measured of this session, the saved Tx Loss."""
        slot_id = self._slot_id()
        saved = self.engine.loss_store.get(slot_id)
        golden, measured = self._golden.get(slot_id, {}), self._measured.get(slot_id, {})
        wavelengths = self.engine.product["tx_power_meter"]["wavelengths_nm"]
        rx_table = self.engine.rx_tables.load(slot_id)
        for ch in range(1, params.CHANNELS + 1):
            row = ch - 1
            self._cell(row, 0, f"Ch{ch} (DDMI)")
            self._cell(row, 1, str(wavelengths[row]))
            self._cell(row, COL_GOLDEN, f"{golden[ch]:.3f}" if ch in golden else "")
            self._cell(row, COL_MEASURED, f"{measured[ch]:.3f}" if ch in measured else "")
            self._loss_cell(row, saved[row] if saved is not None else None)
            points = len(rx_table.get(ch, []))
            self._cell(row, COL_RX, str(points) if points else "not calibrated")

    # ---- Power Meter ----------------------------------------------------------------------
    def _station_port(self) -> str:
        """The COM port of the station's configured meter, or '' if it cannot be found."""
        from ...hw.ports import resolve_port

        try:
            return resolve_port(self.engine.station_cfg["power_meter"]["resource"])
        except Exception:
            return ""

    def refresh_com_ports(self):
        if self._sim():
            self.combo_com.clear()
            self.combo_com.addItem("simulator")
            return
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except Exception as exc:
            ports = []
            self.log(f"Could not list COM ports: {exc}")
        current = self.combo_com.currentText() or self._station_port()
        self.combo_com.clear()
        self.combo_com.addItems(ports)
        if current:
            self.combo_com.setCurrentText(current)

    def on_connect_click(self):
        if self._meter is not None:
            self._disconnect()
            return
        if self._sim():
            self._sim_station = build_station(self.engine.slot_station_cfg(self._slot_id()), slot=self._slot_id())
            self._on_connected(True, (self._sim_station.bench.power_meter, self._sim_station.close))
            return
        port = self.combo_com.currentText().strip()
        if not port:
            QMessageBox.warning(self, "Power Meter", "Select or enter a COM port first.")
            return
        cfg = self.engine.station_cfg
        if port != self._station_port():
            # Another port than the station's meter: opened under its own name, not shared.
            cfg = dict(cfg, power_meter=dict(cfg["power_meter"], resource=port))
        self.btn_connect.setEnabled(False)
        self.lbl_pm_status.setText("Connecting...")
        self._call(lambda: acquire_power_meter(cfg, lambda _l: None), self._on_connected)

    def _on_connected(self, ok: bool, result):
        self.btn_connect.setEnabled(True)
        if not ok:
            self.lbl_pm_status.setText("Not connected")
            self.log(f"ERROR: power meter: {result}")
            QMessageBox.critical(self, "Power Meter", f"Connection error: {result}")
            return
        self._meter, self._release_meter = result
        self.btn_connect.setText("Disconnect")
        self.lbl_pm_status.setText(f"Connected on {self.combo_com.currentText()}")
        self.log(f"Power meter connected on {self.combo_com.currentText()}")

    def _disconnect(self):
        release, self._meter, self._release_meter = self._release_meter, None, None
        self._sim_station = None
        if release:
            try:
                release()
            except Exception as exc:
                self.log(f"power meter close: {exc}")
        self.btn_connect.setText("Connect")
        self.lbl_pm_status.setText("Not connected")
        self.log("Power meter disconnected")

    def shutdown(self):
        """App closing: stop a running stabilization and release the meter."""
        if self._stabilizer is not None and self._stabilizer.isRunning():
            self._stabilizer.abort()
            self._stabilizer.wait(3000)
        for worker in list(self._calls):
            worker.wait(5000)
        if self._meter is not None:
            self._disconnect()

    def on_read_power_click(self):
        if self._meter is None:
            QMessageBox.warning(self, "Power Meter", "Connect to the power meter first.")
            return
        cfg, meter, slot_id = self.engine.product["tx_power_meter"], self._meter, self._slot_id()

        def read():
            meter.configure(cfg["wavelengths_nm"], cfg["avg_time_ms"])
            return meter.read_all_dbm()

        self.btn_read_power.setEnabled(False)
        self._call(read, lambda ok, result: self._on_power_read(slot_id, ok, result))

    def _on_power_read(self, slot_id: int, ok: bool, powers):
        self.btn_read_power.setEnabled(True)
        if not ok:
            self.log(f"ERROR: power meter read: {powers}")
            QMessageBox.critical(self, "Power Meter", f"Failed to read power: {powers}")
            return
        self._measured[slot_id] = {ch: float(powers[ch - 1]) for ch in range(1, params.CHANNELS + 1)}
        self.log("Measured: " + ", ".join(f"CH{ch} {p:.3f}" for ch, p in self._measured[slot_id].items())
                 + " dBm")
        self._show_column(slot_id, COL_MEASURED, self._measured[slot_id])
        self._auto_calculate(slot_id)

    def _show_column(self, slot_id: int, col: int, values: dict):
        if slot_id == self._slot_id():
            for ch, value in values.items():
                self._cell(ch - 1, col, f"{value:.3f}")

    # ---- Module Temperature ---------------------------------------------------------------
    def _history(self, text: str):
        self.temp_history.append(text)

    def on_stabilize_click(self):
        if self._stabilizer is not None and self._stabilizer.isRunning():
            self._stabilizer.abort()
            return
        if not self._module_free("Stabilization"):
            return
        slot_id = self._slot_id()
        self._stabilizer = StabilizeWorker(slot_id, self.engine.slot_station_cfg(slot_id), self.engine.product)
        self._stabilizer.log.connect(lambda _slot, text: self._history(text))
        self._stabilizer.temp.connect(self._on_temp)
        self._stabilizer.done.connect(self._on_stabilize_done)
        self.btn_stabilize.setText("Stop Stabilization")
        self.btn_read_once.setEnabled(False)
        self.temp_status.setText("Current: --   Δ: --   Status: Starting...")
        self.temp_status.setStyleSheet("")
        self._stabilizer.start()

    @pyqtSlot(object)
    def _on_temp(self, sample: dict):
        delta = "--" if sample["delta_c"] is None else f"{sample['delta_c']:.3f} °C"
        stable = sample["status"].startswith("STABLE")
        self.temp_status.setText(f"Current: {sample['temp_c']:.3f} °C   Δ: {delta}   Status: {sample['status']}")
        self.temp_status.setStyleSheet(f"font-weight: 700; color: {'#218838' if stable else '#b8860b'};")

    @pyqtSlot(int, bool)
    def _on_stabilize_done(self, slot_id: int, stable: bool):
        self.btn_stabilize.setText("Start Stabilization")
        self.btn_read_once.setEnabled(True)
        if stable:
            self.log(f"Slot-{slot_id}: temperature stable - you can now read the power")
        elif "STABLE" not in self.temp_status.text():
            self.temp_status.setText(self.temp_status.text().rsplit("Status:", 1)[0] + "Status: Stopped")
            self.temp_status.setStyleSheet("")

    def on_read_once_click(self):
        if not self._module_free("Read Once"):
            return
        cfg, slot_id = self.engine.slot_station_cfg(self._slot_id()), self._slot_id()
        self.btn_read_once.setEnabled(False)
        self._call(lambda: read_golden_temperature(cfg, slot_id), self._on_read_once)

    def _on_read_once(self, ok: bool, temp):
        self.btn_read_once.setEnabled(True)
        if not ok:
            self.log(f"ERROR: temperature read: {temp}")
            QMessageBox.critical(self, "Module Temperature", f"Read failed: {temp}")
            return
        self._history(f"[{datetime.now():%H:%M:%S}] Read once  T= {temp:.3f} °C")
        self.temp_status.setText(f"Current: {temp:.3f} °C   Δ: --   Status: Idle")
        self.temp_status.setStyleSheet("")

    # ---- Golden Power ---------------------------------------------------------------------
    def on_fetch_golden_click(self):
        slot_id = self._slot_id()
        if self._sim():
            station = self._sim_station or build_station(self.engine.slot_station_cfg(slot_id), slot=slot_id)
            golden = {ch: station.sim_module.tx_out_dbm(ch) for ch in range(1, params.CHANNELS + 1)}
            self.log("Simulator: the simulated module's true output power stands in for the MES golden record")
            self._on_golden(slot_id, True, golden)
            return
        component_id = self.component_id.text().strip()
        if not component_id:
            QMessageBox.warning(self, "Golden Power", "Enter a Component ID / Serial Number first.")
            return
        product = self.engine.product
        self.btn_fetch_golden.setEnabled(False)
        self.log(f"Fetching golden power for {component_id} from MES")
        self._call(lambda: fetch_golden(component_id, product),
                   lambda ok, result: self._on_golden(slot_id, ok, result))

    def _on_golden(self, slot_id: int, ok: bool, golden):
        self.btn_fetch_golden.setEnabled(True)
        if not ok:
            self.log(f"ERROR: golden power: {golden}")
            QMessageBox.critical(self, "Golden Power", f"DB fetch failed: {golden}")
            return
        self._golden[slot_id] = golden
        self.log("Golden: " + ", ".join(f"CH{ch} {p:.3f}" for ch, p in sorted(golden.items())) + " dBm")
        self._show_column(slot_id, COL_GOLDEN, golden)
        self._auto_calculate(slot_id)

    # ---- Calculate / Save -----------------------------------------------------------------
    def _auto_calculate(self, slot_id: int):
        """As the production tool: calculate once golden and measured are both there."""
        if slot_id == self._slot_id() and len(self._golden.get(slot_id, {})) == params.CHANNELS \
                and len(self._measured.get(slot_id, {})) == params.CHANNELS:
            self.on_calculate_click()

    def on_calculate_click(self):
        slot_id = self._slot_id()
        golden, measured = self._golden.get(slot_id, {}), self._measured.get(slot_id, {})
        if len(measured) < params.CHANNELS:
            QMessageBox.warning(self, "TX Loss", "Read power from the meter for all 8 channels first.")
            return
        if len(golden) < params.CHANNELS:
            QMessageBox.warning(self, "TX Loss", "Fetch golden power from the DB for all 8 channels first.")
            return
        losses = []
        for ch in range(1, params.CHANNELS + 1):
            loss = round(golden[ch] - measured[ch], 3)
            losses.append(loss)
            self._loss_cell(ch - 1, loss)
        self.log("TX Loss (not saved yet): " + ", ".join(f"CH{i + 1} {v:.3f}" for i, v in enumerate(losses)) + " dB")
        high = [f"CH{i + 1}" for i, v in enumerate(losses) if v > params.TX_LOSS_WARN_DB]
        if high:
            self.log(f"WARNING: TX Loss above {params.TX_LOSS_WARN_DB} dB on {', '.join(high)}")

    def on_browse_click(self):
        path, _ = QFileDialog.getSaveFileName(self, "Select system.ini destination", self.edit_ini_path.text(),
                                              "INI files (*.ini);;All files (*)")
        if path:
            self.edit_ini_path.setText(path)

    def on_save_click(self):
        try:
            values = [float(self.table.item(r, COL_LOSS).text()) for r in range(params.CHANNELS)]
        except (ValueError, AttributeError):
            QMessageBox.warning(self, "Save", "Calculate TX Loss for all 8 channels first.")
            return
        path_text = self.edit_ini_path.text().strip()
        if not path_text:
            QMessageBox.warning(self, "Save", "Choose a destination system.ini path first.")
            return
        slot_id = self._slot_id()
        path, run_path = Path(path_text), self.engine.loss_store.path(slot_id)
        if path.resolve() != run_path.resolve():
            reply = QMessageBox.question(
                self, "Save",
                f"Test runs on Slot-{slot_id} read their Tx Loss from:\n{run_path}\n\n"
                f"Save to this other file anyway?\n{path}",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        try:
            stationfiles.write_tx_loss(path, values)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Save", f"Failed to save: {exc}")
            return
        self.log(f"Slot-{slot_id} TX Loss saved to {path}: " + ", ".join(f"{v:.3f}" for v in values))
        QMessageBox.information(self, "Save", f"TX Loss saved to:\n{path}")
        self.refresh_from_state()

    def on_rx_table_click(self):
        RxAttTablePopup(self._slot_id(), self.engine, parent=self).exec_()
        self.refresh_from_state()
