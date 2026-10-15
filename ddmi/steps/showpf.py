"""ShowPF module clean-up (build map 6.11). Pass/fail itself is done by Results."""
from __future__ import annotations

from ..runctx import RunContext


MODE_REGISTERS = ("AWS_Test_Mode", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "TempOffFunc")


def cleanup(ctx: RunContext) -> None:
    """Put the module back into its shipping state.

    showpf.mode_cleanup = "restore" (default): write back the mode registers exactly as they
    were read before the run, reset, and check they read back the same. "labview": the V6.8
    sequence (disable temp offset, test mode 0xCC, reset, DSP mode 0/0), whose values are
    UNVERIFIED (build map 5.5).
    """
    if ctx.product.get("showpf", {}).get("mode_cleanup", "restore") == "restore" and ctx.meta.get("pre_run"):
        _restore_modes(ctx)
        return
    module, value = ctx.module, ctx.module.regmap.value
    verify = not module.dry_run      # nothing was written in a dry run, so read-backs mean nothing

    module.write_value("TempOffFunc", "temp_off_func_disable")
    if verify and module.read("TempOffFunc") != value("temp_off_func_disable"):
        ctx.results.fail("Disable Temp Offset Fail!")

    module.write_value("TestMode", "test_mode_exit")
    if verify and module.read("TestMode") != value("test_mode_exit"):
        ctx.results.fail("Test Mode Set Fail!")

    module.write_value("W_Reset", "reset")
    ctx.sleep(ctx.product["tx_cal"]["after_reset_wait_s"])

    modes = module.read("DSP_OperatingMode"), module.read("DSP_TestMode")
    if verify and modes != (value("dsp_mission"), value("dsp_normal")):
        ctx.results.fail("DSP Mode Reset Fail!")
    ctx.log("ShowPF: temp offset function disabled, test mode exited, module reset")


def _restore_modes(ctx: RunContext) -> None:
    module, pre = ctx.module, ctx.meta["pre_run"]
    for name in MODE_REGISTERS:
        module.write(name, pre[name])
    module.write_value("W_Reset", "reset")
    ctx.sleep(ctx.product["tx_cal"]["after_reset_wait_s"])
    if not module.dry_run:
        wrong = [f"{name} {module.read(name)} (expected {pre[name]})" for name in MODE_REGISTERS
                 if module.read(name) != pre[name]]
        if wrong:
            ctx.results.fail("Mode restore failed: " + ", ".join(wrong))
    ctx.log("ShowPF: mode registers restored to their pre-run values, module reset")
