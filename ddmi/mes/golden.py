"""Golden sample power from the MES database, as the production DDMI TX Loss tool reads it.

The latest TestNumber for the ComponentID in TestResult_800G_2XFR4_TRX_TEST, then
[Power(dBm)] per [chnumber]; chnumber starts with the lane number ("3_...").
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import yaml

from ..config import CONFIG_DIR, ConfigError

DB_CONFIG_FILE = CONFIG_DIR / "mes_db.yaml"   # git-ignored: holds the login
GOLDEN_TABLE = "TestResult_800G_2XFR4_TRX_TEST"


def load_db_config(path: Path = DB_CONFIG_FILE) -> dict:
    """Server, database, login. DDMI_MES_DB_UID / DDMI_MES_DB_PWD override the file."""
    try:
        cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        raise ConfigError(f"no MES database settings: copy config/mes_db.example.yaml to {path.name} "
                          "and fill it in") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    cfg["uid"] = os.environ.get("DDMI_MES_DB_UID", cfg.get("uid"))
    cfg["pwd"] = os.environ.get("DDMI_MES_DB_PWD", cfg.get("pwd"))
    missing = [k for k in ("driver", "server", "database", "uid", "pwd") if not cfg.get(k)]
    if missing:
        raise ConfigError(f"{path.name} is missing {', '.join(missing)}")
    cfg.setdefault("golden_table", GOLDEN_TABLE)
    return cfg


def _connect(cfg: dict):
    import pyodbc   # only needed on a real station

    return pyodbc.connect(f"DRIVER={cfg['driver']};SERVER={cfg['server']};DATABASE={cfg['database']};"
                          f"UID={cfg['uid']};PWD={cfg['pwd']};")


def fetch_golden_powers(component_id: str, cfg: dict, channels: int = 8,
                        connect: Callable = _connect) -> dict[int, float]:
    """{channel: power dBm} for every channel; raises if the SN or any channel is missing."""
    table = cfg.get("golden_table", GOLDEN_TABLE)
    conn = connect(cfg)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT TOP 1 TestNumber FROM {table} WHERE ComponentId = ? "
                       f"ORDER BY TestNumber DESC", component_id)
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"no records in {table} for ComponentID '{component_id}'")
        test_number = row[0]
        cursor.execute(f"SELECT [chnumber], [Power(dBm)] FROM {table} "
                       f"WHERE ComponentId = ? AND TestNumber = ?", (component_id, test_number))
        golden = {}
        for ch_name, power in cursor.fetchall():
            prefix = str(ch_name).split("_")[0]
            if "_" in str(ch_name) and prefix.isdigit() and 1 <= int(prefix) <= channels:
                golden[int(prefix)] = float(power)
    finally:
        conn.close()
    missing = [ch for ch in range(1, channels + 1) if ch not in golden]
    if missing:
        raise ValueError(f"missing golden power for channel(s) {missing} (TestNumber {test_number})")
    return golden
