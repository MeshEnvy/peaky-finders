"""Peaky web server entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from peaky_finders.web.settings import debug_enabled


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="peaky")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(
        "peaky_finders.web.app:app",
        host=args.host,
        port=args.port,
        log_level="debug" if debug_enabled() else "info",
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parents[1])],
        # SSE clients (project events) stay open; without a cap reload hangs forever.
        timeout_graceful_shutdown=1.0,
    )


if __name__ == "__main__":
    main()
