"""Test sequence of 800G_TRx_Test.vi for V6.8 DDMI Cal (build map Section 6).

Start button: Reini -> Set_Temp -> Read SN -> VCC -> StartTest
StartTest:    Scan_MPD -> DDMI_CAL -> ShowPF
Move_in / Read_Spec / Move_out belong to the MES wrapper (ddmi/mes/wrapper.py).
"""
from __future__ import annotations

from .runctx import RunContext
from .steps import mpd_scan, rx_cal, showpf, startup, temp, tx_cal, vcc_cal

# Stages shown as lights in the UI, in run order.
STAGES = ["Startup", "Scan MPD", "TX Cal", "RX Cal", "VCC Cal", "Final"]

# DDMI_CAL parts: name -> (stage, functions)
DDMI_CAL = {
    "tx": ("TX Cal", (tx_cal.tx_cal, tx_cal.tx_check)),
    "rx": ("RX Cal", (rx_cal.rx_cal, rx_cal.rx_check)),
    "vcc": ("VCC Cal", (vcc_cal.vcc_cal, vcc_cal.vcc_check)),
    "temp": ("VCC Cal", (temp.read_temp,)),
}


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
    """After an abort or error: lanes enabled, loops back on, supply at nominal. Best effort."""
    for action in (
        lambda: ctx.module.write("W_TXDis", 0x00),
        lambda: ctx.module.write_value("W_Reset", "reset"),
        lambda: ctx.bench.power_supply.set_voltage(ctx.product["vcc"]["nominal_v"]),
    ):
        try:
            action()
        except Exception as exc:   # one failing action must not stop the others
            ctx.log(f"safe state: {exc}")
    ctx.log("Module returned to a safe state (TX enabled, reset, supply at nominal)")


def run(ctx: RunContext) -> None:
    """Run the whole sequence. Raises Aborted or StopTest; results accumulate in ctx.results."""
    try:
        _stage(ctx, "Startup", (startup.reini, startup.set_temp, startup.read_sn, startup.set_vcc))
        _stage(ctx, "Scan MPD", (mpd_scan.scan_mpd, mpd_scan.scan_disable))
        for part in ctx.product["flow"]["ddmi_cal_order"]:
            stage, functions = DDMI_CAL[part]
            _stage(ctx, stage, functions)
        _stage(ctx, "Final", (showpf.cleanup,))
    except BaseException:
        safe_state(ctx)
        raise
