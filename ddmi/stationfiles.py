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


TX_LOSS_LINE_RE = re.compile(r'(Tx Loss\s*=\s*")([^"]*)(")')


def format_tx_loss(values: list[float]) -> str:
    """The payload the production Tx Loss tool writes: 3 decimals, real tabs, then the
    literal 6 characters \\0D\\0A (LabVIEW's escaped line ending)."""
    return "\t".join(f"{v:.3f}" for v in values) + "\\0D\\0A"


def write_tx_loss(path: Path, values: list[float]) -> None:
    """Replace only the text between the quotes of the Tx Loss line.

    Every other byte of the file is left as it is (other keys, spacing, CR LF), as the
    production tool does. A file without a Tx Loss line gets one added under [Data];
    the production tool refuses such a file instead.
    """
    if len(values) != CHANNELS:
        raise ValueError(f"Tx Loss needs {CHANNELS} values, got {len(values)}")
    path = Path(path)
    payload = format_tx_loss(values)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            content = fh.read()
    except FileNotFoundError:
        content = ""

    content, count = TX_LOSS_LINE_RE.subn(lambda m: m.group(1) + payload + m.group(3), content, count=1)
    if count == 0:
        eol = "\r\n" if "\r\n" in content or not content else "\n"
        line = f'Tx Loss = "{payload}"'
        if "[Data]" in content:
            head, tail = content.split("[Data]", 1)
            content = head + "[Data]" + eol + line + (tail if tail.startswith(("\r", "\n")) else eol + tail)
        else:
            content = content + ("" if not content or content.endswith(("\r", "\n")) else eol) \
                + "[Data]" + eol + line + eol

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)


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
