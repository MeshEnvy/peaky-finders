"""Persisted mesh coverage depth bands and per-site slice geometry (WGS84)."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from peaky_finders.geometry_preview_png import write_wgs84_geometry_preview_png

MESH_DEPTH_GEOMETRY_FORMAT = "peaky_mesh_depth_geometry/v1"
MESH_DEPTH_FLAT_KML_PLAIN_FMT = "peaky_mesh_depth_flat_kml_plain/v1"
MESH_DEPTH_FLAT_KML_ELIG_FMT = "peaky_mesh_depth_flat_kml_eligible/v1"

EMPTY_SENTINEL = "empty"
COMPLETE_JSON = "complete.json"
BAND_GPKG = "band.gpkg"
BAND_LAYER = "band"
SLICE_GPKG = "slice.gpkg"
SLICE_LAYER = "slice"
BAND_PREVIEW_PNG = "band.png"
SLICE_PREVIEW_PNG = "slice.png"

MESH_DEPTH_BAND_IDS: tuple[str, ...] = ("d1_unique", "d2_pair", "d3_quad", "d5_plus")

# Distinct fill tints (aabbggrr) for quick visual scan in a file browser.
_MESH_DEPTH_BAND_FACE_AABBGGRR: dict[str, str] = {
    "d1_unique": "668800ff",
    "d2_pair": "66ffaa00",
    "d3_quad": "6600cc88",
    "d5_plus": "66aa6600",
}


def mesh_depth_set_digest_body(*, viewshed_digests: Sequence[str], max_raster_dimension: int) -> str:
    """Canonical multiset of viewshed digests (sorted) plus raster size."""
    lines = ["peaky_mesh_depth/v1", f"max_raster={int(max_raster_dimension)}"]
    for vd in sorted(viewshed_digests):
        lines.append(vd)
    return "\n".join(lines) + "\n"


def mesh_depth_set_digest(*, viewshed_digests: Sequence[str], max_raster_dimension: int) -> str:
    """SHA256 hex for ``…/mesh_depth/<digest>/`` directory name."""
    return hashlib.sha256(
        mesh_depth_set_digest_body(
            viewshed_digests=viewshed_digests,
            max_raster_dimension=max_raster_dimension,
        ).encode("utf-8")
    ).hexdigest()


def resolved_mesh_depth_set_dir(*, rel_label: str, cache_root: Path) -> Path:
    return Path(cache_root).expanduser().resolve() / rel_label


def mesh_depth_flat_kml_slug_id(slug: str) -> str:
    """Short stable ASCII id for filenames (multiple preset sites may share one viewshed digest)."""
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()[:24]


def _mesh_depth_flat_kml_paths(slice_dir: Path, role: Literal["plain", "eligible"], slug: str) -> tuple[Path, Path]:
    hid = mesh_depth_flat_kml_slug_id(slug)
    pdir = Path(slice_dir)
    if role == "plain":
        return pdir / f"mesh_depth_plain_{hid}.kml", pdir / f"mesh_depth_plain_{hid}.meta.json"
    return pdir / f"mesh_depth_eligible_{hid}.kml", pdir / f"mesh_depth_eligible_{hid}.meta.json"


def mesh_depth_stitched_flat_kml_path(slice_dir: Path, *, role: Literal["plain", "eligible"], slug: str) -> Path:
    """Persisted flat depth KML for one site-band slice."""
    return _mesh_depth_flat_kml_paths(slice_dir, role, slug)[0]


def _unlink_mesh_depth_slice_flat_kml_caches(slice_dir: Path) -> None:
    """Remove stitched flat-KML artifacts when overlap slice geometry changes."""
    pdir = Path(slice_dir)
    for pattern in (
        "mesh_depth_plain_*.kml",
        "mesh_depth_plain_*.meta.json",
        "mesh_depth_eligible_*.kml",
        "mesh_depth_eligible_*.meta.json",
    ):
        for f in list(pdir.glob(pattern)):
            f.unlink(missing_ok=True)





def write_cached_mesh_depth_flat_kml(
    *,
    slice_dir: Path,
    role: Literal["plain", "eligible"],
    slug: str,
    fingerprint: str,
    source_kml: Path,
) -> None:
    """Persist final flat mesh-depth KML (after style injection) next to geometry slice."""
    want_fmt = MESH_DEPTH_FLAT_KML_PLAIN_FMT if role == "plain" else MESH_DEPTH_FLAT_KML_ELIG_FMT
    pdir = Path(slice_dir).expanduser().resolve()
    pdir.mkdir(parents=True, exist_ok=True)
    kml_p, meta_p = _mesh_depth_flat_kml_paths(pdir, role, slug)
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


def write_cached_mesh_depth_bands(
    *,
    set_dir: Path,
    max_raster_dimension: int,
    viewshed_digests: Sequence[str],
    bands_wgs84: Mapping[str, BaseGeometry],
) -> None:
    """Persist band geometries under ``set_dir/bands/<band>/``."""
    sdir = Path(set_dir).expanduser().resolve()
    sdir.mkdir(parents=True, exist_ok=True)
    exp = sorted(viewshed_digests)
    top = {
        "format": MESH_DEPTH_GEOMETRY_FORMAT,
        "max_raster_dimension": int(max_raster_dimension),
        "viewshed_digests": exp,
    }
    (sdir / COMPLETE_JSON).write_text(
        json.dumps(top, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    bands_root = sdir / "bands"
    bands_root.mkdir(parents=True, exist_ok=True)

    for band in MESH_DEPTH_BAND_IDS:
        bdir = bands_root / band
        bdir.mkdir(parents=True, exist_ok=True)
        geom = bands_wgs84.get(band)
        bc = {"format": MESH_DEPTH_GEOMETRY_FORMAT, "band": band}
        (bdir / COMPLETE_JSON).write_text(
            json.dumps(bc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        sentinel = bdir / EMPTY_SENTINEL
        gpkg = bdir / BAND_GPKG
        if geom is None or getattr(geom, "is_empty", True):
            gpkg.unlink(missing_ok=True)
            (bdir / BAND_PREVIEW_PNG).unlink(missing_ok=True)
            sentinel.write_text("", encoding="utf-8")
            continue
        sentinel.unlink(missing_ok=True)
        gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
        gdf.reset_index(drop=True).to_file(gpkg, driver="GPKG", layer=BAND_LAYER)
        write_wgs84_geometry_preview_png(
            geom,
            bdir / BAND_PREVIEW_PNG,
            face_aabbggrr=_MESH_DEPTH_BAND_FACE_AABBGGRR.get(band, "66888888"),
        )




def write_cached_mesh_depth_slice(
    *,
    slice_dir: Path,
    band: str,
    site_vd: str,
    slice_wgs84: BaseGeometry | None,
) -> None:
    """Write one per-site band slice slot keyed by viewshed digest."""
    pdir = Path(slice_dir).expanduser().resolve()
    pdir.mkdir(parents=True, exist_ok=True)
    _unlink_mesh_depth_slice_flat_kml_caches(pdir)
    meta = {
        "format": MESH_DEPTH_GEOMETRY_FORMAT,
        "band": band,
        "site_viewshed_digest": site_vd,
    }
    (pdir / COMPLETE_JSON).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sentinel = pdir / EMPTY_SENTINEL
    gpkg = pdir / SLICE_GPKG
    if slice_wgs84 is None or getattr(slice_wgs84, "is_empty", True):
        gpkg.unlink(missing_ok=True)
        (pdir / SLICE_PREVIEW_PNG).unlink(missing_ok=True)
        sentinel.write_text("", encoding="utf-8")
        return
    sentinel.unlink(missing_ok=True)
    gdf = gpd.GeoDataFrame(geometry=[slice_wgs84], crs="EPSG:4326")
    gdf.reset_index(drop=True).to_file(gpkg, driver="GPKG", layer=SLICE_LAYER)
    write_wgs84_geometry_preview_png(
        slice_wgs84,
        pdir / SLICE_PREVIEW_PNG,
        face_aabbggrr=_MESH_DEPTH_BAND_FACE_AABBGGRR.get(band, "66888888"),
    )


def resolved_mesh_depth_slice_dir(*, set_dir: Path, band: str, site_vd: str) -> Path:
    """``…/slices/<band>/<site_vd>/`` under a set directory."""
    return Path(set_dir) / "slices" / band / site_vd
