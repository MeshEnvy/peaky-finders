"""mesh_depth_cache: set digest and band/slice geometry persistence."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon, box

from peaky_finders.mesh_depth_cache import (
    BAND_PREVIEW_PNG,
    MESH_DEPTH_BAND_IDS,
    SLICE_PREVIEW_PNG,
    mesh_depth_set_digest,
    mesh_depth_set_digest_body,
    mesh_depth_flat_kml_slug_id,
    resolved_mesh_depth_set_dir,
    resolved_mesh_depth_slice_dir,
    try_read_cached_mesh_depth_bands,
    try_read_cached_mesh_depth_flat_kml,
    try_read_cached_mesh_depth_slice,
    write_cached_mesh_depth_bands,
    write_cached_mesh_depth_flat_kml,
    write_cached_mesh_depth_slice,
)


def test_mesh_depth_set_digest_multiset_order_stable() -> None:
    a = mesh_depth_set_digest_body(viewshed_digests=["b", "a"], max_raster_dimension=4096)
    b = mesh_depth_set_digest_body(viewshed_digests=["a", "b"], max_raster_dimension=4096)
    assert a == b
    c = mesh_depth_set_digest_body(viewshed_digests=["a", "a", "b"], max_raster_dimension=4096)
    d = mesh_depth_set_digest_body(viewshed_digests=["a", "b", "a"], max_raster_dimension=4096)
    assert c == d


def test_mesh_depth_set_digest_max_raster_sensitive() -> None:
    body_4k = mesh_depth_set_digest(viewshed_digests=["x"], max_raster_dimension=4096)
    body_2k = mesh_depth_set_digest(viewshed_digests=["x"], max_raster_dimension=2048)
    assert body_4k != body_2k


def test_mesh_depth_set_digest_hex_length() -> None:
    d = mesh_depth_set_digest(viewshed_digests=["vd1", "vd2"], max_raster_dimension=512)
    assert len(d) == 64


def test_try_read_mesh_depth_bands_miss_without_top_complete(tmp_path: Path) -> None:
    root = resolved_mesh_depth_set_dir(
        set_digest=mesh_depth_set_digest(viewshed_digests=["a"], max_raster_dimension=512),
        cache_root=tmp_path,
    )
    k, bands = try_read_cached_mesh_depth_bands(
        root,
        expected_max_raster=512,
        expected_viewshed_digests=["a"],
    )
    assert k == "miss" and bands is None


def test_round_trip_mesh_depth_bands(tmp_path: Path) -> None:
    vds = ["vd1", "vd2"]
    dig = mesh_depth_set_digest(viewshed_digests=vds, max_raster_dimension=4096)
    sdir = resolved_mesh_depth_set_dir(set_digest=dig, cache_root=tmp_path)
    g1 = box(-115.02, 39.01, -115.00, 39.03)
    g2 = box(-115.01, 39.00, -114.99, 39.02)
    bands = {"d1_unique": g1, "d2_pair": g2}
    write_cached_mesh_depth_bands(
        set_dir=sdir,
        max_raster_dimension=4096,
        viewshed_digests=vds,
        bands_wgs84=bands,
    )
    kind, loaded = try_read_cached_mesh_depth_bands(
        sdir,
        expected_max_raster=4096,
        expected_viewshed_digests=vds,
    )
    assert kind == "hit" and loaded is not None
    assert "d1_unique" in loaded and "d2_pair" in loaded
    for b in MESH_DEPTH_BAND_IDS:
        if b not in ("d1_unique", "d2_pair"):
            assert b not in loaded
        else:
            assert (sdir / "bands" / b / BAND_PREVIEW_PNG).is_file()


def test_mesh_depth_slice_empty_round_trip(tmp_path: Path) -> None:
    sdir = tmp_path / "set"
    slice_dir = resolved_mesh_depth_slice_dir(set_dir=sdir, band="d1_unique", site_vd="vdx")
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d1_unique", site_vd="vdx", slice_wgs84=None)
    k, g = try_read_cached_mesh_depth_slice(slice_dir)
    assert k == "empty" and g is None


def test_mesh_depth_slice_geometry_round_trip(tmp_path: Path) -> None:
    sdir = tmp_path / "set"
    slice_dir = resolved_mesh_depth_slice_dir(set_dir=sdir, band="d2_pair", site_vd="vdz")
    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d2_pair", site_vd="vdz", slice_wgs84=geom)
    assert (slice_dir / SLICE_PREVIEW_PNG).is_file()
    k, loaded = try_read_cached_mesh_depth_slice(slice_dir)
    assert k == "geometry" and loaded is not None
    assert abs(geom.area - loaded.area) < 1e-14


def test_mesh_depth_flat_kml_slug_id_distinct_slugs() -> None:
    assert mesh_depth_flat_kml_slug_id("sa") != mesh_depth_flat_kml_slug_id("sb")


def test_mesh_depth_flat_kml_try_read_miss_without_files(tmp_path: Path) -> None:
    slug = "n1"
    slice_dir = tmp_path / "s"
    slice_dir.mkdir()
    dest = tmp_path / "out.kml"
    assert not try_read_cached_mesh_depth_flat_kml(
        slice_dir=slice_dir,
        role="plain",
        slug=slug,
        fingerprint="ab" * 32,
        dest_kml=dest,
    )


def test_mesh_depth_flat_kml_roundtrip(tmp_path: Path) -> None:
    slug = "site_x"
    slice_dir = tmp_path / "s"
    slice_dir.mkdir()
    src = slice_dir / "scratch.kml"
    src.write_text("<kml>x</kml>", encoding="utf-8")
    fp = "cd" * 32
    write_cached_mesh_depth_flat_kml(
        slice_dir=slice_dir,
        role="plain",
        slug=slug,
        fingerprint=fp,
        source_kml=src,
    )
    hid = mesh_depth_flat_kml_slug_id(slug)
    assert (slice_dir / f"mesh_depth_plain_{hid}.kml").is_file()
    dest = tmp_path / "copy.kml"
    assert try_read_cached_mesh_depth_flat_kml(
        slice_dir=slice_dir,
        role="plain",
        slug=slug,
        fingerprint=fp,
        dest_kml=dest,
    )
    assert dest.read_text(encoding="utf-8") == "<kml>x</kml>"


def test_write_mesh_depth_slice_clears_flat_kml_sidecars(tmp_path: Path) -> None:
    sdir = tmp_path / "set"
    slice_dir = resolved_mesh_depth_slice_dir(set_dir=sdir, band="d1_unique", site_vd="vdz")
    slice_dir.mkdir(parents=True)
    slug = "s_slug"
    src = slice_dir / "t.kml"
    src.write_text("<kml/>", encoding="utf-8")
    write_cached_mesh_depth_flat_kml(
        slice_dir=slice_dir,
        role="eligible",
        slug=slug,
        fingerprint="ee" * 32,
        source_kml=src,
    )
    hid = mesh_depth_flat_kml_slug_id(slug)
    elig_kml = slice_dir / f"mesh_depth_eligible_{hid}.kml"
    assert elig_kml.is_file()
    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d1_unique", site_vd="vdz", slice_wgs84=geom)
    assert not elig_kml.is_file()
