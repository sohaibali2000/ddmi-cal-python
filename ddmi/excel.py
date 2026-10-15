"""Excel copy of a run's results (written for every PASS when the station sets excel_dir)."""
from __future__ import annotations

from pathlib import Path

CHANNELS = 8
_RX_PREFIXES = ("RSSI@", "Rxp_", "DD_RxP", "dRxP")


def _section(item: str, cells: dict) -> str:
    if "module" in cells:
        return "DDMI"
    return "RX" if item.startswith(_RX_PREFIXES) else "TX"


def save_excel(record: dict, out_dir: Path) -> Path:
    """<SN>_<start>_slot<n>_<RESULT>.xlsx: run details, then one row per item with Ch1-Ch8
    and its limits. Module-level items (ICC, VCC, temperature) fill all 8 channel columns."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "DDMI Cal"
    bold = Font(bold=True)
    center = Alignment(horizontal="center")
    green, red = Font(color="008000"), Font(color="FF0000")
    band = PatternFill("solid", fgColor="E1E8F0")

    info = [("SN", record.get("sn", "")), ("PN", record.get("pn", "")), ("Rev", record.get("rev", "")),
            ("Plan", record.get("plan", "")), ("Station", record.get("station", "")),
            ("Slot", record.get("slot", "")), ("User", record.get("user", "")),
            ("Start", record.get("start_time", "")), ("End", record.get("end_time", "")),
            ("Elapsed", record.get("elapsed", "")), ("Result", record.get("result", ""))]
    for name, value in info:
        ws.append([name, value])
        ws.cell(ws.max_row, 1).font = bold
    result_cell = ws.cell(ws.max_row, 2)
    result_cell.font = Font(bold=True, color="008000" if record.get("result") == "PASS" else "FF0000")
    ws.append([])

    ws.append(["Test Item"] + [f"Ch{c}" for c in range(1, CHANNELS + 1)] + ["Low", "High"])
    for cell in ws[ws.max_row]:
        cell.font, cell.alignment = bold, center

    limits = record.get("limits", {})
    results = record.get("results", {})
    for section in ("TX", "RX", "DDMI"):
        items = [(k, v) for k, v in results.items() if _section(k, v) == section]
        if not items:
            continue
        ws.append([section])
        for cell in ws[ws.max_row][:CHANNELS + 3]:
            cell.fill = band
        ws.cell(ws.max_row, 1).font = bold
        for item, cells in items:
            low, high = (limits.get(item) or [None, None])[:2]
            row = [item]
            oks = []
            for ch in range(1, CHANNELS + 1):
                cell = cells.get("module") or cells.get(f"ch{ch}")
                row.append(None if cell is None else cell.get("value"))
                oks.append(None if cell is None else cell.get("ok", True))
            ws.append(row + [low, high])
            r = ws.max_row
            for c, ok in enumerate(oks, start=2):
                ws.cell(r, c).alignment = center
                if ok is not None:
                    ws.cell(r, c).font = green if ok else red
            for c in (CHANNELS + 2, CHANNELS + 3):
                ws.cell(r, c).alignment = center

    ws.column_dimensions["A"].width = 18
    for col in "BCDEFGHIJK":
        ws.column_dimensions[col].width = 12

    stamp = record.get("start_time", "").replace(":", "").replace("-", "").replace(" ", "_")
    name = f"{record.get('sn') or 'NOSN'}_{stamp}_slot{record.get('slot', 1)}_{record.get('result', '')}.xlsx"
    path = out_dir / name
    wb.save(path)
    return path
