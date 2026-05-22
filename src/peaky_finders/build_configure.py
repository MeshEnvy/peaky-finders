"""Configure-time planning: preset → concrete Makefile artifact paths."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import geopandas as gpd

from peaky_finders.bundle_build import (
    _clip_stem,
    _flatten_gdb_layer_jobs,
    bundle_paths,
    require_bundle_config,
)
from peaky_finders.bundle_clips import (
    MANIFEST_BASENAME,
    ELIGIBLE_SLICE_LAYERS_SUBDIR,
    ELIGIBLE_WORKSPACE_MANIFEST_NAME,
    PlannedClipLayer,
    bundle_resolve_path,
    composite_union_gpkg,
    composite_workspace_dir,
    eligible_gpkg_path,
    eligible_land_use_workspace_dir,
    plan_clip_layer_jobs,
    plan_reference_entries,
    reference_gpkg_path,
)
from peaky_finders.eligible_union_store import (
    COMPLETE_JSON as ELIGIBLE_UNION_COMPLETE,
    resolved_eligible_union_data_dir,
)
from peaky_finders.mesh_depth_store import (
    COMPLETE_JSON as MESH_DEPTH_COMPLETE,
    resolved_mesh_depth_set_dir,
)
from peaky_finders.mesh_pairwise_store import (
    COMPLETE_JSON as MESH_PAIRWISE_COMPLETE,
    resolved_mesh_pairwise_pair_dir,
)
from peaky_finders.path_labels import mesh_depth_network_rel_dir
from peaky_finders.preset_stamps import list_stamp_sections
from peaky_finders.plss_mlrs_fetch import plss_mlrs_loc_cache_path
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import (
    Preset,
    load_preset,
    resolved_aggregate_kmz_path,
    resolved_preset_build_dir,
    resolved_preset_clips_dir,
    resolved_preset_dem_tile_cache_dir,
    resolved_bundle_dir,
    resolved_eligible_union_build_dir,
    resolved_mesh_depth_dir,
    resolved_mesh_pairwise_dir,
    resolved_preset_bundle_data_dir,
    resolved_preset_slug,
    resolved_viewshed_dir,
)
from peaky_finders.skadi_dem import iter_skadi_tile_names_for_wgs84_bounds, skadi_mirror_tile_gz_path
from peaky_finders.splat_polygonize import COVERAGE_GPKG_NAME, SPLAT_OUTPUT_PPM_BASENAME
from peaky_finders.viewshed_workspace import (
    propagation_digest_to_workspace_master_slug,
    resolved_viewshed_workdir,
    viewshed_workspace_digest,
)


class ConfigureError(Exception):
    """Preset or bundle inputs cannot be resolved for configure."""


@dataclass(frozen=True)
class PlannedComposite:
    role: str
    sha: str
    union_gpkg: Path
    manifest: Path


@dataclass(frozen=True)
class PlannedReference:
    entry_id: str
    sha: str
    gpkg: Path


@dataclass(frozen=True)
class PlannedViewshedWorkspace:
    digest: str
    workdir: Path
    output_ppm: Path
    splat_png: Path
    coverage_gpkg: Path
    request_json: Path
    site_slugs: tuple[str, ...]
    rep_slug: str


@dataclass(frozen=True)
class PlannedMeshPair:
    slug_a: str
    slug_b: str
    complete_json: Path


@dataclass(frozen=True)
class BuildConfigurePlan:
    preset_path: Path
    preset_rel: str
    slug: str
    kmz: Path
    peaky_cmd: str
    make_root: Path
    preset_build_root: Path
    clips_root: Path
    bundles_root: Path
    viewsheds_root: Path
    splat_tiles_root: Path
    mesh_pairwise_root: Path
    mesh_depth_root: Path
    eligible_union_root: Path
    plss_cache: Path
    bundle_dir: Path
    bundle_resolve: Path
    clip_layers: tuple[PlannedClipLayer, ...]
    composites: tuple[PlannedComposite, ...]
    references: tuple[PlannedReference, ...]
    dem_tiles: tuple[Path, ...]
    viewshed_workspaces: tuple[PlannedViewshedWorkspace, ...]
    mesh_pairs: tuple[PlannedMeshPair, ...]
    mesh_depth_complete: Path | None
    eligible_union_complete: Path | None
    mesh_links_kml: Path | None
    eligible_slice_kml_paths: tuple[Path, ...]
    has_bundle: bool
    emit_mesh: bool
    stamp_sections: tuple[str, ...]


def _dem_tile_paths_for_eligible_gpkg(eligible_gpkg: Path, splat_tiles_root: Path) -> tuple[Path, ...]:
    if not eligible_gpkg.is_file():
        return ()
    try:
        gdf = gpd.read_file(eligible_gpkg)
    except (OSError, ValueError):
        return ()
    if gdf.empty or gdf.geometry.is_empty.all():
        return ()
    minx, miny, maxx, maxy = map(float, gdf.total_bounds)
    names = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    root = splat_tiles_root.expanduser().resolve()
    return tuple(skadi_mirror_tile_gz_path(root, name) for name in names)


def configure_preset_build(
    *,
    preset_path: Path,
    make_root: Path | None = None,
    peaky_cmd: str = "peaky",
    data_dir: Path | None = None,
) -> BuildConfigurePlan:
    """Walk preset + bundle inputs; resolve digests and on-disk artifact paths.

    ``make_root`` is the anchor for relatives in emitted Makefiles — default ``None``
    uses the preset's parent directory (same cwd as ``make -C <preset_dir`` from ``peaky build``).
    Paths outside the preset directory (clips under ``build/clips``, etc.) may still be emitted
    absolute when they cannot be expressed relative to ``make_root``.
    """
    resolved_preset = preset_path.expanduser().resolve()
    preset = load_preset(resolved_preset)
    root = resolved_preset.parent if make_root is None else make_root.expanduser().resolve()

    preset_build_root = resolved_preset_build_dir(resolved_preset)
    bundles_root = resolved_bundle_dir(preset_path=resolved_preset)
    clips_root = resolved_preset_clips_dir(resolved_preset)
    viewsheds_root = resolved_viewshed_dir(bundles_root)
    splat_tiles_root = resolved_preset_dem_tile_cache_dir(resolved_preset)
    mesh_pairwise_root = resolved_mesh_pairwise_dir(bundles_root)
    mesh_depth_root = resolved_mesh_depth_dir(bundles_root)
    eligible_union_root = resolved_eligible_union_build_dir(bundles_root)
    plss_cache = plss_mlrs_loc_cache_path(preset_build_root)

    slug = resolved_preset_slug(resolved_preset)
    kmz = resolved_aggregate_kmz_path(resolved_preset)

    clip_layers: tuple[PlannedClipLayer, ...] = ()
    composites: tuple[PlannedComposite, ...] = ()
    references: tuple[PlannedReference, ...] = ()
    bundle_dir = bundles_root
    bundle_resolve = bundle_resolve_path(bundles_root)
    dem_tiles: tuple[Path, ...] = ()
    has_bundle = preset.bundle is not None
    eligible_slice_kml_paths: tuple[Path, ...] = ()

    if has_bundle:
        plc = require_bundle_config(preset)
        bb_data_dir = resolved_preset_bundle_data_dir(
            preset_path=resolved_preset,
            preset=preset,
            cli_override=data_dir,
        )
        try:
            clip_layers, clip_meta = plan_clip_layer_jobs(
                plc=plc,
                data_dir=bb_data_dir,
                clips_root=clips_root,
            )
        except FileNotFoundError as e:
            raise ConfigureError(str(e)) from e

        composites = (
            PlannedComposite(
                role="aoi",
                sha=clip_meta.aoi_sha,
                union_gpkg=composite_union_gpkg(clips_root, "aoi", clip_meta.aoi_sha),
                manifest=composite_workspace_dir(clips_root, "aoi") / MANIFEST_BASENAME,
            ),
            PlannedComposite(
                role="include",
                sha=clip_meta.include_sha,
                union_gpkg=composite_union_gpkg(clips_root, "include", clip_meta.include_sha),
                manifest=composite_workspace_dir(clips_root, "include") / MANIFEST_BASENAME,
            ),
            PlannedComposite(
                role="exclude",
                sha=clip_meta.exclude_sha,
                union_gpkg=composite_union_gpkg(clips_root, "exclude", clip_meta.exclude_sha),
                manifest=composite_workspace_dir(clips_root, "exclude") / MANIFEST_BASENAME,
            ),
            PlannedComposite(
                role="eligible",
                sha=clip_meta.eligible_sha,
                union_gpkg=eligible_gpkg_path(clips_root),
                manifest=eligible_land_use_workspace_dir(clips_root) / ELIGIBLE_WORKSPACE_MANIFEST_NAME,
            ),
        )

        ew_layers_root = clips_root / "eligible" / ELIGIBLE_SLICE_LAYERS_SUBDIR
        slice_paths_list: list[Path] = []
        for preset_path_r, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.include, bb_data_dir):
            stem = _clip_stem(preset_path_r, layer_name, where)
            slice_paths_list.append(ew_layers_root / f"{stem}.kml")
        eligible_slice_kml_paths = tuple(slice_paths_list)

        ref_shas = plan_reference_entries(plc=plc, data_dir=bb_data_dir, clips_root=clips_root)
        references = tuple(
            PlannedReference(
                entry_id=entry_id,
                sha=sha,
                gpkg=reference_gpkg_path(clips_root, entry_id),
            )
            for entry_id, sha in sorted(ref_shas.items())
        )

        bundle_dir, _eligible = bundle_paths(
            bundles_root,
            preset=preset,
            data_dir=bb_data_dir,
        )
        bundle_resolve = bundle_resolve_path(bundle_dir)
        dem_tiles = _dem_tile_paths_for_eligible_gpkg(clip_meta.eligible_gpkg, splat_tiles_root)

    digest_to_slugs: dict[str, list[str]] = {}
    try:
        for site_slug, site in preset.sites.items():
            req = preset_to_request(preset, float(site.lat), float(site.lon))
            vd = viewshed_workspace_digest(request=req)
            digest_to_slugs.setdefault(vd, []).append(site_slug)
    except ValueError as e:
        raise ConfigureError(str(e)) from e

    digest_workspace_master_slug = propagation_digest_to_workspace_master_slug(preset)

    viewshed_workspaces: list[PlannedViewshedWorkspace] = []
    for digest, slugs in sorted(digest_to_slugs.items()):
        slugs_sorted = tuple(sorted(slugs))
        master_slug = digest_workspace_master_slug[digest]
        workdir = resolved_viewshed_workdir(
            canonical_site_slug=master_slug,
            viewshed_root=viewsheds_root,
        )
        viewshed_workspaces.append(
            PlannedViewshedWorkspace(
                digest=digest,
                workdir=workdir,
                output_ppm=workdir / SPLAT_OUTPUT_PPM_BASENAME,
                splat_png=workdir / "splat.png",
                coverage_gpkg=workdir / COVERAGE_GPKG_NAME,
                request_json=workdir / "request.json",
                site_slugs=slugs_sorted,
                rep_slug=master_slug,
            )
        )

    site_slugs = tuple(preset.sites.keys())
    emit_mesh = len(site_slugs) >= 2 and has_bundle
    mesh_pairs: tuple[PlannedMeshPair, ...] = ()
    mesh_depth_complete: Path | None = None
    eligible_union_complete: Path | None = None
    mesh_links_kml: Path | None = None

    if emit_mesh:
        mesh_pairs = tuple(
            PlannedMeshPair(
                slug_a=a,
                slug_b=b,
                complete_json=resolved_mesh_pairwise_pair_dir(
                    slug_a=a, slug_b=b, cache_root=mesh_pairwise_root
                )
                / MESH_PAIRWISE_COMPLETE,
            )
            for a, b in combinations(site_slugs, 2)
        )

        max_raster = 4096
        if preset.bundle is not None and preset.bundle.mesh_coverage is not None:
            max_raster = preset.bundle.mesh_coverage.max_raster_dimension
        depth_rel = mesh_depth_network_rel_dir(max_raster_dimension=max_raster)
        mesh_depth_complete = (
            resolved_mesh_depth_set_dir(rel_label=depth_rel, cache_root=mesh_depth_root)
            / MESH_DEPTH_COMPLETE
        )

        if has_bundle and clip_layers:
            eligible_union_complete = (
                resolved_eligible_union_data_dir(eligible_union_root) / ELIGIBLE_UNION_COMPLETE
            )

        mesh_links_kml = preset_build_root / "mesh/links/site_to_site.kml"

    preset_rel = _path_for_makefile(resolved_preset, make_root=root)
    stamp_sections = tuple(list_stamp_sections(preset))

    return BuildConfigurePlan(
        preset_path=resolved_preset,
        preset_rel=preset_rel,
        slug=slug,
        kmz=kmz,
        peaky_cmd=peaky_cmd,
        make_root=root,
        preset_build_root=preset_build_root,
        clips_root=clips_root,
        bundles_root=bundles_root,
        viewsheds_root=viewsheds_root,
        splat_tiles_root=splat_tiles_root,
        mesh_pairwise_root=mesh_pairwise_root,
        mesh_depth_root=mesh_depth_root,
        eligible_union_root=eligible_union_root,
        plss_cache=plss_cache,
        bundle_dir=bundle_dir,
        bundle_resolve=bundle_resolve,
        clip_layers=clip_layers,
        composites=composites,
        references=references,
        dem_tiles=dem_tiles,
        viewshed_workspaces=tuple(viewshed_workspaces),
        mesh_pairs=mesh_pairs,
        mesh_depth_complete=mesh_depth_complete,
        eligible_union_complete=eligible_union_complete,
        mesh_links_kml=mesh_links_kml,
        eligible_slice_kml_paths=eligible_slice_kml_paths,
        has_bundle=has_bundle,
        emit_mesh=emit_mesh,
        stamp_sections=stamp_sections,
    )


def _path_for_makefile(path: Path, *, make_root: Path) -> str:
    resolved = path.expanduser().resolve()
    root = make_root.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def make_var_token(raw: str, *, max_len: int = 48) -> str:
    """Stable Make-safe identifier fragment."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").upper()
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or "X"
