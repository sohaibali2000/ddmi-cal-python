import os

from PyQt5.QtWidgets import QLabel, QMainWindow, QMessageBox, QTabWidget

from .. import config
from ..station import is_sim

from .pages.dashboard_page import DashboardPage
from .pages.loss_page import LossPage
from .pages.mpd_page import MpdPage
from .pages.records_page import RecordsPage
from .pages.runs_page import RunsPage
from .pages.settings_page import SettingsPage
from .pages.slots_page import SlotsPage
from .state import Engine
from .stores import SettingsStore


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("800G DDMI Cal - AWS 2xFR4")
        self.resize(1280, 820)

        # DDMI_STATION selects another station file, e.g. config/station_lab.yaml.
        station_file = os.environ.get("DDMI_STATION", str(config.CONFIG_DIR / "station_sim.yaml"))
        self.engine = Engine(SettingsStore(), config.load_station(station_file))

        self.tabs = QTabWidget()
        self.page_dashboard = DashboardPage(self.engine)
        self.page_settings = SettingsPage(self.engine)
        self.page_loss = LossPage(self.engine)
        self.page_records = RecordsPage(self.engine)
        self.page_slots = SlotsPage(self.engine)
        self.page_runs = RunsPage(self.engine)
        self.page_mpd = MpdPage(self.engine)
        self.tabs.addTab(self.page_dashboard, "Dashboard")
        self.tabs.addTab(self.page_settings, "Settings")
        self.tabs.addTab(self.page_loss, "Loss")
        self.tabs.addTab(self.page_records, "Records")
        self.tabs.addTab(self.page_slots, "Slots")
        self.tabs.addTab(self.page_mpd, "MPD Scan")
        self.tabs.addTab(self.page_runs, "Runs")
        self.setCentralWidget(self.tabs)

        if is_sim(self.engine.station_cfg):
            badge = QLabel("  SIMULATOR MODE - no hardware connected, all values are simulated  ")
            badge.setStyleSheet("background: #fff3cd; color: #7a5b00; font-weight: 700; padding: 3px;")
            self.statusBar().addPermanentWidget(badge)

    def closeEvent(self, event):
        if self.engine.any_running():
            answer = QMessageBox.question(
                self, "Exit", "A test is running. Abort it and exit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.engine.shutdown()
        event.accept()
