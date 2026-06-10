"""Granular ``peaky bundle *`` implementations for Make-driven builds."""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely import make_valid

from peaky_finders.bundle_build import (
    ELIGIBLE_LAND_USE_LAYER,
    _file_tree_mtime_size_fingerprint,
    _flatten_gdb_layer_jobs,
    _kml_label_aoi_clip,
    _kml_label_gdb_job,
    aoi_inputs_fingerprint_body,
    bundle_paths,
    effective_skadi_prefetch_workers,
    load_composite_aoi_polygon,
    require_land_config,
)
from peaky_finders.bundle_clips import (
    ClipRole,
    build_clip_layer,
    build_composite_aoi,
    build_composite_exclude,
    build_composite_include,
    build_eligible_workspace,
    build_reference_entry,
    bundle_resolve_path,
    composite_union_gpkg,
    plan_clip_build_result,
    plan_reference_entries,
    write_bundle_resolve,
)
from peaky_finders.plss_fetch import refresh_plss_for_bundle
from peaky_finders.skadi_dem import (
    fetch_skadi_hgt_tile_always,
    iter_skadi_tile_names_for_wgs84_bounds,
    prefetch_skadi_hgt_for_bounds_fatal,
)
from peaky_finders.sites_job import (
    load_preset,
    resolved_bundle_dir,
    resolved_preset_build_dir,
    resolved_preset_bundle_data_dir,
    resolved_preset_clips_dir,
    ensure_skadi_mirror_dir,
    require_cwd_config_yaml,
)

_ROLES_CLIP: tuple[ClipRole, ...] = ("aoi", "include", "exclude")


def _bundle_context(preset_path: Path) -> tuple[Path, object, object, Path, Path, Path, str]:
    preset_path = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path)
    plc = require_land_config(preset)
    data_dir = resolved_preset_bundle_data_dir(preset_path=preset_path, preset=preset)
    cache_root = resolved_bundle_dir(preset_path=preset_path)
    clips_root = resolved_preset_clips_dir(preset_path)
    mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
    return preset_path, preset, plc, data_dir, cache_root, clips_root, mask_body


def run_bundle_clip(role: str, layer: str, preset_path: Path) -> int:
    if role not in _ROLES_CLIP:
        print(f"bundle clip: unsupported role {role!r}", file=sys.stderr)
        return 2
    rr: ClipRole = role  # type: ignore[assignment]
    _preset_path, preset, plc, data_dir, _cache_root, clips_root, mask_body = _bundle_context(preset_path)
    _ = preset
    kml_overlay = preset.display.kml
    gdb_fp = _file_tree_mtime_size_fingerprint

    grp = plc.aoi if rr == "aoi" else plc.include if rr == "include" else plc.exclude
    matched = [(pp, resolved, ln, w) for pp, resolved, ln, w in _flatten_gdb_layer_jobs(grp, data_dir) if ln == layer]
    if not matched:
        print(f"bundle clip: no {rr} GDB layer named {layer!r}", file=sys.stderr)
        return 2
    if len(matched) > 1:
        cand = "; ".join(f"{pp}" for pp, *_ in matched)
        print(f"bundle clip: ambiguous layer name {layer!r} — matches: {cand}", file=sys.stderr)
        return 2

    preset_rel, resolved, layer_name, where = matched[0]
    if not resolved.exists():
        print(f"bundle clip: GDB not found for {preset_rel}: {resolved}", file=sys.stderr)
        return 2

    if rr == "aoi":
        aoi_poly_full = make_valid(load_composite_aoi_polygon(plc, data_dir))
        build_clip_layer(
            clips_root,
            role="aoi",
            preset_path=preset_rel,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
            aoi_poly_4326=aoi_poly_full,
            kml_label=_kml_label_aoi_clip(preset_rel, layer_name),
            kml_overlay=kml_overlay,
            store_crs_3857=False,
            verbose_log=None,
            progress_log=None,
        )
        return 0

    clip_meta = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    aoi_gpkg = composite_union_gpkg(clips_root, "aoi", clip_meta.aoi_sha)
    if not aoi_gpkg.is_file():
        print(
            "bundle clip: AOI composite GeoPackage missing; run bundle composite aoi first.",
            file=sys.stderr,
        )
        return 2
    aoi_poly = make_valid(gpd.read_file(aoi_gpkg, layer="aoi").geometry.iloc[0])

    build_clip_layer(
        clips_root,
        role=rr,
        preset_path=preset_rel,
        resolved=resolved,
        layer=layer_name,
        where=where,
        mask_body=mask_body,
        gdb_tree=gdb_fp(resolved),
        aoi_poly_4326=aoi_poly,
        kml_label=_kml_label_gdb_job(preset_rel, layer_name),
        kml_overlay=kml_overlay,
        store_crs_3857=rr == "include",
        verbose_log=None,
        progress_log=None,
    )
    return 0


def run_bundle_composite(which: str, preset_path: Path) -> int:
    allowed = {"aoi", "include", "exclude"}
    if which not in allowed:
        print(f"bundle composite: expected one of {sorted(allowed)}; got {which!r}", file=sys.stderr)
        return 2
    _preset_path, _preset, plc, data_dir, _cr, clips_root, mask_body = _bundle_context(preset_path)
    kml_overlay = preset.display.kml
    builders = {
        "aoi": build_composite_aoi,
        "include": build_composite_include,
        "exclude": build_composite_exclude,
    }
    builders[which](
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=kml_overlay,
    )
    print(f"bundle composite {which}: built", flush=True)
    return 0


