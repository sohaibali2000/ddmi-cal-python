"""Files the UI reads and writes: settings, Tx Loss, RX tables, run records."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from . import params

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
STATION_DIR = ROOT / "station_data"
RESULTS_DIR = ROOT / "results"

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

    @property
    def slot_count(self) -> int:
        n = int(self.data["station"].get("slot_count", 2))
        return max(1, min(params.MAX_SLOTS, n))

    def slot_name(self, slot_id: int) -> str:
        names = self.data["station"].get("slot_names", [])
        return names[slot_id - 1] if slot_id <= len(names) else f"Slot-{slot_id}"

    def equipment(self, slot_id: int, key: str) -> dict:
        slot = self.data["equipment"].setdefault(str(slot_id), {})
        return slot.setdefault(key, {"model": "", "address": ""})


class LossStore:
    """Tx Loss in the LabVIEW station ini: [Data] Tx Loss="v1<tab>...<tab>v8", CH1->CH8.

    Slot 1 uses system_Data.ini and slot N uses system_DataN.ini, as the Left/Right
    LabVIEW stations do. The tab is written as LabVIEW's \\09 escape; a literal tab
    or \\t is accepted when reading. Check this against an ini saved by the station.
    """

    KEY = "Tx Loss"

    def path(self, slot_id: int) -> Path:
        suffix = "" if slot_id == 1 else str(slot_id)
        return STATION_DIR / f"system_Data{suffix}.ini"

    def get(self, slot_id: int) -> list[float] | None:
        try:
            text = self.path(slot_id).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = re.search(r"^\s*Tx Loss\s*=\s*\"?([^\"\r\n]*)\"?\s*$", text, re.M)
        if not m:
            return None
        raw = m.group(1).replace("\\09", "\t").replace("\\t", "\t")
        try:
            values = [float(v) for v in raw.split("\t") if v.strip()]
        except ValueError:
            return None
        return values if len(values) == params.CHANNELS else None

    def set(self, slot_id: int, values: list[float]):
        path = self.path(slot_id)
        line = f'{self.KEY}="' + "\\09".join(f"{v:.2f}" for v in values) + '"'
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for i, old in enumerate(lines):
            if re.match(r"\s*Tx Loss\s*=", old):
                lines[i] = line
                break
        else:
            if not any(l.strip() == "[Data]" for l in lines):
                lines.append("[Data]")
                lines.append(line)
            else:
                lines.insert(next(i for i, l in enumerate(lines) if l.strip() == "[Data]") + 1, line)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")


class RxTableStore:
    """CHn_Rx_calibration.txt: one row per step, Att / Power (-dBm, positive) / Diff."""

    def dir(self, slot_id: int) -> Path:
        return STATION_DIR / "rx_tables" / f"slot{slot_id}"

    def file(self, slot_id: int, ch: int, folder: Path | None = None) -> Path:
        return (folder or self.dir(slot_id)) / f"CH{ch}_Rx_calibration.txt"

    def load(self, slot_id: int, folder: Path | None = None) -> dict[int, list[tuple[float, float]]]:
        table = {}
        for ch in range(1, params.CHANNELS + 1):
            rows = []
            try:
                text = self.file(slot_id, ch, folder).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                parts = line.split()
                try:
                    rows.append((float(parts[0]), float(parts[1])))
                except (IndexError, ValueError):
                    continue
            if rows:
                table[ch] = rows
        return table

    def save(self, slot_id: int, table: dict[int, list[tuple[float, float]]]):
        self.dir(slot_id).mkdir(parents=True, exist_ok=True)
        for ch, rows in table.items():
            text = "".join(f"{att:.2f}\t{p:.2f}\t{p - att:.2f}\r\n" for att, p in rows)
            self.file(slot_id, ch).write_text(text, encoding="utf-8", newline="")


class RecordStore:
    def __init__(self, root: Path = RESULTS_DIR):
        self.root = root

    def save(self, record: dict, log_lines: list[str]) -> Path:
        stamp = record["start_time"].replace(":", "").replace("-", "").replace(" ", "_")
        name = f"{record['sn'] or 'NOSN'}_{stamp}_slot{record['slot']}"
        log_path = self.root / "logs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        record["log_file"] = str(log_path.relative_to(self.root))
        rec_path = self.root / "records" / f"{name}.json"
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return rec_path

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
