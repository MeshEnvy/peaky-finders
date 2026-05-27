"""Verbose logging helpers for site suggestion."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


def suggest_log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, flush=True)


@contextmanager
def suggest_step(verbose: bool, label: str) -> Iterator[None]:
    """Log step start/end with elapsed time when ``verbose``."""
    if verbose:
        print(f"site suggest:   → {label}…", flush=True)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        if verbose:
            print(f"site suggest:   ✓ {label} ({time.perf_counter() - t0:.1f}s)", flush=True)


def suggest_progress(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"site suggest:     … {msg}", flush=True)
