"""Make sure every lane's TOPS loop is locked at its operating point.

Seen on hardware 2026-10-15 (P185262004341 CH1): after the loop was switched on, one lane
sat at the top of its fringe (+5 dBm, MPD_OUT 27, TXMON/MPD_OUT*1000 = 29000 against a
setpoint of 587) although the Vtops register still read Tops_Mid. Loop off, write Vtops
again, loop on brought it straight back (ratio 622) and it stayed locked.
"""
from __future__ import annotations

from ..runctx import RunContext

NO_ANSWER = 0xFFFF


def _read(ctx: RunContext, name: str) -> list:
    """A monitor read; 0xFFFF on every lane is an I2C hiccup, so read again once."""
    values = ctx.module.read(name)
    if all(v == NO_ANSWER for v in values):
        ctx.sleep(0.2)
        values = ctx.module.read(name)
    return values


def unlocked_lanes(ctx: RunContext) -> dict[int, str]:
    cfg = ctx.product["tops_lock"]
    setpoints = ctx.module.read("tops_setpoint")
    txmon, mpd_out = _read(ctx, "R_TXMON"), _read(ctx, "R_MPD_OUT")
    bad = {}
    for ch in ctx.channels:
        target, tx, mp = setpoints[ch - 1], txmon[ch - 1], mpd_out[ch - 1]
        if target <= 0:
            continue
        ratio = tx / mp * 1000 if mp else float("inf")
        if mp < cfg["min_mpd_out"] or abs(ratio / target - 1) > cfg["ratio_tolerance"]:
            bad[ch] = f"TXMON {tx}, MPD_OUT {mp}, ratio {ratio:.0f} vs setpoint {target}"
    return bad


def relock(ctx: RunContext, where: str, final: bool = False) -> None:
    """Check every lane's TOPS loop is locked (V6.8 itself does not check this). Only if one
    is not: loop off, Vtops back to each lane's Tops_Mid, loop on, check again (up to
    `attempts` times). With final=True a lane still unlocked fails the run."""
    module, cfg = ctx.module, ctx.product["tops_lock"]
    if module.dry_run:
        return
    ctx.sleep(cfg["settle_s"])        # e.g. tops_setpoint was just written
    bad = unlocked_lanes(ctx)
    if not bad:
        ctx.log(f"TOPS lock {where}: all lanes locked")
        return
    ctx.log(f"TOPS lock {where}: not locked - " + "; ".join(f"CH{ch} {text}" for ch, text in bad.items()))
    lanes = ctx.meta.get("mpd_scan", {}).get("lanes", {})
    toppid = ctx.pre_run("W_TOPPID", "toppid_on")
    for attempt in range(1, cfg["attempts"] + 1):
        current = module.read("Vtops")
        mids = [(lanes.get(ch) or {}).get("mid_v") or current[ch - 1] for ch in ctx.channels]
        module.write_value("W_TOPPID", "toppid_off")
        module.write("Vtops", mids)
        ctx.sleep(cfg["settle_s"])
        module.write("W_TOPPID", toppid)
        ctx.sleep(cfg["settle_s"])
        bad = unlocked_lanes(ctx)
        if not bad:
            ctx.log(f"TOPS lock {where}: relocked (attempt {attempt})")
            return
        ctx.log(f"TOPS lock {where}, relock attempt {attempt}: not locked - "
                + "; ".join(f"CH{ch} {text}" for ch, text in bad.items()))
    if final:
        for ch, text in bad.items():
            ctx.results.fail(f"TOPS loop not locked CH{ch}: {text}")
