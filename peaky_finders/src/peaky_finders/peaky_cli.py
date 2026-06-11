"""Unified CLI: incremental build + granular helpers."""

from __future__ import annotations

import argparse
import sys

from peaky_finders.aggregate_kmz_cmd import build_aggregate_kmz_parser, run_aggregate_kmz
from peaky_finders.bundle_commands import run_bundle_entry
from peaky_finders.build_cli import build_build_parser, run_build
from peaky_finders.cli import build_granular_viewshed_argument_parser, run_splat
from peaky_finders.inspect_cli import build_inspect_parser, run_inspect
from peaky_finders.mesh_commands import run_mesh_entry
from peaky_finders.new_cli import build_new_parser, run_new
from peaky_finders.serve_cli import build_serve_parser, run_serve
from peaky_finders.bundled_templates import ensure_peaky_home
from peaky_finders.stamp_cli import build_stamp_parser, run_stamp


def main() -> None:
    ensure_peaky_home()
    parser = argparse.ArgumentParser(
        prog="peaky",
        description=(
            "Peaky Finders: run from a project directory containing config.yaml. "
            "``peaky new`` scaffolds a project; ``peaky build`` runs the preset DAG incrementally "
            "(bundle, viewshed, mesh, KMZ); granular peaky bundle/viewshed/mesh/stamp/kmz for recipes."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser(
        "new",
        parents=[build_new_parser()],
        help="Scaffold a new project (config.yaml + data/ layout)",
    )
    p_new.set_defaults(_handler=run_new)

    p_serve = sub.add_parser(
        "serve",
        parents=[build_serve_parser()],
        help="Run local web UI (project selector under PEAKY_HOME)",
    )
    p_serve.set_defaults(_handler=run_serve)

    build_p = build_build_parser()
    pb = sub.add_parser("build", parents=[build_p], help="Run incremental preset build graph")
    pb.set_defaults(_handler=run_build)

    p_inspect = sub.add_parser(
        "inspect",
        parents=[build_inspect_parser()],
        help="List layers and attributes in a File Geodatabase",
    )
    p_inspect.set_defaults(_handler=run_inspect)

    p_stamp = sub.add_parser(
        "stamp",
        parents=[build_stamp_parser()],
        help="Write build/stamps sections for prerequisite edges",
    )
    p_stamp.set_defaults(_handler=run_stamp)

    p_bundle = sub.add_parser("bundle", help="Granular bundle targets (Make-style recipes)")
    bsub = p_bundle.add_subparsers(dest="bundle_cmd", required=True)

    pcl = bsub.add_parser("clip")
    pcl.add_argument("role", choices=("aoi", "include", "exclude"))
    pcl.add_argument("layer")

    pcs = bsub.add_parser("composite")
    pcs.add_argument("role", choices=("aoi", "include", "exclude"))

    bsub.add_parser("eligible")

    prf = bsub.add_parser("reference")
    prf.add_argument("reference_id")

    bsub.add_parser("resolve")
    bsub.add_parser("plss")
    pdem = bsub.add_parser("dem")
    pdem.add_argument(
        "--tile",
        dest="dem_tile",
        metavar="N37W117",
        default=None,
        help="Fetch exactly this Skadi 1° cell",
    )

    p_bundle.set_defaults(_handler=run_bundle_entry)

    p_mesh = sub.add_parser("mesh", help="Terrain / link geometry overlays for KMZ")
    msub = p_mesh.add_subparsers(dest="mesh_cmd", required=True)

    msub.add_parser("links")
    mpw = msub.add_parser("pairwise")
    mpw.add_argument("slug_a")
    mpw.add_argument("slug_b")
    msub.add_parser("depth")
    msub.add_parser("eligible-union")

    p_mesh.set_defaults(_handler=run_mesh_entry)

    p_viewshed = sub.add_parser(
        "viewshed",
        parents=[build_granular_viewshed_argument_parser()],
        help="Coverage workspace steps for one site slug",
    )
    p_viewshed.add_argument("granular_viewshed_slug", metavar="SITE_SLUG")
    p_viewshed.set_defaults(_handler=run_splat)

    p_kmz = sub.add_parser(
        "kmz",
        parents=[build_aggregate_kmz_parser()],
        help="Assemble aggregate KMZ (no coverage run)",
    )
    p_kmz.set_defaults(_handler=run_aggregate_kmz)

    args = parser.parse_args()
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.error("internal error: missing command handler")

    sys.exit(handler(args))


if __name__ == "__main__":
    main()
