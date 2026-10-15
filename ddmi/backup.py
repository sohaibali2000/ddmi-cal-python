"""Module register backup before a run writes anything, and restore from that backup.

The backup holds the raw bytes of every register the DDMI Cal flow writes, read from
the module before the first write. `python -m ddmi restore --file <backup>` puts them
all back exactly as they were.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .hw.module import Module

# Every register the flow writes (Vtops is left out: the TOPS loop drives it).
BACKUP_REGISTERS = [
    "DTXPCal_A", "DTXPCal_B", "DRXPCal_A", "DRXPCal_B", "VccCal",
    "tops_setpoint", "tops_off", "tops_room",
    "IBias", "W_IBiasPID", "W_TOPPID",
    "AWS_Test_Mode", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "TempOffFunc",
]


def snapshot(module: Module) -> dict[str, str]:
    """{register: raw bytes as hex}, read from the module."""
    return {name: module.read_raw(name).hex() for name in BACKUP_REGISTERS}


def decoded(module: Module, snap: dict[str, str]) -> dict:
    """{register: value} for a snapshot, decoded with the module's register map."""
    return {name: module.regmap[name].decode(bytes.fromhex(raw)) for name, raw in snap.items()}


def save_backup(snap: dict[str, str], sn: str, folder: Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sn or 'NOSN'}_{datetime.now():%Y%m%d_%H%M%S}_backup.json"
    path.write_text(json.dumps({"sn": sn, "time": datetime.now().isoformat(timespec="seconds"),
                                "registers": snap}, indent=2), encoding="utf-8")
    return path


def restore(module: Module, snap: dict[str, str], names: list[str] | None = None) -> list[str]:
    """Write the backed-up bytes back. Returns the registers written."""
    written = []
    for name in names or list(snap):
        module.write(name, module.regmap[name].decode(bytes.fromhex(snap[name])))
        written.append(name)
    return written
