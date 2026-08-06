"""On-demand eligible land geometry (include − exclude) for seek mode."""

from __future__ import annotations

import hashlib
from pathlib import Path

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.core.preset import LandLayerEntry, LandLayerRole, Preset, load_preset, resolved_preset_cache_dir
from peaky_finders.serve.land import build_uber_aoi_geometry, iter_aoi_layer_entries
from peaky_finders.serve.land_import import land_layer_entry_digest, read_land_layer_gdf, resolve_land_source_path


class EligibleLandError(Exception):
    """Eligible land geometry could not be built."""


def _polygonal_area_only(geom: BaseGeometry) -> BaseGeometry:
    if geom is None or geom.is_empty:
        return Polygon()
    gt = geom.geom_type
    if gt == "Polygon":
        return geom
    if gt == "MultiPolygon":
        parts = [g for g in geom.geoms if not g.is_empty]
        if not parts:
            return Polygon()
        return unary_union(parts)
    if gt == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        if not polys:
            return Polygon()
        return _polygonal_area_only(unary_union(polys))
    return Polygon()


def iter_land_layer_entries_by_role(preset: Preset, role: LandLayerRole) -> list[tuple[str, LandLayerEntry]]:
    rows: list[tuple[str, LandLayerEntry]] = []
    for source_id, source in sorted(preset.land.sources.items()):
        for layer in source.layers:
            if layer.role == role:
                rows.append((source_id, layer))
    return rows


def eligible_land_digest(preset_path: Path) -> str:
    preset = load_preset(preset_path)
    project_dir = preset_path.parent
    parts: list[str] = []
    aoi_digest_val = "none"
    aoi_rows = iter_aoi_layer_entries(preset)
    if aoi_rows:
        aoi_parts: list[str] = []
        for source_id, layer in aoi_rows:
            source = preset.land.sources[source_id]
            data_path = resolve_land_source_path(project_dir, source.path)
            stat = data_path.stat()
            aoi_parts.append(
                "|".join((source_id, layer.layer_key(), land_layer_entry_digest(layer), str(stat.st_mtime_ns)))
            )
        aoi_digest_val = hashlib.sha256("\n".join(aoi_parts).encode()).hexdigest()[:16]

    for role in (LandLayerRole.INCLUDE, LandLayerRole.EXCLUDE):
        for source_id, layer in iter_land_layer_entries_by_role(preset, role):
            source = preset.land.sources[source_id]
            data_path = resolve_land_source_path(project_dir, source.path)
            stat = data_path.stat()
            parts.append(
                "|".join(
                    (
                        role.value,
                        source_id,
                        layer.layer_key(),
                        land_layer_entry_digest(layer),
                        str(stat.st_mtime_ns),
                        str(stat.st_size),
                        aoi_digest_val,
                    )
                )
            )
    if not parts:
        return "none"
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def _union_role_layers(
    preset_path: Path,
    *,
    role: LandLayerRole,
    aoi: BaseGeometry | None,
) -> gpd.GeoDataFrame:
    preset = load_preset(preset_path)
    project_dir = preset_path.parent
    pieces: list[BaseGeometry] = []
    for source_id, layer in iter_land_layer_entries_by_role(preset, role):
        source = preset.land.sources[source_id]
        data_path = resolve_land_source_path(project_dir, source.path)
        gdf = read_land_layer_gdf(data_path, layer)
        if gdf.empty:
            continue
        if aoi is not None and not aoi.is_empty:
            aoi_gdf = gpd.GeoDataFrame(geometry=[aoi], crs="EPSG:4326")
            gdf = gpd.clip(gdf, aoi_gdf)
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            pieces.append(make_valid(geom))
    if not pieces:
        return gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
    union = make_valid(unary_union(pieces))
    return gpd.GeoDataFrame(geometry=[union], crs="EPSG:4326")


def build_eligible_geometry(
    include_union: gpd.GeoDataFrame,
    exclude_union: gpd.GeoDataFrame,
) -> BaseGeometry:
    """``include_union \\ exclude_union`` in EPSG:3857; return EPSG:4326 geometry."""
    empty = Polygon()
    if include_union.empty or include_union.geometry.is_empty.iloc[0]:
        return empty
    inc_ll = make_valid(include_union.geometry.iloc[0])
    if inc_ll.is_empty:
        return empty

    inc_3857 = gpd.GeoDataFrame(geometry=[inc_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    ig = make_valid(inc_3857.geometry.iloc[0])
    if ig.is_empty:
        return empty

    exc_ll = Polygon()
    if not exclude_union.empty and not exclude_union.geometry.is_empty.iloc[0]:
        exc_ll = make_valid(exclude_union.geometry.iloc[0])

    if exc_ll.is_empty:
        clean = _polygonal_area_only(inc_ll)
        return clean if not clean.is_empty else empty

    exc_3857 = gpd.GeoDataFrame(geometry=[exc_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    eg = make_valid(exc_3857.geometry.iloc[0])
    if eg.is_empty:
        clean = _polygonal_area_only(inc_ll)
        return clean if not clean.is_empty else empty

    diff = ig.difference(eg)
    diff = make_valid(diff) if not diff.is_valid else diff
    if diff.is_empty:
        return empty
    diff = _polygonal_area_only(diff)
    if diff.is_empty:
        return empty
    elig_3857 = gpd.GeoDataFrame(geometry=[diff], crs="EPSG:3857")
    elig_ll = elig_3857.to_crs("EPSG:4326")
    geom_ll = _polygonal_area_only(make_valid(elig_ll.geometry.iloc[0]))
    return geom_ll if not geom_ll.is_empty else empty


def resolved_eligible_cache_dir(preset_path: Path) -> Path:
    return resolved_preset_cache_dir(preset_path) / "land" / "eligible"


def _eligible_memo_path(cache_root: Path, digest: str) -> Path:
    return cache_root / digest / "union.wkb"


def load_or_build_eligible_geometry(preset_path: Path) -> tuple[BaseGeometry, str]:
    """Return ``(eligible_geom, digest)``. Raises if no include layers."""
    preset = load_preset(preset_path)
    if not iter_land_layer_entries_by_role(preset, LandLayerRole.INCLUDE):
        raise EligibleLandError("Seek requires at least one land layer with role: include")

    digest = eligible_land_digest(preset_path)
    cache_root = resolved_eligible_cache_dir(preset_path)
    memo_path = _eligible_memo_path(cache_root, digest)
    if memo_path.is_file():
        try:
            from shapely import wkb

            geom = wkb.loads(memo_path.read_bytes())
            if geom is not None and not geom.is_empty:
                return geom, digest
        except (OSError, ValueError):
            pass

    aoi = build_uber_aoi_geometry(preset_path)
    include_union = _union_role_layers(preset_path, role=LandLayerRole.INCLUDE, aoi=aoi)
    exclude_union = _union_role_layers(preset_path, role=LandLayerRole.EXCLUDE, aoi=aoi)
    eligible = build_eligible_geometry(include_union, exclude_union)
    if eligible.is_empty:
        raise EligibleLandError("Eligible land geometry is empty after include − exclude")

    memo_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from shapely import wkb

        memo_path.write_bytes(wkb.dumps(eligible))
    except OSError:
        pass
    return eligible, digest
