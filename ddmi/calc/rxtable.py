"""RX attenuation table lookup (build map 7.3).

A table is a list of (attenuation dB, power) rows where power is the level at
the module input as -dBm, stored positive, as in CHn_Rx_calibration.txt.
"""
from __future__ import annotations

from pathlib import Path


class RxTable:
    def __init__(self, rows):
        self.rows = sorted((float(att), float(power)) for att, power in rows)
        if len(self.rows) < 2:
            raise ValueError("an RX table needs at least two rows")

    @classmethod
    def from_file(cls, path) -> "RxTable":
        rows = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            try:
                rows.append((float(parts[0]), float(parts[1])))   # third column (Diff) is unused
            except (IndexError, ValueError):
                continue
        return cls(rows)

    def att_for_power(self, target_dbm: float) -> float:
        """Attenuation that puts target_dbm at the module input, by linear interpolation."""
        target = -target_dbm
        for (att_lo, p_lo), (att_hi, p_hi) in zip(self.rows, self.rows[1:]):
            lo, hi = sorted((p_lo, p_hi))
            if lo <= target <= hi and p_hi != p_lo:
                return att_lo + (target - p_lo) / (p_hi - p_lo) * (att_hi - att_lo)
        powers = [p for _, p in self.rows]
        raise ValueError(f"{target_dbm} dBm is outside the table "
                         f"({-max(powers):.2f} to {-min(powers):.2f} dBm)")
