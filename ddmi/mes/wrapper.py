"""MES wrapper around the test (Cimes_Interface.vi; build map 6.1 and 6.12).

Before: Pcs_Check_IN -> Read_Date -> Read_Spec -> Read_Test_Times.
After:  Save_Data, then Pcs_Check_OUT only if Save_Data succeeded.
An aborted or crashed run is not uploaded (this port's choice; LabVIEW's is not confirmed).
"""
from __future__ import annotations

from typing import Callable

from .client import MesClient


def run_with_mes(mes: MesClient, sn: str, pn: str, station: str,
                 run_test: Callable[[dict | None], dict],
                 log: Callable[[str], None] = print) -> dict:
    """run_test(spec) runs the test and returns its record. Returns what happened with MES."""
    outcome = {"checked_in": False, "tested": False, "saved": False, "checked_out": False,
               "record": None}

    ok, message = mes.check_in(sn)
    if not ok:
        log(f"MES check-in failed, not testing: {message}")
        outcome["message"] = message
        return outcome
    outcome["checked_in"] = True
    log(f"MES check-in OK ({message}); date {mes.read_date()}")

    spec = mes.read_spec(pn, station)
    log(f"MES spec: {spec}" if spec else "MES spec: none, using the product config limits")
    log(f"MES test times so far: {mes.read_test_times(sn)}")

    record = run_test(spec)
    outcome.update(tested=True, record=record)
    if record.get("result") not in ("PASS", "FAIL"):
        log(f"Run ended {record.get('result')}: nothing sent to MES, module stays checked in")
        return outcome

    ok, message = mes.save_data(record)
    if not ok:
        log(f"MES Save_Data failed, check-out blocked: {message}")
        outcome["message"] = message
        return outcome
    outcome["saved"] = True

    ok, message = mes.check_out(record.get("sn") or sn, record.get("result") == "PASS")
    outcome["checked_out"] = ok
    log("MES data saved and checked out" if ok else f"MES check-out failed: {message}")
    return outcome
