from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import PILL_OFF, PILL_ON, STATE_COLORS


class SlotsPage(QWidget):
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
        self.lbl_info = QLabel()
        top.addWidget(self.lbl_info)
        top.addStretch(1)
        lay.addLayout(top)

        # First column = test items, then Ch1..Ch8. Module-level items span all channels.
        self.table = QTableWidget(0, params.CHANNELS + 1)
        self.table.setHorizontalHeaderLabels(
            ["Test Item"] + [f"Ch{i}" for i in range(1, params.CHANNELS + 1)])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)

        self._rows = {}   # param -> row
        self._build_rows()
        self.set_slot(1)
        engine.slot_updated.connect(self._on_slot_updated)

    def _build_rows(self):
        bold = QFont()
        bold.setBold(True)
        for title, rows in params.GROUPS:
            r = self.table.rowCount()
            self.table.insertRow(r)
            head = QTableWidgetItem(title)
            head.setFont(bold)
            head.setBackground(QColor(225, 232, 240))
            self.table.setItem(r, 0, head)
            self.table.setSpan(r, 0, 1, self.table.columnCount())
            for param in rows:
                r = self.table.rowCount()
                self.table.insertRow(r)
                name = QTableWidgetItem(param)
                lim = params.LIMITS.get(param)
                if lim:
                    name.setToolTip(f"Limit: {lim[0]} to {lim[1]}")
                self.table.setItem(r, 0, name)
                for c in range(1, self.table.columnCount()):
                    cell = QTableWidgetItem("")
                    cell.setTextAlignment(Qt.AlignCenter)
                    self.table.setItem(r, c, cell)
                if param in params.MODULE_LEVEL:
                    self.table.setSpan(r, 1, 1, params.CHANNELS)
                self._rows[param] = r
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Stretch)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 220)

    def set_slot(self, slot_id: int):
        self.current_slot_id = slot_id
        for i, btn in enumerate(self.slot_btns):
            btn.setStyleSheet(PILL_ON if i + 1 == slot_id else PILL_OFF)
        self.refresh_from_state()

    @pyqtSlot(int)
    def _on_slot_updated(self, slot_id: int):
        if slot_id == self.current_slot_id:
            self.refresh_from_state()

    def refresh_from_state(self):
        rd = self.engine.run_data[self.current_slot_id]
        state = rd.meta.get("state", "IDLE")
        last = (f"    (last saved run, {rd.meta['start_time']})"
                if rd.meta.get("from_record") and rd.meta.get("start_time") else "")
        self.lbl_info.setText(f"SN: {rd.meta.get('sn') or '—'}    Result: {state}{last}")
        self.lbl_info.setStyleSheet(f"font-weight: 700; color: {STATE_COLORS.get(state, '#555')};")
        for param, row in self._rows.items():
            chans = rd.results.get(param, {})
            module = param in params.MODULE_LEVEL
            for ch in range(1, params.CHANNELS + 1):
                data = chans.get(0 if module else ch)
                cell = self.table.item(row, ch)
                cell.setText("" if data is None else "NaN" if data["value"] is None else str(data["value"]))
                if data is not None:
                    cell.setForeground(QColor(0, 128, 0) if data["ok"] else QColor(255, 0, 0))
                if module:
                    break
