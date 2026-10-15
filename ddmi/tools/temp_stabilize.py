"""Wait for the module's DDMI temperature to settle, as the production DDMI TX Loss tool does.

Read every interval_s; delta = |T - previous T|. A reading with delta < threshold_c counts
toward `consecutive`; any larger delta resets the count ("Warming (counter reset)").
STABLE after `consecutive` readings in a row below the threshold. Production settings:
15 s, 0.1 degC, 3 in a row.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable


def wait_for_stable_temperature(read_temp: Callable[[], float], *, interval_s: float = 15.0,
                                threshold_c: float = 0.1, consecutive: int = 3, timeout_s: float = 1800.0,
                                log: Callable[[str], None] = print,
                                on_sample: Callable[[dict], None] = lambda _s: None,
                                abort: threading.Event | None = None,
                                sleep: Callable[[float], None] | None = None) -> bool:
    """True once stable; False if aborted or timed out."""
    abort = abort or threading.Event()
    sleep = sleep or (lambda s: abort.wait(s))
    log(f"--- Stabilization run started at {datetime.now():%H:%M:%S} (interval {interval_s:g}s, "
        f"threshold {threshold_c:g} °C, consecutive {consecutive}) ---")
    started, previous, count, n = time.monotonic(), None, 0, 0
    while True:
        if abort.is_set():
            log(f"--- Run stopped at {datetime.now():%H:%M:%S} ---")
            return False
        n += 1
        temp = read_temp()
        if previous is None:
            delta, status = None, "initial"
        else:
            delta = abs(temp - previous)
            if delta < threshold_c:
                count += 1
                status = f"STABLE ({count}/{consecutive})" if count >= consecutive else f"Stabilizing {count}/{consecutive}"
            else:
                status = "Warming (counter reset)" if count else "Warming"
                count = 0
        previous = temp
        delta_text = "--" if delta is None else f"{delta:.3f} °C"
        log(f"[{datetime.now():%H:%M:%S}] #{n:3d}  T= {temp:.3f} °C   Δ= {delta_text}   {status}")
        on_sample({"n": n, "temp_c": temp, "delta_c": delta, "status": status, "count": count})
        if count >= consecutive:
            log(f"--- Run ended at {datetime.now():%H:%M:%S}: STABLE ({consecutive} consecutive samples "
                f"Δ < {threshold_c:g} °C) - you can now measure the power. ---")
            return True
        if time.monotonic() - started > timeout_s:
            log(f"--- Run ended: not stable after {timeout_s / 60:g} min ---")
            return False
        sleep(interval_s)
