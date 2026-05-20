"""Mutual viewshed links between sites (pairwise polygon ``covers`` on coverage footprints)."""

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
    footprint: BaseGeometry


def mutual_site_link_pairs(sites: Sequence[_SiteLinkNode]) -> list[tuple[int, int]]:
    """Indices (i, j) with i < j where each pin lies in the other's footprint."""
    n = len(sites)
    out: list[tuple[int, int]] = []
    for i in range(n):
        fi = sites[i].footprint
        pi = Point(sites[i].lon, sites[i].lat)
        for j in range(i + 1, n):
            fj = sites[j].footprint
            pj = Point(sites[j].lon, sites[j].lat)
            if fi.covers(pj) and fj.covers(pi):
                out.append((i, j))
    return out


def write_site_links_kml(
    *,
    coverage_gpkg_by_slug: Mapping[str, Path],
    sites: Sequence[AggregateSiteOverlay],
    out_kml: Path,
) -> bool:
    """Write LineString KML for site–site mutual viewshed links; False if none or unsolvable.

    ``coverage_gpkg_by_slug`` maps each site ``slug`` to its ``coverage_area.gpkg`` path
    (propagation workspaces may be shared across slugs when inputs match).

    Lines use ``altitudeMode`` ``relativeToGround`` and endpoint altitudes from
    ``AggregateSiteOverlay.antenna_height_agl_m`` (preset transmitter AGL).
    """
    nodes: list[_SiteLinkNode] = []
    for s in sites:
        gpkg = Path(coverage_gpkg_by_slug[s.slug])
        fp = read_coverage_footprint(gpkg)
        if fp is None or fp.is_empty:
            continue
        fp = make_valid(fp) if not fp.is_valid else fp
        if fp.is_empty:
            continue
        nodes.append(
            _SiteLinkNode(
                slug=s.slug,
                folder_name=s.folder_name,
                lat=float(s.center_lat),
                lon=float(s.center_lon),
                antenna_height_agl_m=float(s.antenna_height_agl_m),
                footprint=fp,
            ),
        )

    pairs = mutual_site_link_pairs(nodes)
    if not pairs:
        return False

    _write_site_links_kml_document(nodes=nodes, pairs=pairs, out_kml=out_kml)
    return True


def _write_site_links_kml_document(
    *,
    nodes: Sequence[_SiteLinkNode],
    pairs: Sequence[tuple[int, int]],
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
    ET.SubElement(doc, t("name")).text = "Mutual viewshed links"

    for idx_a, idx_b in pairs:
        a, b = nodes[idx_a], nodes[idx_b]
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
