"""Host CLI: batch coverage from ``<preset>.yaml`` → one KMZ (Docker).

When RF propagation inputs plus tile mirror/image match, Docker coverage may skip (see
``splat_input_hash.py``); workspaces persist under ``<cache>/viewsheds/<digest>/``.
Plain pairwise footprint∩polygon geometry may persist under ``<cache>/mesh_pairwise/<digest>/``
(``overlap.gpkg`` plus optional ``dem_peak_plain.json`` / ``dem_peak_eligible.json`` for Skadi DEM pins).
Mesh coverage depth band + slice geometry may persist under ``<cache>/mesh_depth/<digest>/``.
Stitched mesh-depth flat KML (plain + eligible) may cache beside each slice keyed by preset slug under the same dirs.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import Future
from pathlib import Path
from typing import Literal

from peaky_finders import kml_bundle
from peaky_finders.link_overlap import (
    read_eligible_land_use_union,
    write_pairwise_link_overlap_kml_pairs,
)
from peaky_finders.mesh_coverage_depth import write_mesh_depth_kml_layers
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import (
    BundleKmlLayerStyle,
    CoverageProvider,
    Preset,
    SiteEntry,
    load_preset,
    mesh_edges_site_to_site_kml_arcname,
    resolved_aggregate_kmz_path,
    resolved_bundle_cache_root,
    resolved_coverage_dispatcher_max_workers,
    resolved_kmz_document_layers,
    resolved_eligible_union_cache_root,
    resolved_mesh_depth_cache_root,
    resolved_preset_bundle_data_dir,
    resolved_preset_slug,
    resolve_preset_yaml_arg,
    resolved_mesh_pairwise_eligible_kml_style,
    resolved_mesh_pairwise_eligible_peak_pin_kml_style,
    resolved_mesh_pairwise_geometry_cache_root,
    resolved_mesh_pairwise_eligible_kml_style,
    resolved_mesh_pairwise_eligible_peak_pin_kml_style,
    resolved_mesh_pairwise_kml_style,
    resolved_mesh_pairwise_peak_pin_kml_style,
    resolved_splat_tile_cache_dir,
    resolved_viewshed_cache_root,
    resolved_viewshed_coverage_kml_style,
    viewshed_polygon_coverage_kml_arcname,
    viewshed_raster_png_arcname,
)
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
    VIEWSHED_COVERAGE_KML_STYLE_ID,
    inject_peaky_polygon_kml_style,
)
from peaky_finders.viewshed_links import write_site_links_kml
from peaky_finders.splat_polygonize import COVERAGE_GPKG_NAME, COVERAGE_KML_NAME
from peaky_finders.splat_dispatcher import SplatDispatcher, SplatSiteJob, SplatSiteResult
from peaky_finders.splat_pipeline import coverage_docker_run_needed

DEFAULT_IMAGE_LOS = "splatter:latest"
DEFAULT_IMAGE_SPLAT = "peaky-finders-splat:latest"
IMAGE_ENV = "PEAKY_SPLAT_IMAGE"


def resolved_coverage_image(job: Preset) -> str:
    """Docker tag for coverage runs: ``PEAKY_SPLAT_IMAGE`` overrides preset ``simulation.provider``."""
    env_tag = os.environ.get(IMAGE_ENV)
    if env_tag:
        return env_tag
    if job.simulation.provider == CoverageProvider.SPLAT:
        return DEFAULT_IMAGE_SPLAT
    return DEFAULT_IMAGE_LOS


def resolved_coverage_dockerfile(job: Preset) -> str:
    return (
        "Dockerfile.splat"
        if job.simulation.provider == CoverageProvider.SPLAT
        else "splatter/Dockerfile"
    )


def resolved_coverage_docker_context(job: Preset) -> str:
    return "." if job.simulation.provider == CoverageProvider.SPLAT else "splatter"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundle_render_data_dir(args: argparse.Namespace, preset_path: Path, job: Preset) -> Path:
    raw = getattr(args, "data_dir", None)
    cli = None if raw is None else Path(raw).expanduser().resolve()
    return resolved_preset_bundle_data_dir(
        preset_path=preset_path,
        preset=job,
        cli_override=cli,
    )


def _bundle_render_cache_root(args: argparse.Namespace) -> Path:
    raw = getattr(args, "cache_dir", None)
    cli = None if raw is None else Path(raw).expanduser().resolve()
    return resolved_bundle_cache_root(cli_bundle_cache_root=cli)


def _ensure_tile_cache_dir() -> Path:
    d = resolved_splat_tile_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_aggregate_kmz(
    *,
    kmz_path: Path,
    kml_xml: str,
    bundle_zpairs: list[tuple[Path, str]],
    overlays: list[kml_bundle.AggregateSiteOverlay],
    png_paths: list[Path],
    coverage_kml_paths: list[Path | None],
    mesh_kml_writes: list[tuple[Path, str]] | None = None,
    viewshed_coverage_style: BundleKmlLayerStyle | None = None,
) -> None:
    from peaky_finders.sites_job import DEFAULT_VIEWSHED_COVERAGE_KML_STYLE

    cov_style = viewshed_coverage_style or DEFAULT_VIEWSHED_COVERAGE_KML_STYLE
    with zipfile.ZipFile(kmz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_xml.encode("utf-8"))
        for src, arc in bundle_zpairs:
            zf.write(src, arcname=arc)
        for ent, png, cov in zip(overlays, png_paths, coverage_kml_paths, strict=True):
            zf.write(png, arcname=ent.overlay_href)
            if cov is not None and ent.coverage_kml_href and cov.is_file():
                inject_peaky_polygon_kml_style(
                    cov,
                    style_id=VIEWSHED_COVERAGE_KML_STYLE_ID,
                    spec=cov_style,
                    gx_draw_order=GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
                )
                zf.write(cov, arcname=ent.coverage_kml_href)
        for disk, arc in mesh_kml_writes or []:
            if disk.is_file():
                zf.write(disk, arcname=arc)


def _bundle_land_use_layers_for_kmz(
    bundle_dir: Path,
    *,
    preset: Preset | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[Path, str]], list[tuple[str, str, bool]]]:
    """``(bundle_network_links, zip_write_pairs, reference_links)``.

    ``reference_links`` entries are ``(folder_label, kml_path_in_kmz, visible)`` from ``preset.bundle.reference``.
    """
    from peaky_finders.bundle_build import (
        SUBDIR_REFERENCE,
        reference_entry_kml_stem,
    )
    from peaky_finders.bundle_clips import (
        REFERENCE_EMPTY_MARKER,
        bundle_resolve_path,
        composite_kml_from_bundle_dir,
        eligible_gpkg_from_bundle_dir,
        read_bundle_resolve,
        reference_kml_from_bundle_dir,
    )

    nets: list[tuple[str, str]] = []
    zpairs: list[tuple[Path, str]] = []
    if bundle_resolve_path(bundle_dir).is_file():
        quartet: tuple[tuple[str, Literal["aoi", "include", "exclude"], str], ...] = (
            ("aoi", "aoi", "aoi/aoi.kml"),
            ("include", "include", "include/include.kml"),
            ("exclude", "exclude", "exclude/exclude.kml"),
        )
        for label, role, kmz_arc_s in quartet:
            kml_disk = composite_kml_from_bundle_dir(bundle_dir, role)
            if kml_disk.is_file():
                nets.append((label, kmz_arc_s))
                zpairs.append((kml_disk, kmz_arc_s))
        elig_kml = eligible_gpkg_from_bundle_dir(bundle_dir).with_suffix(".kml")
    else:
        from peaky_finders.bundle_build import SUBDIR_ELIGIBLE_LAND_USE

        quartet_legacy: tuple[tuple[str, Path, str], ...] = (
            ("aoi", bundle_dir / "aoi" / "aoi.kml", "aoi/aoi.kml"),
            ("include", bundle_dir / "include" / "include.kml", "include/include.kml"),
            ("exclude", bundle_dir / "exclude" / "exclude.kml", "exclude/exclude.kml"),
        )
        for label, kml_disk, kmz_arc_s in quartet_legacy:
            if kml_disk.is_file():
                nets.append((label, kmz_arc_s))
                zpairs.append((kml_disk, kmz_arc_s))
        elig_kml = bundle_dir / SUBDIR_ELIGIBLE_LAND_USE / "eligible_land_use.kml"
    elig_arc = "eligible/eligible_land_use.kml"
    if elig_kml.is_file():
        nets.append(("eligible", elig_arc))
        zpairs.append((elig_kml, elig_arc))

    ref_links: list[tuple[str, str, bool]] = []
    b = preset.bundle if preset is not None else None
    if b is not None:
        resolve_ref = (
            read_bundle_resolve(bundle_dir).get("reference")
            if bundle_resolve_path(bundle_dir).is_file()
            else None
        )
        for ent in b.reference:
            arc = f"reference/{ent.id}.kml"
            kml_disk: Path | None = None
            if isinstance(resolve_ref, dict) and ent.id in resolve_ref:
                try:
                    kml_disk = reference_kml_from_bundle_dir(bundle_dir, ent.id)
                    ref_dir_empty = (
                        kml_disk.parent / REFERENCE_EMPTY_MARKER
                    ).is_file()
                    if ref_dir_empty:
                        kml_disk = None
                except KeyError:
                    kml_disk = None
            if kml_disk is None:
                stem = reference_entry_kml_stem(ent.id)
                legacy = bundle_dir / SUBDIR_REFERENCE / f"{stem}.kml"
                if legacy.is_file():
                    kml_disk = legacy
                    arc = f"{SUBDIR_REFERENCE}/{stem}.kml"
            if kml_disk is not None and kml_disk.is_file():
                ref_links.append((ent.id, arc, ent.visible))
                zpairs.append((kml_disk, arc))
    return nets, zpairs, ref_links


def _docker_build(repo: Path, *, dockerfile_name: str, image: str, context: str = ".") -> int:
    print(f"Building Docker image {image!r} ({dockerfile_name})...", flush=True)
    r = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            image,
            "-f",
            str(repo / dockerfile_name),
            str(repo / context),
        ],
        check=False,
    )
    return r.returncode


def _resolve_job_path(sites_arg: Path) -> Path:
    return resolve_preset_yaml_arg(sites_arg)


def _pretty_m(value: float) -> float | int:
    return int(value) if float(value) == int(value) else value


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers (WGS84 sphere)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r * c


def _centroid_lat_lon(sites: list[tuple[str, SiteEntry]]) -> tuple[float, float]:
    n = len(sites)
    lat_m = sum(float(s.lat) for _, s in sites) / n
    lon_m = sum(float(s.lon) for _, s in sites) / n
    return lat_m, lon_m


def _sites_closest_to_centroid(
    sites_items: list[tuple[str, SiteEntry]],
    *,
    limit: int,
) -> list[tuple[str, SiteEntry]]:
    """Keep ``limit`` sites with smallest haversine distance to the centroid of *all* listed sites."""
    if limit >= len(sites_items):
        return list(sites_items)
    lat_c, lon_c = _centroid_lat_lon(sites_items)
    scored = [
        (
            _haversine_km(lat_c, lon_c, float(ent.lat), float(ent.lon)),
            slug,
            (slug, ent),
        )
        for slug, ent in sites_items
    ]
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored[:limit]]


def _closest_pair(sites_items: list[tuple[str, SiteEntry]]) -> list[tuple[str, SiteEntry]]:
    """The two sites with minimum pairwise great-circle distance (tie-break: slug order)."""
    n = len(sites_items)
    best_key: tuple[float, str, str] | None = None
    best_ij: tuple[int, int] | None = None
    for i in range(n):
        sa, ea = sites_items[i]
        for j in range(i + 1, n):
            sb, eb = sites_items[j]
            d = _haversine_km(float(ea.lat), float(ea.lon), float(eb.lat), float(eb.lon))
            a, b = sorted((sa, sb))
            key = (d, a, b)
            if best_key is None or key < best_key:
                best_key = key
                best_ij = (i, j)
    assert best_ij is not None
    i, j = best_ij
    return [sites_items[i], sites_items[j]]


def _site_pin_parts(
    *,
    pin_lat: float,
    pin_lon: float,
    elevation_m: float | None,
) -> str:
    parts = [f"Center {pin_lat:.6f}, {pin_lon:.6f}"]
    if elevation_m is not None:
        v = _pretty_m(float(elevation_m))
        parts.append(f"{v} m elevation")
    return " · ".join(parts)


def _add_splat_only_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=None,
        help="Process at most N sites: N≠2 → N closest to the preset centroid; N=2 → closest pair "
        "by great-circle distance",
    )
    p.add_argument(
        "--force-splat",
        action="store_true",
        help="Re-run SPLAT even when cached outputs match the current request hash",
    )
    p.add_argument(
        "--force-pairwise-geometry",
        action="store_true",
        help="Recompute pairwise footprint∩polygon geometry ignoring viewshed-keyed mesh_pairwise cache",
    )
    p.add_argument(
        "--force-mesh-depth-geometry",
        action="store_true",
        help="Recompute mesh coverage depth bands and per-site slices ignoring mesh_depth cache",
    )


def _add_splat_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "preset_yaml",
        metavar="PRESET",
        help="Preset YAML path or project slug (e.g. nevada → projects/nevada/config.yaml)",
    )
    _add_splat_only_arguments(p)


def build_splat_only_argument_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    _add_splat_only_arguments(p)
    return p


def build_splat_argument_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    _add_splat_arguments(p)
    return p


def build_render_argument_parser() -> argparse.ArgumentParser:
    """Flags for ``peaky render``: bundle/DEM phase + SPLAT/KMZ phase (one preset path)."""
    from peaky_finders.bundle_build_cli import build_bundle_argument_parser

    return argparse.ArgumentParser(
        add_help=False,
        parents=[build_bundle_argument_parser(), build_splat_only_argument_parser()],
    )


def splat_cli_parser(*, prog: str = "splat") -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=prog,
        description="LoRa coverage from `<preset>.yaml` (simulation.provider los|splat) → KMZ (Docker)",
        parents=[build_splat_argument_parser()],
    )


def run_render(args: argparse.Namespace) -> int:
    """Build AOI bundle + DEM tiles, then SPLAT + aggregate KMZ (``peaky render``)."""
    from peaky_finders.bundle_build_cli import run_bundle_build

    code = run_bundle_build(args, log_prefix="render")
    if code != 0:
        return code
    return run_splat(args)


def run_splat(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2

    repo = _repo_root()
    job_path = _resolve_job_path(Path(args.preset_yaml)).expanduser().resolve()
    if not job_path.is_file():
        print(f"Preset file not found: {job_path}", file=sys.stderr)
        return 2

    try:
        job = load_preset(job_path)
    except Exception as e:
        print(f"Invalid preset YAML: {e}", file=sys.stderr)
        return 2

    preset_id = resolved_preset_slug(job_path)
    try:
        _first = next(iter(job.sites.values()))
        _ = preset_to_request(job, _first.lat, _first.lon)
    except ValueError as e:
        print(f"Invalid preset or RF parameters: {e}", file=sys.stderr)
        return 2

    sites_items = list(job.sites.items())
    total = len(job.sites)
    if args.limit is not None:
        if args.limit == 2 and total >= 2:
            sites_items = _closest_pair(sites_items)
            a, b = sites_items[0][0], sites_items[1][0]
            print(
                f"Limit: 2 sites chosen as closest pair ({a!r} ↔ {b!r}); {total} site(s) in preset",
                flush=True,
            )
        else:
            sites_items = _sites_closest_to_centroid(sites_items, limit=args.limit)
            print(
                f"Limit: processing {len(sites_items)} of {total} site(s) nearest preset centroid",
                flush=True,
            )

    site_order = [slug for slug, _ in sites_items]

    try:
        job = load_preset(job_path)
    except Exception as e:
        print(f"Invalid preset YAML: {e}", file=sys.stderr)
        return 2

    total = len(job.sites)
    sites_items = []
    for slug in site_order:
        ent = job.sites.get(slug)
        if ent is None:
            print(f"Preset no longer contains site {slug!r}", file=sys.stderr)
            return 2
        sites_items.append((slug, ent))

    try:
        first = sites_items[0][1]
        _ = preset_to_request(job, float(first.lat), float(first.lon))
    except ValueError as e:
        print(f"Invalid preset or RF parameters: {e}", file=sys.stderr)
        return 2

    tile_cache_host = _ensure_tile_cache_dir()

    image = resolved_coverage_image(job)
    dockerfile_name = resolved_coverage_dockerfile(job)
    pool_workers = resolved_coverage_dispatcher_max_workers(job)
    print(
        f"Coverage provider: {job.simulation.provider.value}  image={image!r}  dockerfile={dockerfile_name}"
        f"  simulation.verbose={job.simulation.verbose}"
        f"  dispatcher_max_workers={pool_workers}",
        flush=True,
    )
    if job.bundle is None:
        print(
            "SPLAT requires a preset with bundle.* (AOI / land-use); "
            "propagation workspaces live under $PEAKY_CACHE/viewsheds/<digest>/ (sibling to bundles/).",
            file=sys.stderr,
        )
        return 2

    from peaky_finders.bundle_build import (
        bundle_directory_for_preset,
        bundle_eligible_land_use_gpkg,
        bundle_kml_overlay_inputs_digest,
        bundle_land_use_inputs_digest,
        require_bundle_config,
    )
    from peaky_finders.viewshed_cache import resolved_viewshed_workdir, viewshed_workspace_digest

    bundle_cache_root = _bundle_render_cache_root(args)

    bundle_bb_data_dir = _bundle_render_data_dir(args, job_path, job)

    bundle_dir = bundle_directory_for_preset(
        preset_path=job_path,
        data_dir=bundle_bb_data_dir,
        cache_root=bundle_cache_root,
    )
    if bundle_dir is None:
        print("Could not resolve bundle directory for preset.", file=sys.stderr)
        return 2

    plc_for_pairwise_digest = require_bundle_config(job)
    pairwise_bundle_overlay_digest = bundle_kml_overlay_inputs_digest(plc_for_pairwise_digest)
    pairwise_bundle_land_digest = bundle_land_use_inputs_digest(
        plc_for_pairwise_digest, bundle_bb_data_dir
    )

    kml_overlay = job.bundle.kml_overlay if job.bundle else None
    viewshed_cov_style = resolved_viewshed_coverage_kml_style(kml_overlay)
    mesh_pairwise_style = resolved_mesh_pairwise_kml_style(kml_overlay)
    mesh_pairwise_eligible_style = resolved_mesh_pairwise_eligible_kml_style(kml_overlay)
    mesh_pairwise_peak_pin_style = resolved_mesh_pairwise_peak_pin_kml_style(kml_overlay)
    mesh_pairwise_eligible_peak_pin_style = resolved_mesh_pairwise_eligible_peak_pin_kml_style(
        kml_overlay
    )
    max_raster_dim = 4096
    emit_pairwise_dem_peak = True
    if job.bundle and job.bundle.mesh_coverage is not None:
        max_raster_dim = job.bundle.mesh_coverage.max_raster_dimension
        emit_pairwise_dem_peak = job.bundle.mesh_coverage.pairwise_dem_peak_pin

    viewshed_root = resolved_viewshed_cache_root(bundle_cache_root)
    viewshed_root.mkdir(parents=True, exist_ok=True)
    print(f"Viewshed cache root: {viewshed_root}", flush=True)
    pairwise_geom_root = resolved_mesh_pairwise_geometry_cache_root(bundle_cache_root)
    pairwise_geom_root.mkdir(parents=True, exist_ok=True)
    print(f"Pairwise footprint geometry cache: {pairwise_geom_root}", flush=True)
    mesh_depth_geom_root = resolved_mesh_depth_cache_root(bundle_cache_root)
    mesh_depth_geom_root.mkdir(parents=True, exist_ok=True)
    print(f"Mesh depth geometry cache: {mesh_depth_geom_root}", flush=True)
    eligible_union_root = resolved_eligible_union_cache_root(bundle_cache_root)
    eligible_union_root.mkdir(parents=True, exist_ok=True)
    print(f"Eligible union cache: {eligible_union_root}", flush=True)
    provider = job.simulation.provider
    cov_verbose = job.simulation.verbose

    digest_order: list[str] = []
    digest_site_groups: dict[str, list[tuple[str, SiteEntry]]] = {}
    slug_to_digest: dict[str, str] = {}

    for site_slug, site in sites_items:
        lat, lon = float(site.lat), float(site.lon)
        req = preset_to_request(job, lat, lon)
        vd = viewshed_workspace_digest(request=req)
        slug_to_digest[site_slug] = vd
        if vd not in digest_site_groups:
            digest_order.append(vd)
            digest_site_groups[vd] = []
        digest_site_groups[vd].append((site_slug, site))

    tx_antenna_agl_m = max(1.0, float(job.simulation.transmitter["height_m"]))

    overlays: list[kml_bundle.AggregateSiteOverlay] = []
    png_paths: list[Path] = []
    coverage_kml_paths: list[Path | None] = []
    coverage_gpkg_paths: list[Path] = []
    coverage_gpkg_by_slug: dict[str, Path] = {}

    workspace_dirs: dict[str, Path] = {}
    for vd in digest_order:
        _rep_slug, rep_site = digest_site_groups[vd][0]
        data_dir = resolved_viewshed_workdir(workspace_digest=vd, viewshed_root=viewshed_root)
        data_dir.mkdir(parents=True, exist_ok=True)
        req = preset_to_request(job, float(rep_site.lat), float(rep_site.lon))
        (data_dir / "request.json").write_text(
            req.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )
        workspace_dirs[vd] = data_dir

    if job.build_docker:
        needs_docker_image = any(
            coverage_docker_run_needed(data_dir=workspace_dirs[vd], force_splat=args.force_splat)
            for vd in digest_order
        )
        if needs_docker_image:
            code = _docker_build(
                repo,
                dockerfile_name=dockerfile_name,
                image=image,
                context=resolved_coverage_docker_context(job),
            )
            if code != 0:
                return code
        else:
            print(
                f"Skipping Docker image build for {image!r} (all propagation workspaces cached)",
                flush=True,
            )

    dispatcher = SplatDispatcher(max_workers=pool_workers)
    futures_by_digest: list[tuple[str, Future[SplatSiteResult]]] = []
    for vd in digest_order:
        _rep_slug, rep_site = digest_site_groups[vd][0]
        data_dir = workspace_dirs[vd]
        n_grp = len(digest_site_groups[vd])
        site_label = (
            rep_site.name.strip()
            if n_grp == 1
            else f"{rep_site.name.strip()} (+{n_grp - 1} preset site(s), same propagation key)"
        )
        futures_by_digest.append(
            (
                vd,
                dispatcher.submit(
                    SplatSiteJob(
                        site_name=site_label,
                        image=image,
                        provider=provider,
                        data_dir=data_dir,
                        tile_cache_dir=tile_cache_host,
                        force_splat=args.force_splat,
                        coverage_kml_style=viewshed_cov_style,
                        coverage_verbose=cov_verbose,
                    )
                ),
            )
        )

    vd_result: dict[str, SplatSiteResult] = {}
    print("Recomputing overlays and KMZ...", flush=True)
    n_jobs = len(futures_by_digest)
    try:
        for jidx, (vd, fut) in enumerate(futures_by_digest, start=1):
            print(
                f"Sync propagation {jidx}/{n_jobs}: {digest_site_groups[vd][0][1].name.strip()} …",
                flush=True,
            )
            result = fut.result()
            vd_result[vd] = result
            code = result.exit_code
            if code != 0:
                return code
    finally:
        dispatcher.close()

    for idx, (site_slug, site) in enumerate(sites_items, start=1):
        print(f"Assemble site layer {idx}/{len(sites_items)}: {site.name.strip()}…", flush=True)
        vd = slug_to_digest[site_slug]
        result = vd_result[vd]
        data_dir = result.data_dir

        manifest_path = data_dir / "manifest.json"
        bounds = kml_bundle.load_bounds_from_manifest(manifest_path)
        if bounds is None:
            kml_raw = data_dir / "output.kml"
            if not kml_raw.is_file():
                print(f"No output.kml for site {site.name!r}", file=sys.stderr)
                return 1
            bounds = kml_bundle.parse_lat_lon_box(kml_raw.read_bytes())
        png_disk = data_dir / "splat.png"
        if not png_disk.is_file():
            print(f"Missing splat.png for site {site.name!r}", file=sys.stderr)
            return 1

        pin_lat, pin_lon = float(site.lat), float(site.lon)
        elev_f = float(site.elevation_m) if site.elevation_m is not None else None
        folder_name = site.name.strip()

        cov_href = (
            viewshed_polygon_coverage_kml_arcname(site_slug) if result.polygons_written else None
        )
        cov_disk = (data_dir / COVERAGE_KML_NAME) if result.polygons_written else None

        overlays.append(
            kml_bundle.AggregateSiteOverlay(
                slug=site_slug,
                folder_name=folder_name,
                overlay_href=viewshed_raster_png_arcname(site_slug),
                north=bounds["north"],
                south=bounds["south"],
                east=bounds["east"],
                west=bounds["west"],
                rotation=float(bounds.get("rotation", 0.0)),
                center_lat=pin_lat,
                center_lon=pin_lon,
                antenna_height_agl_m=tx_antenna_agl_m,
                pin_description=_site_pin_parts(
                    pin_lat=pin_lat,
                    pin_lon=pin_lon,
                    elevation_m=elev_f,
                ),
                rationale=site.rationale,
                site_description=site.description,
                plss=site.plss,
                mlrs=site.mlrs,
                coverage_kml_href=cov_href,
            )
        )
        png_paths.append(png_disk.resolve())
        coverage_kml_paths.append(cov_disk)
        coverage_gpkg_by_slug[site_slug] = data_dir / COVERAGE_GPKG_NAME
        if result.polygons_written:
            gp = data_dir / COVERAGE_GPKG_NAME
            if gp.is_file():
                coverage_gpkg_paths.append(gp.resolve())

    print("Aggregate KMZ: attaching bundle layers...", flush=True)
    bundle_nets, bundle_zpairs, reference_bundle_links = _bundle_land_use_layers_for_kmz(
        bundle_dir, preset=job
    )

    doc_title = preset_id.replace("-", " ")
    overlay_opacity_pct = kml_bundle.overlay_opacity_pct_from_display_transparency(job.display)

    with tempfile.TemporaryDirectory() as overlap_td:
        overlap_dir = Path(overlap_td)
        mesh_depth_plain: list[tuple[str, str, Path, str, str]] = []
        mesh_depth_elig: list[tuple[str, str, Path, str, str]] = []
        pairwise_layers: list[tuple[str, Path, str]] = []
        eligible_pairwise_layers: list[tuple[str, Path, str]] = []
        if len(coverage_gpkg_paths) >= 2:
            nf = len(coverage_gpkg_paths)
            npairs = nf * (nf - 1) // 2
            print(
                f"Aggregate KMZ: mesh pairwise overlaps ({nf} footprints → {npairs} pairs) + depth bands…",
                flush=True,
            )
            footprint_rows = list(
                zip(coverage_gpkg_paths, overlays, strict=True),
            )
            footprints_arg = [(gp, o.slug, o.folder_name) for gp, o in footprint_rows]
            eligible_gpkg_path = bundle_eligible_land_use_gpkg(bundle_dir)
            eligible_sha: str | None = None
            try:
                from peaky_finders.bundle_clips import (
                    bundle_resolve_path,
                    eligible_gpkg_from_bundle_dir,
                    read_bundle_resolve,
                )

                if bundle_resolve_path(bundle_dir).is_file():
                    resolve = read_bundle_resolve(bundle_dir)
                    if eligible_gpkg_path.resolve() == eligible_gpkg_from_bundle_dir(bundle_dir).resolve():
                        sha = resolve.get("eligible")
                        if isinstance(sha, str) and sha:
                            eligible_sha = sha
            except (OSError, ValueError, KeyError):
                pass
            eligible_union = read_eligible_land_use_union(
                eligible_gpkg_path,
                cache_root=eligible_union_root,
                eligible_sha=eligible_sha,
            )
            emit_eligible = eligible_union is not None and not eligible_union.is_empty
            mesh_cov = job.bundle.mesh_coverage if job.bundle is not None else None
            depth_workers = mesh_cov.mesh_depth_workers if mesh_cov is not None else 8
            mesh_depth_plain, mesh_depth_elig = write_mesh_depth_kml_layers(
                footprints=footprints_arg,
                kml_overlay=kml_overlay,
                scratch_depth_dir=overlap_dir / "mesh_depth",
                scratch_depth_eligible_dir=(
                    overlap_dir / "mesh_depth_eligible" if emit_eligible else None
                ),
                eligible_ll=eligible_union if emit_eligible else None,
                max_raster_dimension=max_raster_dim,
                mesh_depth_workers=depth_workers,
                geometry_cache_root=mesh_depth_geom_root,
                slug_to_viewshed_digest=slug_to_digest,
                force_mesh_depth_geometry=args.force_mesh_depth_geometry,
                bundle_kml_overlay_digest=pairwise_bundle_overlay_digest,
                bundle_land_use_inputs_digest=(
                    pairwise_bundle_land_digest if emit_eligible else None
                ),
            )
            pair_workers = mesh_cov.pairwise_overlap_workers if mesh_cov is not None else 8
            pairwise_layers, eligible_pairwise_layers = write_pairwise_link_overlap_kml_pairs(
                footprints=footprints_arg,
                emit_plain=True,
                link_scratch_dir=overlap_dir / "mesh_pairwise",
                link_polygon_style=mesh_pairwise_style,
                emit_eligible=emit_eligible,
                eligible_scratch_dir=(
                    overlap_dir / "mesh_pairwise_eligible" if emit_eligible else None
                ),
                eligible_polygon_style=(
                    mesh_pairwise_eligible_style if emit_eligible else None
                ),
                eligible_ll=eligible_union if emit_eligible else None,
                dem_mirror_root=tile_cache_host if emit_pairwise_dem_peak else None,
                emit_dem_peak_pins=emit_pairwise_dem_peak,
                pairwise_peak_pin_style=mesh_pairwise_peak_pin_style,
                eligible_peak_pin_style=mesh_pairwise_eligible_peak_pin_style,
                pairwise_overlap_workers=pair_workers,
                geometry_cache_root=pairwise_geom_root,
                slug_to_viewshed_digest=slug_to_digest,
                force_pairwise_geometry=args.force_pairwise_geometry,
                bundle_kml_overlay_digest=pairwise_bundle_overlay_digest,
                bundle_land_use_inputs_digest=(
                    pairwise_bundle_land_digest if emit_eligible else None
                ),
            )

        mesh_edges_disk: Path | None = None
        mesh_edges_href: str | None = None
        sl_kml = overlap_dir / "site_to_site.kml"
        if write_site_links_kml(
            coverage_gpkg_by_slug=coverage_gpkg_by_slug, sites=overlays, out_kml=sl_kml
        ):
            mesh_edges_disk = sl_kml
            mesh_edges_href = mesh_edges_site_to_site_kml_arcname()

        mesh_kml_writes: list[tuple[Path, str]] = []
        mesh_kml_writes.extend(
            (p, a) for _band, _ttl, p, a, _v in mesh_depth_plain if p.is_file()
        )
        mesh_kml_writes.extend(
            (p, a) for _band, _ttl, p, a, _v in mesh_depth_elig if p.is_file()
        )
        mesh_kml_writes.extend((p, a) for _ttl, p, a in pairwise_layers if p.is_file())
        mesh_kml_writes.extend((p, a) for _ttl, p, a in eligible_pairwise_layers if p.is_file())
        if mesh_edges_disk is not None and mesh_edges_disk.is_file():
            mesh_kml_writes.append((mesh_edges_disk, mesh_edges_site_to_site_kml_arcname()))

        kml_xml = kml_bundle.build_aggregate_document_kml(
            document_title=doc_title,
            sites=overlays,
            overlay_opacity_pct=overlay_opacity_pct,
            bundle_network_links=bundle_nets,
            reference_bundle_links=reference_bundle_links,
            mesh_edges_href=mesh_edges_href,
            mesh_depth_network_links=[
                (b, ttl, arc, vis)
                for b, ttl, p, arc, vis in mesh_depth_plain
                if p.is_file()
            ],
            mesh_depth_eligible_network_links=[
                (b, ttl, arc, vis)
                for b, ttl, p, arc, vis in mesh_depth_elig
                if p.is_file()
            ],
            mesh_pairwise_network_links=[
                (ttl, arc) for ttl, p, arc in pairwise_layers if p.is_file()
            ],
            mesh_pairwise_eligible_network_links=[
                (ttl, arc) for ttl, p, arc in eligible_pairwise_layers if p.is_file()
            ],
            layer_visibility=resolved_kmz_document_layers(job.bundle),
        )

        kmz_path = resolved_aggregate_kmz_path(job_path)
        print(f"Aggregate KMZ: writing zip ({kmz_path.name})...", flush=True)
        _write_aggregate_kmz(
            kmz_path=kmz_path,
            kml_xml=kml_xml,
            bundle_zpairs=bundle_zpairs,
            overlays=overlays,
            png_paths=png_paths,
            coverage_kml_paths=coverage_kml_paths,
            mesh_kml_writes=mesh_kml_writes,
            viewshed_coverage_style=viewshed_cov_style,
        )

    print(f"Wrote {kmz_path}", flush=True)
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = splat_cli_parser()
    args = parser.parse_args(argv)
    return run_splat(args)


def cli() -> None:
    sys.exit(run())


if __name__ == "__main__":
    sys.exit(run())
