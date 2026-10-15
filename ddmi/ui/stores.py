"""Files the UI reads and writes: its own settings, Tx Loss, RX tables, saved run records."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .. import stationfiles
from . import params

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

DEFAULT_SETTINGS = {
    "run": {
        "simulator": True,
        "offline": True,
        "dry_run": False,
        "allow_unverified_writes": False,
        "sim_fault": "None",
    },
    "station": {
        "station_id": "800G_DDMI_Cal",
        "slot_count": 2,
        "slot_names": ["Left", "Right", "Slot-3", "Slot-4"],
        "engineer_password": "admin",
    },
    "equipment": {},  # slot -> key -> {"model", "address"}
}


class SettingsStore:
    def __init__(self, path: Path = CONFIG_DIR / "ui_settings.json"):
        self.path = path
        self.data = self.load()

    def load(self) -> dict:
        data = copy.deepcopy(DEFAULT_SETTINGS)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(data.get(key), dict):
                data[key].update(value)
            else:
                data[key] = value
        return data

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    force_slot_count: int | None = None     # set by the app on the real station (one DUT position)

    @property
    def slot_count(self) -> int:
        if self.force_slot_count:
            return self.force_slot_count
        n = int(self.data["station"].get("slot_count", 2))
        return max(1, min(params.MAX_SLOTS, n))

    def slot_name(self, slot_id: int) -> str:
        names = self.data["station"].get("slot_names", [])
        return names[slot_id - 1] if slot_id <= len(names) else f"Slot-{slot_id}"

    def equipment(self, slot_id: int, key: str) -> dict:
        slot = self.data["equipment"].setdefault(str(slot_id), {})
        return slot.setdefault(key, {"model": "", "address": ""})


class LossStore:
    """Tx Loss per slot, in the LabVIEW station ini (see ddmi.stationfiles)."""

    def __init__(self, base: Path):
        self.base = Path(base)

    def path(self, slot_id: int) -> Path:
        return stationfiles.tx_loss_path(slot_id, self.base)

    def get(self, slot_id: int) -> list[float] | None:
        return stationfiles.read_tx_loss(self.path(slot_id))

    def set(self, slot_id: int, values: list[float]):
        stationfiles.write_tx_loss(self.path(slot_id), values)


class RxTableStore:
    """CHn_Rx_calibration.txt per slot (see ddmi.stationfiles)."""

    def __init__(self, base: Path):
        self.base = Path(base)

    def dir(self, slot_id: int) -> Path:
        return stationfiles.rx_table_dir(slot_id, self.base)

    def load(self, slot_id: int, folder: Path | None = None) -> dict[int, list[tuple[float, float]]]:
        return stationfiles.read_rx_rows(folder or self.dir(slot_id))

    def save(self, slot_id: int, table: dict[int, list[tuple[float, float]]]):
        stationfiles.write_rx_rows(self.dir(slot_id), table)


class RecordStore:
    """Reads the records and logs the runner saves under the results folder."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def load_all(self) -> list[dict]:
        records = []
        for path in (self.root / "records").glob("*.json"):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        records.sort(key=lambda r: r.get("start_time", ""), reverse=True)
        return records

    def read_log(self, record: dict) -> str:
        try:
            return (self.root / record.get("log_file", "")).read_text(encoding="utf-8")
        except OSError:
            return "(log file not found)"
