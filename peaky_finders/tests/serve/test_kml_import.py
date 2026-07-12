"""KML/KMZ Point placemark parsing for serve import."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest

from peaky_finders.serve.kml_import import (
    parse_kml_point_placemarks,
    parse_kmz_point_placemarks,
    serialize_kml_point,
)

ONX_KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Bald Mountain {HSC}</name>
      <Point>
        <coordinates>-118.83434,38.78424,2800.800049</coordinates>
      </Point>
    </Placemark>
    <Placemark>
      <name>Stillwater {HSC}</name>
      <Point>
        <coordinates>-117.831657,40.07833,2087.050048828</coordinates>
      </Point>
    </Placemark>
    <Placemark>
      <name>Route only</name>
      <LineString>
        <coordinates>-118,38,0 -117,39,0</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""


def test_parse_kml_point_placemarks_onx_shape() -> None:
    sites, skipped = parse_kml_point_placemarks(ONX_KML)
    assert skipped == 1
    assert len(sites) == 2
    assert sites[0].name == "Bald Mountain {HSC}"
    assert sites[0].lat == pytest.approx(38.78424)
    assert sites[0].lon == pytest.approx(-118.83434)
    assert sites[1].name == "Stillwater {HSC}"


def test_parse_kml_point_placemarks_rejects_invalid_xml() -> None:
    with pytest.raises(ValueError, match="invalid KML XML"):
        parse_kml_point_placemarks(b"<not-kml")


def test_parse_kmz_point_placemarks() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", ONX_KML)
    sites, skipped = parse_kmz_point_placemarks(buf.getvalue())
    assert len(sites) == 2
    assert skipped == 1


def test_parse_kmz_point_placemarks_rejects_bad_archive() -> None:
    with pytest.raises(ValueError, match="invalid KMZ"):
        parse_kmz_point_placemarks(b"not-a-zip")


def test_serialize_kml_point() -> None:
    sites, _ = parse_kml_point_placemarks(ONX_KML)
    row = serialize_kml_point(sites[0])
    assert row["name"] == "Bald Mountain {HSC}"
    assert row["lat"] == pytest.approx(38.78424)
    assert row["lon"] == pytest.approx(-118.83434)
    assert "elevation_m" not in row


def test_parse_nevada_onx_fixture_if_present() -> None:
    fixture = (
        Path(__file__).resolve().parents[3]
        / "peaky_home"
        / "projects"
        / "nevada"
        / "data"
        / "onx-markups-06232026.kml"
    )
    if not fixture.is_file():
        pytest.skip("nevada onx fixture not available")
    sites, skipped = parse_kml_point_placemarks(fixture.read_bytes())
    assert len(sites) >= 1
    assert skipped == 0
