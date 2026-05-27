"""mesh_depth_store: band/slice geometry on disk keyed by digest."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, box

from peaky_finders.mesh_depth_store import (
    ACC_NPY,
    BAND_GPKG,
    BAND_PREVIEW_PNG,
    COMPLETE_JSON,
    EMPTY_SENTINEL,
    GRID_META_JSON,
    MESH_DEPTH_BAND_IDS,
    MESH_DEPTH_FLAT_KML_PLAIN_FMT,
    SLICE_PREVIEW_PNG,
    mesh_depth_set_digest,
    mesh_depth_set_digest_body,
    mesh_depth_set_update_kind,
    mesh_depth_flat_kml_slug_id,
    mesh_depth_stitched_flat_kml_path,
    read_mesh_depth_grid,
    resolved_mesh_depth_set_dir,
    resolved_mesh_depth_slice_dir,
    write_cached_mesh_depth_bands,
    write_cached_mesh_depth_flat_kml,
    write_mesh_depth_grid,
    write_cached_mesh_depth_slice,
)

from peaky_finders.path_labels import mesh_depth_network_rel_dir


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


def test_round_trip_mesh_depth_bands(tmp_path: Path) -> None:
    vds = ["vd1", "vd2"]
    lbl = mesh_depth_network_rel_dir(max_raster_dimension=4096)
    sdir = resolved_mesh_depth_set_dir(rel_label=lbl, cache_root=tmp_path)
    g1 = box(-115.02, 39.01, -115.00, 39.03)
    g2 = box(-115.01, 39.00, -114.99, 39.02)
    bands = {"d1_unique": g1, "d2_pair": g2}
    write_cached_mesh_depth_bands(
        set_dir=sdir,
        max_raster_dimension=4096,
        viewshed_digests=vds,
        site_slugs=["s1", "s2"],
        bands_wgs84=bands,
    )

    meta = json.loads((sdir / COMPLETE_JSON).read_text(encoding="utf-8"))
    assert meta.get("viewshed_digests") == sorted(vds)
    assert meta.get("site_slugs") == ["s1", "s2"]

    for b in MESH_DEPTH_BAND_IDS:
        bdir = sdir / "bands" / b
        if b in ("d1_unique", "d2_pair"):
            assert (bdir / BAND_GPKG).is_file()
            assert (bdir / BAND_PREVIEW_PNG).is_file()
            assert not (bdir / EMPTY_SENTINEL).is_file()
        else:
            assert (bdir / EMPTY_SENTINEL).is_file()


def test_mesh_depth_slice_empty_on_disk(tmp_path: Path) -> None:
    sdir = tmp_path / "set"
    slice_dir = resolved_mesh_depth_slice_dir(set_dir=sdir, band="d1_unique", site_vd="vdx")
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d1_unique", site_vd="vdx", slice_wgs84=None)
    assert (slice_dir / EMPTY_SENTINEL).is_file()


def test_mesh_depth_slice_geometry_round_trip(tmp_path: Path) -> None:
    sdir = tmp_path / "set"
    slice_dir = resolved_mesh_depth_slice_dir(set_dir=sdir, band="d2_pair", site_vd="vdz")
    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d2_pair", site_vd="vdz", slice_wgs84=geom)
    assert (slice_dir / SLICE_PREVIEW_PNG).is_file()
    loaded = gpd.read_file(slice_dir / "slice.gpkg").geometry.iloc[0]
    assert loaded is not None
    assert abs(geom.area - loaded.area) < 1e-14


def test_mesh_depth_flat_kml_slug_id_distinct_slugs() -> None:
    assert mesh_depth_flat_kml_slug_id("sa") != mesh_depth_flat_kml_slug_id("sb")


def test_mesh_depth_flat_kml_roundtrip_meta(tmp_path: Path) -> None:
    slug = "site_x"
    slice_dir = tmp_path / "s"
    slice_dir.mkdir(parents=True)
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
    kml_p = mesh_depth_stitched_flat_kml_path(slice_dir, role="plain", slug=slug)
    assert kml_p.read_text(encoding="utf-8") == "<kml>x</kml>"
    meta_path = slice_dir / f"mesh_depth_plain_{mesh_depth_flat_kml_slug_id(slug)}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["format"] == MESH_DEPTH_FLAT_KML_PLAIN_FMT
    assert meta["fingerprint"] == fp


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
    elig_kml = mesh_depth_stitched_flat_kml_path(slice_dir, role="eligible", slug=slug)
    assert elig_kml.is_file()
    geom = Polygon(
        ((-115.02, 39.01), (-115.0, 39.01), (-115.0, 39.03), (-115.02, 39.03), (-115.02, 39.01))
    )
    write_cached_mesh_depth_slice(slice_dir=slice_dir, band="d1_unique", site_vd="vdz", slice_wgs84=geom)
    assert not elig_kml.is_file()


def test_mesh_depth_set_update_kind_current_extend_rebuild(tmp_path: Path) -> None:
    import numpy as np
    from rasterio.transform import from_bounds

    sdir = tmp_path / "set"
    vds = ["vd1", "vd2"]
    write_cached_mesh_depth_bands(
        set_dir=sdir,
        max_raster_dimension=4096,
        viewshed_digests=vds,
        site_slugs=["s1", "s2"],
        bands_wgs84={},
    )
    acc = np.zeros((4, 4), dtype=np.uint32)
    write_mesh_depth_grid(
        set_dir=sdir,
        acc=acc,
        transform=from_bounds(0, 0, 100, 100, 4, 4),
        bounds=(0.0, 0.0, 100.0, 100.0),
    )
    slug_map = {"s1": "vd1", "s2": "vd2"}

    assert (
        mesh_depth_set_update_kind(
            set_dir=sdir,
            viewshed_digests=vds,
            site_slugs=["s1", "s2"],
            max_raster_dimension=4096,
            site_slug_to_digest=slug_map,
        )
        == "current"
    )
    assert (
        mesh_depth_set_update_kind(
            set_dir=sdir,
            viewshed_digests=[*vds, "vd3"],
            site_slugs=["s1", "s2", "s3"],
            max_raster_dimension=4096,
            site_slug_to_digest={**slug_map, "s3": "vd3"},
        )
        == "extend"
    )
    assert (
        mesh_depth_set_update_kind(
            set_dir=sdir,
            viewshed_digests=["vd9"],
            site_slugs=["s9"],
            max_raster_dimension=4096,
            site_slug_to_digest={"s9": "vd9"},
        )
        == "rebuild"
    )
    assert (sdir / ACC_NPY).is_file()
    assert (sdir / GRID_META_JSON).is_file()
    loaded = read_mesh_depth_grid(sdir)
    assert loaded is not None
    assert loaded[0].shape == (4, 4)
