"""Peaky web server entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path


def run_web(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="peaky web")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(
        "peaky_finders.web.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parents[1])],
    )
    return 0
