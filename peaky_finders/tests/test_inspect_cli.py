"""Tests for ``peaky inspect`` (GPKG/GDB/KML and ArcGIS web map JSON)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from peaky_finders import inspect_cli

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


def _ns(**over: Any) -> argparse.Namespace:
    base = dict(
        path=Path("."),
        layers=None,
        layers_only=False,
        include_tables=False,
        skip_hidden=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_inspect_kml_summary_and_attributes(tmp_path: Path) -> None:
    kml = tmp_path / "t.kml"
    kml.write_text(MINIMAL_KML, encoding="utf-8")
    ns = _ns(path=kml, layers_only=False)
    assert inspect_cli.run_inspect(ns) == 0


def test_inspect_unknown_extension_returns_2(tmp_path: Path) -> None:
    f = tmp_path / "thing.bin"
    f.write_bytes(b"\x00")
    assert inspect_cli.run_inspect(_ns(path=f)) == 2


def test_inspect_plain_json_returns_2(tmp_path: Path) -> None:
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert inspect_cli.run_inspect(_ns(path=f)) == 2


def test_inspect_webmap_lists_id_and_title_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wm = {
        "operationalLayers": [
            {
                "id": "layer-1",
                "layerType": "ArcGISFeatureLayer",
                "title": "Test Layer",
                "url": "https://example.com/arcgis/rest/services/Foo/FeatureServer/0",
            },
        ],
    }
    p = tmp_path / "map.pjson"
    p.write_text(json.dumps(wm), encoding="utf-8")

    assert inspect_cli.run_inspect(_ns(path=p, layers_only=False)) == 0
    out = capsys.readouterr().out
    assert "id" in out
    assert "title" in out
    assert "layer-1" in out
    assert "Test Layer" in out


def test_inspect_webmap_unknown_layer_returns_2(tmp_path: Path) -> None:
    wm = {
        "operationalLayers": [
            {
                "layerType": "ArcGISFeatureLayer",
                "title": "Alpha",
                "url": "https://example.com/x/FeatureServer/0",
            },
        ],
    }
    p = tmp_path / "wm.pjson"
    p.write_text(json.dumps(wm), encoding="utf-8")

    ec = inspect_cli.run_inspect(_ns(path=p, layers="Missing", layers_only=True))
    assert ec == 2


def test_detect_inspect_format_json_with_operational_layers(tmp_path: Path) -> None:
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"operationalLayers": []}), encoding="utf-8")
    assert inspect_cli.detect_inspect_format(p) == "webmap"
