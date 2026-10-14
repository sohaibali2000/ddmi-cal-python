"""MES actions of Cimes_All.vi used at DDMI Cal (build map Appendix C)."""
from __future__ import annotations

from typing import Protocol

RESULT_TABLE = "TestResult_800G_TRX_TEST"


class MesClient(Protocol):
    def check_in(self, sn: str) -> tuple[bool, str]: ...            # Pcs_Check_IN
    def read_date(self) -> str: ...                                 # Read_Date
    def read_spec(self, pn: str, station: str) -> dict | None: ...  # Read_Spec: limit key -> [low, high]
    def read_test_times(self, sn: str) -> int: ...                  # Read_Test_Times
    def save_data(self, record: dict) -> tuple[bool, str]: ...      # Save_Data
    def check_out(self, sn: str, passed: bool) -> tuple[bool, str]: ...   # Pcs_Check_OUT
