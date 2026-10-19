"""Instruments shared by several slots in one process (power meter, attenuator).

A serial port can only be opened once, and two slots must not interleave commands on one
instrument. acquire() opens an instrument the first time, hands every slot the same object
behind a lock (each method call runs alone), and closes it when the last slot releases it.
"""
from __future__ import annotations

import threading
from typing import Callable

_pool_lock = threading.Lock()
_pool: dict[str, list] = {}          # key -> [instrument, users, call lock]


class Locked:
    """Calls the wrapped instrument's methods one at a time."""

    def __init__(self, obj, lock):
        self._obj, self._lock = obj, lock

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return call


def acquire(key: str, factory: Callable[[], object]) -> tuple[Locked, Callable[[], None]]:
    """(shared instrument, release function). The factory opens it if no slot has it yet."""
    with _pool_lock:
        entry = _pool.get(key)
        if entry is None:
            entry = _pool[key] = [factory(), 0, threading.RLock()]
        entry[1] += 1
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        with _pool_lock:
            entry[1] -= 1
            if entry[1] == 0:
                _pool.pop(key, None)
                entry[0].close()

    return Locked(entry[0], entry[2]), release


def users(key: str) -> int:
    with _pool_lock:
        return _pool[key][1] if key in _pool else 0
