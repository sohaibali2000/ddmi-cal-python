"""Test sequence of 800G_TRx_Test.vi V6.8, station DDMI CAL, AWS 2xFR4.

Read from the V6.8 VIs:
  states:    Reini -> Set_Temp -> Read SN -> Read_Spec -> Wait_Temp -> VCC -> StartTest
  StartTest: Scan_MPD -> DDMI_CAL -> ShowPF -> Move_out -> Stop
  Scan_MPD:  (Scan Vtops -> Adjust Power -> Scan_Disable), up to 2 passes
  DDMI_CAL:  DDMI_Adjust_TX -> DDMI_RXP -> DDMI_VCC -> DDMI_Temp, each re-run once when it
             fails its slope / error check
Read_Spec / Move_in / Move_out belong to the MES wrapper (runner.py).
"""
from __future__ import annotations

from . import backup
from .runctx import RunContext
from .steps import mpd_scan, rx_cal, showpf, startup, temp, tx_cal, vcc_cal

# Stages shown as lights in the UI, in run order.
STAGES = ["Startup", "Scan MPD", "TX Cal", "RX Cal", "VCC Cal", "Final"]

# DDMI_CAL parts: name -> (stage, functions, items whose failure re-runs the part once, or a
# function returning the reasons for a re-run)
DDMI_CAL = {
    "tx": ("TX Cal", (tx_cal.tx_cal, tx_cal.tx_check), ("Txp_Slope", "dTxP(dB)")),
    "rx": ("RX Cal", (rx_cal.rx_cal, rx_cal.rx_check), ("Rxp_Slope", "dRxP(dB)")),
    "vcc": ("VCC Cal", (vcc_cal.vcc_cal, vcc_cal.vcc_check), vcc_cal.needs_rerun),
    "temp": ("VCC Cal", (temp.read_temp,), ()),
}


def _failed(ctx: RunContext, items) -> list[str]:
    return [f for f in ctx.results.failing_items() if any(f == i or f.startswith(i + "_CH") for i in items)]


def _stage(ctx: RunContext, name: str, functions) -> None:
    ctx.check_abort()
    ctx.on_step(name, "running")
    if ctx.meta.get("stage") != name:
        ctx.log(f"=== {name} ===")
        ctx.meta["stage"] = name
    for fn in functions:
        fn(ctx)
    ctx.on_step(name, "done")


def safe_state(ctx: RunContext) -> None:
    """After an abort or error: lanes enabled, loops back on, supply at nominal. Best effort.
    Skipped when nothing was written to the module yet (nothing to undo)."""
    if ctx.module.writes_sent == 0:
        ctx.log("Nothing was written to the module - no clean-up needed")
        return
    failed = []
    for name, action in (
        ("TX enable", lambda: ctx.module.write("W_TXDis", 0x00)),
        ("reset", lambda: ctx.module.write_value("W_Reset", "reset")),
        ("supply to nominal", lambda: ctx.bench.power_supply.set_voltage(ctx.product["vcc"]["nominal_v"])),
    ):
        try:
            action()
        except Exception as exc:   # one failing action must not stop the others
            failed.append(name)
            ctx.log(f"safe state: {name} failed: {exc}")
    if failed:
        ctx.log(f"SAFE STATE INCOMPLETE - {', '.join(failed)} failed; check the module before the next run")
    else:
        ctx.log("Module returned to a safe state (TX enabled, reset - loops back on, supply at nominal)")


def take_backup(ctx: RunContext) -> None:
    """Read every register the flow writes, before the first write, and save it."""
    info = ctx.module.read("R_Information")          # SN first, so the backup carries it
    ctx.meta.setdefault("module_sn", info[18:34].strip())
    snap = backup.snapshot(ctx.module)
    ctx.meta["pre_run"] = backup.decoded(ctx.module, snap)
    folder = ctx.meta.get("backup_dir")
    if folder:
        path = backup.save_backup(snap, ctx.scanned_sn or ctx.meta.get("module_sn", ""), folder)
        ctx.meta["backup_file"] = str(path)
        ctx.log(f"Module registers backed up to {path.name} (restore with: python -m ddmi restore --file {path})")
    pre = ctx.meta["pre_run"]
    ctx.detail(f"pre-run values: TOPPID {pre['W_TOPPID']}, IBiasPID {pre['W_IBiasPID']}, IBias {pre['IBias']}, "
               f"modes {[pre[n] for n in ('AWS_Test_Mode', 'TestMode', 'DSP_TestMode', 'DSP_OperatingMode', 'TempOffFunc')]}")


def run(ctx: RunContext) -> None:
    """Run the whole sequence. Raises Aborted or StopTest; results accumulate in ctx.results."""
    try:
        take_backup(ctx)
        # 800G_TRx_Test.vi V6.8 Start event: Reini, Set_Temp, Read SN, (Move_in), (Read_Spec:
        # done by the MES wrapper / runner before this), Wait_Temp, VCC
        _stage(ctx, "Startup", (startup.reini, startup.set_temp, startup.read_sn, startup.wait_temp,
                                startup.set_vcc))
        passes = ctx.product["flow"].get("scan_mpd_passes", 2)
        for attempt in range(1, passes + 1):
            # 800G_TRx_Test.vi V6.8: Adjust Power sits in a case selected by the loop counter,
            # so it runs on the first pass only
            steps = (mpd_scan.scan_mpd, mpd_scan.adjust_power, mpd_scan.scan_disable) if attempt == 1 \
                else (mpd_scan.scan_mpd, mpd_scan.scan_disable)
            _stage(ctx, "Scan MPD", steps)
            bright = _failed(ctx, ("Tx_Disable(dBm)",))
            if not bright or attempt == passes:
                break
            ctx.log(f"Scan_MPD pass {attempt}: {', '.join(bright)} failed - scanning again")
        for part in ctx.product["flow"]["ddmi_cal_order"]:
            stage, functions, retry_items = DDMI_CAL[part]
            _stage(ctx, stage, functions)
            failed = retry_items(ctx) if callable(retry_items) else _failed(ctx, retry_items)
            if failed:
                ctx.log(f"{stage}: {', '.join(failed)} failed - running it once more")
                _stage(ctx, stage, functions)
        _stage(ctx, "Final", (showpf.cleanup,))
    except BaseException:
        safe_state(ctx)
        raise