def run_bundle_eligible(preset_path: Path, *, verbose: bool = False) -> int:
    _preset_path, preset, plc, data_dir, _cr, clips_root, mask_body = _bundle_context(preset_path)
    vlog = (lambda msg: print(f"bundle eligible: {msg}", flush=True)) if verbose else None
    build_eligible_workspace(
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=preset.display.kml,
        verbose_log=vlog,
        progress_log=vlog,
    )
    print("bundle eligible: built eligible workspace", flush=True)
    return 0


def run_bundle_reference(entry_id: str, preset_path: Path) -> int:
    _preset_path, preset, plc, data_dir, _cache_root, clips_root, _mask_body = _bundle_context(preset_path)
    if not plc.reference:
        print("bundle reference: preset has no bundle.reference entries", file=sys.stderr)
        return 2

    clip_meta = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    try:
        build_reference_entry(
            entry_id=entry_id.strip(),
            plc=plc,
            data_dir=data_dir,
            clips_root=clips_root,
            aoi_sha=clip_meta.aoi_sha,
            kml_overlay=preset.display.kml,
        )
    except KeyError as e:
        print(f"bundle reference: {e}", file=sys.stderr)
        return 2
    print(f"bundle reference {entry_id.strip()}: built", flush=True)
    return 0


def run_bundle_resolve(preset_path: Path) -> int:
    preset_path_r, preset, plc, data_dir, cache_root, clips_root, _mask_body = _bundle_context(preset_path)

    clip_meta = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    refs = plan_reference_entries(plc=plc, data_dir=data_dir, clips_root=clips_root)
    bundle_dir, _eligible = bundle_paths(cache_root, preset=preset, data_dir=data_dir)
    write_bundle_resolve(
        bundle_dir,
        clips_root=clips_root,
        aoi_sha=clip_meta.aoi_sha,
        include_sha=clip_meta.include_sha,
        exclude_sha=clip_meta.exclude_sha,
        eligible_sha=clip_meta.eligible_sha,
        reference=refs,
    )
    print(f"bundle resolve: {bundle_resolve_path(bundle_dir)}", flush=True)
    return 0


def run_bundle_plss(preset_path: Path) -> int:
    preset_path_r = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path_r)
    refresh_plss_for_bundle(
        preset_path=preset_path_r,
        cache_base=resolved_preset_build_dir(preset_path_r),
        preset=preset,
    )
    preset = load_preset(preset_path_r)
    print("bundle plss: preset PLSS refreshed under build/", flush=True)
    return 0


def run_bundle_dem(preset_path: Path, *, tile: str | None = None, verbose: bool = False) -> int:
    _preset_path_r, _preset, plc, data_dir, _cache_root, clips_root, _mb = _bundle_context(preset_path)

    clip_meta = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    eligible_gpkg = clip_meta.eligible_gpkg
    if not eligible_gpkg.is_file():
        print(
            "bundle dem: eligible land-use GeoPackage missing; run bundle eligible first.",
            file=sys.stderr,
        )
        return 2

    splat_tile_dir = ensure_skadi_mirror_dir()

    loaded = gpd.read_file(eligible_gpkg, layer=ELIGIBLE_LAND_USE_LAYER)
    if loaded.empty or loaded.geometry.is_empty.all():
        print("bundle dem: empty eligible land geometry — nothing to fetch", flush=True)
        return 0
    minx, miny, maxx, maxy = map(float, loaded.total_bounds)

    prefetch_workers = effective_skadi_prefetch_workers(None)
    tiles = tuple(iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy))
    if tile is not None:
        stem = str(tile).strip()
        if stem.endswith(".hgt.gz"):
            stem = stem[: -len(".hgt.gz")]
        want = f"{stem}.hgt.gz"
        if want not in tiles:
            print(
                f"bundle dem: tile {want!r} not in eligible bbox tile set ({len(tiles)} tiles).",
                file=sys.stderr,
            )
            return 2
        fetch_skadi_hgt_tile_always(want, splat_tile_dir)
        print(f"bundle dem: Skadi tile {want} → {splat_tile_dir}", flush=True)
        return 0

    vlog = (lambda msg: print(msg, flush=True)) if verbose else None
    n_tiles, n_downloaded, n_skipped = prefetch_skadi_hgt_for_bounds_fatal(
        minx=minx,
        miny=miny,
        maxx=maxx,
        maxy=maxy,
        splat_tile_cache_dir=splat_tile_dir,
        max_workers=prefetch_workers,
        verbose_log=vlog,
    )
    print(
        "bundle dem: Skadi prefetch "
        f"(tiles={n_tiles} downloaded={n_downloaded} cached={n_skipped} mirror={splat_tile_dir})",
        flush=True,
    )
    return 0


def run_bundle_entry(args: object) -> int:
    cmd = getattr(args, "bundle_cmd", None)
    if cmd is None:
        print("internal error: missing bundle_cli fields", file=sys.stderr)
        return 2
    try:
        p = require_cwd_config_yaml()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    if cmd == "clip":
        return run_bundle_clip(args.role, args.layer, p)
    if cmd == "composite":
        return run_bundle_composite(args.role, p)
    if cmd == "eligible":
        return run_bundle_eligible(p)
    if cmd == "reference":
        return run_bundle_reference(args.reference_id, p)
    if cmd == "resolve":
        return run_bundle_resolve(p)
    if cmd == "plss":
        return run_bundle_plss(p)
    if cmd == "dem":
        tile = getattr(args, "dem_tile", None)
        return run_bundle_dem(p, tile=tile)
    print(f"bundle: unknown subcommand {cmd!r}", file=sys.stderr)
    return 2
