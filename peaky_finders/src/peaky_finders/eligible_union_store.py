"""Filesystem store for WGS-84 eligible land-use union geometry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from peaky_finders.geometry_preview_png import write_wgs84_geometry_preview_png
from peaky_finders.path_labels import ELIGIBLE_UNION_DIRNAME

ELIGIBLE_UNION_FORMAT = "peaky_eligible_union/v1"
COMPLETE_JSON = "complete.json"
UNION_GPKG = "union.gpkg"
UNION_LAYER = "eligible_union"
UNION_PREVIEW_PNG = "union.png"
EMPTY_SENTINEL = "empty"

_UNION_FACE_AABBGGRR = "6644cc44"
# Edge outlines call ``boundary().buffer()`` — catastrophic on state-scale MultiPolygons.
_UNION_PREVIEW_MAX_PARTS = 512
_UNION_PREVIEW_MAX_COORDS = 100_000


def _union_preview_too_heavy(geom: BaseGeometry) -> bool:
    """Skip PNG sidecar when fill/edge rasterization would dominate build time."""
    if geom is None or getattr(geom, "is_empty", True):
        return True
    parts: list[BaseGeometry]
    if geom.geom_type == "MultiPolygon":
        parts = list(geom.geoms)
    elif geom.geom_type == "Polygon":
        parts = [geom]
    else:
        return True
    if len(parts) > _UNION_PREVIEW_MAX_PARTS:
        return True
    coords = 0
    for part in parts:
        if part.geom_type != "Polygon":
            continue
        coords += len(part.exterior.coords)
        for ring in part.interiors:
            coords += len(ring.coords)
        if coords > _UNION_PREVIEW_MAX_COORDS:
            return True
    return False


def eligible_union_digest_body(
    *,
    eligible_sha: str | None,
    eligible_gpkg: Path,
) -> str:
    """Canonical digest body for one eligible source GPKG."""
    if eligible_sha:
        return f"peaky_eligible_union/v1\neligible_sha={eligible_sha}\n"
    p = Path(eligible_gpkg).expanduser().resolve()
    st = p.stat()
    return (
        "peaky_eligible_union/v1\n"
        f"path={p}\n"
        f"size={st.st_size}\n"
        f"mtime_ns={st.st_mtime_ns}\n"
    )


def eligible_union_digest(
    *,
    eligible_gpkg: Path,
    eligible_sha: str | None = None,
) -> str:
    """SHA256 hex directory name under ``…/eligible_union/<digest>/``."""
    body = eligible_union_digest_body(
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def resolved_eligible_union_data_dir(cache_root: Path) -> Path:
    """Directory for eligible-layer union artifacts (single slot per preset build layout)."""
    return Path(cache_root).expanduser().resolve() / ELIGIBLE_UNION_DIRNAME


def eligible_union_complete_matches(
    *,
    cache_dir: Path,
    cache_digest: str,
    eligible_sha: str | None,
    source_gpkg: Path,
) -> bool:
    """Whether ``complete.json`` matches ``write_cached_eligible_union`` semantics."""

    cdir = Path(cache_dir).expanduser().resolve()
    p = cdir / COMPLETE_JSON
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if raw.get("format") != ELIGIBLE_UNION_FORMAT:
        return False
    if raw.get("cache_digest") != cache_digest:
        return False
    if raw.get("eligible_sha") != eligible_sha:
        return False
    if str(raw.get("source_gpkg")) != str(Path(source_gpkg).expanduser().resolve()):
        return False
    sentinel = cdir / EMPTY_SENTINEL
    gpkg = cdir / UNION_GPKG
    if sentinel.is_file():
        return True
    return gpkg.is_file()


def load_cached_eligible_union(*, cache_dir: Path) -> BaseGeometry | None:
    """Load union geometry from a populated cache dir; ``None`` if empty sentinel or missing GPKG."""
    cdir = Path(cache_dir).expanduser().resolve()
    if (cdir / EMPTY_SENTINEL).is_file():
        return None
    gpkg = cdir / UNION_GPKG
    if not gpkg.is_file():
        return None
    loaded = gpd.read_file(gpkg, layer=UNION_LAYER).geometry.iloc[0]
    if loaded is None or getattr(loaded, "is_empty", True):
        return None
    return loaded


def write_cached_eligible_union(
    *,
    cache_dir: Path,
    cache_digest: str,
    eligible_sha: str | None,
    source_gpkg: Path,
    union_wgs84: BaseGeometry | None,
) -> None:
    """Persist union geometry (or empty sentinel) under ``cache_dir``."""
    cdir = Path(cache_dir).expanduser().resolve()
    cdir.mkdir(parents=True, exist_ok=True)
    meta = {
        "format": ELIGIBLE_UNION_FORMAT,
        "cache_digest": cache_digest,
        "eligible_sha": eligible_sha,
        "source_gpkg": str(Path(source_gpkg).expanduser().resolve()),
    }
    complete = cdir / COMPLETE_JSON
    complete.unlink(missing_ok=True)
    sentinel = cdir / EMPTY_SENTINEL
    gpkg = cdir / UNION_GPKG
    preview = cdir / UNION_PREVIEW_PNG
    if union_wgs84 is None or getattr(union_wgs84, "is_empty", True):
        gpkg.unlink(missing_ok=True)
        preview.unlink(missing_ok=True)
        sentinel.write_text("", encoding="utf-8")
        complete.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return

    sentinel.unlink(missing_ok=True)
    gdf = gpd.GeoDataFrame(geometry=[union_wgs84], crs="EPSG:4326")
    gdf.reset_index(drop=True).to_file(gpkg, driver="GPKG", layer=UNION_LAYER)
    preview.unlink(missing_ok=True)
    if not _union_preview_too_heavy(union_wgs84):
        write_wgs84_geometry_preview_png(
            union_wgs84,
            preview,
            face_aabbggrr=_UNION_FACE_AABBGGRR,
            line_width=0.0,
        )
    complete.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
