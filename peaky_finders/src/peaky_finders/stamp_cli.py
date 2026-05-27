"""``peaky stamp``: write dependency stamps under ``build/stamps``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peaky_finders.preset_stamps import list_stamp_sections, write_stamp
from peaky_finders.sites_job import load_preset, require_cwd_config_yaml


def build_stamp_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Write one line + header under build/stamps for Make edges. "
            "Use: peaky stamp list | peaky stamp all | peaky stamp <section> | peaky stamp site <slug>"
        ),
    )
    p.add_argument(
        "stamp_args",
        nargs=argparse.REMAINDER,
        metavar="...",
        help="stamp command (run from project directory with config.yaml)",
    )
    return p


def run_stamp(args: argparse.Namespace) -> int:
    raw = [x for x in getattr(args, "stamp_args", []) if x.strip()]
    if not raw:
        print(
            "usage: peaky stamp (list|all|<section>|site <slug>) — run from project directory",
            file=sys.stderr,
        )
        return 2

    try:
        preset_path = require_cwd_config_yaml()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    spec = raw

    if spec == ["list"]:
        preset = load_preset(preset_path)
        for s in list_stamp_sections(preset):
            print(s, flush=True)
        return 0

    try:
        if spec == ["all"]:
            preset = load_preset(preset_path)
            for sec in list_stamp_sections(preset):
                write_stamp(sec, preset_path)
            return 0

        if len(spec) >= 2 and spec[0] == "site":
            slug = spec[1]
            write_stamp(f"site__{slug}", preset_path)
            return 0

        if len(spec) == 1:
            write_stamp(spec[0], preset_path)
            return 0

    except (OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"Unsupported stamp invocation: {' '.join(spec)!r}", file=sys.stderr)
    return 2
