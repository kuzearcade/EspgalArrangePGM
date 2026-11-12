"""Worker processes for the build's independent slow steps (the port caches, the sprite search,
the de-translated listings).

The build uses 3/4 of the cores this process may run on (at least one); ESPGAL_JOBS=n sets the
number instead (1 runs everything in this process). Results come back in the order of the
inputs whatever order the workers finish in, so the output does not depend on the count.
"""
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Iterable


def cores() -> int:
    """The cores this process may run on."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:                      # (no affinity call on macOS / Windows)
        return os.cpu_count() or 1


def workers() -> int:
    env = os.environ.get("ESPGAL_JOBS", "").strip()
    if env:
        return max(1, int(env))
    return max(1, cores() * 3 // 4)


def _context():
    # fork shares the parent's loaded modules and ROM images; where it is missing (Windows,
    # and the default on macOS) the workers start fresh and import what they need
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def map_ordered(fn: Callable, items: Iterable, n: int | None = None) -> list:
    """[fn(x) for x in items] on up to n worker processes (fn and the items must pickle: a
    module-level function, plain data). Runs in this process when one worker is enough."""
    items = list(items)
    n = min(n or workers(), len(items))
    if n <= 1:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=n, mp_context=_context()) as ex:
        return list(ex.map(fn, items))
