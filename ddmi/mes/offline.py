"""Offline MES stand-in: everything is a JSON file under a folder, nothing leaves the PC."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .client import RESULT_TABLE


class OfflineMes:
    """check_in always succeeds; saved data goes to <root>/outbox.

    A limits override can be supplied as <root>/spec.json: {"dtxp_db": [-0.5, 0.5], ...}.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def check_in(self, sn: str) -> tuple[bool, str]:
        return True, "offline"

    def read_date(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def read_spec(self, pn: str, station: str) -> dict | None:
        try:
            spec = json.loads((self.root / "spec.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return spec if isinstance(spec, dict) else None

    def read_test_times(self, sn: str) -> int:
        return len(list((self.root / "outbox").glob(f"{sn}_*.json")))

    def save_data(self, record: dict) -> tuple[bool, str]:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.root / "outbox" / f"{record.get('sn') or 'NOSN'}_{stamp}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"table": RESULT_TABLE, "record": record}, indent=2),
                            encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        return True, str(path)

    def check_out(self, sn: str, passed: bool) -> tuple[bool, str]:
        return True, "offline"
