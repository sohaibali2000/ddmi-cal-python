"""ShowPF module clean-up (build map 6.11). Pass/fail itself is done by Results."""
from __future__ import annotations

from ..runctx import RunContext
from .tops_lock import relock


MODE_REGISTERS = ("AWS_Test_Mode", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "TempOffFunc")


def cleanup(ctx: RunContext) -> None:
    """Put the module back into its shipping state.

    showpf.mode_cleanup = "labview" (default): 800G_TRx_Test.vi V6.8 ShowPF for AWS DDMI CAL -
    Disable Temp Off Func (59) and TestMode 0xCC, each written and read back up to 2 times
    200 ms apart, then W_Reset, 10 s, and the DSP modes must read back 0.
    "restore": write back the mode registers exactly as they were read before the run.
    """
    if ctx.product.get("showpf", {}).get("mode_cleanup", "labview") == "restore" and ctx.meta.get("pre_run"):
        _restore_modes(ctx)
        return
    module, value = ctx.module, ctx.module.regmap.value
    verify = not module.dry_run      # nothing was written in a dry run, so read-backs mean nothing

    def write_verified(name: str, value_name: str, message: str) -> None:
        for _attempt in range(2):
            module.write_value(name, value_name)
            ctx.sleep(0.2)
            if not verify or module.read(name) == value(value_name):
                return
        ctx.results.fail(message)

    write_verified("TempOffFunc", "temp_off_func_disable", "Disable Temp Offset Fail!")
    ctx.sleep(2.0)
    write_verified("TestMode", "test_mode_exit", "Test Mode Set Fail!")

    module.write_value("W_Reset", "reset")
    ctx.sleep(ctx.product["tx_cal"]["after_reset_wait_s"])

    modes = module.read("DSP_OperatingMode"), module.read("DSP_TestMode")
    if verify and modes != (value("dsp_mission"), value("dsp_normal")):
        ctx.results.fail("DSP Mode Reset Fail!")
    ctx.log("ShowPF: temp offset function disabled, test mode exited, module reset")
    relock(ctx, "after the final reset", final=True)


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
    relock(ctx, "after the final reset", final=True)
