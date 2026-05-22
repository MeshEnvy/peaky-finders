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
