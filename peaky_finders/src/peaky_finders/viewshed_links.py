"""Mutual site–site links from coverage footprints and/or preset ``links``."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.kml_bundle import AggregateSiteOverlay
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
    footprint: BaseGeometry | None = None


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def manual_link_slug_pairs(manual_links: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Canonical slug pairs from preset ``links`` (already sorted per pair)."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a, b in manual_links:
        key = _canonical_pair(str(a).strip(), str(b).strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return sorted(out)


def mutual_footprint_link_pairs(sites: Sequence[_SiteLinkNode]) -> list[tuple[int, int]]:
    """Indices (i, j) with i < j where each pin lies in the other's footprint."""
    n = len(sites)
    out: list[tuple[int, int]] = []
    for i in range(n):
        fi = sites[i].footprint
        if fi is None:
            continue
        pi = Point(sites[i].lon, sites[i].lat)
        for j in range(i + 1, n):
            fj = sites[j].footprint
            if fj is None:
                continue
            pj = Point(sites[j].lon, sites[j].lat)
            if fi.covers(pj) and fj.covers(pi):
                out.append((i, j))
    return out


def mutual_site_link_slug_pairs(
    *,
    footprint_nodes: Sequence[_SiteLinkNode],
    manual_links: Sequence[tuple[str, str]] = (),
) -> list[tuple[str, str]]:
    """Union of mutual footprint coverage and preset ``links`` pairs (canonical slug order)."""
    pairs: set[tuple[str, str]] = set()
    for i, j in mutual_footprint_link_pairs(footprint_nodes):
        pairs.add(_canonical_pair(footprint_nodes[i].slug, footprint_nodes[j].slug))
    pairs.update(manual_link_slug_pairs(manual_links))
    return sorted(pairs)


def write_site_links_kml(
    *,
    coverage_gpkg_by_slug: Mapping[str, Path],
    sites: Sequence[AggregateSiteOverlay],
    manual_links: Sequence[tuple[str, str]] | None = None,
    out_kml: Path,
) -> bool:
    """Write LineString KML for mutual site links; False if none.

    A pair is linked when footprints mutually cover both pins **or** it appears in preset ``links``.
    """
    overlay_by_slug = {s.slug: s for s in sites}
    footprint_nodes: list[_SiteLinkNode] = []
    for s in sites:
        gpkg = coverage_gpkg_by_slug.get(s.slug)
        fp: BaseGeometry | None = None
        if gpkg is not None:
            loaded = read_coverage_footprint(Path(gpkg))
            if loaded is not None and not loaded.is_empty:
                fp = make_valid(loaded) if not loaded.is_valid else loaded
                if fp.is_empty:
                    fp = None
        footprint_nodes.append(
            _SiteLinkNode(
                slug=s.slug,
                folder_name=s.folder_name,
                lat=float(s.center_lat),
                lon=float(s.center_lon),
                antenna_height_agl_m=float(s.antenna_height_agl_m),
                footprint=fp,
            ),
        )

    pair_slugs = mutual_site_link_slug_pairs(
        footprint_nodes=footprint_nodes,
        manual_links=manual_links or (),
    )
    if not pair_slugs:
        return False

    endpoints: list[tuple[_SiteLinkNode, _SiteLinkNode]] = []
    node_by_slug = {n.slug: n for n in footprint_nodes}
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
