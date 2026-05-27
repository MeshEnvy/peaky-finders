"""Unified CLI: incremental build + granular helpers."""

from __future__ import annotations

import argparse
import sys


from pathlib import Path

from peaky_finders.aggregate_kmz_cmd import build_aggregate_kmz_parser, run_aggregate_kmz
from peaky_finders.bundle_commands import run_bundle_entry
from peaky_finders.build_cli import build_build_parser, run_build
from peaky_finders.cli import build_granular_viewshed_argument_parser, run_splat

from peaky_finders.inspect_cli import build_inspect_parser, run_inspect



from peaky_finders.mesh_commands import run_mesh_entry
from peaky_finders.stamp_cli import build_stamp_parser, run_stamp




def main() -> None:


    parser = argparse.ArgumentParser(




        prog="peaky",

        description=(
            "Peaky Finders: ``peaky build`` runs the preset DAG incrementally "
            "(bundle, viewshed, mesh, KMZ); granular peaky bundle/viewshed/mesh/stamp/kmz for recipes."
        ),
    )


    sub = parser.add_subparsers(dest="command", required=True)







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


    pcl.add_argument("preset_yaml", type=Path)


    pcs = bsub.add_parser("composite")
    pcs.add_argument("role", choices=("aoi", "include", "exclude"))

    pcs.add_argument("preset_yaml", type=Path)

    pel = bsub.add_parser("eligible")


    pel.add_argument("preset_yaml", type=Path)


    prf = bsub.add_parser("reference")
    prf.add_argument("reference_id")

    prf.add_argument("preset_yaml", type=Path)


    prs = bsub.add_parser("resolve")


    prs.add_argument("preset_yaml", type=Path)


    pls = bsub.add_parser("plss")


    pls.add_argument("preset_yaml", type=Path)

    pdem = bsub.add_parser("dem")
    pdem.add_argument(
        "--tile",

        dest="dem_tile",

        metavar="N37W117",
        default=None,
        help="Fetch exactly this Skadi 1° cell",
    )


    pdem.add_argument("preset_yaml", type=Path)







    p_bundle.set_defaults(_handler=run_bundle_entry)


    p_mesh = sub.add_parser("mesh", help="Terrain / link geometry overlays for KMZ")


    msub = p_mesh.add_subparsers(dest="mesh_cmd", required=True)



    mpl = msub.add_parser("links")


    mpl.add_argument("preset_yaml", type=Path)



    mpw = msub.add_parser("pairwise")


    mpw.add_argument("slug_a")


    mpw.add_argument("slug_b")



    mpw.add_argument("preset_yaml", type=Path)


    mdp = msub.add_parser("depth")



    mdp.add_argument("preset_yaml", type=Path)


    meu = msub.add_parser("eligible-union")


    meu.add_argument("preset_yaml", type=Path)



    p_mesh.set_defaults(_handler=run_mesh_entry)


    p_viewshed = sub.add_parser(
        "viewshed",
        parents=[build_granular_viewshed_argument_parser()],
        help="Coverage workspace steps for one site slug",
    )


    p_viewshed.add_argument("granular_viewshed_slug", metavar="SITE_SLUG")


    p_viewshed.add_argument("preset_yaml", type=Path)


    p_viewshed.set_defaults(_handler=run_splat)



    p_kmz = sub.add_parser(
        "kmz",

        parents=[build_aggregate_kmz_parser()],

        help="Assemble aggregate KMZ (no SPLAT/Docker)",
    )


    p_kmz.set_defaults(_handler=run_aggregate_kmz)



    args = parser.parse_args()
    handler = getattr(args, "_handler", None)
    if handler is None:


        parser.error("internal error: missing command handler")


    sys.exit(handler(args))



if __name__ == "__main__":




    main()
