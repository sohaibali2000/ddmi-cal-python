import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import CONSOLE_STYLE, STATE_COLORS

COLUMNS = ["Start", "SN", "Slot", "Plan", "Result", "Failing items", "Elapsed", "User"]


class _RecordDialog(QDialog):
    def __init__(self, record: dict, log_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{record.get('sn', '')} - {record.get('start_time', '')}")
        self.resize(1000, 700)
        lay = QVBoxLayout(self)
        result = record.get("result", "")
        head = QLabel(f"<b>SN {record.get('sn', '')}</b> &nbsp; Slot-{record.get('slot')} &nbsp; "
                      f"{record.get('station', '')} &nbsp; <b>{result}</b>"
                      + (" &nbsp; (simulated)" if record.get("simulated") else ""))
        head.setStyleSheet(f"color: {STATE_COLORS.get(result, '#333')}; font-size: 13px;")
        lay.addWidget(head)

        tabs = QTabWidget()
        table = QTableWidget(0, params.CHANNELS + 1)
        table.setHorizontalHeaderLabels(["Test Item"] + [f"Ch{i}" for i in range(1, params.CHANNELS + 1)])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for param, cells in record.get("results", {}).items():
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(param))
            for key, cell in cells.items():
                item = QTableWidgetItem("NaN" if cell.get("value") is None else str(cell.get("value")))
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QColor(0, 128, 0) if cell.get("ok") else QColor(255, 0, 0))
                if key == "module":
                    table.setSpan(r, 1, 1, params.CHANNELS)
                    table.setItem(r, 1, item)
                else:
                    table.setItem(r, int(key[2:]), item)
        table.setColumnWidth(0, 200)
        tabs.addTab(table, "Results")

        log = QTextEdit()
        log.setReadOnly(True)
        log.setStyleSheet(CONSOLE_STYLE)
        log.setPlainText(log_text)
        tabs.addTab(log, "Log")
        lay.addWidget(tabs, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)


class RecordsPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        self._records = []
        self._shown = []

        lay = QVBoxLayout(self)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<b style='font-size:14px;'>Records - Saved Run History</b>"))
        hdr.addStretch(1)
        hdr.addWidget(QLabel("SN filter:"))
        self.filter = QLineEdit()
        self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(self._populate)
        hdr.addWidget(self.filter)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_from_state)
        hdr.addWidget(btn_refresh)
        btn_folder = QPushButton("Open Results Folder")
        btn_folder.clicked.connect(self._open_folder)
        hdr.addWidget(btn_folder)
        lay.addLayout(hdr)

        self.summary = QLabel()
        lay.addWidget(self.summary)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        for c, w in enumerate((150, 130, 50, 210, 80, 330, 70)):
            self.table.setColumnWidth(c, w)
        self.table.doubleClicked.connect(self._open_selected)
        lay.addWidget(self.table, 1)
        lay.addWidget(QLabel("Double-click a row to open its results and full log."))

        engine.record_saved.connect(self.refresh_from_state)
        self.refresh_from_state()

    def _open_folder(self):
        root = self.engine.records.root
        root.mkdir(parents=True, exist_ok=True)
        os.startfile(root)

    def refresh_from_state(self):
        self._records = self.engine.records.load_all()
        self._populate()

    def _populate(self):
        needle = self.filter.text().strip().lower()
        self._shown = [r for r in self._records if needle in str(r.get("sn", "")).lower()]
        passes = sum(r.get("result") == "PASS" for r in self._shown)
        self.summary.setText(f"{len(self._shown)} records, {passes} PASS, "
                             f"{sum(r.get('result') == 'FAIL' for r in self._shown)} FAIL")
        self.table.setRowCount(len(self._shown))
        for r, rec in enumerate(self._shown):
            values = [rec.get("start_time", ""), rec.get("sn", ""), rec.get("slot", ""),
                      rec.get("plan", ""), rec.get("result", ""),
                      ", ".join(rec.get("failing_items", [])), rec.get("elapsed", ""),
                      rec.get("user", "")]
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c == 4:
                    item.setForeground(QColor(STATE_COLORS.get(str(value), "#333")))
                self.table.setItem(r, c, item)

    def _open_selected(self, _index=None):
        row = self.table.currentRow()
        if 0 <= row < len(self._shown):
            rec = self._shown[row]
            _RecordDialog(rec, self.engine.records.read_log(rec), self).exec_()
