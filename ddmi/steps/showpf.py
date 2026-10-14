"""ShowPF module clean-up (build map 6.11). Pass/fail itself is done by Results."""
from __future__ import annotations

from ..runctx import RunContext


def cleanup(ctx: RunContext) -> None:
    """AWS DDMI Cal: disable the temp offset function, leave test mode, reset, check DSP mode.

    Every register and value here is UNVERIFIED (build map 5.5).
    """
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
