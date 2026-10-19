"""One complete run for one slot: build the station, run the flow inside the MES wrapper, save the record.

The CLI and the UI both call run_slot.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import flow, stationfiles
from .mes.client import MesClient
from .mes.offline import OfflineMes
from .mes.wrapper import run_with_mes
from .results import Results, item_name, limits_from, save_record
from .runctx import Aborted, RunContext, StopTest
from .station import Station, build_station, is_sim

ROOT = Path(__file__).resolve().parents[1]


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def run_slot(*, station_cfg: dict, product: dict, slot: int = 1, scanned_sn: str = "",
             user: str = "UNKNOWN", plan: str = "DDMI Cal", fault: str = "none",
             dry_run: bool | None = None, allow_unverified: bool | None = None,
             log: Callable[[str], None] = print,
             on_result: Callable[[str, int, object, bool], None] | None = None,
             on_step: Callable[[str, str], None] = lambda _n, _s: None,
             on_data: Callable[[str, dict], None] = lambda _k, _d: None,
             on_sn: Callable[[str], None] = lambda _sn: None,
             abort: threading.Event | None = None,
             mes: MesClient | None = None, station: Station | None = None) -> dict:
    """Returns the saved record; record["result"] is PASS, FAIL, ABORTED, ERROR or NOT_TESTED."""
    abort = abort or threading.Event()
    results_dir = _path(station_cfg["results_dir"])
    lines: list[str] = []

    def stamp(text: str) -> str:
        return f"[{datetime.now().strftime('%H:%M:%S')}] {text}"

    def say(text: str) -> None:
        lines.append(stamp(text))
        log(lines[-1])

    def detail(text: str) -> None:
        lines.append(stamp("    " + text))

    sim = is_sim(station_cfg)
    sim_sn = ""
    if sim:
        # The simulated module carries the scanned SN, unless the fault is a mismatch.
        sim_sn = scanned_sn or f"SIM{int(time.time() * 1000) % 1000000:06d}"
        if fault == "sn_mismatch":
            sim_sn = "SIM999999"
    own_station = station is None
    if station is None:
        station = build_station(station_cfg, slot=slot, sim_sn=sim_sn, fault=fault, dry_run=dry_run,
                                allow_unverified=allow_unverified, log=detail)
    else:
        station.module.log = detail

    try:
        started = time.time()
        record = {
            "sn": scanned_sn, "slot": slot, "plan": plan, "user": user,
            "station": station_cfg["station_name"], "simulated": sim,
            "dry_run": station.module.dry_run,
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "result": "NOT_TESTED", "failing_items": [], "results": {}, "limits": {},
        }
        say(f"Start: {plan} | station {record['station']} slot {slot} | user {user}"
            + (" | SIMULATOR" if sim else "") + (" | DRY RUN" if record["dry_run"] else ""))

        tx_loss_file = stationfiles.tx_loss_path(slot, _path(station_cfg["tx_loss_file"]))
        tx_loss = stationfiles.read_tx_loss(tx_loss_file)
        if tx_loss is None and not record["dry_run"]:
            # Without it every true TX power would be wrong by the path loss
            record["failing_items"] = [f"Tx Loss missing in {tx_loss_file.name} - run the Tx Loss calibration"]
            say(f"Not started: {record['failing_items'][0]}")
            return record
        if tx_loss is None:
            say(f"WARNING: no Tx Loss in {tx_loss_file.name}; dry run continues with 0 dB")
            tx_loss = [0.0] * product["channels"]
        elif max(tx_loss) > product["limits"]["tx_loss_warn_db"]:
            say(f"WARNING: Tx Loss above {product['limits']['tx_loss_warn_db']} dB: {tx_loss}")
        rx_tables = stationfiles.load_rx_tables(
            stationfiles.rx_table_dir(slot, _path(station_cfg["rx_table_dir"])))

        # Pre-flight: refuse to start (and to check in) when the station is not calibrated,
        # rather than failing at RX Cal after the module has already been written to.
        missing = [ch for ch in range(1, product["channels"] + 1) if ch not in rx_tables]
        if missing and record["dry_run"]:
            say(f"WARNING: no RX table for CH{missing}; dry run continues and will stop at RX Cal")
        elif missing:
            record["failing_items"] = [f"RX table missing for CH{missing} - run the RX Attenuation Table calibration"]
            say(f"Not started: {record['failing_items'][0]}")
            return record

        def run_test(spec: dict | None) -> dict:
            limits = limits_from(product["limits"], spec)
            results = Results(limits, on_result)
            ctx = RunContext(
                product=product, module=station.module, bench=station.bench, results=results,
                tx_loss=tx_loss, rx_tables=rx_tables, scanned_sn=scanned_sn,
            time_scale=station_cfg.get("time_scale", 1.0), log=say, detail=detail,
                on_step=on_step, on_data=on_data, abort=abort,
            )
            ctx.meta["backup_dir"] = str(results_dir / "backups")
            reason = ""
            try:
                if not ctx.meta.get("module_sn"):
                    info = station.module.read("R_Information")
                    ctx.meta["module_sn"] = info[18:34].strip()
                    on_sn(scanned_sn or ctx.meta["module_sn"])
                flow.run(ctx)
            except Aborted:
                reason = "ABORTED"
            except StopTest as exc:
                results.fail(str(exc))
            except Exception as exc:
                reason = f"ERROR: {type(exc).__name__}: {exc}"
                say(reason)
                ctx.detail(traceback.format_exc().rstrip())

            if ctx.meta.get("module_sn"):
                record["sn"] = record["sn"] or ctx.meta["module_sn"]
                on_sn(record["sn"])
            fails = results.failing_items()
            for item, chans in results.cells.items():
                for ch, cell in sorted(chans.items()):
                    if not cell["ok"]:
                        limit = f" (limit {limits[item][0]} to {limits[item][1]})" if item in limits else ""
                        say(f"FAIL {item_name(item, ch)} = {cell['value']}{limit}")
            for message in results.failures:
                say(f"FAIL {message}")
            if reason == "ABORTED":
                result = "ABORTED"
            elif reason:
                result, fails = "ERROR", [reason] + fails
            else:
                result = "PASS" if not fails else "FAIL"
            ctx.meta.pop("stage", None)
            record.update(ctx.meta, result=result, failing_items=fails, results=results.as_dict(),
                          limits={k: list(v) for k, v in limits.items()})
            return record

        mes = mes or OfflineMes(results_dir / "mes_offline")
        pn = ""   # the PN is only known after Read SN; the offline spec does not depend on it
        if record["dry_run"]:
            # A dry run never checks in, uploads or checks out: it only records locally.
            say("Dry run: MES is skipped (no check-in, upload or check-out)")
            run_test(None)
            outcome = {"checked_in": False, "saved": False, "checked_out": False, "dry_run": True}
        else:
            outcome = run_with_mes(mes, scanned_sn, pn, record["station"], run_test, say)
        if not outcome["checked_in"] and not outcome.get("dry_run"):
            # Results are only saved for modules that checked in OK.
            record["failing_items"] = [f"MES check-in: {outcome.get('message', '')}"]
            return record
        record["mes"] = {k: outcome[k] for k in ("checked_in", "saved", "checked_out")}

        secs = int(time.time() - started)
        record.update(end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      elapsed=f"{secs // 60:02d}:{secs % 60:02d}")
        say(f"Result: {record['result']}"
            + (f" - failing: {', '.join(record['failing_items'])}" if record["failing_items"] else ""))
        try:
            path = save_record(record, lines, results_dir)
            log(stamp(f"Record saved: {path.name}"))
        except OSError as exc:
            log(stamp(f"ERROR: could not save record: {exc}"))
        if (record["result"] in ("PASS", "FAIL") and station_cfg.get("production_csv_dir")
                and not record.get("dry_run") and not record.get("simulated")):
            try:
                from .prod_csv import append_run
                csv_path = append_run(record, _path(station_cfg["production_csv_dir"]))
                log(stamp(f"Production CSV: {csv_path}"))
            except Exception as exc:        # never lose a result over the CSV copy
                log(stamp(f"ERROR: could not write the production CSV: {exc}"))
        return record
    finally:
        if own_station:
            station.close()     # release serial ports and the attenuator host
