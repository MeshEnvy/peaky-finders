"""GdbLayerGroup: optional layers and auto-expand to polygon OGR layers."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import Polygon

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.bundle_build import (
    _flatten_gdb_layer_jobs,
    _read_and_clip_gdb_layer_to_aoi,
    gdb_layer_group_specs,
)
from peaky_finders.sites_job import (
    LandConfig,
    GdbLayerGroup,
    _gdb_layer_group_payload,
    canonical_land_land_use_config_text,
    load_preset,
)

MINIMAL_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Spot</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>-120,38,0 -119,38,0 -119,39,0 -120,39,0 -120,38,0</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""


def test_gdb_layer_group_accepts_path_only() -> None:
    g = GdbLayerGroup.model_validate({"path": "exclude/foo.kml"})
    assert g.layers == []


def test_gdb_layer_group_accepts_explicit_empty_layers_list() -> None:
    g = GdbLayerGroup.model_validate({"path": "exclude/foo.kml", "layers": []})
    assert g.layers == []


def test_canonical_payload_uses_all_layers_sentinel() -> None:
    g = GdbLayerGroup(path="exclude/foo.kml", layers=[])
    assert _gdb_layer_group_payload(g) == {"layers": "*", "path": "exclude/foo.kml"}


def test_gdb_layer_group_specs_expands_polygon_layers(tmp_path: Path) -> None:
    kml = tmp_path / "poly.kml"
    kml.write_text(MINIMAL_KML, encoding="utf-8")
    g = GdbLayerGroup(path="poly.kml")
    specs = gdb_layer_group_specs(g, tmp_path)
    assert len(specs) == 1
    assert specs[0].name


def test_read_and_clip_kml_exclude_layer_with_unknown_ogr_metadata(tmp_path: Path) -> None:
    kml = tmp_path / "poly.kml"
    kml.write_text(MINIMAL_KML, encoding="utf-8")
    layer = gdb_layer_group_specs(GdbLayerGroup(path="poly.kml"), tmp_path)[0].name
    aoi = Polygon([(-121, 37), (-118, 37), (-118, 40), (-121, 40), (-121, 37)])
    _full, clipped = _read_and_clip_gdb_layer_to_aoi(
        "exclude/poly.kml",
        kml,
        layer,
        aoi,
        kind="exclude",
    )
    assert not clipped.empty


def test_flatten_gdb_layer_jobs_expands_path_only_kml(tmp_path: Path) -> None:
    kml = tmp_path / "poly.kml"
    kml.write_text(MINIMAL_KML, encoding="utf-8")
    g = GdbLayerGroup(path="poly.kml")
    jobs = _flatten_gdb_layer_jobs([g], tmp_path)
    assert len(jobs) == 1
    assert jobs[0][0] == "poly.kml"
    assert jobs[0][2] == gdb_layer_group_specs(g, tmp_path)[0].name


def test_bundle_config_accepts_path_only_exclude() -> None:
    cfg = LandConfig.model_validate(
        {
            "aoi": [{"path": "aoi/test.gdb", "layers": ["boundary"]}],
            "include": [{"path": "include/test.gdb", "layers": ["inc"]}],
            "exclude": [{"path": "exclude/land.kml"}],
        }
    )
    assert cfg.exclude[0].layers == []
    text = canonical_land_land_use_config_text(cfg)
    assert '"layers": "*"' in text
    assert "exclude/land.kml" in text


def test_sample_preset_loads_with_explicit_layers() -> None:
    preset = load_preset(SAMPLE_PROJECT_CONFIG)
    assert preset.land is not None
    assert preset.land.exclude == []
