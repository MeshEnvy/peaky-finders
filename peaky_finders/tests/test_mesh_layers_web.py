"""Web mesh layer catalog and GeoJSON collectors."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import box

from peaky_finders.mesh_pairwise_store import write_cached_pair_overlap_geometry
from peaky_finders.path_labels import mesh_pairwise_rel_dir
from peaky_finders.sites_job import resolved_mesh_pairwise_dir
from peaky_finders.web.mesh_layers import (
    display_mode_from_kmz_bools,
    kmz_bools_from_display_mode,
    mesh_layer_geojson,
    mesh_layers_catalog,
    pairwise_built,
    pairwise_geojson,
)
from peaky_finders.web.project_maps import project_maps_catalog

from fixture_paths import PEAKY_TEST_HOME


def _write_pairwise_overlap(bundle_dir: Path, slug_a: str, slug_b: str, geom) -> None:
    pairwise_root = resolved_mesh_pairwise_dir(bundle_dir)
    pair_dir = pairwise_root / mesh_pairwise_rel_dir(slug_a, slug_b)
    write_cached_pair_overlap_geometry(
        pair_dir=pair_dir,
        vd_a="vd_a",
        vd_b="vd_b",
        overlap_wgs84=geom,
    )


def test_display_mode_roundtrip() -> None:
    assert display_mode_from_kmz_bools(plain=False, eligible=False) == "off"
    assert display_mode_from_kmz_bools(plain=True, eligible=False) == "all"
    assert display_mode_from_kmz_bools(plain=False, eligible=True) == "eligible"
    assert kmz_bools_from_display_mode("all") == (True, False)
    assert kmz_bools_from_display_mode("eligible") == (False, True)


def test_mesh_layers_catalog_pairwise_and_depth(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    catalog = mesh_layers_catalog("sample")
    assert "pairwise_pairs" in catalog
    assert "depth_bands" in catalog
    assert len(catalog["depth_bands"]) == 4
    band_ids = [e["band"] for e in catalog["depth_bands"]]
    assert "d1_unique" in band_ids
    assert "d5_plus" in band_ids
    for entry in catalog["depth_bands"]:
        assert entry["display_mode"] in ("off", "eligible", "all")
        assert "build_phase" in entry


def test_mesh_layers_catalog_hides_disabled_mesh_coverage(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    proj = tmp_path / "projects" / "off"
    proj.mkdir(parents=True)
    (proj / "config.yaml").write_text(
        """simulation:
  provider: los
  radius_km: 10
sites:
  a:
    name: A
    loc: [39.0, -115.0]
    participates_in_rf: true
  b:
    name: B
    loc: [39.1, -115.1]
    participates_in_rf: true
bundle:
  mesh_coverage:
    pairwise: false
    depth: false
""",
        encoding="utf-8",
    )
    catalog = mesh_layers_catalog("off")
    assert catalog["pairwise_pairs"] == []
    assert catalog["depth_bands"] == []


def test_project_maps_catalog_includes_mesh(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    out = project_maps_catalog("sample")
    assert "mesh" in out
    assert "build" in out
    assert len(out["mesh"]["depth_bands"]) == 4


def test_pairwise_geojson_from_overlap_cache(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "build" / "bundle"
    bundle_dir.mkdir(parents=True)
    geom = box(-115.02, 39.01, -115.00, 39.03)
    _write_pairwise_overlap(bundle_dir, "site_a", "site_b", geom)
    assert pairwise_built(bundle_dir)
    gj = pairwise_geojson(bundle_dir)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 1
    assert gj["features"][0]["properties"]["pair"]


def test_mesh_layer_geojson_unknown_id(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    with pytest.raises(KeyError):
        mesh_layer_geojson("sample", "mesh_depth:unknown")
