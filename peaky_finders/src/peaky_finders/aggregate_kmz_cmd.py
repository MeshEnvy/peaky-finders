"""``peaky kmz`` — build aggregate KMZ from bundle + persisted viewshed workspaces."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peaky_finders.bundle_build import (
    bundle_directory_for_preset,
    require_land_config,
)
from peaky_finders.cli import (
    _bundle_render_cache_root,
    _bundle_render_data_dir,
    package_aggregate_kmz,
)
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.preset_overlays import collect_site_workspace_assets
from peaky_finders.sites_job import (
    load_preset,
    require_cwd_config_yaml,
    resolved_mesh_depth_dir,
    resolved_mesh_pairwise_dir,
    resolved_preset_slug,
    resolved_viewshed_coverage_kml_style,
)
from peaky_finders.viewshed_workspace import viewshed_workspace_digest


def build_aggregate_kmz_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="Preset bundle GDB inputs root override",
    )
    return p


def run_aggregate_kmz(args: argparse.Namespace) -> int:
    try:
        job_path = require_cwd_config_yaml()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        job = load_preset(job_path)
    except Exception as e:
        print(f"Invalid preset YAML: {e}", file=sys.stderr)
        return 2
    if job.land is None:
        print("kmz requires a preset with land.* (AOI / land-use)", file=sys.stderr)
        return 2

    bundle_cache_root = _bundle_render_cache_root(job_path)
    bundle_bb_data_dir = _bundle_render_data_dir(args, job_path, job)

    _ = require_land_config(job)

    bundle_dir = bundle_directory_for_preset(
        preset_path=job_path,
        data_dir=bundle_bb_data_dir,
        cache_root=bundle_cache_root,
    )
    if bundle_dir is None:
        print("Could not resolve bundle directory for preset.", file=sys.stderr)
        return 2

    preset_id = resolved_preset_slug(job_path)

    kml_ov = job.display.kml if job.land else None
    viewshed_cov_style = resolved_viewshed_coverage_kml_style(kml_ov)

    pairwise_geom_root = resolved_mesh_pairwise_dir(bundle_cache_root)
    mesh_depth_geom_root = resolved_mesh_depth_dir(bundle_cache_root)

    max_raster_dim = 4096
    if job.land and job.mesh is not None:
        max_raster_dim = job.mesh.max_raster_dimension

    slug_to_digest: dict[str, str] = {}
    for slug, site in job.sites.items():
        vd = viewshed_workspace_digest(
            request=preset_to_request(job, float(site.lat), float(site.lon)),
        )
        slug_to_digest[slug] = vd

    try:
        assets = collect_site_workspace_assets(
            preset_path=job_path,
            preset=job,
            bundle_cache_root=bundle_cache_root,
            sites_items=list(job.sites.items()),
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    kmz_path = package_aggregate_kmz(
        job_path=job_path,
        job=job,
        preset_id=preset_id,
        bundle_dir=bundle_dir,
        bundle_bb_data_dir=bundle_bb_data_dir,
        overlays=assets.overlays,
        png_paths=assets.png_paths,
        coverage_kml_paths=assets.coverage_kml_paths,
        slug_to_digest=slug_to_digest,
        viewshed_cov_style=viewshed_cov_style,
        pairwise_geom_root=pairwise_geom_root,
        mesh_depth_geom_root=mesh_depth_geom_root,
        max_raster_dim=max_raster_dim,
    )
    print(f"Wrote {kmz_path}", flush=True)
    return 0
