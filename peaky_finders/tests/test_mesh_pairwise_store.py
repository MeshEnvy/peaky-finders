"""mesh_pairwise_store: digest and persisted overlap geometry on disk."""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Polygon, box
import geopandas as gpd

from peaky_finders.mesh_pairwise_store import (
    COMPLETE_JSON,
    DEM_PEAK_ELIGIBLE_JSON,
    DEM_PEAK_PLAIN_JSON,
    EMPTY_SENTINEL,
    OVERLAP_GPKG,
    OVERLAP_PREVIEW_PNG,
    PAIRWISE_FLAT_ELIG_KML,
    PAIRWISE_FLAT_PLAIN_KML,
    PAIRWISE_DEM_PEAK_FORMAT,
    mesh_pairwise_pair_digest,
    mesh_pairwise_pair_digest_body,
    pairwise_overlap_geometry_digest_sha256,
    resolved_mesh_pairwise_pair_dir,
    write_cached_pair_overlap_geometry,
    write_cached_pairwise_dem_peak,
    write_cached_pairwise_flat_kml,
)


def test_mesh_pairwise_pair_digest_symmetry() -> None:
    a = "aaa111"
    b = "bbb222"
    assert mesh_pairwise_pair_digest(a, b) == mesh_pairwise_pair_digest(b, a)
    digest_aa = mesh_pairwise_pair_digest(a, a)
    assert len(digest_aa) == 64


def test_mesh_pairwise_pair_digest_body_stable_order() -> None:
    assert mesh_pairwise_pair_digest_body("x", "y") == mesh_pairwise_pair_digest_body("y", "x")


def test_miss_when_only_complete_json_partial(tmp_path: Path) -> None:
    pd = tmp_path / "slot"
    pd.mkdir()
    (pd / COMPLETE_JSON).write_text("{}", encoding="utf-8")
    assert not (pd / EMPTY_SENTINEL).is_file()


def test_round_trip_geometry_gpkg(tmp_path: Path) -> None:
    root = Path(tmp_path) / "cache"
    pdir = resolved_mesh_pairwise_pair_dir(slug_a="site_a", slug_b="site_b", cache_root=root)
    geom = box(-115.02, 39.01, -115.00, 39.03)
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="vd1", vd_b="vd2", overlap_wgs84=geom)

    assert (pdir / OVERLAP_PREVIEW_PNG).is_file()
    assert (pdir / COMPLETE_JSON).is_file()
    loaded = gpd.read_file(pdir / OVERLAP_GPKG).geometry.iloc[0]
    assert loaded is not None and not loaded.is_empty
    assert abs(geom.area - loaded.area) < 1e-14


def test_empty_sentinel_round_trip(tmp_path: Path) -> None:
    pdir = tmp_path / "z"
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=None)

    assert (pdir / EMPTY_SENTINEL).is_file()
    assert not (pdir / OVERLAP_GPKG).is_file()


def test_write_nonempty_removes_prior_empty_sentinel(tmp_path: Path) -> None:
    pdir = tmp_path / "mix"
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=None)
    assert (pdir / EMPTY_SENTINEL).is_file()

    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=geom)
    assert not (pdir / EMPTY_SENTINEL).is_file()
    assert (pdir / OVERLAP_GPKG).is_file()


def test_dem_peak_digest_stable_under_tiny_coordinate_jitter() -> None:
    a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    b = Polygon([(1e-12, 0), (1, 0), (1, 1), (0, 1), (1e-12, 0)])
    assert pairwise_overlap_geometry_digest_sha256(a) == pairwise_overlap_geometry_digest_sha256(b)


def _read_dem_peak(path: Path) -> tuple[float, float, float] | None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("format") != PAIRWISE_DEM_PEAK_FORMAT:
        return None
    peak = raw.get("peak")
    if peak is None:
        return None
    return float(peak["lon"]), float(peak["lat"]), float(peak["elev_m"])


def test_dem_peak_plain_round_trip(tmp_path: Path) -> None:
    pdir = tmp_path / "pair"
    pdir.mkdir()
    path = pdir / DEM_PEAK_PLAIN_JSON
    write_cached_pairwise_dem_peak(path, peak_llz=(-115.1, 39.2, 50.0))
    got = _read_dem_peak(path)
    assert got == (-115.1, 39.2, 50.0)


def test_dem_peak_eligible_stores_digest(tmp_path: Path) -> None:
    pdir = tmp_path / "pair"
    pdir.mkdir()
    path = pdir / DEM_PEAK_ELIGIBLE_JSON
    write_cached_pairwise_dem_peak(path, peak_llz=(1.0, 2.0, 3.0), geometry_digest="aaa")
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body.get("geometry_digest") == "aaa"
    got = _read_dem_peak(path)
    assert got == (1.0, 2.0, 3.0)


def test_pairwise_flat_kml_written(tmp_path: Path) -> None:
    slot = tmp_path / "slot"
    slot.mkdir()
    src_plain = slot / "out_plain.kml"
    src_plain.write_text("<kml>plain</kml>", encoding="utf-8")
    fp_plain = "ab" * 32
    write_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="plain",
        fingerprint=fp_plain,
        source_kml=src_plain,
    )
    assert (slot / PAIRWISE_FLAT_PLAIN_KML).read_text(encoding="utf-8") == "<kml>plain</kml>"

    elig_src = slot / "out_elig.kml"
    elig_src.write_text("<kml>elig</kml>", encoding="utf-8")
    fp_elig = "ef" * 32
    write_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="eligible",
        fingerprint=fp_elig,
        source_kml=elig_src,
    )
    assert (slot / PAIRWISE_FLAT_ELIG_KML).read_text(encoding="utf-8") == "<kml>elig</kml>"
