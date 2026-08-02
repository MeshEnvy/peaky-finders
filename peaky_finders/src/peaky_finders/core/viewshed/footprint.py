"""Read unioned SPLAT ``splat.gpkg`` footprint geometry from GeoPackage."""

from __future__ import annotations

from pathlib import Path

import pyogrio
import shapely.wkb
from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

FOOTPRINT_WKB_NAME = "splat.wkb"


def _footprint_wkb_path(gpkg_path: Path) -> Path:
    return gpkg_path.parent / FOOTPRINT_WKB_NAME


def _read_wkb_sidecar(gpkg_path: Path) -> BaseGeometry | None | str:
    """Return cached geometry, ``None`` for a cached-empty marker, or ``"miss"``."""
    wkb_path = _footprint_wkb_path(gpkg_path)
    try:
        if not wkb_path.is_file():
            return "miss"
        if wkb_path.stat().st_mtime < gpkg_path.stat().st_mtime:
            return "miss"
        data = wkb_path.read_bytes()
    except OSError:
        return "miss"
    if not data:
        return None
    try:
        return shapely.wkb.loads(data)
    except (ShapelyError, ValueError):
        return "miss"


def _write_wkb_sidecar(gpkg_path: Path, geom: BaseGeometry | None) -> None:
    wkb_path = _footprint_wkb_path(gpkg_path)
    tmp = wkb_path.with_name(wkb_path.name + ".tmp")
    try:
        tmp.write_bytes(geom.wkb if geom is not None else b"")
        tmp.replace(wkb_path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_coverage_footprint(path: Path) -> BaseGeometry | None:
    """Return EPSG:4326 footprint union from a coverage GPKG, or None if missing/empty.

    A ``splat.wkb`` sidecar caches the unioned geometry — GDAL GPKG opens cost ~1s
    each on Docker bind mounts, flat WKB reads are ~ms.
    """
    p = Path(path)
    if not p.is_file():
        return None
    cached = _read_wkb_sidecar(p)
    if cached != "miss":
        return cached  # type: ignore[return-value]
    gdf = pyogrio.read_dataframe(str(p))
    if gdf.empty:
        _write_wkb_sidecar(p, None)
        return None
    raw = unary_union(list(gdf.geometry))
    if raw.is_empty:
        _write_wkb_sidecar(p, None)
        return None
    g = make_valid(raw) if not raw.is_valid else raw
    result = g if not g.is_empty else None
    _write_wkb_sidecar(p, result)
    return result
