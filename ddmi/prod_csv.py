"""Results in the production LabVIEW station's CSV layout (YYYY-MM-DD_DDMI2XFR4.csv).

One file per day. Per module: a MAX row and a MIN row with the limits, then one row per
channel. Module-level items (VCC, temperature) repeat on every channel row. Layout copied
from the production files of 2026-10.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

HEADER = [
    "", "ALL-SN", "Result", "Test Time", "Machine time(S)",
    "Tx_Disable(dBm)", "Tops_Mid", "Tops_off", "Power(dBm)", "DDMI_TxP", "dTxP",
    "Txp_Offset", "Txp_Slope", "Bias_setpoint", "Tops_setpoint",
    "RxP1", "DD_RxP1", "dRxP1", "Rxp_Offset", "Rxp_Slope",
    "I2C_Vcc", "DDMI_Vcc", "dVcc(%)", "Vcc_Offset", "Vcc_Slope", "DDMI_Temp",
    "TXP_MPDOut_4.5dB", "TXP_MPDOut_3.5dB", "TXP_MPDOut_3dB", "TXP_MPDOut_2.5dB", "error item",
]

# CSV column -> (result item, decimals, MAX/MIN when the record has no limit for it).
# "/" = no limit (written as "/" like the production file), "" = left empty.
COLUMNS = {
    "Tx_Disable(dBm)": ("Tx_Disable(dBm)", 2, None),
    "Tops_Mid": ("Tops_Mid(V)", 2, None),
    "Tops_off": ("Tops_off(V)", 2, None),
    "Power(dBm)": ("TxP(dBm)", 2, None),
    "DDMI_TxP": ("DDMI_TxP(dBm)", 2, None),
    "dTxP": ("dTxP(dB)", 2, None),
    "Txp_Offset": ("Txp_Offset", 2, None),
    "Txp_Slope": ("Txp_Slope", 2, None),
    "Bias_setpoint": ("Bias_setpoint", 0, None),
    "Tops_setpoint": ("Tops_Setpoint", 0, None),
    "RxP1": (None, 0, ""),
    "DD_RxP1": ("DD_RxP@", 3, ""),               # prefix: the item name carries the check power
    "dRxP1": ("dRxP(dB)", 3, None),
    "Rxp_Offset": ("Rxp_Offset", 2, None),
    "Rxp_Slope": ("Rxp_Slope", 2, None),
    "I2C_Vcc": ("Real_Vcc(V)", 2, None),
    "DDMI_Vcc": ("DDMI_Vcc(V)", 2, "/"),
    "dVcc(%)": ("dVcc(V)", 2, None),
    "Vcc_Offset": ("Vcc_Offset", 2, None),
    "Vcc_Slope": ("Vcc_Slope", 2, None),
    "DDMI_Temp": ("DDMI_Temp(C)", 2, None),
}
# The production file's MAX/MIN rows (2026-10), used for any column whose limit the
# record does not carry (records saved before these limits were checked).
PROD_LIMITS = {
    "Tx_Disable(dBm)": (-60, -16), "Tops_Mid": (0, 3), "Tops_off": (0, 3),
    "Power(dBm)": (-3.3, 10000), "DDMI_TxP": (-3.8, 10000), "dTxP": (-0.5, 0.5),
    "Txp_Offset": (-10000, 10000), "Txp_Slope": (5, 150), "Bias_setpoint": (100, 2500),
    "Tops_setpoint": (100, 2500), "dRxP1": (-0.5, 0.5), "Rxp_Offset": (-10000, 10000),
    "Rxp_Slope": (5, 30), "I2C_Vcc": (3.234, 3.366), "dVcc(%)": (-0.033, 0.033),
    "Vcc_Offset": (-10000, 10000), "Vcc_Slope": (15, 30), "DDMI_Temp": (30, 60),
}
MPD_COLUMNS = {"TXP_MPDOut_4.5dB": "0.65", "TXP_MPDOut_3.5dB": "0.56",
               "TXP_MPDOut_3dB": "0.5", "TXP_MPDOut_2.5dB": "0.44"}


def _fmt(value, decimals: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    text = f"{float(value):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _item(results: dict, name: str) -> str | None:
    if name.endswith("@"):
        return next((k for k in results if k.startswith(name)), None)
    return name if name in results else None


def _cell(results: dict, item: str | None, ch: int) -> dict | None:
    if item is None:
        return None
    cells = results.get(item, {})
    return cells.get("module") or cells.get(f"ch{ch}")


def _limit_rows(record: dict) -> tuple[list, list]:
    results, limits = record.get("results", {}), record.get("limits", {})
    sn = record.get("sn", "")
    mx, mn = ["MAX", sn, "", "", ""], ["MIN", sn, "", "", ""]
    for col in HEADER[5:-1]:
        if col in MPD_COLUMNS:
            mx.append("/"), mn.append("/")
            continue
        name, dec, fallback = COLUMNS[col]
        item = _item(results, name) if name else None
        lim = limits.get(item) if item else None
        lim = lim or PROD_LIMITS.get(col)
        if lim:
            mx.append(_fmt(lim[1], 3)), mn.append(_fmt(lim[0], 3))
        else:
            mx.append(fallback or ""), mn.append(fallback or "")
    return mx + [""], mn + [""]


def channel_rows(record: dict) -> list[list[str]]:
    results = record.get("results", {})
    lanes = record.get("mpd_scan", {}).get("lanes", {})
    end = record.get("end_time", "")
    test_time = end.split(" ")[-1] if end else ""
    try:
        m, s = record.get("elapsed", "0:0").split(":")
        machine = _fmt(int(m) * 60 + int(s), 1)
    except ValueError:
        machine = ""
    dd_item = _item(results, "DD_RxP@")
    rxp1 = dd_item[len("DD_RxP@"):].split("(")[0] if dd_item else ""
    extra = [f for f in record.get("failing_items", []) if f.startswith(("ERROR", "MES"))]

    rows, module_errors = [], ""
    for col, (name, dec, _fb) in COLUMNS.items():          # module-level failures go on every row
        item = _item(results, name) if name else None
        cell = (results.get(item, {}) if item else {}).get("module")
        if cell and not cell.get("ok", True):
            module_errors += f"{col}:{_fmt(cell.get('value'), dec)}"
    for ch in range(1, 9):
        row, errors = [], module_errors
        for col in HEADER[5:-1]:
            if col in MPD_COLUMNS:
                lane = lanes.get(str(ch)) or lanes.get(ch) or {}
                row.append(_fmt(lane.get("mpd_out", {}).get(MPD_COLUMNS[col]), 0))
                continue
            if col == "RxP1":
                row.append(rxp1)
                continue
            name, dec, _fb = COLUMNS[col]
            item = _item(results, name) if name else None
            cell = _cell(results, item, ch)
            value = None if cell is None else cell.get("value")
            if col == "Tops_Mid" and cell is None:       # records saved before Tops_Mid was an item
                value = (lanes.get(str(ch)) or lanes.get(ch) or {}).get("mid_v")
            row.append(_fmt(value, dec))
            if cell is not None and not cell.get("ok", True) and "module" not in results.get(item, {}):
                errors += f"{col}:{_fmt(value, dec) or 'NaN'}"
        if extra:
            errors += "".join(extra)
        result = "Fail" if errors else "Pass"
        rows.append([f"CH{ch}", record.get("sn", ""), result, test_time, machine] + row + [errors or "0"])
    return rows


def append_run(record: dict, out_dir: Path) -> Path:
    """Append this run to the day's file (header written when the file is new). If the
    day's file is open in Excel (locked), the run goes to a separate file instead."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    day = (record.get("end_time") or record.get("start_time") or datetime.now().strftime("%Y-%m-%d"))[:10]
    path = out_dir / f"{day}_DDMI2XFR4.csv"
    mx, mn = _limit_rows(record)
    lines = [mx, mn] + channel_rows(record)
    text = "".join(",".join(r) + "\n" for r in lines)
    try:
        new = not path.exists()
        with open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write((",".join(HEADER) + "\n" if new else "") + text)
        return path
    except PermissionError:
        alt = out_dir / f"{day}_DDMI2XFR4_{datetime.now().strftime('%H%M%S')}.csv"
        with open(alt, "w", encoding="utf-8", newline="") as fh:
            fh.write(",".join(HEADER) + "\n" + text)
        return alt
