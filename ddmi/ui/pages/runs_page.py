import re
from collections import Counter

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import params
from ..state import Engine
from ..widgets import Pie

_CH_OK = "background: #e9f5ec; border: 1px solid #b7dcc0; border-radius: 4px; padding: 6px;"
_CH_BAD = "background: #fbe9eb; border: 1px solid #e6a8ae; border-radius: 4px; padding: 6px; font-weight: 700;"


class _SlotSection(QGroupBox):
    def __init__(self, title: str):
        super().__init__(title)
        row = QHBoxLayout(self)
        self.pie = Pie()
        row.addWidget(self.pie)
        col = QVBoxLayout()
        self.lbl_info = QLabel("—")
        self.lbl_info.setStyleSheet("font-size: 13px;")
        col.addWidget(self.lbl_info)
        col.addWidget(QLabel("Failures per channel:"))
        grid = QGridLayout()
        self.ch_labels = []
        for ch in range(1, params.CHANNELS + 1):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            self.ch_labels.append(lbl)
            grid.addWidget(lbl, 0, ch - 1)
        col.addLayout(grid)
        self.lbl_top = QLabel()
        self.lbl_top.setStyleSheet("color: #555;")
        self.lbl_top.setWordWrap(True)
        col.addWidget(self.lbl_top)
        col.addStretch(1)
        row.addLayout(col, 1)

    def show_records(self, records: list):
        passes = sum(r.get("result") == "PASS" for r in records)
        fails = sum(r.get("result") == "FAIL" for r in records)
        self.pie.set_data(passes, fails)
        other = len(records) - passes - fails
        self.lbl_info.setText(f"<b>{passes}</b> PASS &nbsp; <b>{fails}</b> FAIL"
                              + (f" &nbsp; {other} aborted/error" if other else ""))
        per_ch, per_param = Counter(), Counter()
        for rec in records:
            for item in rec.get("failing_items", []):
                m = re.fullmatch(r"(.+)_CH(\d)", item)
                per_param[m.group(1) if m else item] += 1
                if m:
                    per_ch[int(m.group(2))] += 1
        for ch, lbl in enumerate(self.ch_labels, start=1):
            lbl.setText(f"Ch{ch}\n{per_ch[ch]}")
            lbl.setStyleSheet(_CH_BAD if per_ch[ch] else _CH_OK)
        top = ", ".join(f"{p} ({n})" for p, n in per_param.most_common(5))
        self.lbl_top.setText(f"Most common failures: {top}" if top else "No failures recorded.")


class RunsPage(QWidget):
    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<b style='font-size:14px;'>Run History</b>"))
        hdr.addStretch(1)
        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh_from_state)
        hdr.addWidget(btn)
        lay.addLayout(hdr)

        self.sections = {}
        for slot_id in range(1, engine.settings.slot_count + 1):
            section = _SlotSection(f"Slot-{slot_id} ({engine.settings.slot_name(slot_id)})")
            self.sections[slot_id] = section
            lay.addWidget(section)
        lay.addStretch(1)

        engine.record_saved.connect(self.refresh_from_state)
        self.refresh_from_state()

    def refresh_from_state(self):
        records = self.engine.records.load_all()
        for slot_id, section in self.sections.items():
            section.show_records([r for r in records if r.get("slot") == slot_id])
