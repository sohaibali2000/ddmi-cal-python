"""MPD scan graph: TXMON vs Vtops per lane, with peak, valley, operating points, Tops_Mid and TOPS_OFF."""
from __future__ import annotations

import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget,
)

LANE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf"]

# marker: (key in the lane analysis, pyqtgraph symbol, size, legend label)
MARKERS = [
    ("peak", "t1", 12, "Peak"),
    ("valley", "t", 12, "Valley"),
    ("points", "o", 8, "Operating points"),
    ("mid", "d", 13, "Tops_Mid"),
    ("tops_off", "x", 12, "TOPS_OFF (tuned)"),
]


def _lane(lanes: dict, ch: int):
    """Lane analysis by channel; saved records use string keys."""
    return lanes.get(ch) or lanes.get(str(ch))


def _y_at(volts: list, curve: list, v: float) -> float | None:
    """TXMON on the swept curve at voltage v (linear interpolation)."""
    for i in range(len(volts) - 1):
        if volts[i] <= v <= volts[i + 1]:
            span = volts[i + 1] - volts[i]
            return curve[i] + (curve[i + 1] - curve[i]) * ((v - volts[i]) / span if span else 0)
    return None


class MpdPlot(QWidget):
    def __init__(self, parent=None, title: str | None = None):
        """title set = compact plot for the 8-graph grid: no legend, no axis titles."""
        super().__init__(parent)
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(background="w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        if title:
            self.plot.setTitle(title, color="#333", size="10pt")
        else:
            self.plot.setLabel("bottom", "Vtops (V)")
            self.plot.setLabel("left", "TXMON (ADC)")
            self.plot.addLegend(offset=(-10, 10), colCount=4, brush=pg.mkBrush(255, 255, 255, 210),
                                pen=pg.mkPen("#bbb"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot)
        self._key = None

    def show_scan(self, scan: dict | None, only_ch: int | None = None):
        """Draw a scan: {"volts": [...], "curves": [[lane 1], ...], "lanes": {ch: analysis}}."""
        volts = (scan or {}).get("volts", [])
        curves = (scan or {}).get("curves", [])
        lanes = (scan or {}).get("lanes", {})
        channels = [only_ch] if only_ch else list(range(1, len(curves) + 1))

        # Rebuild the items only when the layout changes; during a sweep just update the data.
        key = (tuple(channels), bool(lanes), id(scan))
        if key != self._key:
            self.plot.clear()
            if self.plot.getPlotItem().legend is not None:
                self.plot.getPlotItem().legend.clear()
            self._curves = {ch: self.plot.plot(name=f"CH{ch}", pen=pg.mkPen(LANE_COLORS[(ch - 1) % 8], width=2))
                            for ch in channels}
            self._markers = []
            if lanes:
                for _key, symbol, size, label in MARKERS:
                    item = pg.ScatterPlotItem(symbol=symbol, size=size, pen=pg.mkPen("k"),
                                              brush=pg.mkBrush("#888"))
                    self.plot.addItem(item)     # unnamed: the marker key above the graph explains them
                    self._markers.append(item)
            self._key = key

        for ch, item in self._curves.items():
            if ch - 1 < len(curves):
                n = min(len(volts), len(curves[ch - 1]))
                item.setData(volts[:n], curves[ch - 1][:n])

        if not lanes:
            return
        spots = {name: [] for name, *_ in MARKERS}
        for ch in channels:
            info, curve = _lane(lanes, ch), curves[ch - 1] if ch - 1 < len(curves) else []
            if not info or not curve:
                continue
            brush = pg.mkBrush(LANE_COLORS[(ch - 1) % 8])
            spots["peak"].append({"pos": (info["peak_v"], info["peak"]), "brush": brush})
            spots["valley"].append({"pos": (info["valley_v"], info["valley"]), "brush": brush})
            for v in (info.get("points") or {}).values():
                y = _y_at(volts, curve, v)
                if y is not None:
                    spots["points"].append({"pos": (v, y), "brush": pg.mkBrush("w"),
                                            "pen": pg.mkPen(LANE_COLORS[(ch - 1) % 8], width=2)})
            for name, field in (("mid", "mid_v"), ("tops_off", "tops_off_v")):
                v = info.get(field)
                y = _y_at(volts, curve, v) if v is not None else None
                if y is not None:
                    spots[name].append({"pos": (v, y), "brush": brush,
                                        "pen": pg.mkPen(LANE_COLORS[(ch - 1) % 8], width=2)})
        for (name, *_), item in zip(MARKERS, self._markers):
            item.setData(spots[name])


MARKER_KEY = "▲ Peak    ▼ Valley    ○ Operating points    ◆ Tops_Mid    ✕ TOPS_OFF (tuned)"
VIEWS = ["8 graphs (one per channel)", "All channels on one graph"]


class MpdView(QWidget):
    """The MPD scan in one of three views: 8-graph grid (default), all lanes overlaid, one channel.

    Double-click a graph in the grid to open that channel on its own.
    """

    def __init__(self, channels: int = 8, parent=None):
        super().__init__(parent)
        self.channels = channels
        self._scan = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("View:"))
        self.view = QComboBox()
        self.view.addItems(VIEWS + [f"CH{ch} only" for ch in range(1, channels + 1)])
        self.view.currentIndexChanged.connect(self._on_view_changed)
        top.addWidget(self.view)
        top.addSpacing(15)
        self.key = QLabel(MARKER_KEY)
        self.key.setStyleSheet("color: #555;")
        top.addWidget(self.key)
        top.addStretch(1)
        lay.addLayout(top)

        self.stack = QStackedWidget()
        grid_page = QWidget()
        grid = QGridLayout(grid_page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.small = []
        for ch in range(1, channels + 1):
            plot = MpdPlot(title=f"CH{ch}")
            plot.plot.scene().sigMouseClicked.connect(lambda ev, ch=ch: ev.double() and self.show_channel(ch))
            plot.setToolTip("Double-click to open this channel on its own")
            grid.addWidget(plot, (ch - 1) // 4, (ch - 1) % 4)
            self.small.append(plot)
        self.big = MpdPlot()
        self.stack.addWidget(grid_page)
        self.stack.addWidget(self.big)
        lay.addWidget(self.stack, 1)

    def show_channel(self, ch: int):
        self.view.setCurrentIndex(len(VIEWS) + ch - 1)

    def _on_view_changed(self, *_args):
        self.show_scan(self._scan)

    def show_scan(self, scan: dict | None):
        self._scan = scan
        index = self.view.currentIndex()
        if index == 0:
            self.stack.setCurrentIndex(0)
            for ch, plot in enumerate(self.small, start=1):
                plot.show_scan(scan, ch)
        else:
            self.stack.setCurrentIndex(1)
            self.big.show_scan(scan, None if index == 1 else index - len(VIEWS) + 1)
