"""Filesystem cache for WGS-84 eligible land-use union geometry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from peaky_finders.geometry_preview_png import write_wgs84_geometry_preview_png

ELIGIBLE_UNION_FORMAT = "peaky_eligible_union/v1"
COMPLETE_JSON = "complete.json"
UNION_GPKG = "union.gpkg"
UNION_LAYER = "eligible_union"
UNION_PREVIEW_PNG = "union.png"
EMPTY_SENTINEL = "empty"

_UNION_FACE_AABBGGRR = "6644cc44"


def eligible_union_cache_digest_body(
    *,
    eligible_sha: str | None,
    eligible_gpkg: Path,
) -> str:
    """Canonical cache key body for one eligible source GPKG."""
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


def eligible_union_cache_digest(
    *,
    eligible_gpkg: Path,
    eligible_sha: str | None = None,
) -> str:
    """SHA256 hex directory name under ``…/eligible_union/<digest>/``."""
    body = eligible_union_cache_digest_body(
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def resolved_eligible_union_cache_dir(*, cache_digest: str, cache_root: Path) -> Path:
    return Path(cache_root).expanduser().resolve() / cache_digest


def try_read_cached_eligible_union(
    cache_dir: Path,
    *,
    expected_digest: str,
    expected_eligible_sha: str | None,
    expected_source_gpkg: Path,
) -> tuple[Literal["miss", "empty", "geometry"], BaseGeometry | None]:
    """Load cached union geometry or report miss/empty."""
    cdir = Path(cache_dir)
    complete = cdir / COMPLETE_JSON
    if not complete.is_file():
        return "miss", None
    try:
        raw = json.loads(complete.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "miss", None
    if raw.get("format") != ELIGIBLE_UNION_FORMAT:
        return "miss", None
    if raw.get("cache_digest") != expected_digest:
        return "miss", None
    got_sha = raw.get("eligible_sha")
    if expected_eligible_sha is None:
        if got_sha is not None:
            return "miss", None
    elif got_sha != expected_eligible_sha:
        return "miss", None
    src = raw.get("source_gpkg")
    if src != str(Path(expected_source_gpkg).expanduser().resolve()):
        return "miss", None

    sentinel = cdir / EMPTY_SENTINEL
    gpkg = cdir / UNION_GPKG
    if sentinel.is_file():
        return "empty", None
    if not gpkg.is_file():
        return "miss", None
    try:
        gdf = gpd.read_file(gpkg, layer=UNION_LAYER)
    except Exception:
        return "miss", None
    if gdf.empty or not gdf.geometry.notna().any():
        return "empty", None
    geo = gdf.geometry.iloc[0]
    if geo is None:
        return "empty", None
    gg = make_valid(geo) if not geo.is_valid else geo
    if gg.is_empty:
        return "empty", None
    return "geometry", gg


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
    (cdir / COMPLETE_JSON).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sentinel = cdir / EMPTY_SENTINEL
    gpkg = cdir / UNION_GPKG
    if union_wgs84 is None or getattr(union_wgs84, "is_empty", True):
        gpkg.unlink(missing_ok=True)
        (cdir / UNION_PREVIEW_PNG).unlink(missing_ok=True)
        sentinel.write_text("", encoding="utf-8")
        return

    sentinel.unlink(missing_ok=True)
    gdf = gpd.GeoDataFrame(geometry=[union_wgs84], crs="EPSG:4326")
    gdf.reset_index(drop=True).to_file(gpkg, driver="GPKG", layer=UNION_LAYER)
    write_wgs84_geometry_preview_png(
        union_wgs84,
        cdir / UNION_PREVIEW_PNG,
        face_aabbggrr=_UNION_FACE_AABBGGRR,
    )
