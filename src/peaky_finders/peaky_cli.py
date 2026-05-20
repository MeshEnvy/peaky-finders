"""Unified CLI: ``peaky render`` (full pipeline), ``peaky inspect`` (GDB layers)."""

from __future__ import annotations

import argparse
import sys

from peaky_finders.cli import build_render_argument_parser, run_render
from peaky_finders.gdb_inspect_cli import build_inspect_parser, run_inspect


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="peaky",
        description="Peaky Finders: render a preset to KMZ, or inspect GDB datasets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser(
        "render",
        parents=[build_render_argument_parser()],
        help="Build AOI bundle, prefetch DEM tiles, run SPLAT (Docker), write aggregate KMZ",
    )
    p_render.set_defaults(_handler=run_render)

    p_inspect = sub.add_parser(
        "inspect",
        parents=[build_inspect_parser()],
        help="List layers and attributes in a File Geodatabase",
    )
    p_inspect.set_defaults(_handler=run_inspect)

    args = parser.parse_args()
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error("internal error: missing command handler")
    sys.exit(handler(args))


if __name__ == "__main__":
    main()
