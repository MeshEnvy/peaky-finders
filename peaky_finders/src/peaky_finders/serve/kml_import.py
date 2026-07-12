"""Parse KML/KMZ Point placemarks for serve site import."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from io import BytesIO

KML_NS = "http://www.opengis.net/kml/2.2"
KML = f"{{{KML_NS}}}"


@dataclass(frozen=True)
class KmlPointSite:
    name: str
    lat: float
    lon: float


def _placemark_name(pm: ET.Element) -> str:
    name_el = pm.find(f"{KML}name")
    if name_el is not None and name_el.text and str(name_el.text).strip():
        return str(name_el.text).strip()
    return "Unnamed site"


def _parse_point_coordinates(coords_text: str) -> tuple[float, float] | None:
    text = str(coords_text or "").strip()
    if not text:
        return None
    first = text.split()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 2:
        return None
    try:
        lon = float(parts[0])
        lat = float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _parse_placemark_point(pm: ET.Element) -> KmlPointSite | None:
    coords_el = pm.find(f".//{KML}Point/{KML}coordinates")
    if coords_el is None or not coords_el.text:
        return None
    parsed = _parse_point_coordinates(coords_el.text)
    if parsed is None:
        return None
    lat, lon = parsed
    return KmlPointSite(name=_placemark_name(pm), lat=lat, lon=lon)


def parse_kml_point_placemarks(data: bytes) -> tuple[list[KmlPointSite], int]:
    """Return Point placemarks and count of placemarks skipped (no usable Point)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"invalid KML XML: {e}") from e

    sites: list[KmlPointSite] = []
    skipped = 0
    for pm in root.iter(f"{KML}Placemark"):
        site = _parse_placemark_point(pm)
        if site is None:
            skipped += 1
        else:
            sites.append(site)
    return sites, skipped


def parse_kmz_point_placemarks(data: bytes) -> tuple[list[KmlPointSite], int]:
    """Extract the first ``.kml`` member from a KMZ archive and parse Point placemarks."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            kml_name = next((n for n in zf.namelist() if n.lower().endswith(".kml")), None)
            if kml_name is None:
                raise ValueError("no .kml member in KMZ archive")
            kml_bytes = zf.read(kml_name)
    except zipfile.BadZipFile as e:
        raise ValueError(f"invalid KMZ archive: {e}") from e
    return parse_kml_point_placemarks(kml_bytes)


def serialize_kml_point(site: KmlPointSite) -> dict[str, object]:
    return {
        "name": site.name,
        "lat": site.lat,
        "lon": site.lon,
    }
