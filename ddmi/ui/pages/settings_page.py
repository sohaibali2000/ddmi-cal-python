from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from .. import params
from ...station import is_sim
from ..state import Engine

_DOT = "font-size: 16px; color: {};"


class SettingsPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.settings = engine.settings
        self._locked = True
        self._loading = False
        self._equip = {}   # (slot, key) -> (model combo, address edit, status label)

        outer = QVBoxLayout(self)

        lock_row = QHBoxLayout()
        self._lbl_lock = QLabel()
        self._btn_lock = QPushButton()
        self._btn_lock.clicked.connect(self._toggle_lock)
        lock_row.addWidget(self._lbl_lock)
        lock_row.addStretch(1)
        lock_row.addWidget(self._btn_lock)
        outer.addLayout(lock_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._body = QWidget()
        lay = QVBoxLayout(self._body)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, 1)

        runbox = QGroupBox("Run Options")
        g = QGridLayout(runbox)
        real = not is_sim(engine.station_cfg)
        self.cb_sim = QCheckBox("Real hardware - lab station" if real else "Simulator (no hardware)")
        self.cb_sim.setChecked(True)
        self.cb_sim.setEnabled(False)
        self.cb_sim.setToolTip("Set by the station file the app was started with (run_ui_lab.bat = real station)")
        self.cb_offline = QCheckBox("Offline (disable MES)")
        self.cb_dry = QCheckBox("Dry run (print every module write, send nothing)")
        self.cb_unverified = QCheckBox("Allow writes to unverified registers (engineering only)")
        self.cb_unverified.setStyleSheet("color: #c82333;")
        self.fault = QComboBox()
        self.fault.addItems(params.SIM_FAULTS)
        self.station_id = QLineEdit()
        self.slot_count = QComboBox()
        self.slot_count.addItems(["2", "4"])
        g.addWidget(self.cb_sim, 0, 0, 1, 2)
        g.addWidget(self.cb_offline, 1, 0, 1, 2)
        g.addWidget(self.cb_dry, 2, 0, 1, 2)
        g.addWidget(self.cb_unverified, 3, 0, 1, 2)
        g.addWidget(QLabel("Simulator fault injection:"), 4, 0)
        g.addWidget(self.fault, 4, 1)
        g.addWidget(QLabel("Station ID:"), 5, 0)
        g.addWidget(self.station_id, 5, 1)
        g.addWidget(QLabel("Number of slots (applies after restart):"), 6, 0)
        g.addWidget(self.slot_count, 6, 1)
        g.setColumnStretch(2, 1)
        lay.addWidget(runbox)

        tabs = QTabWidget()
        for slot_id in range(1, self.settings.slot_count + 1):
            tab = QWidget()
            grid = QGridLayout(tab)
            for c, head in enumerate(("Equipment", "Model", "Address", "Status", "Action")):
                grid.addWidget(QLabel(f"<b>{head}</b>"), 0, c)
            for r, (key, label, models) in enumerate(params.EQUIPMENT, start=1):
                model = QComboBox()
                model.addItems(models)
                address = QLineEdit()
                address.setPlaceholderText("e.g. COM5 or 192.168.1.100:8080")
                status = QLabel("●")
                status.setStyleSheet(_DOT.format("#a0a0a0"))
                status.setToolTip("Simulated - no driver connected")
                reconnect = QPushButton("Reconnect")
                reconnect.clicked.connect(lambda _, lbl=label: self._reconnect(lbl))
                grid.addWidget(QLabel(f"{label}:"), r, 0)
                grid.addWidget(model, r, 1)
                grid.addWidget(address, r, 2)
                grid.addWidget(status, r, 3)
                grid.addWidget(reconnect, r, 4)
                self._equip[(slot_id, key)] = (model, address, status)
            grid.setColumnStretch(2, 1)
            grid.setRowStretch(len(params.EQUIPMENT) + 1, 1)
            tabs.addTab(tab, f"Slot-{slot_id} ({self.settings.slot_name(slot_id)})")
        eqbox = QGroupBox("Equipment (per slot)")
        QVBoxLayout(eqbox).addWidget(tabs)
        lay.addWidget(eqbox)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Save Settings")
        self.btn_reload = QPushButton("Reload")
        self.btn_save.clicked.connect(self.save)
        self.btn_reload.clicked.connect(self.load)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_reload)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addStretch(1)

        for box in (self.cb_offline, self.cb_dry, self.cb_unverified):
            box.toggled.connect(self._apply_run_options)
        self.fault.currentIndexChanged.connect(self._apply_run_options)
        self.load()
        self._apply_lock()

    def _toggle_lock(self):
        if not self._locked:
            self._locked = True
        else:
            text, ok = QInputDialog.getText(self, "Engineer Lock", "Password:", QLineEdit.Password)
            if not ok:
                return
            if text != self.settings.data["station"].get("engineer_password", ""):
                QMessageBox.warning(self, "Engineer Lock", "Wrong password.")
                return
            self._locked = False
        self._apply_lock()

    def _apply_lock(self):
        self._body.setEnabled(not self._locked)
        if self._locked:
            self._lbl_lock.setText("Engineer lock - settings are read-only")
            self._lbl_lock.setStyleSheet("font-weight: 700; color: #c82333;")
            self._btn_lock.setText("Enter Password")
        else:
            self._lbl_lock.setText("Unlocked - settings can be edited")
            self._lbl_lock.setStyleSheet("font-weight: 700; color: #218838;")
            self._btn_lock.setText("Lock")

    def hideEvent(self, event):
        self._locked = True
        self._apply_lock()
        super().hideEvent(event)

    def _reconnect(self, label: str):
        QMessageBox.information(
            self, "Reconnect",
            f"{label}: no hardware driver yet.\n\nThe station runs on the simulator "
            "until the real drivers are added.")

    def _apply_run_options(self, *_args):
        """Run options take effect and are saved the moment they are changed."""
        if self._loading:
            return
        run = self.settings.data["run"]
        run.update(offline=self.cb_offline.isChecked(), dry_run=self.cb_dry.isChecked(),
                   allow_unverified_writes=self.cb_unverified.isChecked(),
                   sim_fault=self.fault.currentText())
        try:
            self.settings.save()
        except OSError as exc:
            QMessageBox.critical(self, "Settings", f"Could not save settings:\n{exc}")

    def load(self):
        self._loading = True
        try:
            self._load()
        finally:
            self._loading = False

    def _load(self):
        self.settings.data = self.settings.load()
        run, station = self.settings.data["run"], self.settings.data["station"]
        self.cb_offline.setChecked(bool(run.get("offline", True)))
        self.cb_dry.setChecked(bool(run.get("dry_run", False)))
        self.cb_unverified.setChecked(bool(run.get("allow_unverified_writes", False)))
        fault = run.get("sim_fault", "None")
        self.fault.setCurrentText(fault if fault in params.SIM_FAULTS else "None")
        self.station_id.setText(station.get("station_id", ""))
        self.slot_count.setCurrentText(str(station.get("slot_count", 2)))
        for (slot_id, key), (model, address, _status) in self._equip.items():
            saved = self.settings.equipment(slot_id, key)
            if saved["model"]:
                model.setCurrentText(saved["model"])
            address.setText(saved["address"])

    def save(self):
        if self.engine.any_running():
            QMessageBox.warning(self, "Settings", "Stop the running tests before saving settings.")
            return
        run, station = self.settings.data["run"], self.settings.data["station"]
        run.update(offline=self.cb_offline.isChecked(), dry_run=self.cb_dry.isChecked(),
                   allow_unverified_writes=self.cb_unverified.isChecked(),
                   sim_fault=self.fault.currentText())
        station.update(station_id=self.station_id.text().strip() or "800G_DDMI_Cal",
                       slot_count=int(self.slot_count.currentText()))
        for (slot_id, key), (model, address, _status) in self._equip.items():
            self.settings.equipment(slot_id, key).update(
                model=model.currentText(), address=address.text().strip())
        try:
            self.settings.save()
        except OSError as exc:
            QMessageBox.critical(self, "Settings", f"Could not save settings:\n{exc}")
            return
        QMessageBox.information(self, "Settings", f"Saved to {self.settings.path.name}.")
