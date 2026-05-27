"""Granular ``peaky mesh *`` (Make targets): pairwise, depth, eligible-union, site links."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from peaky_finders.bundle_build import (
    bundle_directory_for_preset,
    bundle_eligible_land_use_gpkg,
    bundle_kml_overlay_inputs_digest,
    bundle_land_use_inputs_digest,
    require_bundle_config,
)
from peaky_finders.bundle_clips import bundle_resolve_path, eligible_gpkg_from_bundle_dir, read_bundle_resolve
from peaky_finders.link_overlap import read_eligible_land_use_union, write_pairwise_link_overlap_kml_pairs
from peaky_finders.mesh_coverage_depth import write_mesh_depth_kml_layers
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.preset_overlays import collect_site_workspace_assets, standard_bundle_roots
from peaky_finders.sites_job import (
    load_preset,
    resolved_eligible_union_build_dir,
    resolved_mesh_depth_dir,
    resolved_mesh_depth_enabled,
    resolved_mesh_pairwise_dir,
    resolved_mesh_pairwise_eligible_kml_style,
    resolved_mesh_pairwise_eligible_peak_pin_kml_style,
    resolved_mesh_pairwise_enabled,
    resolved_mesh_pairwise_kml_style,
    resolved_mesh_pairwise_peak_pin_kml_style,
    resolved_mesh_site_links_kml,
    resolved_preset_dem_tile_cache_dir,
    resolve_preset_yaml_arg,
)
from peaky_finders.mesh_pairwise_store import resolved_mesh_pairwise_pair_dir
from peaky_finders.viewshed_workspace import viewshed_workspace_digest
from peaky_finders.viewshed_links import write_site_links_kml


def _slug_digest_map(job) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for site_slug, site in job.sites.items():
        req = preset_to_request(job, float(site.lat), float(site.lon))
        vd = viewshed_workspace_digest(request=req)
        mapping[site_slug] = vd
    return mapping


def _mesh_pairwise_site_pins(job, slugs: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    return {slug: (float(job.sites[slug].lat), float(job.sites[slug].lon)) for slug in slugs}


def _simulation_viewshed_radius_m(job) -> float:
    return float(job.simulation.radius_km) * 1000.0


def run_mesh_links(preset_yaml: Path) -> int:
    preset_path = resolve_preset_yaml_arg(Path(preset_yaml))
    job = load_preset(preset_path)
    job_path_r, bundle_cache_root, _bb_dd = standard_bundle_roots(preset_path, job)

    assets = collect_site_workspace_assets(
        preset_path=job_path_r,
        preset=job,
        bundle_cache_root=bundle_cache_root,
        sites_items=list(job.sites.items()),
    )

    outp = resolved_mesh_site_links_kml(job_path_r)
    outp.parent.mkdir(parents=True, exist_ok=True)
    sees_by_slug = {slug: entry.sees for slug, entry in job.sites.items()}
    if write_site_links_kml(
        coverage_gpkg_by_slug=assets.coverage_gpkg_by_slug,
        sites=assets.overlays,
        sees_by_slug=sees_by_slug,
        out_kml=outp,
    ):
        print(f"mesh links: wrote {outp}", flush=True)
        return 0
    if outp.is_file():
        outp.unlink()
    print("mesh links: nothing to emit (no mutual links)", flush=True)
    return 0


def _mesh_roots(bundle_cache_root: Path) -> tuple[Path, Path, Path]:
    pairwise_root = resolved_mesh_pairwise_dir(bundle_cache_root)
    mesh_depth_root = resolved_mesh_depth_dir(bundle_cache_root)
    eligible_union_root = resolved_eligible_union_build_dir(bundle_cache_root)
    return pairwise_root, mesh_depth_root, eligible_union_root


def run_mesh_pairwise(slug_a: str, slug_b: str, preset_yaml: Path) -> int:
    preset_path = resolve_preset_yaml_arg(Path(preset_yaml))
    job = load_preset(preset_path)
    if job.bundle is None:
        print("mesh pairwise: preset needs bundle.*", file=sys.stderr)
        return 2
    mesh_cov = job.bundle.mesh_coverage if job.bundle else None
    if not resolved_mesh_pairwise_enabled(mesh_cov):
        print("mesh pairwise: disabled (bundle.mesh_coverage.pairwise: false)", flush=True)
        return 0
    slug_a_, slug_b_ = slug_a.strip(), slug_b.strip()
    if slug_a_ not in job.sites or slug_b_ not in job.sites:
        print("mesh pairwise: unknown site slug(s)", file=sys.stderr)
        return 2

    job_path_r, bundle_cache_root, bb_dd = standard_bundle_roots(preset_path, job)
    bundle_dir = bundle_directory_for_preset(preset_path=job_path_r, data_dir=bb_dd, cache_root=bundle_cache_root)
    assert bundle_dir is not None

    plc = require_bundle_config(job)
    pairwise_overlay = bundle_kml_overlay_inputs_digest(plc)
    pairwise_land = bundle_land_use_inputs_digest(plc, bb_dd)

    kml_ov = job.bundle.kml_overlay if job.bundle else None
    pairwise_style = resolved_mesh_pairwise_kml_style(kml_ov)
    elig_style = resolved_mesh_pairwise_eligible_kml_style(kml_ov)
    peak_plain = resolved_mesh_pairwise_peak_pin_kml_style(kml_ov)
    peak_elig = resolved_mesh_pairwise_eligible_peak_pin_kml_style(kml_ov)

    # Only validate/load workspaces for this pair (-j parallel can run pairwise before every site completes).
    pair_items = [(slug_a_, job.sites[slug_a_]), (slug_b_, job.sites[slug_b_])]
    assets = collect_site_workspace_assets(
        preset_path=job_path_r,
        preset=job,
        bundle_cache_root=bundle_cache_root,
        sites_items=pair_items,
    )
    ov_by = {o.slug: o for o in assets.overlays}
    footprints_arg = []
    for s in (slug_a_, slug_b_):
        g = assets.coverage_gpkg_by_slug[s]
        o = ov_by[s]
        footprints_arg.append((g.resolve(), o.slug, o.folder_name))

    eligible_path = bundle_eligible_land_use_gpkg(bundle_dir)
    eligible_sha: str | None = None
    try:
        if bundle_resolve_path(bundle_dir).is_file():
            resolve = read_bundle_resolve(bundle_dir)
            if eligible_path.resolve() == eligible_gpkg_from_bundle_dir(bundle_dir).resolve():
                es = resolve.get("eligible")
                if isinstance(es, str) and es:
                    eligible_sha = es
    except (OSError, ValueError, KeyError):
        pass

    geo_root, _md, eu_root = _mesh_roots(bundle_cache_root)
    tile_host = resolved_preset_dem_tile_cache_dir(job_path_r)
    tile_host.mkdir(parents=True, exist_ok=True)

    mesh_cov = job.bundle.mesh_coverage if job.bundle else None
    emit_dem_peak = True if mesh_cov is None else mesh_cov.pairwise_dem_peak_pin

    eu = read_eligible_land_use_union(
        eligible_path,
        cache_root=eu_root,
        eligible_sha=eligible_sha,
    )
    emit_eligible = eu is not None and not eu.is_empty
    pair_workers = mesh_cov.pairwise_overlap_workers if mesh_cov is not None else 8

    pdir = resolved_mesh_pairwise_pair_dir(slug_a=slug_a_, slug_b=slug_b_, cache_root=geo_root)
    pdir.mkdir(parents=True, exist_ok=True)
    write_pairwise_link_overlap_kml_pairs(
        footprints=footprints_arg,
        emit_plain=True,
        link_polygon_style=pairwise_style,
        link_scratch_dir=pdir,
        emit_eligible=emit_eligible,
        eligible_scratch_dir=pdir if emit_eligible else None,
        eligible_polygon_style=elig_style if emit_eligible else None,
        eligible_ll=eu if emit_eligible else None,
        dem_mirror_root=tile_host if emit_dem_peak else None,
        emit_dem_peak_pins=emit_dem_peak,
        pairwise_peak_pin_style=peak_plain,
        eligible_peak_pin_style=peak_elig,
        pairwise_overlap_workers=pair_workers,
        geometry_cache_root=geo_root,
        slug_to_viewshed_digest=_slug_digest_map(job),
        bundle_kml_overlay_digest=pairwise_overlay,
        bundle_land_use_inputs_digest=pairwise_land if emit_eligible else None,
        site_pins=_mesh_pairwise_site_pins(job, (slug_a_, slug_b_)),
        viewshed_radius_m=_simulation_viewshed_radius_m(job),
    )
    print(f"mesh pairwise {slug_a_} ↔ {slug_b_}: wrote pairwise KML under {pdir}", flush=True)
    return 0


def run_mesh_depth(preset_yaml: Path) -> int:
    preset_path = resolve_preset_yaml_arg(Path(preset_yaml))
    job = load_preset(preset_path)
    if job.bundle is None:
        print("mesh depth: preset needs bundle.*", file=sys.stderr)
        return 2
    mesh_cov = job.bundle.mesh_coverage if job.bundle else None
    if not resolved_mesh_depth_enabled(mesh_cov):
        print("mesh depth: disabled (bundle.mesh_coverage.depth: false)", flush=True)
        return 0

    job_path_r, bundle_cache_root, bb_dd = standard_bundle_roots(preset_path, job)
    bundle_dir = bundle_directory_for_preset(preset_path=job_path_r, data_dir=bb_dd, cache_root=bundle_cache_root)
    assert bundle_dir is not None

    plc = require_bundle_config(job)
    pairwise_overlay = bundle_kml_overlay_inputs_digest(plc)
    pairwise_land = bundle_land_use_inputs_digest(plc, bb_dd)

    assets = collect_site_workspace_assets(
        preset_path=job_path_r,
        preset=job,
        bundle_cache_root=bundle_cache_root,
        sites_items=list(job.sites.items()),
    )
    footprint_rows = list(zip(assets.coverage_gpkg_paths, assets.overlays, strict=True))
    footprints_arg = [(gp.resolve(), o.slug, o.folder_name) for gp, o in footprint_rows]

    eligible_path = bundle_eligible_land_use_gpkg(bundle_dir)
    eligible_sha: str | None = None
    try:
        if bundle_resolve_path(bundle_dir).is_file():
            resolve = read_bundle_resolve(bundle_dir)
            if eligible_path.resolve() == eligible_gpkg_from_bundle_dir(bundle_dir).resolve():
                es = resolve.get("eligible")
                if isinstance(es, str) and es:
                    eligible_sha = es
    except (OSError, ValueError, KeyError):
        pass

    _geo_root, mesh_depth_geom_root, eu_root = _mesh_roots(bundle_cache_root)
    mesh_cov = job.bundle.mesh_coverage if job.bundle else None
    depth_workers = mesh_cov.mesh_depth_workers if mesh_cov is not None else 8
    max_raster = mesh_cov.max_raster_dimension if mesh_cov is not None else 4096

    eu = read_eligible_land_use_union(
        eligible_path,
        cache_root=eu_root,
        eligible_sha=eligible_sha,
    )
    emit_eligible = eu is not None and not eu.is_empty

    kml_ov = job.bundle.kml_overlay if job.bundle else None

    with tempfile.TemporaryDirectory() as td:
        overlap_dir = Path(td)
        write_mesh_depth_kml_layers(
            footprints=footprints_arg,
            kml_overlay=kml_ov,
            scratch_depth_dir=overlap_dir / "mesh_depth",
            scratch_depth_eligible_dir=overlap_dir / "mesh_depth_elig" if emit_eligible else None,
            eligible_ll=eu if emit_eligible else None,
            max_raster_dimension=max_raster,
            mesh_depth_workers=depth_workers,
            geometry_cache_root=mesh_depth_geom_root,
            slug_to_viewshed_digest=_slug_digest_map(job),
            bundle_kml_overlay_digest=pairwise_overlay,
            bundle_land_use_inputs_digest=pairwise_land if emit_eligible else None,
        )
    print("mesh depth: depth geometry cache refreshed", flush=True)
    return 0


def run_mesh_eligible_union(preset_yaml: Path) -> int:
    preset_path = resolve_preset_yaml_arg(Path(preset_yaml))
    job = load_preset(preset_path)
    if job.bundle is None:
        print("mesh eligible-union: preset needs bundle.*", file=sys.stderr)
        return 2

    job_path_r, bundle_cache_root, bb_dd = standard_bundle_roots(preset_path, job)
    bundle_dir = bundle_directory_for_preset(preset_path=job_path_r, data_dir=bb_dd, cache_root=bundle_cache_root)
    assert bundle_dir is not None

    eligible_path = bundle_eligible_land_use_gpkg(bundle_dir)
    eligible_sha: str | None = None
    try:
        if bundle_resolve_path(bundle_dir).is_file():
            resolve = read_bundle_resolve(bundle_dir)
            es = resolve.get("eligible")
            if isinstance(es, str) and es:
                eligible_sha = es
    except (OSError, ValueError, KeyError):
        pass

    _p, _md, eu_root = _mesh_roots(bundle_cache_root)
    geo = read_eligible_land_use_union(
        eligible_path,
        cache_root=eu_root,
        eligible_sha=eligible_sha,
    )
    msg = "empty" if geo is None or getattr(geo, "is_empty", True) else "materialized union"
    print(f"mesh eligible-union: {msg}", flush=True)
    return 0


def run_mesh_entry(args: object) -> int:
    preset = getattr(args, "preset_yaml", None)
    cmd = getattr(args, "mesh_cmd", None)
    if preset is None or cmd is None:
        print("internal error: mesh_cli", file=sys.stderr)
        return 2
    p = Path(preset)
    if cmd == "links":
        return run_mesh_links(p)
    if cmd == "pairwise":
        return run_mesh_pairwise(args.slug_a, args.slug_b, p)
    if cmd == "depth":
        return run_mesh_depth(p)
    if cmd == "eligible-union":
        return run_mesh_eligible_union(p)
    print(f"mesh: unknown {cmd!r}", file=sys.stderr)
    return 2
