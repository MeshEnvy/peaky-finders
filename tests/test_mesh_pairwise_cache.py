"""mesh_pairwise_cache: digest and plain overlap geometry persistence."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon, box

from peaky_finders.mesh_pairwise_cache import (
    mesh_pairwise_pair_digest,
    mesh_pairwise_pair_digest_body,
    resolved_mesh_pairwise_pair_dir,
    try_read_cached_pair_overlap_geometry,
    write_cached_pair_overlap_geometry,
)


def test_mesh_pairwise_pair_digest_symmetry() -> None:
    a = "aaa111"
    b = "bbb222"
    assert mesh_pairwise_pair_digest(a, b) == mesh_pairwise_pair_digest(b, a)
    digest_aa = mesh_pairwise_pair_digest(a, a)
    assert len(digest_aa) == 64


def test_mesh_pairwise_pair_digest_body_stable_order() -> None:
    assert mesh_pairwise_pair_digest_body("x", "y") == mesh_pairwise_pair_digest_body("y", "x")


def test_try_read_miss_when_no_manifest(tmp_path: Path) -> None:
    pd = tmp_path / "slot"
    pd.mkdir()
    k, geo = try_read_cached_pair_overlap_geometry(pd)
    assert k == "miss" and geo is None


def test_round_trip_geometry_gpkg(tmp_path: Path) -> None:
    root = Path(tmp_path) / "cache"
    dig = mesh_pairwise_pair_digest("vd1", "vd2")
    pdir = resolved_mesh_pairwise_pair_dir(pair_digest=dig, cache_root=root)
    geom = box(-115.02, 39.01, -115.00, 39.03)
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="vd1", vd_b="vd2", overlap_wgs84=geom)

    assert (pdir / "overlap.png").is_file()
    kind, loaded = try_read_cached_pair_overlap_geometry(pdir)
    assert kind == "geometry"
    assert loaded is not None and not loaded.is_empty
    assert abs(geom.area - loaded.area) < 1e-14


def test_empty_sentinel_round_trip(tmp_path: Path) -> None:
    pdir = tmp_path / "z"
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=None)

    kind, geo = try_read_cached_pair_overlap_geometry(pdir)
    assert kind == "empty" and geo is None


def test_write_nonempty_removes_prior_empty_sentinel(tmp_path: Path) -> None:
    pdir = tmp_path / "mix"
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=None)
    assert (pdir / "empty").is_file()

    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_pair_overlap_geometry(pair_dir=pdir, vd_a="a", vd_b="b", overlap_wgs84=geom)
    assert not (pdir / "empty").is_file()
    assert (pdir / "overlap.gpkg").is_file()


def test_dem_peak_digest_stable_under_tiny_coordinate_jitter() -> None:
    from shapely.geometry import Polygon

    from peaky_finders.mesh_pairwise_cache import pairwise_overlap_geometry_digest_sha256

    a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    b = Polygon([(1e-12, 0), (1, 0), (1, 1), (0, 1), (1e-12, 0)])
    assert pairwise_overlap_geometry_digest_sha256(a) == pairwise_overlap_geometry_digest_sha256(b)


def test_dem_peak_plain_round_trip(tmp_path: Path) -> None:
    from peaky_finders.mesh_pairwise_cache import (
        DEM_PEAK_PLAIN_JSON,
        try_read_cached_pairwise_dem_peak,
        write_cached_pairwise_dem_peak,
    )

    pdir = tmp_path / "pair"
    pdir.mkdir()
    path = pdir / DEM_PEAK_PLAIN_JSON
    write_cached_pairwise_dem_peak(path, peak_llz=(-115.1, 39.2, 50.0))
    hit, got = try_read_cached_pairwise_dem_peak(path)
    assert hit and got == (-115.1, 39.2, 50.0)


def test_dem_peak_eligible_requires_digest_match(tmp_path: Path) -> None:
    from peaky_finders.mesh_pairwise_cache import (
        DEM_PEAK_ELIGIBLE_JSON,
        try_read_cached_pairwise_dem_peak,
        write_cached_pairwise_dem_peak,
    )

    pdir = tmp_path / "pair"
    pdir.mkdir()
    path = pdir / DEM_PEAK_ELIGIBLE_JSON
    write_cached_pairwise_dem_peak(
        path, peak_llz=(1.0, 2.0, 3.0), geometry_digest="aaa"
    )
    assert not try_read_cached_pairwise_dem_peak(path, expected_geometry_digest="bbb")[0]
    hit, got = try_read_cached_pairwise_dem_peak(path, expected_geometry_digest="aaa")
    assert hit and got == (1.0, 2.0, 3.0)


def test_pairwise_flat_kml_cache_roundtrip(tmp_path: Path) -> None:
    from peaky_finders.mesh_pairwise_cache import (
        PAIRWISE_FLAT_ELIG_KML,
        PAIRWISE_FLAT_PLAIN_KML,
        try_read_cached_pairwise_flat_kml,
        write_cached_pairwise_flat_kml,
    )

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
    dest_plain = tmp_path / "scratch_plain.kml"
    assert try_read_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="plain",
        fingerprint=fp_plain,
        dest_kml=dest_plain,
    )
    assert dest_plain.read_text(encoding="utf-8") == "<kml>plain</kml>"
    assert (slot / PAIRWISE_FLAT_PLAIN_KML).is_file()

    assert not try_read_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="plain",
        fingerprint="cd" * 32,
        dest_kml=dest_plain,
    )

    elig_src = slot / "out_elig.kml"
    elig_src.write_text("<kml>elig</kml>", encoding="utf-8")
    fp_elig = "ef" * 32
    write_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="eligible",
        fingerprint=fp_elig,
        source_kml=elig_src,
    )
    dest_elig = tmp_path / "scratch_elig.kml"
    assert try_read_cached_pairwise_flat_kml(
        pair_dir=slot,
        role="eligible",
        fingerprint=fp_elig,
        dest_kml=dest_elig,
    )
    assert dest_elig.read_text(encoding="utf-8") == "<kml>elig</kml>"
    assert (slot / PAIRWISE_FLAT_ELIG_KML).is_file()
