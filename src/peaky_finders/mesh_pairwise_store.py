"""Pairwise footprint intersection geometry keyed by viewshed digests (on-disk store)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Literal

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from peaky_finders.geometry_preview_png import write_wgs84_geometry_preview_png

PAIRWISE_GEOMETRY_FORMAT = "peaky_mesh_pairwise_geometry/v1"
EMPTY_SENTINEL = "empty"
COMPLETE_JSON = "complete.json"
OVERLAP_GPKG = "overlap.gpkg"
OVERLAP_LAYER = "overlap"
OVERLAP_PREVIEW_PNG = "overlap.png"

PAIRWISE_DEM_PEAK_FORMAT = "peaky_mesh_pairwise_dem_peak/v1"
DEM_PEAK_PLAIN_JSON = "dem_peak_plain.json"
DEM_PEAK_ELIGIBLE_JSON = "dem_peak_eligible.json"

PAIRWISE_FLAT_KML_CACHE_PLAIN = "peaky_mesh_pairwise_flat_kml_plain/v1"
PAIRWISE_FLAT_KML_CACHE_ELIG = "peaky_mesh_pairwise_flat_kml_eligible/v1"
PAIRWISE_FLAT_PLAIN_KML = "pairwise_plain.kml"
PAIRWISE_FLAT_PLAIN_META = "pairwise_plain.meta.json"
PAIRWISE_FLAT_ELIG_KML = "pairwise_eligible.kml"
PAIRWISE_FLAT_ELIG_META = "pairwise_eligible.meta.json"


def mesh_pairwise_pair_digest_body(vd_a: str, vd_b: str) -> str:
    lo, hi = sorted((vd_a, vd_b))
    return f"peaky_mesh_pairwise/v1\n{lo}\n{hi}\n"


def mesh_pairwise_pair_digest(vd_a: str, vd_b: str) -> str:
    """SHA256 of canonical unordered ``(viewshed_workspace_digest,)`` pair."""
    return hashlib.sha256(mesh_pairwise_pair_digest_body(vd_a, vd_b).encode("utf-8")).hexdigest()


def resolved_mesh_pairwise_pair_dir(*, slug_a: str, slug_b: str, cache_root: Path) -> Path:
    from peaky_finders.path_labels import mesh_pairwise_rel_dir

    rel = mesh_pairwise_rel_dir(slug_a, slug_b)
    return Path(cache_root).expanduser().resolve() / rel


def _canonical_vds(vd_a: str, vd_b: str) -> tuple[str, str]:
    xs = sorted((vd_a, vd_b))
    return (xs[0], xs[1])




def write_cached_pair_overlap_geometry(
    *,
    pair_dir: Path,
    vd_a: str,
    vd_b: str,
    overlap_wgs84: BaseGeometry | None,
) -> None:
    """Write cache entry for one unordered viewshed digest pair."""

    pdir = Path(pair_dir).expanduser().resolve()
    pdir.mkdir(parents=True, exist_ok=True)
    lo, hi = _canonical_vds(vd_a, vd_b)
    complete = {"format": PAIRWISE_GEOMETRY_FORMAT, "vd_lo": lo, "vd_hi": hi}
    (pdir / COMPLETE_JSON).write_text(
        json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sentinel = pdir / EMPTY_SENTINEL
    gpkg = pdir / OVERLAP_GPKG
    if overlap_wgs84 is None or getattr(overlap_wgs84, "is_empty", True):
        if gpkg.exists():
            gpkg.unlink(missing_ok=True)
        (pdir / OVERLAP_PREVIEW_PNG).unlink(missing_ok=True)
        _unlink_mesh_pairwise_pair_derived_caches_except_preview(pdir)
        sentinel.write_text("", encoding="utf-8")
        return

    sentinel.unlink(missing_ok=True)
    _unlink_mesh_pairwise_pair_derived_caches(pdir)
    gdf = gpd.GeoDataFrame(geometry=[overlap_wgs84], crs="EPSG:4326")
    gdf.reset_index(drop=True).to_file(gpkg, driver="GPKG", layer=OVERLAP_LAYER)
    write_wgs84_geometry_preview_png(overlap_wgs84, pdir / OVERLAP_PREVIEW_PNG)


def pairwise_overlap_geometry_digest_sha256(geom: BaseGeometry) -> str:
    """SHA256 of grid-snapped WKB.

    Snapping removes float noise so eligible-clip digests match after overlap geometry is
    round-tripped through the ``overlap.gpkg`` cache (GPKG/Shapely can perturb coordinates).
    """
    from shapely import set_precision

    g = make_valid(geom) if not geom.is_valid else geom
    if g.is_empty:
        return ""
    # ~1e-7° ≈ 1.1 cm longitude at equator — enough to stabilize interchange without changing coverage much.
    g2 = set_precision(g, 1e-7)
    return hashlib.sha256(g2.wkb).hexdigest()


def _unlink_mesh_pairwise_pair_derived_caches_except_preview(pdir: Path) -> None:
    """Clear overlap-derived sidecars (empty-slot path still has PNG removed above)."""
    pdir = Path(pdir)
    (pdir / DEM_PEAK_PLAIN_JSON).unlink(missing_ok=True)
    (pdir / DEM_PEAK_ELIGIBLE_JSON).unlink(missing_ok=True)
    (pdir / PAIRWISE_FLAT_PLAIN_KML).unlink(missing_ok=True)
    (pdir / PAIRWISE_FLAT_PLAIN_META).unlink(missing_ok=True)
    (pdir / PAIRWISE_FLAT_ELIG_KML).unlink(missing_ok=True)
    (pdir / PAIRWISE_FLAT_ELIG_META).unlink(missing_ok=True)


def _unlink_mesh_pairwise_pair_derived_caches(pdir: Path) -> None:
    """DEM peaks + stitched flat KML; call before writing a fresh ``overlap.gpkg``."""
    _unlink_mesh_pairwise_pair_derived_caches_except_preview(pdir)




def write_cached_pairwise_flat_kml(
    *,
    pair_dir: Path,
    role: Literal["plain", "eligible"],
    fingerprint: str,
    source_kml: Path,
) -> None:
    """Persist final flat pairwise KML (after style injection)."""
    pdir = Path(pair_dir).expanduser().resolve()
    pdir.mkdir(parents=True, exist_ok=True)
    if role == "plain":
        kml_p, meta_p, want_fmt = pdir / PAIRWISE_FLAT_PLAIN_KML, pdir / PAIRWISE_FLAT_PLAIN_META, PAIRWISE_FLAT_KML_CACHE_PLAIN
    else:
        kml_p, meta_p = pdir / PAIRWISE_FLAT_ELIG_KML, pdir / PAIRWISE_FLAT_ELIG_META
        want_fmt = PAIRWISE_FLAT_KML_CACHE_ELIG
    shutil.copy2(source_kml, kml_p)
    meta_p.write_text(
        json.dumps(
            {"format": want_fmt, "fingerprint": fingerprint},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_cached_pairwise_dem_peak(
    json_path: Path,
    *,
    peak_llz: tuple[float, float, float] | None,
    geometry_digest: str | None = None,
) -> None:
    """Persist Skadi sample. Set ``geometry_digest`` for eligible clip peaks only."""

    path = Path(json_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {"format": PAIRWISE_DEM_PEAK_FORMAT, "peak": None}
    if geometry_digest is not None:
        body["geometry_digest"] = geometry_digest
    if peak_llz is not None:
        lon, lat, z = peak_llz
        body["peak"] = {"lon": lon, "lat": lat, "elev_m": z}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
