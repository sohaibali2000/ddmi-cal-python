import time

from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import CONSOLE_STYLE, STATE_COLORS, Led

_BTN = """QPushButton {{ background-color: {bg}; color: {fg}; font-weight: 700;
    border: 2px solid {bd}; border-radius: 6px; padding: 7px 16px; }}
    QPushButton:hover {{ background-color: {hv}; }}
    QPushButton:disabled {{ background-color: #d5d5d5; border-color: #c2c2c2; color: #f4f4f4; }}"""


class SlotCard(QFrame):
    def __init__(self, engine: Engine, slot_id: int):
        super().__init__()
        self.engine = engine
        self.slot_id = slot_id
        self.setObjectName("SlotCard")
        self.setStyleSheet("#SlotCard { background-color: #f5f5f5; border: 1px solid #bbb; border-radius: 12px; }")

        lay = QVBoxLayout(self)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        title = QLabel(f"Slot-{slot_id}  ({engine.settings.slot_name(slot_id)})")
        title.setStyleSheet("font-weight: 800; font-size: 16px;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        self.lbl_status = QLabel("IDLE")
        hdr.addWidget(self.lbl_status)
        lay.addLayout(hdr)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Plan:"))
        self.plan = QComboBox()
        self.plan.addItems(params.PLANS)
        row.addWidget(self.plan, 2)
        row.addSpacing(12)
        self._leds = {}
        for step in params.STEPS:
            col = QVBoxLayout()
            col.setSpacing(1)
            led = Led()
            lbl = QLabel(step)
            lbl.setStyleSheet("font-size: 9px; font-weight: bold; color: #555;")
            col.addWidget(led, 0, Qt.AlignHCenter)
            col.addWidget(lbl, 0, Qt.AlignHCenter)
            row.addLayout(col)
            self._leds[step] = led
        lay.addLayout(row)

        sn_row = QHBoxLayout()
        sn_row.addWidget(QLabel("Scan SN:"))
        self.sn_edit = QLineEdit()
        self.sn_edit.setPlaceholderText("Scan module SN (blank = use the simulated module's SN)")
        self.sn_edit.returnPressed.connect(self.start)
        sn_row.addWidget(self.sn_edit, 1)
        lay.addLayout(sn_row)

        info = QHBoxLayout()
        info.setSpacing(12)
        self.lbl_sn = QLabel()
        self.lbl_start = QLabel()
        self.lbl_end = QLabel()
        self.lbl_elapsed = QLabel()
        for lbl in (self.lbl_sn, self.lbl_start, self.lbl_end, self.lbl_elapsed):
            lbl.setStyleSheet("font-size: 11px; color: #555;")
            info.addWidget(lbl)
        info.addStretch(1)
        lay.addLayout(info)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(100)
        self.console.setStyleSheet(CONSOLE_STYLE)
        lay.addWidget(self.console, 1)

        btns = QHBoxLayout()
        btns.setSpacing(12)
        self.btn_start = QPushButton("Start Test")
        self.btn_clear = QPushButton("Clear Log")
        self.btn_abort = QPushButton("Abort Test")
        self.btn_start.setStyleSheet(_BTN.format(bg="#218838", fg="white", bd="#17692b", hv="#1e7e34"))
        self.btn_abort.setStyleSheet(_BTN.format(bg="#c82333", fg="white", bd="#9f1c28", hv="#bd2130"))
        self.btn_clear.setStyleSheet(_BTN.format(bg="#f8f9fa", fg="#343a40", bd="#6c757d", hv="#e2e6ea"))
        for b in (self.btn_start, self.btn_clear, self.btn_abort):
            b.setMinimumHeight(38)
            b.setCursor(Qt.PointingHandCursor)
        btns.addWidget(self.btn_start, 2)
        btns.addWidget(self.btn_clear)
        btns.addSpacing(18)
        btns.addWidget(self.btn_abort, 2)
        lay.addLayout(btns)

        self.btn_start.clicked.connect(self.start)
        self.btn_abort.clicked.connect(self.abort)
        self.btn_clear.clicked.connect(self.console.clear)
        self.refresh()

    def blink_tick(self):
        for led in self._leds.values():
            led.blink_tick()

    def start(self):
        if self.engine.is_running(self.slot_id):
            return
        offline = bool(self.engine.settings.data["run"].get("offline", True))
        if not offline and not self.engine.user_id:
            QMessageBox.warning(self, "User ID Required",
                                "Please enter a User ID before starting in online mode.")
            return
        self.console.clear()
        self.engine.start_slot(self.slot_id, self.plan.currentText(), self.sn_edit.text().strip())
        self.sn_edit.clear()

    def abort(self):
        if not self.engine.is_running(self.slot_id):
            return
        answer = QMessageBox.question(
            self, f"Abort Slot-{self.slot_id}?",
            "Abort the running test?\n\nThe current test sequence will stop.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.engine.abort_slot(self.slot_id)

    def append_log(self, line: str):
        self.console.append(line)

    def refresh(self):
        rd = self.engine.run_data[self.slot_id]
        state = rd.meta.get("state", "IDLE")
        self.lbl_status.setText(state)
        self.lbl_status.setStyleSheet(
            f"font-weight: 800; font-size: 15px; color: {STATE_COLORS.get(state, '#555')};")
        running = state == "RUNNING"
        elapsed = rd.meta.get("elapsed", "")
        if running:
            secs = int(time.time() - rd.meta["start_ts"])
            elapsed = f"{secs // 60:02d}:{secs % 60:02d}"
        self.lbl_sn.setText(f"SN: {rd.meta.get('sn') or '—'}")
        self.lbl_start.setText(f"Start: {rd.meta.get('start_time', '—')}")
        self.lbl_end.setText(f"End: {rd.meta.get('end_time', '—')}")
        self.lbl_elapsed.setText(f"Elapsed: {elapsed or '—'}")
        self.btn_start.setEnabled(not running)
        self.btn_abort.setEnabled(running)
        self.plan.setEnabled(not running)
        self.sn_edit.setEnabled(not running)
        for step, led in self._leds.items():
            led.set_state(rd.steps.get(step, "idle"))


class DashboardPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("User ID:"))
        self.user = QLineEdit()
        self.user.setPlaceholderText("Enter User ID")
        self.user.setMaximumWidth(260)
        self.user.textChanged.connect(self._set_user)
        top.addWidget(self.user)
        top.addStretch(1)
        lay.addLayout(top)

        grid = QGridLayout()
        self.cards = []
        for i in range(engine.settings.slot_count):
            card = SlotCard(engine, i + 1)
            self.cards.append(card)
            grid.addWidget(card, i // 2, i % 2)
        lay.addLayout(grid, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        engine.slot_updated.connect(self._on_slot_updated)
        engine.log_line.connect(self._on_log_line)

    def _set_user(self, text: str):
        self.engine.user_id = text.strip()

    def _tick(self):
        for card in self.cards:
            card.blink_tick()
            if self.engine.is_running(card.slot_id):
                card.refresh()

    @pyqtSlot(int)
    def _on_slot_updated(self, slot_id: int):
        if 1 <= slot_id <= len(self.cards):
            self.cards[slot_id - 1].refresh()

    @pyqtSlot(int, str)
    def _on_log_line(self, slot_id: int, line: str):
        if 1 <= slot_id <= len(self.cards):
            self.cards[slot_id - 1].append_log(line)
