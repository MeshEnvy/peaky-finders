"""Mutual site–site links from RF splatter checks and preset ``sites.*.sees`` overrides."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from peaky_finders.kml_bundle import AggregateSiteOverlay
from peaky_finders.site_suggestions.rf_link import rf_mutual_link_slug_pairs
from peaky_finders.sites_job import Preset
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_MESH_SITE_TO_SITE,
    GX_NS,
)

KML_NS = "http://www.opengis.net/kml/2.2"


@dataclass(frozen=True)
class _SiteLinkNode:
    slug: str
    folder_name: str
    lat: float
    lon: float
    antenna_height_agl_m: float


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def mutual_sees_slug_pairs(sees_by_slug: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    """Slug pairs where each site lists the other in ``sees``."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for slug_a, targets in sees_by_slug.items():
        target_set = {str(t).strip() for t in targets if str(t).strip()}
        for slug_b in target_set:
            if slug_a == slug_b:
                continue
            if slug_a not in {str(t).strip() for t in sees_by_slug.get(slug_b, ())}:
                continue
            key = _canonical_pair(slug_a, slug_b)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _rf_site_slugs(preset: Preset) -> frozenset[str]:
    return frozenset(slug for slug, ent in preset.sites.items() if ent.participates_in_rf)


def mutual_site_link_slug_pairs(
    *,
    preset: Preset,
    sees_by_slug: Mapping[str, Sequence[str]],
) -> list[tuple[str, str]]:
    """Union of mutual RF links and mutual ``sees`` field-override pairs."""
    rf_slugs = _rf_site_slugs(preset)
    pairs: set[tuple[str, str]] = set(rf_mutual_link_slug_pairs(preset))
    pairs.update(mutual_sees_slug_pairs(sees_by_slug))
    return sorted(p for p in pairs if p[0] in rf_slugs and p[1] in rf_slugs)


def _site_link_nodes(
    sites: Sequence[AggregateSiteOverlay],
) -> list[_SiteLinkNode]:
    return [
        _SiteLinkNode(
            slug=s.slug,
            folder_name=s.folder_name,
            lat=float(s.center_lat),
            lon=float(s.center_lon),
            antenna_height_agl_m=float(s.antenna_height_agl_m),
        )
        for s in sites
    ]


def site_links_geojson(
    *,
    preset: Preset,
    sites: Sequence[AggregateSiteOverlay],
    sees_by_slug: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """GeoJSON FeatureCollection of LineStrings for mutual site links."""
    overlay_by_slug = {s.slug: s for s in sites}
    link_nodes = _site_link_nodes(sites)
    pair_slugs = mutual_site_link_slug_pairs(
        preset=preset,
        sees_by_slug=sees_by_slug or {},
    )
    features: list[dict[str, Any]] = []
    node_by_slug = {n.slug: n for n in link_nodes}
    for slug_a, slug_b in pair_slugs:
        if slug_a not in overlay_by_slug or slug_b not in overlay_by_slug:
            continue
        a = node_by_slug[slug_a]
        b = node_by_slug[slug_b]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": f"{slug_a}--{slug_b}",
                    "from": slug_a,
                    "to": slug_b,
                    "label": f"{a.folder_name} ↔ {b.folder_name}",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [a.lon, a.lat],
                        [b.lon, b.lat],
                    ],
                },
            },
        )
    return {"type": "FeatureCollection", "features": features}


def write_site_links_kml(
    *,
    preset: Preset,
    sites: Sequence[AggregateSiteOverlay],
    sees_by_slug: Mapping[str, Sequence[str]] | None = None,
    out_kml: Path,
) -> bool:
    """Write LineString KML for mutual site links; False if none.

    A pair is linked when splatter RF mutual-hop checks pass **or** both sites list each
    other under ``sites.<slug>.sees`` in the preset.
    """
    overlay_by_slug = {s.slug: s for s in sites}
    link_nodes = _site_link_nodes(sites)
    pair_slugs = mutual_site_link_slug_pairs(
        preset=preset,
        sees_by_slug=sees_by_slug or {},
    )
    if not pair_slugs:
        return False

    endpoints: list[tuple[_SiteLinkNode, _SiteLinkNode]] = []
    node_by_slug = {n.slug: n for n in link_nodes}
    for slug_a, slug_b in pair_slugs:
        if slug_a not in overlay_by_slug or slug_b not in overlay_by_slug:
            continue
        endpoints.append((node_by_slug[slug_a], node_by_slug[slug_b]))

    if not endpoints:
        return False

    _write_site_links_kml_document(pairs=endpoints, out_kml=out_kml)
    return True


def _write_site_links_kml_document(
    *,
    pairs: Sequence[tuple[_SiteLinkNode, _SiteLinkNode]],
    out_kml: Path,
) -> None:
    out_kml = Path(out_kml)
    out_kml.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("", KML_NS)

    def t(local: str) -> str:
        return f"{{{KML_NS}}}{local}"

    root = ET.Element(t("kml"))
    doc = ET.SubElement(root, t("Document"))
    ET.register_namespace("gx", GX_NS)
    ET.SubElement(doc, t("name")).text = "Mutual site links"

    for a, b in pairs:
        pm = ET.SubElement(doc, t("Placemark"))
        ET.SubElement(pm, t("name")).text = f"{a.folder_name} ↔ {b.folder_name}"
        ls = ET.SubElement(pm, t("LineString"))
        gx_do = ET.SubElement(ls, f"{{{GX_NS}}}drawOrder")
        gx_do.text = str(int(GX_DRAW_ORDER_MESH_SITE_TO_SITE))
        ET.SubElement(ls, t("altitudeMode")).text = "relativeToGround"
        coord_text = (
            f"{a.lon:.8f},{a.lat:.8f},{a.antenna_height_agl_m:.6f} "
            f"{b.lon:.8f},{b.lat:.8f},{b.antenna_height_agl_m:.6f}"
        )
        ET.SubElement(ls, t("coordinates")).text = coord_text

    ET.ElementTree(root).write(out_kml, encoding="utf-8", xml_declaration=True)
