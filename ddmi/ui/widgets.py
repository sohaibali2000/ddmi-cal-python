from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import QWidget

GREEN = (40, 167, 69)
RED = (220, 53, 69)
GRAY = (160, 160, 160)

PILL_ON = """QPushButton { background-color: #007bff; color: white; border: 1px solid #0056b3;
    border-radius: 15px; padding: 6px 14px; font-weight: bold; }"""
PILL_OFF = """QPushButton { background-color: #f0f0f0; color: black; border: 1px solid #ccc;
    border-radius: 15px; padding: 6px 14px; }
    QPushButton:hover { background-color: #e0e0e0; }"""

CONSOLE_STYLE = ("font-family: Consolas, monospace; font-size: 11px; "
                 "background: #111; color: #ddd; padding: 8px;")

STATE_COLORS = {"PASS": "#218838", "FAIL": "#c82333", "RUNNING": "#b8860b",
                "ABORTED": "#6c757d", "ERROR": "#c82333", "NOT_TESTED": "#c82333",
                "IDLE": "#555"}


class Led(QWidget):
    """idle = gray, running = blinking yellow, pass = green, fail = red."""

    _D = 14
    _COLORS = {"idle": GRAY, "running": (255, 200, 0), "pass": GREEN, "fail": RED}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._on = True
        self.setFixedSize(self._D + 4, self._D + 4)

    def set_state(self, state: str):
        if state != self._state:
            self._state, self._on = state, True
            self.update()

    def blink_tick(self):
        if self._state == "running":
            self._on = not self._on
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r, g, b = self._COLORS.get(self._state, GRAY)
        if self._state == "running" and not self._on:
            r, g, b = 90, 70, 0
        p.setBrush(QColor(r, g, b))
        p.setPen(QColor(max(r - 60, 0), max(g - 60, 0), max(b - 60, 0)))
        p.drawEllipse(2, 2, self._D, self._D)


class Pie(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.passes = self.fails = 0
        self.setFixedSize(130, 130)

    def set_data(self, passes: int, fails: int):
        self.passes, self.fails = passes, fails
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRect(8, 8, 114, 114)
        total = self.passes + self.fails
        p.setPen(Qt.NoPen)
        if not total:
            p.setBrush(QColor(*GRAY))
            p.drawEllipse(rect)
        else:
            span = round(360 * 16 * self.passes / total)
            p.setBrush(QColor(*GREEN))
            p.drawPie(rect, 90 * 16, -span)
            p.setBrush(QColor(*RED))
            p.drawPie(rect, 90 * 16 - span, -(360 * 16 - span))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(rect.adjusted(30, 30, -30, -30))
        p.setPen(QColor(40, 40, 40))
        font = QFont()
        font.setBold(True)
        p.setFont(font)
        text = f"{round(100 * self.passes / total)}%" if total else "-"
        p.drawText(rect, Qt.AlignCenter, text)
