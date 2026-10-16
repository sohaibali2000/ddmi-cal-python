"""Result table: every measured item with its limit and pass/fail, and the saved record."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Callable

# Result item -> key in the product config `limits` (and in the MES spec).
LIMIT_KEYS = {
    "Tops_Setpoint": "tops_setpoint",
    "Bias_setpoint": "bias_setpoint",
    "Tops_Mid(V)": "tops_mid_v",
    "Tops_off(V)": "tops_off_v",
    "TxP(dBm)": "txp_dbm",
    "DDMI_TxP(dBm)": "ddmi_txp_dbm",
    "Txp_Offset": "txp_offset",
    "Txp_Slope": "txp_slope",
    "Rxp_Offset": "rxp_offset",
    "Real_Vcc(V)": "real_vcc_v",
    "Vcc_Offset": "vcc_offset",
    "Vcc_Slope": "vcc_slope",
    "Rxp_Slope": "rx_slope",
    "Tx_Disable(dBm)": "tx_disable_dbm",
    "dTxP(dB)": "dtxp_db",
    "dRxP(dB)": "rx_check_error_db",
    "dVcc(V)": "dvcc_v",
    "DDMI_Temp(C)": "ddmi_temp_c",
}


def limits_from(product_limits: dict, spec: dict | None = None) -> dict[str, tuple]:
    """Limits per result item; a MES spec overrides the product config."""
    merged = dict(product_limits)
    merged.update(spec or {})
    return {item: tuple(merged[key]) for item, key in LIMIT_KEYS.items() if key in merged}


def item_name(item: str, ch: int) -> str:
    return item if ch == 0 else f"{item}_CH{ch}"


class Results:
    def __init__(self, limits: dict[str, tuple],
                 on_add: Callable[[str, int, object, bool], None] | None = None):
        self.limits = limits
        self.on_add = on_add
        self.cells: dict[str, dict[int, dict]] = {}   # item -> ch (0 = module level) -> cell
        self.failures: list[str] = []                 # failures that are not a measured item

    def add(self, item: str, ch: int, value) -> bool:
        """Record one value. None, NaN and infinity always fail."""
        ok = True
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            value, ok = None, False
        elif isinstance(value, float) and value == 0:
            value = 0.0     # never show -0.0
        if ok and item in self.limits and isinstance(value, (int, float)):
            low, high = self.limits[item]
            ok = low <= value <= high
        self.cells.setdefault(item, {})[ch] = {"value": value, "ok": ok}
        if self.on_add:
            self.on_add(item, ch, value, ok)
        return ok

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def value(self, item: str, ch: int = 0):
        return self.cells[item][ch]["value"]

    def failing_items(self) -> list[str]:
        items = [item_name(item, ch) for item, chans in self.cells.items()
                 for ch, cell in sorted(chans.items()) if not cell["ok"]]
        return items + self.failures

    @property
    def passed(self) -> bool:
        return not self.failing_items()

    def as_dict(self) -> dict:
        return {item: {("module" if ch == 0 else f"ch{ch}"): cell for ch, cell in sorted(chans.items())}
                for item, chans in self.cells.items()}


def save_record(record: dict, log_lines: list[str], results_dir: Path) -> Path:
    """Write <name>.json and <name>.csv under records/, and the full log under logs/."""
    results_dir = Path(results_dir)
    stamp = record["start_time"].replace(":", "").replace("-", "").replace(" ", "_")
    name = f"{record.get('sn') or 'NOSN'}_{stamp}_slot{record.get('slot', 1)}"

    log_path = results_dir / "logs" / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
    record["log_file"] = f"logs/{name}.log"

    rec_dir = results_dir / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    json_path = rec_dir / f"{name}.json"
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    limits = record.get("limits", {})
    with open(rec_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["SN", "Item", "Channel", "Value", "Low", "High", "Result"])
        for item, cells in record.get("results", {}).items():
            low, high = limits.get(item, ("", ""))
            for key, cell in cells.items():
                writer.writerow([record.get("sn", ""), item, "" if key == "module" else key[2:],
                                 cell["value"], low, high, "PASS" if cell["ok"] else "FAIL"])
    return json_path
