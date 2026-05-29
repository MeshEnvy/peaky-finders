"""Web runtime settings from environment (``DEBUG=1`` verbose tracing)."""

from __future__ import annotations

import os
from collections.abc import Callable


def debug_enabled() -> bool:
    return os.environ.get("DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def stderr_verbose_log() -> Callable[[str], None] | None:
    if not debug_enabled():
        return None
    return lambda msg: print(msg, flush=True)


def log_debug_mode_at_boot() -> None:
    if debug_enabled():
        print("peaky web: DEBUG=1 (verbose operation logging)", flush=True)
