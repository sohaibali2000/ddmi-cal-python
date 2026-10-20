"""ShowPF module clean-up (build map 6.11). Pass/fail itself is done by Results."""
from __future__ import annotations

from ..runctx import RunContext
from .tops_lock import relock


MODE_REGISTERS = ("AWS_Test_Mode", "TestMode", "DSP_TestMode", "DSP_OperatingMode", "TempOffFunc")


def cleanup(ctx: RunContext) -> None:
    """Put the module back into its shipping state.

    showpf.mode_cleanup = "labview" (default): 800G_TRx_Test.vi V6.8 ShowPF, AWS, DDMI CAL.
      Loop 1, up to 3 tries: Disable Temp Off Func (59), 2 s, read back; stop when it reads 59.
      Loop 2, up to 3 tries: W_TestMode 0xCC, W_Reset, 10 s, read TestMode, DSP operating mode
      and DSP test mode; stop when they read 0xCC, 0, 0.
      (Each loop also has a 200 ms wait running in parallel, shorter than its chain.)
      Then "Disable Temp Offset Fail!" / "Test Mode Set Fail!" / "DSP Mode Reset Fail!".
    "restore": write back the mode registers exactly as they were read before the run.
    """
    if ctx.product.get("showpf", {}).get("mode_cleanup", "labview") == "restore" and ctx.meta.get("pre_run"):
        _restore_modes(ctx)
        return
    module, value = ctx.module, ctx.module.regmap.value
    verify = not module.dry_run      # nothing was written in a dry run, so read-backs mean nothing
    reset_wait = ctx.product["tx_cal"]["after_reset_wait_s"]

    temp_off_ok = False
    for attempt in range(1, SHOWPF_TRIES + 1):
        module.write_value("TempOffFunc", "temp_off_func_disable")
        ctx.sleep(2.0)
        temp_off_ok = not verify or module.read("TempOffFunc") == value("temp_off_func_disable")
        if temp_off_ok:
            break
        ctx.log(f"ShowPF: Temp Off Func read-back wrong (try {attempt})")

    test_mode_ok = dsp_ok = False
    for attempt in range(1, SHOWPF_TRIES + 1):
        module.write_value("TestMode", "test_mode_exit")
        module.write_value("W_Reset", "reset")
        ctx.sleep(reset_wait)
        if not verify:
            test_mode_ok = dsp_ok = True
            break
        test_mode_ok = module.read("TestMode") == value("test_mode_exit")
        dsp_ok = (module.read("DSP_OperatingMode"), module.read("DSP_TestMode")) == \
            (value("dsp_mission"), value("dsp_normal"))
        if test_mode_ok and dsp_ok:
            break
        ctx.log(f"ShowPF: TestMode ok {test_mode_ok}, DSP modes ok {dsp_ok} (try {attempt})")

    for ok, message in ((temp_off_ok, "Disable Temp Offset Fail!"), (test_mode_ok, "Test Mode Set Fail!"),
                        (dsp_ok, "DSP Mode Reset Fail!")):
        if not ok:
            ctx.results.fail(message)
    ctx.log("ShowPF: temp offset function disabled, test mode exited, module reset")
    relock(ctx, "after the final reset", final=True)


# 800G_TRx_Test.vi V6.8 ShowPF: both loops stop at loop counter >= 2, i.e. after 3 tries
SHOWPF_TRIES = 3


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
