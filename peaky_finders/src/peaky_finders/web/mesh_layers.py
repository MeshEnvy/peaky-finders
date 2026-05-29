"""Derived mesh coverage layers (pairwise, depth bands) for the web Layers tab."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal

LayerDisplayMode = Literal["off", "eligible", "all"]

import geopandas as gpd
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from peaky_finders.bundle_build import bundle_land_use_inputs_digest
from peaky_finders.bundle_clips import eligible_gpkg_path, read_bundle_resolve
from peaky_finders.link_overlap import (
    _clip_plain_link_overlap_to_eligible,
    read_eligible_land_use_union,
    write_pairwise_eligible_link_overlap_layers,
    write_pairwise_link_overlap_layers,
)
from peaky_finders.mesh_coverage_depth import MESH_DEPTH_BANDS, MESH_DEPTH_DISPLAY, write_mesh_depth_kml_layers
from peaky_finders.mesh_depth_store import BAND_GPKG, BAND_LAYER, EMPTY_SENTINEL, MESH_DEPTH_BAND_IDS
from peaky_finders.mesh_pairwise_store import OVERLAP_GPKG, OVERLAP_LAYER
from peaky_finders.path_labels import mesh_pairwise_rel_dir
from peaky_finders.path_labels import mesh_depth_network_rel_dir
from peaky_finders.preset_overlays import collect_site_workspace_assets
from peaky_finders.sites_job import (
    Preset,
    SiteEntry,
    ensure_skadi_mirror_dir,
    load_preset,
    resolved_bundle_dir,
    resolved_kmz_document_layers,
    resolved_mesh_depth_band_kml_style,
    resolved_mesh_depth_dir,
    resolved_mesh_pairwise_dir,
    resolved_mesh_pairwise_eligible_kml_style,
    resolved_mesh_pairwise_kml_style,
    resolved_preset_clips_dir,
)
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME


def _preset_path(slug: str) -> Path:
    from peaky_finders.sites_job import peaky_projects_dir

    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def _aabbggrr_to_css_rgba(hex8: str, *, fallback: str) -> str:
    s = str(hex8).strip().lower().removeprefix("#")
    if len(s) != 8:
        return fallback
    try:
        aa = int(s[0:2], 16) / 255.0
        bb = int(s[2:4], 16)
        gg = int(s[4:6], 16)
        rr = int(s[6:8], 16)
        return f"rgba({rr}, {gg}, {bb}, {aa:.3f})"
    except ValueError:
        return fallback


def _kml_style_colors(style) -> dict[str, str]:
    fill = _aabbggrr_to_css_rgba(style.fill or "660000ff", fallback="rgba(255, 0, 0, 0.4)")
    line = _aabbggrr_to_css_rgba(style.line or "ff0000ff", fallback="#ff0000")
    return {"fill": fill, "line": line}


def _gdf_to_geojson(gdf: gpd.GeoDataFrame, *, tolerance: float = 0.0001) -> dict[str, Any]:
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    g = gdf.to_crs("EPSG:4326")
    if tolerance > 0:
        g = g.copy()
        g["geometry"] = g.geometry.simplify(tolerance, preserve_topology=True)
    features = []
    for _, row in g.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {k: v for k, v in row.items() if k != "geometry"}
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
    return {"type": "FeatureCollection", "features": features}


def _geom_to_geojson(geoms: list[BaseGeometry], *, props: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for i, geom in enumerate(geoms):
        if geom is None or geom.is_empty:
            continue
        p = props[i] if props and i < len(props) else {}
        rows.append({"geometry": geom, **p})
    if not rows:
        return {"type": "FeatureCollection", "features": []}
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    return _gdf_to_geojson(gdf)


def _mesh_depth_set_dir(bundle_dir: Path, preset: Preset) -> Path | None:
    mesh_cfg = preset.bundle.mesh_coverage if preset.bundle else None
    max_dim = int(mesh_cfg.max_raster_dimension) if mesh_cfg is not None else 4096
    rel = mesh_depth_network_rel_dir(max_raster_dimension=max_dim)
    root = resolved_mesh_depth_dir(bundle_dir)
    candidate = root / rel
    return candidate if candidate.is_dir() else None


def pairwise_built(bundle_dir: Path) -> bool:
    root = resolved_mesh_pairwise_dir(bundle_dir)
    if not root.is_dir():
        return False
    for pair_dir in root.iterdir():
        if not pair_dir.is_dir():
            continue
        gpkg = pair_dir / OVERLAP_GPKG
        if gpkg.is_file():
            return True
        if (pair_dir / EMPTY_SENTINEL).is_file():
            continue
    return False


def depth_band_built(bundle_dir: Path, preset: Preset, band: str) -> bool:
    set_dir = _mesh_depth_set_dir(bundle_dir, preset)
    if set_dir is None:
        return False
    bdir = set_dir / "bands" / band
    if (bdir / EMPTY_SENTINEL).is_file():
        return False
    return (bdir / BAND_GPKG).is_file()


def depth_built(bundle_dir: Path, preset: Preset) -> bool:
    return any(depth_band_built(bundle_dir, preset, band) for band in MESH_DEPTH_BAND_IDS)


def _load_eligible_geometry(preset_path: Path, bundle_dir: Path) -> BaseGeometry | None:
    clips_root = resolved_preset_clips_dir(preset_path)
    gpkg = eligible_gpkg_path(clips_root)
    if not gpkg.is_file():
        return None
    eligible_sha = None
    try:
        resolve = read_bundle_resolve(bundle_dir)
        eligible_sha = resolve.get("eligible_sha")
    except (FileNotFoundError, ValueError, OSError):
        pass
    return read_eligible_land_use_union(
        gpkg,
        layer="eligible_land_use",
        cache_root=bundle_dir.parent / "eligible_union",
        eligible_sha=eligible_sha,
    )


def _collect_pairwise_geometries(bundle_dir: Path) -> tuple[list[BaseGeometry], list[dict[str, Any]]]:
    root = resolved_mesh_pairwise_dir(bundle_dir)
    geoms: list[BaseGeometry] = []
    props: list[dict[str, Any]] = []
    if not root.is_dir():
        return geoms, props
    for pair_dir in sorted(root.iterdir()):
        if not pair_dir.is_dir():
            continue
        gpkg = pair_dir / OVERLAP_GPKG
        if not gpkg.is_file():
            continue
        gdf = gpd.read_file(gpkg, layer=OVERLAP_LAYER)
        if gdf.empty:
            continue
        geom = gdf.geometry.iloc[0]
        if geom is None or geom.is_empty:
            continue
        parts = pair_dir.name.split("__", 1)
        slug_a = parts[0] if parts else pair_dir.name
        slug_b = parts[1] if len(parts) > 1 else ""
        geoms.append(geom)
        props.append({"slug_a": slug_a, "slug_b": slug_b, "pair": pair_dir.name})
    return geoms, props


def pairwise_geojson(bundle_dir: Path) -> dict[str, Any]:
    geoms, props = _collect_pairwise_geometries(bundle_dir)
    return _geom_to_geojson(geoms, props=props)


def pairwise_eligible_geojson(bundle_dir: Path, preset_path: Path) -> dict[str, Any]:
    eligible_ll = _load_eligible_geometry(preset_path, bundle_dir)
    if eligible_ll is None or eligible_ll.is_empty:
        return {"type": "FeatureCollection", "features": []}
    geoms, props = _collect_pairwise_geometries(bundle_dir)
    clipped_geoms: list[BaseGeometry] = []
    clipped_props: list[dict[str, Any]] = []
    for geom, prop in zip(geoms, props, strict=True):
        clipped = _clip_plain_link_overlap_to_eligible(plain_link_ll=geom, eligible_ll=eligible_ll)
        if clipped is None or clipped.is_empty:
            continue
        clipped_geoms.append(clipped)
        clipped_props.append(prop)
    return _geom_to_geojson(clipped_geoms, props=clipped_props)


def _read_depth_band_geom(bundle_dir: Path, preset: Preset, band: str) -> BaseGeometry | None:
    set_dir = _mesh_depth_set_dir(bundle_dir, preset)
    if set_dir is None:
        return None
    gpkg = set_dir / "bands" / band / BAND_GPKG
    if not gpkg.is_file():
        return None
    gdf = gpd.read_file(gpkg, layer=BAND_LAYER)
    if gdf.empty:
        return None
    geom = gdf.geometry.iloc[0]
    if geom is None or geom.is_empty:
        return None
    return geom


def depth_band_geojson(bundle_dir: Path, preset: Preset, band: str) -> dict[str, Any]:
    geom = _read_depth_band_geom(bundle_dir, preset, band)
    if geom is None:
        return {"type": "FeatureCollection", "features": []}
    return _geom_to_geojson([geom], props=[{"band": band}])


def depth_eligible_band_geojson(bundle_dir: Path, preset_path: Path, preset: Preset, band: str) -> dict[str, Any]:
    geom = _read_depth_band_geom(bundle_dir, preset, band)
    if geom is None:
        return {"type": "FeatureCollection", "features": []}
    eligible_ll = _load_eligible_geometry(preset_path, bundle_dir)
    if eligible_ll is None or eligible_ll.is_empty:
        return {"type": "FeatureCollection", "features": []}
    clipped = _clip_plain_link_overlap_to_eligible(plain_link_ll=geom, eligible_ll=eligible_ll)
    if clipped is None or clipped.is_empty:
        return {"type": "FeatureCollection", "features": []}
    return _geom_to_geojson([clipped], props=[{"band": band, "eligible": True}])


def _rf_sites_with_footprints(preset: Preset, preset_path: Path, bundle_dir: Path) -> list[tuple[str, SiteEntry]]:
    sites_items = [
        (slug, site) for slug, site in preset.sites.items() if site.participates_in_rf
    ]
    if len(sites_items) < 2:
        return []
    viewshed_root = bundle_dir.parent / "viewsheds"
    out: list[tuple[str, SiteEntry]] = []
    for slug, site in sites_items:
        from peaky_finders.viewshed_workspace import resolved_viewshed_workdir, viewshed_workspace_digest
        from peaky_finders.preset_mapping import preset_to_request

        vd = viewshed_workspace_digest(request=preset_to_request(preset, float(site.lat), float(site.lon)))
        workdir = resolved_viewshed_workdir(digest=vd, viewshed_root=viewshed_root)
        if (workdir / SPLAT_GPKG_NAME).is_file():
            out.append((slug, site))
    return out


def _footprints_for_sites(
    preset: Preset,
    preset_path: Path,
    bundle_dir: Path,
    sites_items: list[tuple[str, SiteEntry]],
) -> list[tuple[Path, str, str]]:
    assets = collect_site_workspace_assets(
        preset_path=preset_path,
        preset=preset,
        bundle_cache_root=bundle_dir,
        sites_items=sites_items,
    )
    footprints: list[tuple[Path, str, str]] = []
    for slug, site in sites_items:
        gp = assets.coverage_gpkg_by_slug.get(slug)
        if gp is not None and gp.is_file():
            footprints.append((gp, slug, site.name.strip() or slug))
    return footprints


def display_mode_from_kmz_bools(*, plain: bool, eligible: bool) -> LayerDisplayMode:
    if eligible and not plain:
        return "eligible"
    if plain:
        return "all"
    return "off"


def kmz_bools_from_display_mode(mode: LayerDisplayMode) -> tuple[bool, bool]:
    if mode == "eligible":
        return False, True
    if mode == "all":
        return True, False
    return False, False


def _mesh_kmz_block(root: dict[str, Any]) -> dict[str, Any]:
    bundle = root.get("bundle")
    if not isinstance(bundle, dict):
        return {}
    kmz = bundle.get("kmz")
    if not isinstance(kmz, dict):
        return {}
    layers = kmz.get("layers")
    if not isinstance(layers, dict):
        return {}
    mesh = layers.get("mesh")
    return mesh if isinstance(mesh, dict) else {}


def _read_pair_display_mode(mesh: dict[str, Any], pair_key: str, *, default: LayerDisplayMode) -> LayerDisplayMode:
    pair_modes = mesh.get("pair_modes")
    if isinstance(pair_modes, dict):
        raw = pair_modes.get(pair_key)
        if raw in ("off", "eligible", "all"):
            return raw
    agg = mesh.get("pairwise_mode")
    if agg in ("off", "eligible", "all"):
        return agg
    return display_mode_from_kmz_bools(
        plain=bool(mesh.get("pairwise", False)),
        eligible=bool(mesh.get("pairwise_eligible", False)),
    ) if default == "off" else default


def _read_depth_display_mode(mesh: dict[str, Any], band: str, vis) -> LayerDisplayMode:
    depth_modes = mesh.get("depth_modes")
    if isinstance(depth_modes, dict):
        raw = depth_modes.get(band)
        if raw in ("off", "eligible", "all"):
            return raw
    plain_key = f"depth_{band}"
    elig_key = f"depth_eligible_{band}"
    return display_mode_from_kmz_bools(
        plain=bool(mesh.get(plain_key, getattr(vis, f"mesh_depth_{band}", False))),
        eligible=bool(mesh.get(elig_key, getattr(vis, f"mesh_depth_eligible_{band}", False))),
    )


def _pair_artifact_present(bundle_dir: Path, *, slug_a: str, slug_b: str) -> bool:
    """Fast on-disk presence check for catalog rows (no viewshed digest scan)."""
    pair_dir = resolved_mesh_pairwise_dir(bundle_dir) / mesh_pairwise_rel_dir(slug_a, slug_b)
    return (pair_dir / OVERLAP_GPKG).is_file() or (pair_dir / EMPTY_SENTINEL).is_file()


def _enumerate_pairwise_entries(
    slug: str,
    preset: Preset,
    preset_path: Path,
    bundle_dir: Path,
    yaml_root: Any,
) -> list[dict[str, Any]]:
    kml_overlay = preset.bundle.kml_overlay if preset.bundle else None
    mesh_yaml = _mesh_kmz_block(yaml_root)

    # Catalog only: list RF site pairs from preset (no per-site viewshed scan on request path).
    sites_items = [(slug, site) for slug, site in preset.sites.items() if site.participates_in_rf]
    if len(sites_items) < 2:
        return []

    slugs = [slug for slug, _ in sites_items]

    from peaky_finders.web.maps_build_scheduler import layer_build_phase

    default_mode = _read_pair_display_mode(mesh_yaml, "", default="off")

    entries: list[dict[str, Any]] = []
    for i, sa in enumerate(slugs):
        for sb in slugs[i + 1 :]:
            pair_key = mesh_pairwise_rel_dir(sa, sb)
            built = _pair_artifact_present(bundle_dir, slug_a=sa, slug_b=sb)
            name_a = preset.sites[sa].name.strip() or sa
            name_b = preset.sites[sb].name.strip() or sb
            entries.append(
                {
                    "id": f"mesh_pair:{pair_key}",
                    "pair": pair_key,
                    "slug_a": sa,
                    "slug_b": sb,
                    "name": f"{name_a} ∩ {name_b}",
                    "description": "Footprint intersection",
                    "type": "derived",
                    "mesh_kind": "pairwise",
                    "display_mode": _read_pair_display_mode(mesh_yaml, pair_key, default=default_mode),
                    "build_phase": layer_build_phase(slug, built=built, mesh_layer=True),
                    "map_style": _kml_style_colors(resolved_mesh_pairwise_kml_style(kml_overlay)),
                    "eligible_map_style": _kml_style_colors(
                        resolved_mesh_pairwise_eligible_kml_style(kml_overlay)
                    ),
                }
            )
    return entries


def _enumerate_depth_band_entries(
    slug: str,
    preset: Preset,
    preset_path: Path,
    bundle_dir: Path,
    yaml_root: dict[str, Any],
) -> list[dict[str, Any]]:
    kml_overlay = preset.bundle.kml_overlay if preset.bundle else None
    vis = resolved_kmz_document_layers(preset.bundle)
    mesh_yaml = _mesh_kmz_block(yaml_root)

    from peaky_finders.web.maps_build_scheduler import layer_build_phase

    entries: list[dict[str, Any]] = []
    for band in MESH_DEPTH_BANDS:
        band_built = depth_band_built(bundle_dir, preset, band)
        style = resolved_mesh_depth_band_kml_style(kml_overlay, band, eligible=False)
        elig_style = resolved_mesh_depth_band_kml_style(kml_overlay, band, eligible=True)
        entries.append(
            {
                "id": f"mesh_depth:{band}",
                "band": band,
                "name": MESH_DEPTH_DISPLAY.get(band, band),
                "description": "Footprint overlap-count band",
                "type": "derived",
                "mesh_kind": "depth",
                "display_mode": _read_depth_display_mode(mesh_yaml, band, vis),
                "build_phase": layer_build_phase(slug, built=band_built, mesh_layer=True),
                "map_style": _kml_style_colors(style),
                "eligible_map_style": _kml_style_colors(elig_style),
            }
        )
    return entries


def mesh_layers_catalog_for_preset(
    slug: str,
    preset: Preset,
    preset_path: Path,
    yaml_root: dict[str, Any],
) -> dict[str, Any]:
    bundle_dir = resolved_bundle_dir(preset_path=preset_path)
    return {
        "pairwise_pairs": _enumerate_pairwise_entries(slug, preset, preset_path, bundle_dir, yaml_root),
        "depth_bands": _enumerate_depth_band_entries(slug, preset, preset_path, bundle_dir, yaml_root),
    }


def mesh_layers_catalog(slug: str) -> dict[str, Any]:
    from peaky_finders.sites_job import read_preset_yaml_tree

    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    _, yaml_root = read_preset_yaml_tree(preset_path)
    return mesh_layers_catalog_for_preset(slug, preset, preset_path, yaml_root)


def _pair_geojson_one(bundle_dir: Path, pair_key: str, *, eligible: bool, preset_path: Path) -> dict[str, Any]:
    pair_dir = resolved_mesh_pairwise_dir(bundle_dir) / pair_key
    gpkg = pair_dir / OVERLAP_GPKG
    if not gpkg.is_file():
        return {"type": "FeatureCollection", "features": []}
    gdf = gpd.read_file(gpkg, layer=OVERLAP_LAYER)
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    geom = gdf.geometry.iloc[0]
    if geom is None or geom.is_empty:
        return {"type": "FeatureCollection", "features": []}
    parts = pair_key.split("__", 1)
    slug_a = parts[0] if parts else pair_key
    slug_b = parts[1] if len(parts) > 1 else ""
    prop = {"slug_a": slug_a, "slug_b": slug_b, "pair": pair_key}
    if eligible:
        eligible_ll = _load_eligible_geometry(preset_path, bundle_dir)
        if eligible_ll is None or eligible_ll.is_empty:
            return {"type": "FeatureCollection", "features": []}
        clipped = _clip_plain_link_overlap_to_eligible(plain_link_ll=geom, eligible_ll=eligible_ll)
        if clipped is None or clipped.is_empty:
            return {"type": "FeatureCollection", "features": []}
        return _geom_to_geojson([clipped], props=[{**prop, "eligible": True}])
    return _geom_to_geojson([geom], props=[prop])


def mesh_layer_geojson(slug: str, layer_id: str, *, view: LayerDisplayMode = "all") -> dict[str, Any]:
    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    bundle_dir = resolved_bundle_dir(preset_path=preset_path)

    if layer_id.startswith("mesh_pair:"):
        pair_key = layer_id.removeprefix("mesh_pair:")
        return _pair_geojson_one(bundle_dir, pair_key, eligible=view == "eligible", preset_path=preset_path)
    if layer_id.startswith("mesh_depth:"):
        band = layer_id.removeprefix("mesh_depth:")
        if band not in MESH_DEPTH_BAND_IDS:
            raise KeyError(f"unknown mesh layer: {layer_id!r}")
        if not depth_band_built(bundle_dir, preset, band):
            raise FileNotFoundError(f"mesh depth band {band!r} not built")
        if view == "eligible":
            return depth_eligible_band_geojson(bundle_dir, preset_path, preset, band)
        return depth_band_geojson(bundle_dir, preset, band)
    raise KeyError(f"unknown mesh layer: {layer_id!r}")


def _catalog_entry_by_id(slug: str, layer_id: str) -> dict[str, Any]:
    catalog = mesh_layers_catalog(slug)
    for entry in catalog["pairwise_pairs"] + catalog["depth_bands"]:
        if entry["id"] == layer_id:
            return entry
    raise KeyError(f"unknown mesh layer: {layer_id!r}")


def patch_mesh_layer_display_mode(slug: str, layer_id: str, mode: LayerDisplayMode) -> dict[str, Any]:
    from peaky_finders.sites_job import dump_preset_yaml_document, parse_preset_dict, read_preset_yaml_tree

    if mode not in ("off", "eligible", "all"):
        raise ValueError(f"invalid display mode: {mode!r}")

    preset_path = _preset_path(slug)
    y, root = read_preset_yaml_tree(preset_path)
    bundle = root.get("bundle")
    if not isinstance(bundle, dict):
        bundle = {}
        root["bundle"] = bundle
    kmz = bundle.get("kmz")
    if not isinstance(kmz, dict):
        kmz = {}
        bundle["kmz"] = kmz
    layers = kmz.get("layers")
    if not isinstance(layers, dict):
        layers = {}
        kmz["layers"] = layers
    mesh = layers.get("mesh")
    if not isinstance(mesh, dict):
        mesh = {}
        layers["mesh"] = mesh

    plain, eligible = kmz_bools_from_display_mode(mode)

    if layer_id.startswith("mesh_pair:"):
        pair_key = layer_id.removeprefix("mesh_pair:")
        pair_modes = mesh.get("pair_modes")
        if not isinstance(pair_modes, dict):
            pair_modes = {}
            mesh["pair_modes"] = pair_modes
        pair_modes[pair_key] = mode
    elif layer_id.startswith("mesh_depth:"):
        band = layer_id.removeprefix("mesh_depth:")
        if band not in MESH_DEPTH_BAND_IDS:
            raise KeyError(f"unknown mesh layer: {layer_id!r}")
        depth_modes = mesh.get("depth_modes")
        if not isinstance(depth_modes, dict):
            depth_modes = {}
            mesh["depth_modes"] = depth_modes
        depth_modes[band] = mode
        mesh[f"depth_{band}"] = plain
        mesh[f"depth_eligible_{band}"] = eligible
    else:
        raise KeyError(f"unknown mesh layer: {layer_id!r}")

    parse_preset_dict(root)
    dump_preset_yaml_document(y, root, preset_path)
    entry = _catalog_entry_by_id(slug, layer_id)
    entry["display_mode"] = mode
    return entry


def patch_mesh_layer_visibility(slug: str, layer_id: str, visible: bool) -> dict[str, Any]:
    mode: LayerDisplayMode = "all" if visible else "off"
    return patch_mesh_layer_display_mode(slug, layer_id, mode)


def run_mesh_rebuild(
    slug: str,
    *,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
    on_pair_complete: Callable[[str, str], None] | None = None,
    on_depth_band_complete: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def vlog(msg: str) -> None:
        if verbose_log:
            verbose_log(msg)

    def plog(msg: str) -> None:
        if progress_log:
            progress_log(msg)

    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    bundle_dir = resolved_bundle_dir(preset_path=preset_path)
    mesh_cfg = preset.bundle.mesh_coverage if preset.bundle else None
    kml_overlay = preset.bundle.kml_overlay if preset.bundle else None

    sites_items = _rf_sites_with_footprints(preset, preset_path, bundle_dir)
    if len(sites_items) < 2:
        raise ValueError("need at least two RF sites with polygonized viewshed footprints")

    footprints = _footprints_for_sites(preset, preset_path, bundle_dir, sites_items)
    if len(footprints) < 2:
        raise ValueError("need at least two polygonized coverage footprints")

    eligible_ll = _load_eligible_geometry(preset_path, bundle_dir)
    if eligible_ll is None or eligible_ll.is_empty:
        raise ValueError("eligible land not built — run clip rebuild first")

    assets = collect_site_workspace_assets(
        preset_path=preset_path,
        preset=preset,
        bundle_cache_root=bundle_dir,
        sites_items=sites_items,
    )
    slug_to_digest = assets.slug_to_digest
    pairwise_root = resolved_mesh_pairwise_dir(bundle_dir)
    depth_root = resolved_mesh_depth_dir(bundle_dir)
    pairwise_root.mkdir(parents=True, exist_ok=True)
    depth_root.mkdir(parents=True, exist_ok=True)

    dem_mirror = ensure_skadi_mirror_dir() if (mesh_cfg and mesh_cfg.pairwise_dem_peak_pin) else None
    workers = mesh_cfg.pairwise_overlap_workers if mesh_cfg else 8
    depth_workers = mesh_cfg.mesh_depth_workers if mesh_cfg else 8
    max_raster = mesh_cfg.max_raster_dimension if mesh_cfg else 4096
    emit_pins = bool(mesh_cfg.pairwise_dem_peak_pin) if mesh_cfg else True

    data_dir = preset_path.parent / "data"
    land_digest = bundle_land_use_inputs_digest(preset, data_dir)
    overlay_digest = None
    if preset.bundle is not None:
        from peaky_finders.bundle_build import bundle_kml_overlay_inputs_digest

        overlay_digest = bundle_kml_overlay_inputs_digest(preset.bundle)

    site_pins = {slug: (float(site.lat), float(site.lon)) for slug, site in sites_items}
    viewshed_radius_m = float(preset.simulation.radius_km) * 1000.0

    plog(f"mesh rebuild: {len(footprints)} footprint(s)")
    vlog(f"mesh rebuild start: workers={workers}, depth_workers={depth_workers}")

    pairwise_count = 0
    depth_plain = 0
    depth_elig = 0

    with tempfile.TemporaryDirectory(prefix="peaky-mesh-") as scratch:
        scratch_dir = Path(scratch)

        def run_plain_pairwise() -> list[tuple[str, Path, str]]:
            plog("mesh pairwise: plain overlaps…")
            return write_pairwise_link_overlap_layers(
                footprints=footprints,
                scratch_dir=scratch_dir / "pairwise",
                polygon_style=resolved_mesh_pairwise_kml_style(kml_overlay),
                dem_mirror_root=dem_mirror,
                emit_dem_peak_pins=emit_pins,
                pairwise_peak_pin_style=None,
                pairwise_overlap_workers=workers,
                geometry_cache_root=pairwise_root,
                slug_to_viewshed_digest=slug_to_digest,
                site_pins=site_pins,
                viewshed_radius_m=viewshed_radius_m,
                progress_log=progress_log,
                on_pair_complete=on_pair_complete,
            )

        def run_depth_layers() -> tuple[
            list[tuple[str, str, Path, str, str]],
            list[tuple[str, str, Path, str, str]],
        ]:
            plog("mesh depth: raster bands…")
            return write_mesh_depth_kml_layers(
                footprints=footprints,
                kml_overlay=kml_overlay,
                scratch_depth_dir=scratch_dir / "depth",
                scratch_depth_eligible_dir=scratch_dir / "depth_eligible",
                eligible_ll=eligible_ll,
                max_raster_dimension=max_raster,
                mesh_depth_workers=depth_workers,
                geometry_cache_root=depth_root,
                slug_to_viewshed_digest=slug_to_digest,
                bundle_kml_overlay_digest=overlay_digest,
                bundle_land_use_inputs_digest=land_digest,
                on_band_complete=on_depth_band_complete,
            )

        plog("mesh rebuild: plain pairwise + depth in parallel…")
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_plain = ex.submit(run_plain_pairwise)
            fut_depth = ex.submit(run_depth_layers)
            plain = fut_plain.result()
            plain_rows, elig_rows = fut_depth.result()

        pairwise_count = len(plain)
        plog(f"mesh pairwise plain done: {pairwise_count} pair(s)")
        depth_plain = len(plain_rows)
        depth_elig = len(elig_rows)
        plog(f"mesh depth done: {depth_plain} plain slice(s), {depth_elig} eligible slice(s)")

        plog("mesh pairwise: eligible overlaps…")
        elig = write_pairwise_eligible_link_overlap_layers(
            footprints=footprints,
            scratch_dir=scratch_dir / "pairwise_eligible",
            polygon_style=resolved_mesh_pairwise_eligible_kml_style(kml_overlay),
            eligible_ll=eligible_ll,
            dem_mirror_root=dem_mirror,
            emit_dem_peak_pins=emit_pins,
            eligible_peak_pin_style=None,
            pairwise_overlap_workers=workers,
            geometry_cache_root=pairwise_root,
            slug_to_viewshed_digest=slug_to_digest,
            site_pins=site_pins,
            viewshed_radius_m=viewshed_radius_m,
            progress_log=progress_log,
        )
        plog(f"mesh pairwise eligible done: {len(elig)} pair(s)")

    vlog("mesh rebuild done")
    return {
        "pairwise_pairs": pairwise_count,
        "depth_plain_slices": depth_plain,
        "depth_eligible_slices": depth_elig,
    }
