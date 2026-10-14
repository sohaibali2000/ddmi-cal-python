"""Station data files shared with LabVIEW: Tx Loss in system_Data.ini, and the RX tables."""
from __future__ import annotations

import re
from pathlib import Path

from .calc.rxtable import RxTable

CHANNELS = 8
ROOT = Path(__file__).resolve().parents[1]
TX_LOSS_FILE = ROOT / "station_data" / "system_Data.ini"
RX_TABLE_DIR = ROOT / "station_data" / "rx_tables"


def tx_loss_path(slot: int = 1, base: Path = TX_LOSS_FILE) -> Path:
    """Slot 1 uses system_Data.ini, slot N system_DataN.ini (LabVIEW Left/Right stations)."""
    base = Path(base)
    return base if slot == 1 else base.with_name(f"{base.stem}{slot}{base.suffix}")


def read_tx_loss(path: Path) -> list[float] | None:
    """[Data] Tx Loss="v1<tab>...<tab>v8", CH1 -> CH8. None if absent or malformed.

    Values may be negative. Tabs may be literal, \\t or \\09, and a trailing \\0D\\0A is ignored.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^\s*Tx Loss\s*=\s*\"?([^\"\r\n]*)\"?\s*$", text, re.M)
    if not m:
        return None
    raw = m.group(1).replace("\\0D", "").replace("\\0A", "")     # LabVIEW's escaped line ending
    raw = raw.replace("\\09", "\t").replace("\\t", "\t")
    try:
        values = [float(v) for v in raw.split() if v.strip()]
    except ValueError:
        return None
    return values if len(values) == CHANNELS else None


def write_tx_loss(path: Path, values: list[float]) -> None:
    """Replace the Tx Loss line, keeping the rest of the ini.

    Written the way LabVIEW saves a tab-delimited array: values to 3 decimals, tabs as
    the \\09 escape and a trailing \\0D\\0A. The trailing escape is confirmed from a
    station file; the \\09 spelling of the tab is not (a literal tab is read too).
    """
    if len(values) != CHANNELS:
        raise ValueError(f"Tx Loss needs {CHANNELS} values, got {len(values)}")
    path = Path(path)
    line = 'Tx Loss="' + "\\09".join(f"{v:.3f}" for v in values) + '\\0D\\0A"'
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for i, old in enumerate(lines):
        if re.match(r"\s*Tx Loss\s*=", old):
            lines[i] = line
            break
    else:
        sections = [i for i, l in enumerate(lines) if l.strip() == "[Data]"]
        if sections:
            lines.insert(sections[0] + 1, line)
        else:
            lines += ["[Data]", line]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")


def rx_table_dir(slot: int = 1, base: Path = RX_TABLE_DIR) -> Path:
    return Path(base) / f"slot{slot}"


def rx_table_file(folder: Path, ch: int) -> Path:
    return Path(folder) / f"CH{ch}_Rx_calibration.txt"


def read_rx_rows(folder: Path) -> dict[int, list[tuple[float, float]]]:
    """{ch: [(att dB, power as -dBm positive), ...]} for the channel files that exist."""
    table = {}
    for ch in range(1, CHANNELS + 1):
        rows = []
        try:
            text = rx_table_file(folder, ch).read_text(encoding="utf-8", errors="replace")
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


def write_rx_rows(folder: Path, table: dict[int, list[tuple[float, float]]]) -> None:
    """One row per step: Att, Power (-dBm, positive), Diff (= Power - Att, unused)."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    for ch, rows in table.items():
        text = "".join(f"{att:.2f}\t{p:.2f}\t{p - att:.2f}\r\n" for att, p in rows)
        rx_table_file(folder, ch).write_text(text, encoding="utf-8", newline="")


def load_rx_tables(folder: Path) -> dict[int, RxTable]:
    return {ch: RxTable(rows) for ch, rows in read_rx_rows(folder).items() if len(rows) >= 2}
