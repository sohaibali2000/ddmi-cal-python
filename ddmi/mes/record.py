"""The Save_Data payload as Table Value to MES Data.vi V6.8 builds it.

Per channel n: "<n>_DDMI,<Pass|Fail>,<error item>" followed by that channel's values, all
comma-separated; channels joined by "$". A value that is NaN, -Inf or 20 characters or longer
is sent as "0" (an empty value too: LabVIEW's table would hold NaN there).

The column order of LabVIEW's result table is not in the V6.8 copy; the production CSV's item
columns (Tx_Disable ... TXP_MPDOut_2.5dB) are used, which is the order the station displays.
"""
from __future__ import annotations

from ..prod_csv import channel_rows

STATION_LABEL = "DDMI"      # Table Value to MES Data.vi: "DDMI" for DDMI Cal and Golden_DDMI


def _clean(cell: str) -> str:
    text = (cell or "").strip()
    if text == "" or text.upper() in ("NAN", "-INF", "INF") or len(text) >= 20:
        return "0"
    return text


def mes_data_string(record: dict) -> str:
    parts = []
    for n, row in enumerate(channel_rows(record), start=1):
        result, values, error = row[2], row[5:-1], row[-1]
        prefix = f"{n}_{STATION_LABEL},{result},{error}"
        parts.append(",".join([prefix] + [_clean(v) for v in values]))
    return "$".join(parts)
