"""CLI: ``peaky generate-makefile`` — emit a Makefile for preset-driven builds."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peaky_finders.build_configure import ConfigureError
from peaky_finders.makefile_gen import generate_makefile_text
from peaky_finders.sites_job import resolve_preset_yaml_arg


def build_generate_makefile_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "preset_yaml",
        type=Path,
        help="Preset YAML path or project slug (e.g. sample → projects/sample/config.yaml)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write Makefile here (default: Makefile beside the preset)",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Write Makefile to stdout instead of a file",
    )
    p.add_argument(
        "--make-root",
        type=Path,
        default=None,
        metavar="PATH",
        help="Express paths relative to this directory "
        "(default: preset parent — matches peaky build's make -C <preset dir>)",
    )
    p.add_argument(
        "--peaky-cmd",
        default="peaky",
        metavar="CMD",
        help="Command name embedded in generated recipes (default: peaky)",
    )
    return p


def run_generate_makefile(args: argparse.Namespace) -> int:
    preset_path = resolve_preset_yaml_arg(args.preset_yaml)
    if not preset_path.is_file():
        print(f"Preset file not found: {preset_path}", file=sys.stderr)
        return 2

    mr = Path(args.make_root).expanduser().resolve() if args.make_root is not None else None
    preset_arg = str(args.preset_yaml)
    try:
        text = generate_makefile_text(
            preset_path=preset_path,
            make_root=mr,
            peaky_cmd=str(args.peaky_cmd),
            preset_arg=preset_arg,
        )
    except ConfigureError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.stdout:
        sys.stdout.write(text)
        return 0

    out_path = (
        Path(args.output).expanduser().resolve()
        if args.output is not None
        else preset_path.parent / "Makefile"
    )
    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0
