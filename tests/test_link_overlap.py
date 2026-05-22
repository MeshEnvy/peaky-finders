"""Pairwise SPLAT footprint overlap geometry and standalone KML output."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

import peaky_finders.link_overlap as link_overlap_module
from peaky_finders.path_labels import mesh_depth_network_rel_dir, mesh_pairwise_rel_dir
from peaky_finders.link_overlap import (
    compute_pair_eligible_overlap_geometry,
    compute_pair_overlap_geometry,
    read_eligible_land_use_union,
    write_pairwise_eligible_link_overlap_layers,
    write_pairwise_link_overlap_layers,
)
from peaky_finders.mesh_coverage_depth import compute_footprint_depth_bands_wgs84
from peaky_finders.sites_job import (
    DEFAULT_MESH_PAIRWISE_ELIGIBLE_KML_STYLE,
    DEFAULT_MESH_PAIRWISE_KML_STYLE,
    mesh_pairwise_eligible_kml_arcname,
    mesh_pairwise_kml_arcname,
)
from peaky_finders.splat_polygonize import (
    MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID,
    MESH_PAIRWISE_KML_STYLE_ID,
)


def _write_coverage_gpkg(path: Path, geom) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(path, driver="GPKG", layer="coverage")


def _write_eligible_gpkg(path: Path, geom) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(
        path, driver="GPKG", layer="eligible_land_use",
    )


def test_compute_pair_overlap_two_overlapping_squares(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))

    g = compute_pair_overlap_geometry(a, b)
    assert g is not None and not g.is_empty and g.area > 0


def test_compute_pair_overlap_disjoint_returns_none(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-116.0, 39.0, -115.9, 39.1))
    _write_coverage_gpkg(b, box(-114.0, 39.0, -113.9, 39.1))

    assert compute_pair_overlap_geometry(a, b) is None


def test_compute_pair_overlap_bad_path_returns_none(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    _write_coverage_gpkg(a, box(-115.0, 39.0, -114.9, 39.1))
    assert compute_pair_overlap_geometry(a, tmp_path / "missing.gpkg") is None


def test_compute_footprint_depth_bands_two_overlapping_squares(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    bands = compute_footprint_depth_bands_wgs84([a, b], max_raster_dimension=512)
    assert "d2_pair" in bands and not bands["d2_pair"].is_empty
    assert "d1_unique" in bands and not bands["d1_unique"].is_empty


def test_compute_footprint_depth_bands_fewer_than_two_returns_empty(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    assert compute_footprint_depth_bands_wgs84([a], max_raster_dimension=256) == {}
    a = tmp_path / "a.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    out = tmp_path / "staging"
    assert (
        write_pairwise_link_overlap_layers(
            footprints=[(a, "sa", "Site A")],
            scratch_dir=out,
            polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        )
        == []
    )


def test_plain_pairwise_overlap_geometry_computed_each_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    geom_root = tmp_path / "geomcache"
    slugs_digest = {"sa": "vdA", "sb": "vdB"}
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]

    n_compute = {"n": 0}
    orig_geom = link_overlap_module.compute_pair_overlap_geometry

    def wrap_geom(pa: Path, pb: Path):
        n_compute["n"] += 1
        return orig_geom(pa, pb)

    monkeypatch.setattr(link_overlap_module, "compute_pair_overlap_geometry", wrap_geom)

    write_pairwise_link_overlap_layers(
        footprints=footprints,
        scratch_dir=tmp_path / "kml1",
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
    )
    pd = geom_root / mesh_pairwise_rel_dir("sa", "sb")
    assert pd.is_dir() and (pd / "overlap.gpkg").is_file()
    assert n_compute["n"] == 1

    write_pairwise_link_overlap_layers(
        footprints=footprints,
        scratch_dir=tmp_path / "kml2",
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
    )
    assert n_compute["n"] == 2


def test_plain_pairwise_flat_kml_rewrites_scratch_each_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:

    from peaky_finders.link_overlap import write_pairwise_link_overlap_kml_pairs
    from peaky_finders.mesh_pairwise_store import PAIRWISE_FLAT_PLAIN_KML

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    geom_root = tmp_path / "geomcache"
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    slugs_digest = {"sa": "vdAflat", "sb": "vdBflat"}
    base_kw = dict(
        footprints=footprints,
        emit_plain=True,
        link_scratch_dir=tmp_path / "scratch_missing",
        link_polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        emit_eligible=False,
        eligible_scratch_dir=None,
        eligible_polygon_style=None,
        eligible_ll=None,
        dem_mirror_root=None,
        emit_dem_peak_pins=False,
        pairwise_peak_pin_style=None,
        eligible_peak_pin_style=None,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
        bundle_kml_overlay_digest="overlay_xx",
        bundle_land_use_inputs_digest=None,
    )

    counts = {"n": 0}
    orig = link_overlap_module._write_flat_pair_overlap_kml_base

    def count_base(*pa: object, **kw: object) -> None:
        counts["n"] += 1
        return orig(*pa, **kw)

    monkeypatch.setattr(link_overlap_module, "_write_flat_pair_overlap_kml_base", count_base)
    write_pairwise_link_overlap_kml_pairs(**{**base_kw, "link_scratch_dir": tmp_path / "kmla"})
    pd = geom_root / mesh_pairwise_rel_dir("sa", "sb")
    assert (pd / PAIRWISE_FLAT_PLAIN_KML).is_file()
    assert counts["n"] == 1

    write_pairwise_link_overlap_kml_pairs(**{**base_kw, "link_scratch_dir": tmp_path / "kmlb"})
    assert counts["n"] == 2


def test_eligible_pairwise_flat_kml_rewrites_scratch_each_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peaky_finders.link_overlap import write_pairwise_link_overlap_kml_pairs
    from peaky_finders.mesh_pairwise_store import PAIRWISE_FLAT_ELIG_KML, PAIRWISE_FLAT_PLAIN_KML

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    eligible_ll = box(-115.06, 38.98, -114.9, 39.06)
    geom_root = tmp_path / "geomcache"
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    slugs_digest = {"sa": "vdAel", "sb": "vdBel"}
    base_kw = dict(
        footprints=footprints,
        emit_plain=True,
        link_scratch_dir=tmp_path / "p1",
        link_polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        emit_eligible=True,
        eligible_scratch_dir=tmp_path / "e1",
        eligible_polygon_style=DEFAULT_MESH_PAIRWISE_ELIGIBLE_KML_STYLE,
        eligible_ll=eligible_ll,
        dem_mirror_root=None,
        emit_dem_peak_pins=False,
        pairwise_peak_pin_style=None,
        eligible_peak_pin_style=None,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
        bundle_kml_overlay_digest="ov_el",
        bundle_land_use_inputs_digest="lu_el",
    )
    counts = {"n": 0}
    orig = link_overlap_module._write_flat_pair_overlap_kml_base

    def count_base(*pa: object, **kw: object) -> None:
        counts["n"] += 1
        return orig(*pa, **kw)

    monkeypatch.setattr(link_overlap_module, "_write_flat_pair_overlap_kml_base", count_base)

    write_pairwise_link_overlap_kml_pairs(**{**base_kw, "link_scratch_dir": tmp_path / "pa", "eligible_scratch_dir": tmp_path / "ea"})
    pd = geom_root / mesh_pairwise_rel_dir("sa", "sb")
    assert (pd / PAIRWISE_FLAT_PLAIN_KML).is_file()
    assert (pd / PAIRWISE_FLAT_ELIG_KML).is_file()
    assert counts["n"] == 2

    write_pairwise_link_overlap_kml_pairs(**{**base_kw, "link_scratch_dir": tmp_path / "pb", "eligible_scratch_dir": tmp_path / "eb"})
    assert counts["n"] == 4


def test_dem_peak_sampling_runs_on_each_overlap_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    geom_root = tmp_path / "geomcache"
    mirror = tmp_path / "dem_mirror"
    mirror.mkdir()
    slugs_digest = {"sa": "vdA", "sb": "vdB"}
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    calls = {"n": 0}

    def track(geom: object, mirror_root: Path, **kw: object) -> tuple[float, float, float]:
        del geom, mirror_root, kw
        calls["n"] += 1
        return (-115.01, 39.015, 999.0)

    monkeypatch.setattr(link_overlap_module, "global_max_skadi_elevation_in_polygon", track)
    write_pairwise_link_overlap_layers(
        footprints=footprints,
        scratch_dir=tmp_path / "kml_a",
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
        dem_mirror_root=mirror,
        emit_dem_peak_pins=True,
    )
    assert calls["n"] == 1
    write_pairwise_link_overlap_layers(
        footprints=footprints,
        scratch_dir=tmp_path / "kml_b",
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        pairwise_overlap_workers=1,
        geometry_cache_root=geom_root,
        slug_to_viewshed_digest=slugs_digest,
        dem_mirror_root=mirror,
        emit_dem_peak_pins=True,
    )
    assert calls["n"] == 2


def test_write_pairwise_link_overlap_writes_flat_and_arcname(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    staging = tmp_path / "lo"
    got = write_pairwise_link_overlap_layers(
        footprints=[(a, "sa", "Site A"), (b, "sb", "Site B")],
        scratch_dir=staging,
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
    )
    assert len(got) == 1
    label, path, arc = got[0]
    assert label == "Site A <-> Site B"
    assert arc == mesh_pairwise_kml_arcname("sa", "sb")
    assert path == staging / arc.rsplit("/", 1)[-1]
    raw = path.read_text(encoding="utf-8")
    assert "Site A &lt;-&gt; Site B" in raw
    assert "<Folder>" not in raw
    assert "<Polygon>" in raw or "<MultiGeometry>" in raw
    assert DEFAULT_MESH_PAIRWISE_KML_STYLE.fill in raw
    assert "<outline>0</outline>" in raw
    assert f"#{MESH_PAIRWISE_KML_STYLE_ID}" in raw
    assert "<gx:drawOrder>50</gx:drawOrder>" in raw
    assert "<altitudeMode>clampToGround</altitudeMode>" in raw
    assert "<Point>" not in raw


def test_write_pairwise_includes_dem_peak_when_sampler_returns_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import peaky_finders.link_overlap as lo

    def _fake_peak(
        geom_ll: object,
        mirror_root: Path,
        *,
        void_val: int = -32768,
    ) -> tuple[float, float, float]:
        del geom_ll, mirror_root, void_val
        return (-115.01, 39.0125, 1234.0)

    monkeypatch.setattr(lo, "global_max_skadi_elevation_in_polygon", _fake_peak)

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    staging = tmp_path / "lo_peak"
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    got = write_pairwise_link_overlap_layers(
        footprints=[(a, "sa", "Site A"), (b, "sb", "Site B")],
        scratch_dir=staging,
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        dem_mirror_root=mirror,
        emit_dem_peak_pins=True,
    )
    assert len(got) == 1
    _label, path, _arc = got[0]
    raw = path.read_text(encoding="utf-8")
    assert "<Point>" in raw
    assert "1234" in raw
    assert "pushpin/ylw-pushpin.png" in raw
    assert raw.count("<Placemark>") == 2


def test_three_sites_yield_three_pair_layers(tmp_path: Path) -> None:
    gx = ((-115.02, -115.0, 39.01, 39.03), (-115.01, -114.99, 39.0, 39.02), (-115.015, -114.995, 39.005, 39.025))
    paths_slugs_labels: list[tuple[Path, str, str]] = []
    for k, bx in enumerate(gx):
        p = tmp_path / f"s{k}.gpkg"
        _write_coverage_gpkg(p, box(bx[0], bx[2], bx[1], bx[3]))
        paths_slugs_labels.append((p, f"n{k}", f"N{k}"))

    triples = write_pairwise_link_overlap_layers(
        footprints=paths_slugs_labels,
        scratch_dir=tmp_path / "three",
        polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
    )
    labels = sorted(t for t, _p, _a in triples)
    assert len(triples) == 3
    assert labels == sorted(["N0 <-> N1", "N0 <-> N2", "N1 <-> N2"])


def test_compute_pair_eligible_overlap_requires_eligible_geom(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    assert compute_pair_eligible_overlap_geometry(a, b, None) is None


def test_compute_pair_eligible_smaller_than_plain_when_clipped(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    plain = compute_pair_overlap_geometry(a, b)
    elig_geom = box(-115.015, 39.012, -115.005, 39.018)
    clipped = compute_pair_eligible_overlap_geometry(a, b, elig_geom)
    assert plain is not None and clipped is not None
    assert clipped.area < plain.area


def test_compute_pair_eligible_disjoint_eligible_returns_none(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    far = box(-114.0, 39.0, -113.9, 39.1)
    assert compute_pair_eligible_overlap_geometry(a, b, far) is None


def test_read_eligible_land_use_union_reads_layer(tmp_path: Path) -> None:
    ep = tmp_path / "e.gpkg"
    _write_eligible_gpkg(ep, box(-115.05, 39.0, -114.9, 39.05))
    u = read_eligible_land_use_union(ep)
    assert u is not None and not u.is_empty


def test_write_pairwise_eligible_link_overlap_layers(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    eligible_ll = box(-115.05, 39.0, -114.9, 39.05)
    staging = tmp_path / "elo"
    got = write_pairwise_eligible_link_overlap_layers(
        footprints=[(a, "sa", "Site A"), (b, "sb", "Site B")],
        scratch_dir=staging,
        polygon_style=DEFAULT_MESH_PAIRWISE_ELIGIBLE_KML_STYLE,
        eligible_ll=eligible_ll,
    )
    assert len(got) == 1
    label, path, arc = got[0]
    assert label == "Site A <-> Site B"
    assert arc == mesh_pairwise_eligible_kml_arcname("sa", "sb")
    raw = path.read_text(encoding="utf-8")
    assert f"#{MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID}" in raw
    assert "<gx:drawOrder>60</gx:drawOrder>" in raw


def test_write_mesh_depth_emits_per_site_kmls(tmp_path: Path) -> None:
    from peaky_finders.mesh_coverage_depth import write_mesh_depth_kml_layers
    from peaky_finders.sites_job import mesh_coverage_depth_site_kml_arcname

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    plain, elig = write_mesh_depth_kml_layers(
        footprints=[(a, "sa", "Site A"), (b, "sb", "Site B")],
        kml_overlay=None,
        scratch_depth_dir=tmp_path / "md",
        scratch_depth_eligible_dir=None,
        eligible_ll=None,
        max_raster_dimension=512,
    )
    assert elig == []
    assert len(plain) == 4
    arcs = {row[3] for row in plain}
    assert mesh_coverage_depth_site_kml_arcname("d1_unique", "sa") in arcs
    assert mesh_coverage_depth_site_kml_arcname("d2_pair", "sb") in arcs
    for _b, _ttl, path, _a, _v in plain:
        assert path.is_file()


def test_write_mesh_depth_recomputes_bands_each_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from peaky_finders import mesh_coverage_depth as mcd

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    cache_root = tmp_path / "geomcache"
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    digest_map = {"sa": "vdA", "sb": "vdB"}
    kw = dict(
        footprints=footprints,
        kml_overlay=None,
        scratch_depth_dir=tmp_path / "md1",
        scratch_depth_eligible_dir=None,
        eligible_ll=None,
        max_raster_dimension=512,
        geometry_cache_root=cache_root,
        slug_to_viewshed_digest=digest_map,
    )
    n_compute = {"n": 0}
    orig_compute = mcd.compute_footprint_depth_bands_wgs84

    def wrap_compute(paths, **kw):
        n_compute["n"] += 1
        return orig_compute(paths, **kw)

    monkeypatch.setattr(mcd, "compute_footprint_depth_bands_wgs84", wrap_compute)

    plain1, _ = mcd.write_mesh_depth_kml_layers(**kw)
    assert plain1

    plain2, _ = mcd.write_mesh_depth_kml_layers(
        **{
            **kw,
            "scratch_depth_dir": tmp_path / "md2",
        }
    )
    assert len(plain2) == len(plain1)
    assert n_compute["n"] == 2


def test_mesh_depth_flat_kml_rewrites_base_each_plain_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from peaky_finders.mesh_depth_store import mesh_depth_flat_kml_slug_id

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    cache_root = tmp_path / "geomcache"
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    digest_map = {"sa": "vdA", "sb": "vdB"}
    base_kw = dict(
        footprints=footprints,
        kml_overlay=None,
        scratch_depth_dir=tmp_path / "mdp1",
        scratch_depth_eligible_dir=None,
        eligible_ll=None,
        max_raster_dimension=512,
        geometry_cache_root=cache_root,
        slug_to_viewshed_digest=digest_map,
        bundle_kml_overlay_digest="ov_xx",
        bundle_land_use_inputs_digest=None,
    )
    counts = {"n": 0}
    import peaky_finders.mesh_coverage_depth as mcd

    orig = mcd._write_flat_pair_overlap_kml_base

    def count_writes(*pa: object, **kw: object) -> None:
        counts["n"] += 1
        return orig(*pa, **kw)

    monkeypatch.setattr(mcd, "_write_flat_pair_overlap_kml_base", count_writes)

    plain1, _ = mcd.write_mesh_depth_kml_layers(**base_kw)
    assert plain1
    first_n = counts["n"]
    assert first_n >= 1
    hid_sa = mesh_depth_flat_kml_slug_id("sa")
    from peaky_finders.mesh_depth_store import resolved_mesh_depth_slice_dir

    depth_set = cache_root / mesh_depth_network_rel_dir(max_raster_dimension=512)
    one_slice = resolved_mesh_depth_slice_dir(
        set_dir=depth_set,
        band="d1_unique",
        site_vd="vdA",
    )
    assert (one_slice / f"mesh_depth_plain_{hid_sa}.kml").is_file()

    plain2, _ = mcd.write_mesh_depth_kml_layers(
        **{**base_kw, "scratch_depth_dir": tmp_path / "mdp2"},
    )
    assert len(plain2) == len(plain1)
    assert counts["n"] == 2 * first_n


def test_mesh_depth_flat_kml_rewrites_base_each_plain_and_eligible_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import peaky_finders.mesh_coverage_depth as mcd

    a = tmp_path / "a.gpkg"
    b = tmp_path / "b.gpkg"
    _write_coverage_gpkg(a, box(-115.02, 39.01, -115.00, 39.03))
    _write_coverage_gpkg(b, box(-115.01, 39.00, -114.99, 39.02))
    eligible_ll = box(-115.06, 38.98, -114.9, 39.06)
    cache_root = tmp_path / "geomcache"
    footprints = [(a, "sa", "Site A"), (b, "sb", "Site B")]
    digest_map = {"sa": "vdAe", "sb": "vdBe"}
    base_kw = dict(
        footprints=footprints,
        kml_overlay=None,
        scratch_depth_dir=tmp_path / "mx1_plain",
        scratch_depth_eligible_dir=tmp_path / "mx1_elig",
        eligible_ll=eligible_ll,
        max_raster_dimension=512,
        geometry_cache_root=cache_root,
        slug_to_viewshed_digest=digest_map,
        bundle_kml_overlay_digest="ov_el",
        bundle_land_use_inputs_digest="lu_el",
    )

    counts = {"n": 0}
    orig = mcd._write_flat_pair_overlap_kml_base

    def count_writes(*pa: object, **kw: object) -> None:
        counts["n"] += 1
        return orig(*pa, **kw)

    monkeypatch.setattr(mcd, "_write_flat_pair_overlap_kml_base", count_writes)

    plain1, elig1 = mcd.write_mesh_depth_kml_layers(**base_kw)
    assert plain1 and elig1
    first_writes = counts["n"]
    assert first_writes >= len(plain1) + len(elig1)

    plain2, elig2 = mcd.write_mesh_depth_kml_layers(
        **{
            **base_kw,
            "scratch_depth_dir": tmp_path / "mx2_plain",
            "scratch_depth_eligible_dir": tmp_path / "mx2_elig",
        }
    )
    assert len(plain2) == len(plain1) and len(elig2) == len(elig1)
    assert counts["n"] == 2 * first_writes
