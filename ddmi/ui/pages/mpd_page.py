from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import params
from ..mpd_plot import MpdView, _lane
from ..state import Engine
from ..widgets import PILL_OFF, PILL_ON

COLUMNS = ["Ch", "Peak (V)", "Valley (V)", "Tops_Mid (V)", "Tops_Setpoint", "TOPS_OFF tuned (V)",
           "Leakage (dBm)"]


def fill_lane_table(table: QTableWidget, scan: dict | None):
    lanes = (scan or {}).get("lanes", {})
    for ch in range(1, params.CHANNELS + 1):
        info = _lane(lanes, ch) or {}
        values = [f"CH{ch}", info.get("peak_v"), info.get("valley_v"), info.get("mid_v"),
                  info.get("setpoint"), info.get("tops_off_v"), info.get("leakage_dbm")]
        for col, value in enumerate(values):
            if isinstance(value, float):
                text = f"{value:.2f}" if col == 6 else f"{value:.3f}"
            else:
                text = "" if value is None else str(value)
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(ch - 1, col, item)


def make_lane_table() -> QTableWidget:
    table = QTableWidget(params.CHANNELS, len(COLUMNS))
    table.setHorizontalHeaderLabels(COLUMNS)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return table


class MpdPage(QWidget):
    """Live MPD scan graph per slot: TXMON vs Vtops while the sweep runs, then the analysis."""

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self.current_slot_id = 1

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.slot_btns = []
        for i in range(1, engine.settings.slot_count + 1):
            btn = QPushButton(f"Slot-{i} ({engine.settings.slot_name(i)})")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, s=i: self.set_slot(s))
            self.slot_btns.append(btn)
            top.addWidget(btn)
        top.addSpacing(15)
        self.status = QLabel()
        self.status.setStyleSheet("font-weight: 700; color: #555;")
        top.addWidget(self.status)
        top.addStretch(1)
        lay.addLayout(top)

        split = QSplitter(Qt.Vertical)
        self.plot = MpdView(params.CHANNELS)
        split.addWidget(self.plot)
        self.table = make_lane_table()
        split.addWidget(self.table)
        split.setSizes([560, 200])
        lay.addWidget(split, 1)

        engine.mpd_updated.connect(self._on_mpd_updated)
        self.set_slot(1)

    def set_slot(self, slot_id: int):
        self.current_slot_id = slot_id
        for i, btn in enumerate(self.slot_btns):
            btn.setStyleSheet(PILL_ON if i + 1 == slot_id else PILL_OFF)
        self.refresh()

    @pyqtSlot(int)
    def _on_mpd_updated(self, slot_id: int):
        if slot_id == self.current_slot_id and self.isVisible():
            self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def refresh(self, *_args):
        scan = self.engine.run_data[self.current_slot_id].mpd
        if scan is None:
            self.status.setText("No MPD scan yet on this slot - start a test")
        elif not scan["lanes"]:
            self.status.setText(f"Sweeping... {len(scan['volts'])} / {scan.get('planned', '?')} points")
        else:
            self.status.setText(f"Scan complete: {len(scan['volts'])} points")
        self.plot.show_scan(scan)
        fill_lane_table(self.table, scan)
