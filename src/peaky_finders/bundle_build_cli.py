"""CLI for AOI bundle cache: build eligible land-use GeoPackage from preset ``bundle.*`` layers.

Used by ``peaky render`` and ``python -m peaky_finders.bundle_build_cli``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cli_dem_workers(s: str) -> int:
    v = int(s)
    if v < 1:
        raise argparse.ArgumentTypeError("--dem-workers must be a positive integer")
    return v


def build_bundle_argument_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "preset_yaml",
        type=Path,
        help=(
            'Preset YAML with "bundle.aoi" (polygon GDB layers), '
            '"bundle.include", "bundle.exclude", etc.'
        ),
    )
    p.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Rebuild eligible-land AOI bundle even when a matching cache exists",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Override bundle cache root (default: <preset cache_path>/bundles, else repo .cache/bundles)"
        ),
    )
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="Repo data directory for preset GDB paths (default: repo data/)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Log cache layout, clip reuse/build, layer counts, and flushed progress during long "
            "GDB/GPKG/KML steps (same as env PEAKY_BUNDLE_PROGRESS=1)."
        ),
    )
    p.add_argument(
        "--dem-workers",
        type=_cli_dem_workers,
        default=None,
        metavar="N",
        help=(
            "Parallel S3 workers fetching Skadi `*.hgt.gz` into the DEM mirror "
            "(default mirror: <preset cache_path>/splat_tiles, else repo .cache/splat_tiles; "
            "worker default: 16)"
        ),
    )
    p.add_argument(
        "--skip-dem-prefetch",
        action="store_true",
        help=(
            "Do not prefetch Skadi HGT tiles into the DEM mirror "
            "(see preset cache_path / .cache/splat_tiles)."
        ),
    )
    p.add_argument(
        "--no-plss-fetch",
        action="store_true",
        help="Skip BLM CadNSDI refresh of site plss/mlrs (loc-keyed cache under preset cache_path)",
    )
    p.add_argument("--quiet", "-q", action="store_true")
    return p


def add_bundle_subcommands(pre_sub: argparse.Action) -> None:
    """Register ``run`` and ``inspect`` for ``python -m peaky_finders.bundle_build_cli``."""
    from peaky_finders.gdb_inspect_cli import (
        build_inspect_parser,
        run_inspect,
    )

    p_run = pre_sub.add_parser(
        "run",
        parents=[build_bundle_argument_parser()],
        help="Build AOI / eligible-land bundle cache from preset YAML",
    )
    p_run.set_defaults(_handler=run_land_use_bundle_cli)

    p_inspect = pre_sub.add_parser(
        "inspect",
        parents=[build_inspect_parser()],
        help="List layers in a File Geodatabase",
    )
    p_inspect.set_defaults(_handler=run_inspect)


def build_bundle_build_entry_parser(*, prog: str = "bundle_build_cli") -> argparse.ArgumentParser:
    """CLI for ``python -m peaky_finders.bundle_build_cli`` (subcommands: run, inspect)."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="AOI bundle cache: build (run) or list GDB layers (inspect).",
    )
    sub = parser.add_subparsers(dest="bundle_action", required=True)
    add_bundle_subcommands(sub)
    return parser


def bundle_build_cli_parser(*, prog: str = "bundle_build_cli") -> argparse.ArgumentParser:
    return build_bundle_build_entry_parser(prog=prog)


def run_bundle_build(ns: argparse.Namespace, *, log_prefix: str) -> int:
    """Build or reuse bundle via :func:`~peaky_finders.bundle_build.ensure_land_use_bundle`."""
    repo_data = Path(__file__).resolve().parents[2] / "data"

    data_dir = repo_data if ns.data_dir is None else Path(ns.data_dir).expanduser().resolve()
    cache_root = Path(ns.cache_dir).expanduser().resolve() if ns.cache_dir is not None else None
    preset_path = Path(ns.preset_yaml).expanduser().resolve()

    from peaky_finders.bundle_build import ensure_land_use_bundle

    try:
        gpkg, reused_cache = ensure_land_use_bundle(
            preset_path=preset_path,
            data_dir=data_dir,
            cache_root=cache_root,
            force=ns.force,
            verbose=ns.verbose,
            prefetch_dem=not ns.skip_dem_prefetch,
            dem_workers=ns.dem_workers,
            no_plss_fetch=ns.no_plss_fetch,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 2

    if not ns.quiet:
        verb = "reused AOI GeoPackage cache" if reused_cache else "built AOI GeoPackage"
        print(f"{log_prefix}:{verb}: {gpkg}", flush=True)

    return 0


def run_land_use_bundle_cli(ns: argparse.Namespace) -> int:
    return run_bundle_build(ns, log_prefix="bundle")


def bundle_build_main_inner(argv_list: list[str] | None) -> int:
    argv = argv_list if argv_list is not None else sys.argv[1:]
    ns = bundle_build_cli_parser().parse_args(argv)
    handler = getattr(ns, "_handler", None)
    if handler is None:
        return 2
    return handler(ns)


def main() -> None:
    sys.exit(bundle_build_main_inner(None))


def bundle_build_main() -> int:
    return bundle_build_main_inner(sys.argv[1:])


if __name__ == "__main__":
    main()
