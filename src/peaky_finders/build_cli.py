"""``peaky build`` — preset-driven incremental build (Python executor)."""

from __future__ import annotations

import argparse
from pathlib import Path

from peaky_finders.build_executor import run_incremental_build
from peaky_finders.sites_job import resolve_preset_yaml_arg


def build_build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Incremental build driven by preset YAML DAG.", add_help=False)
    p.add_argument(
        "preset_yaml",
        type=Path,
        metavar="PRESET.yaml",
        help="Preset job YAML",

    )


    p.add_argument(
        "--data-dir",

        type=Path,
        default=None,
        metavar="PATH",
        help="Bundle GDB/data directory override",
    )


    p.add_argument(

        "--target",
        metavar="SCOPE",

        dest="selection",
        default="all",

        help="Subgraph root: all | kmz | bundle | mesh | viewsheds | viewshed/<slug>",


    )


    p.add_argument(

        "--dry-run",
        action="store_true",

        help="Print build/fresh flags for DAG nodes",

    )


    p.add_argument(




        "--force",
        action="store_true",

        help="Run every targeted node (ignore staleness)",
    )


    p.add_argument(




        "-j",
        metavar="N",
        type=int,
        default=1,
        dest="parallelism",

        help="Parallel targets within one wave",
    )


    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Log each target",

    )


    return p




def run_build(args: argparse.Namespace) -> int:
    preset = resolve_preset_yaml_arg(Path(args.preset_yaml))

    sel = getattr(args, "selection", None) or "all"


    parallel = getattr(args, "parallelism", 1)


    verbose = getattr(args, "verbose", False)


    return run_incremental_build(
        preset_path=preset.expanduser(),
        data_dir_arg=args.data_dir,
        selection=str(sel),
        force=args.force,

        dry_run=args.dry_run,

        jobs=int(parallel),
        verbose=verbose,
    )
