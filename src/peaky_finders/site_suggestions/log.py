"""Verbose logging helpers for site suggestion."""

from __future__ import annotations


def suggest_log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, flush=True)
