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


class SuggestProgressTicker:
    """Time- and count-throttled progress for long suggest loops (≈0.25–1 s cadence)."""

    def __init__(
        self,
        verbose: bool,
        *,
        label: str = "",
        interval_s: float = 0.5,
        every_n: int = 1,
    ) -> None:
        self.verbose = verbose
        self.label = str(label)
        self.interval_s = max(0.25, float(interval_s))
        self.every_n = max(1, int(every_n))
        self._t0 = time.perf_counter()
        self._t_last = self._t0
        self._n = 0

    def _prefix(self, msg: str) -> str:
        return f"{self.label}: {msg}" if self.label else msg

    def maybe(self, msg: str, *, force: bool = False) -> None:
        if not self.verbose:
            return
        self._n += 1
        now = time.perf_counter()
        if (
            force
            or self._n == 1
            or self._n % self.every_n == 0
            or (now - self._t_last) >= self.interval_s
        ):
            elapsed = now - self._t0
            suggest_progress(self.verbose, f"{self._prefix(msg)} ({elapsed:.1f}s elapsed)")
            self._t_last = now

    def done(self, msg: str) -> None:
        if not self.verbose:
            return
        elapsed = time.perf_counter() - self._t0
        suggest_log(self.verbose, f"site suggest:     {self._prefix(msg)} ({elapsed:.1f}s)")
