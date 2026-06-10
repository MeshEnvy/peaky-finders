"""Parse SPLAT KML bounds and compose a Google Earth KML document."""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

from peaky_finders.mesh_coverage_depth import MESH_DEPTH_BANDS, MESH_DEPTH_DISPLAY, mesh_depth_visibility_field
from peaky_finders.splat_polygonize import GX_DRAW_ORDER_VIEWSHED_RASTER


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_lat_lon_box(kml_bytes: bytes) -> dict[str, float]:
    root = ET.fromstring(kml_bytes)
    box_el = None
    for el in root.iter():
        if _local_tag(el.tag) == "LatLonBox":
            box_el = el
            break
    if box_el is None:
        raise ValueError("No LatLonBox found in KML")
    values: dict[str, float] = {}
    for child in box_el:
        name = _local_tag(child.tag)
        if child.text is None:
            continue
        if name in ("north", "south", "east", "west", "rotation"):
            values[name] = float(child.text.strip())
    for k in ("north", "south", "east", "west"):
        if k not in values:
            raise ValueError(f"LatLonBox missing {k}")
    return values


def point_splat_output_kml_at_png(output_kml: Path, *, raster_basename: str = "splat.png") -> None:
    """Rewrite SPLAT's GroundOverlay raster ``<href>output.ppm`` → PNG (opaque PPM washes out Earth)."""
    raw = output_kml.read_text(encoding="utf-8")
    if not re.search(r"<href>\s*output\.ppm\s*</href>", raw):
        if re.search(rf"<href>\s*{re.escape(raster_basename)}\s*</href>", raw):
            return
        raise ValueError(f"No SPLAT <href>output.ppm</href> and no PNG href in {output_kml}")
    patched = re.sub(r"<href>\s*output\.ppm\s*</href>", f"<href>{raster_basename}</href>", raw)
    output_kml.write_text(patched, encoding="utf-8")


def ground_overlay_color_kml(*, opacity_pct: float) -> str:
    """KML ``<color>`` for GroundOverlay (aabbggrr). ``opacity_pct`` is 0–100 (100 = fully opaque)."""
    o = max(0.0, min(100.0, float(opacity_pct)))
    alpha = max(0, min(255, round(o / 100.0 * 255.0)))
    return f"{alpha:02x}ffffff"


def overlay_opacity_pct_from_display_transparency(display: Any) -> float:
    """``display.transparency`` is percent transparent (0 = solid, 100 = invisible), same as Meshtastic UI."""
    raw = display.transparency if hasattr(display, "transparency") else display.get("transparency")  # type: ignore[union-attr]
    if raw is None:
        return 100.0
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return 100.0
    t = max(0.0, min(100.0, t))
    return 100.0 - t


def load_bounds_from_manifest(path: Path) -> dict[str, float] | None:
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    bbox = data.get("bbox")
    if not bbox:
        return None
    out = {k: float(bbox[k]) for k in ("north", "south", "east", "west")}
    if "rotation" in bbox and bbox["rotation"] is not None:
        out["rotation"] = float(bbox["rotation"])
    return out


def build_site_kml(
    *,
    document_name: str,
    site_label: str,
    center_lat: float,
    center_lon: float,
    overlay_href: str,
    north: float,
    south: float,
    east: float,
    west: float,
    rotation: float = 0.0,
    pin_description: str | None = None,
    overlay_opacity_pct: float = 100.0,
) -> str:
    href_esc = escape(overlay_href, {"'": "&apos;", '"': "&quot;"})
    name_esc = escape(document_name, {"'": "&apos;", '"': "&quot;"})
    label_esc = escape(site_label, {"'": "&apos;", '"': "&quot;"})
    pin_desc_esc = escape(
        pin_description or f"Center {center_lat:.6f}, {center_lon:.6f}",
        {"'": "&apos;", '"': "&quot;"},
    )
    rot = rotation if not math.isnan(rotation) else 0.0
    overlay_color = ground_overlay_color_kml(opacity_pct=overlay_opacity_pct)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{name_esc}</name>
    <Style id="site-center">
      <IconStyle>
        <scale>1.2</scale>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href>
        </Icon>
        <hotSpot x="32" y="1" xunits="pixels" yunits="pixels"/>
      </IconStyle>
      <LabelStyle>
        <scale>0.9</scale>
      </LabelStyle>
    </Style>
    <Folder>
      <name>{label_esc}</name>
      <description>Viewshed from SPLAT; site: {label_esc}</description>
      <GroundOverlay>
        <name>Coverage</name>
        <color>{overlay_color}</color>
        <Icon>
          <href>{href_esc}</href>
        </Icon>
        <LatLonBox>
          <north>{north:.8f}</north>
          <south>{south:.8f}</south>
          <east>{east:.8f}</east>
          <west>{west:.8f}</west>
          <rotation>{rot:.6f}</rotation>
        </LatLonBox>
      </GroundOverlay>
      <Placemark>
        <name>{label_esc}</name>
        <description>{pin_desc_esc}</description>
        <styleUrl>#site-center</styleUrl>
        <Point>
          <coordinates>{center_lon:.8f},{center_lat:.8f},0</coordinates>
        </Point>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


def placemark_description_cdata(
    *,
    site_description: str | None,
    pin_description: str,
    rationale: str | None,
    plss: str | None = None,
) -> str:
    """Human-readable placemark balloon; display label stays short — detail lives here."""
    d = (site_description or "").strip()
    r = (rationale or "").strip()
    p = (plss or "").strip()
    chunks: list[str] = []
    if d:
        chunks.append(d)
    if r:
        chunks.append(f"Rationale:\n{r}")
    if p:
        chunks.append(f"PLSS:\n{p}")
    chunks.append((pin_description or "").strip())
    body = "\n\n".join(c for c in chunks if c)
    if not body:
        body = "(no detail)"
    if "]]>" in body:
        body = body.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{body}]]>"


@dataclass
class AggregateSiteOverlay:
    """One site's slice inside bundled ``doc.kml`` (PNG href relative to KMZ root, under ``sites/``).

    ``antenna_height_agl_m`` is transmitter AGL (preset ``simulation.transmitter.height_m``), clamped to ≥1 m like
    :func:`peaky_finders.preset_mapping.preset_to_request` (pins + mutual link segments use ``relativeToGround``).
    """

    slug: str
    folder_name: str
    overlay_href: str
    north: float
    south: float
    east: float
    west: float
    rotation: float
    center_lat: float
    center_lon: float
    antenna_height_agl_m: float
    pin_description: str
    rationale: str | None = None
    site_description: str | None = None
    plss: str | None = None
    coverage_kml_href: str | None = None


@dataclass(frozen=True)
class KmzDocumentLayerVisibility:
    """Default on/off state for doc.kml folders and NetworkLinks (Google Earth initial checkbox state)."""

    eligible: bool = False
    exclude: bool = False
    include: bool = False
    aoi: bool = False
    viewshed_raster: bool = False
    viewshed_polygon: bool = True
    pins: bool = True
    mesh_edges: bool = True
    mesh_depth_d1_unique: bool = True
    mesh_depth_d2_pair: bool = True
    mesh_depth_d3_quad: bool = True
    mesh_depth_d5_plus: bool = True
    mesh_depth_eligible_d1_unique: bool = True
    mesh_depth_eligible_d2_pair: bool = True
    mesh_depth_eligible_d3_quad: bool = True
    mesh_depth_eligible_d5_plus: bool = True
    mesh_pairwise: bool = True
    mesh_pairwise_eligible: bool = True


def _vis_open(*, visible: bool) -> tuple[str, str]:
    v = "1" if visible else "0"
    return v, v


def _network_link_folder_xml(*, folder_label: str, kml_href: str, visible: bool = False) -> str:
    label_esc = escape(folder_label, {"'": "&apos;", '"': "&quot;"})
    href_esc = escape(kml_href, {"'": "&apos;", '"': "&quot;"})
    vis = "1" if visible else "0"
    opened = "1" if visible else "0"
    return f"""    <Folder>
      <name>{label_esc}</name>
      <visibility>{vis}</visibility>
      <open>{opened}</open>
      <NetworkLink>
        <name>{label_esc}</name>
        <visibility>{vis}</visibility>
        <Link>
          <href>{href_esc}</href>
        </Link>
      </NetworkLink>
    </Folder>"""


def _land_use_layers_folder_xml(*, folder_title: str, links: Sequence[tuple[str, str]], visible: bool) -> str:
    """Parent ``exclude`` / ``include`` folder with per-layer NetworkLinks (preset KMZ visibility)."""
    esc_attr = {"'": "&apos;", '"': "&quot;"}
    title_esc = escape(folder_title, esc_attr)
    vis_s = "1" if visible else "0"
    inner_parts: list[str] = []
    for label, href in links:
        label_esc = escape(label, esc_attr)
        href_esc = escape(href, esc_attr)
        inner_parts.append(
            f"""      <NetworkLink>
        <name>{label_esc}</name>
        <visibility>{vis_s}</visibility>
        <Link>
          <href>{href_esc}</href>
        </Link>
      </NetworkLink>"""
        )
    inner = "\n".join(inner_parts)
    return f"""    <Folder>
      <name>{title_esc}</name>
      <visibility>{vis_s}</visibility>
      <open>0</open>
{inner}
    </Folder>"""


def _sites_network_link_xml(*, link_name: str, kml_href: str, visible: bool = True) -> str:
    """One ``NetworkLink`` under ``sites/`` subtree with explicit visibility."""
    label_esc = escape(link_name, {"'": "&apos;", '"': "&quot;"})
    href_esc = escape(kml_href, {"'": "&apos;", '"': "&quot;"})
    vis = "1" if visible else "0"
    return f"""        <NetworkLink>
          <name>{label_esc}</name>
          <visibility>{vis}</visibility>
          <Link>
            <href>{href_esc}</href>
          </Link>
        </NetworkLink>"""


def _sites_network_link_mesh_band(
    *,
    link_name: str,
    kml_href: str,
    layer_visibility: KmzDocumentLayerVisibility,
    visibility_attr: str,
) -> str:
    vis = bool(getattr(layer_visibility, visibility_attr, True))
    return _sites_network_link_xml(link_name=link_name, kml_href=kml_href, visible=vis)


def _mesh_folder_xml(
    *,
    folder_title: str,
    folder_visible: bool,
    folder_open: bool,
    inner_xml: str,
) -> str:
    inner = inner_xml.strip()
    if not inner:
        return ""
    title_esc = escape(folder_title, {"'": "&apos;", '"': "&quot;"})
    v = "1" if folder_visible else "0"
    o = "1" if folder_open else "0"
    return f"""      <Folder>
        <name>{title_esc}</name>
        <visibility>{v}</visibility>
        <open>{o}</open>
{inner}
      </Folder>
"""


def build_aggregate_document_kml(
    *,
    document_title: str,
    sites: list[AggregateSiteOverlay],
    overlay_opacity_pct: float = 100.0,
    bundle_network_links: Sequence[tuple[str, str]] = (),
    exclude_layer_network_links: Sequence[tuple[str, str]] = (),
    include_layer_network_links: Sequence[tuple[str, str]] = (),
    eligible_layer_network_links: Sequence[tuple[str, str]] = (),
    reference_bundle_links: Sequence[tuple[str, str, bool]] = (),
    mesh_edges_href: str | None = None,
    mesh_depth_network_links: Sequence[tuple[str, str, str, str]] = (),
    mesh_depth_eligible_network_links: Sequence[tuple[str, str, str, str]] = (),
    mesh_pairwise_network_links: Sequence[tuple[str, str]] = (),
    mesh_pairwise_eligible_network_links: Sequence[tuple[str, str]] = (),
    layer_visibility: KmzDocumentLayerVisibility | None = None,
) -> str:
    """KML 2.2 Document: ``sites``, then land-use NetworkLinks (eligible … aoi).

    Google Earth often ignores parent Folder ``visibility`` for ``NetworkLink`` and ``GroundOverlay``
    children, so set ``visibility`` on each ``NetworkLink`` and each raster ``GroundOverlay`` directly.
    Land-use ``bundle_network_links`` use ``NetworkLink`` ``visibility`` 0. ``sites/viewsheds/raster``
    uses folder ``visibility`` 0 plus per-overlay ``visibility`` 0; ``sites/viewsheds/polygon``,
    optional ``sites/mesh`` (edges, depth, pairwise), and ``sites/pins`` follow preset toggles.

    ``layer_visibility`` overrides the default checkbox state (see :class:`KmzDocumentLayerVisibility`);
    typically loaded from preset ``bundle.kmz.layers.mesh`` (flattened from YAML).

    ``bundle_network_links`` entries are ``(folder_label, kml_path_in_kmz)`` for sidecar
    NetworkLinks. Document order is
    ``sites`` → ``eligible`` → ``exclude`` → ``include`` →
    ``reference_bundle_links`` (each ``(name, href, visible)``) → ``aoi`` (omitting missing layers).

    When ``eligible_layer_network_links`` are non-empty, ``eligible`` is one parent folder whose children
    reference per-include ``eligible/layers/*.kml`` sidecars derived from aggregated eligible ∩ each
    include clip (areas removed by exclusions are not shown).

    When ``exclude_layer_network_links`` / ``include_layer_network_links`` are non-empty, that role is one
    parent folder whose children reference per-layer ``exclude/layers/*.kml`` or ``include/layers/*.kml``
    sidecars (merged union KML for that role is omitted).

    ``sites/mesh`` holds mutual viewshed edges (``mesh_edges_href``), footprint depth bands (per-site
    NetworkLinks grouped by band), and pairwise intersections. Depth entries are
    ``(band, Places name, KMZ href, visibility field)``; pairwise links are ``(name, href)`` using
    ``mesh_pairwise`` / ``mesh_pairwise_eligible``.
    """
    lv = layer_visibility or KmzDocumentLayerVisibility()
    title_esc = escape(document_title, {"'": "&apos;", '"': "&quot;"})
    overlay_color = ground_overlay_color_kml(opacity_pct=overlay_opacity_pct)

    pins_site_folders: list[str] = []
    raster_site_folders: list[str] = []
    polygon_site_folders: list[str] = []

    vo_ras, oo_ras = _vis_open(visible=lv.viewshed_raster)
    v_ov = "1" if lv.viewshed_raster else "0"
    v_poly, o_poly = _vis_open(visible=lv.viewshed_polygon)
    v_cov = "1" if lv.viewshed_polygon else "0"

    for s in sites:
        fname_esc = escape(s.folder_name, {"'": "&apos;", '"': "&quot;"})
        balloon = placemark_description_cdata(
            site_description=s.site_description,
            pin_description=s.pin_description,
            rationale=s.rationale,
            plss=s.plss,
        )
        rot = s.rotation if not math.isnan(s.rotation) else 0.0
        pins_site_folders.append(
            f"""        <Folder>
          <name>{fname_esc}</name>
          <Placemark>
            <name>{fname_esc}</name>
            <description>{balloon}</description>
            <styleUrl>#site-center</styleUrl>
            <Point>
              <altitudeMode>relativeToGround</altitudeMode>
              <coordinates>{s.center_lon:.8f},{s.center_lat:.8f},{s.antenna_height_agl_m:.6f}</coordinates>
            </Point>
          </Placemark>
        </Folder>"""
        )
        href_esc = escape(s.overlay_href, {"'": "&apos;", '"': "&quot;"})
        raster_site_folders.append(
            f"""            <Folder>
              <name>{fname_esc}</name>
              <GroundOverlay>
                <name>{fname_esc}</name>
                <visibility>{v_ov}</visibility>
                <gx:drawOrder>{GX_DRAW_ORDER_VIEWSHED_RASTER:d}</gx:drawOrder>
                <color>{overlay_color}</color>
                <Icon>
                  <href>{href_esc}</href>
                </Icon>
                <LatLonBox>
                  <north>{s.north:.8f}</north>
                  <south>{s.south:.8f}</south>
                  <east>{s.east:.8f}</east>
                  <west>{s.west:.8f}</west>
                  <rotation>{rot:.6f}</rotation>
                </LatLonBox>
              </GroundOverlay>
            </Folder>"""
        )
        poly_inner = ""
        if s.coverage_kml_href:
            cov_esc = escape(s.coverage_kml_href, {"'": "&apos;", '"': "&quot;"})
            poly_inner = f"""              <NetworkLink>
                <name>Coverage footprint</name>
                <visibility>{v_cov}</visibility>
                <Link>
                  <href>{cov_esc}</href>
                </Link>
              </NetworkLink>
"""
        polygon_site_folders.append(
            f"""            <Folder>
              <name>{fname_esc}</name>
{poly_inner}            </Folder>"""
        )

    pins_xml = "\n".join(pins_site_folders)
    raster_xml = "\n".join(raster_site_folders)
    polygon_xml = "\n".join(polygon_site_folders)

    v_me, o_me = _vis_open(visible=lv.mesh_edges)
    edges_inner = ""
    if mesh_edges_href:
        sl_esc = escape(mesh_edges_href, {"'": "&apos;", '"': "&quot;"})
        v_nl = "1" if lv.mesh_edges else "0"
        edges_inner = f"""        <NetworkLink>
          <name>Site-to-site links</name>
          <visibility>{v_nl}</visibility>
          <Link>
            <href>{sl_esc}</href>
          </Link>
        </NetworkLink>
"""
    edges_folder = _mesh_folder_xml(
        folder_title="edges",
        folder_visible=lv.mesh_edges,
        folder_open=o_me == "1",
        inner_xml=edges_inner,
    )

    grouped_depth: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for band, nm, href, attr in mesh_depth_network_links:
        grouped_depth[band].append((nm, href, attr))

    depth_sections: list[str] = []
    for band in MESH_DEPTH_BANDS:
        rows = grouped_depth.get(band)
        if not rows:
            continue
        nls = "\n".join(
            _sites_network_link_mesh_band(
                link_name=nm,
                kml_href=href,
                layer_visibility=lv,
                visibility_attr=attr,
            )
            for nm, href, attr in rows
        )
        vis_key = mesh_depth_visibility_field(band, eligible=False)
        band_folder = _mesh_folder_xml(
            folder_title=MESH_DEPTH_DISPLAY.get(band, band),
            folder_visible=getattr(lv, vis_key, True),
            folder_open=any(getattr(lv, attr, True) for _nm, _href, attr in rows),
            inner_xml=nls,
        )
        depth_sections.append(band_folder)

    depth_inner = "".join(depth_sections)

    grouped_del: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for band, nm, href, attr in mesh_depth_eligible_network_links:
        grouped_del[band].append((nm, href, attr))

    del_sections: list[str] = []
    for band in MESH_DEPTH_BANDS:
        rows = grouped_del.get(band)
        if not rows:
            continue
        nls = "\n".join(
            _sites_network_link_mesh_band(
                link_name=nm,
                kml_href=href,
                layer_visibility=lv,
                visibility_attr=attr,
            )
            for nm, href, attr in rows
        )
        vis_key = mesh_depth_visibility_field(band, eligible=True)
        band_folder = _mesh_folder_xml(
            folder_title=MESH_DEPTH_DISPLAY.get(band, band),
            folder_visible=getattr(lv, vis_key, True),
            folder_open=any(getattr(lv, attr, True) for _nm, _href, attr in rows),
            inner_xml=nls,
        )
        del_sections.append(band_folder)

    del_inner = "".join(del_sections)

    depth_folder = _mesh_folder_xml(
        folder_title="depth",
        folder_visible=True,
        folder_open=bool(depth_inner.strip()),
        inner_xml=depth_inner,
    )

    depth_elig_folder = _mesh_folder_xml(
        folder_title="depth_eligible",
        folder_visible=True,
        folder_open=bool(del_inner.strip()),
        inner_xml=del_inner,
    )

    pw_inner = "\n".join(
        _sites_network_link_xml(link_name=nm, kml_href=href, visible=lv.mesh_pairwise)
        for nm, href in mesh_pairwise_network_links
    )
    pw_folder = _mesh_folder_xml(
        folder_title="pairwise",
        folder_visible=lv.mesh_pairwise,
        folder_open=lv.mesh_pairwise,
        inner_xml=pw_inner,
    )

    pwe_inner = "\n".join(
        _sites_network_link_xml(link_name=nm, kml_href=href, visible=lv.mesh_pairwise_eligible)
        for nm, href in mesh_pairwise_eligible_network_links
    )
    pwe_folder = _mesh_folder_xml(
        folder_title="pairwise_eligible",
        folder_visible=lv.mesh_pairwise_eligible,
        folder_open=lv.mesh_pairwise_eligible,
        inner_xml=pwe_inner,
    )

    coverage_body = f"{depth_folder}{depth_elig_folder}{pw_folder}{pwe_folder}"
    coverage_folder = _mesh_folder_xml(
        folder_title="coverage",
        folder_visible=True,
        folder_open=bool(coverage_body.strip()),
        inner_xml=coverage_body,
    )

    mesh_body = f"{edges_folder}{coverage_folder}"
    mesh_folder_xml = _mesh_folder_xml(
        folder_title="mesh",
        folder_visible=True,
        folder_open=bool(mesh_body.strip()),
        inner_xml=mesh_body,
    )

    by_label = {n.strip().lower(): (n, h) for n, h in bundle_network_links}
    bundle_tail_blocks: list[str] = []
    if eligible_layer_network_links:
        bundle_tail_blocks.append(
            _land_use_layers_folder_xml(
                folder_title="eligible",
                links=eligible_layer_network_links,
                visible=getattr(lv, "eligible"),
            )
        )
    elif "eligible" in by_label:
        bundle_tail_blocks.append(
            _network_link_folder_xml(
                folder_label=by_label["eligible"][0],
                kml_href=by_label["eligible"][1],
                visible=getattr(lv, "eligible"),
            )
        )
    if exclude_layer_network_links:
        bundle_tail_blocks.append(
            _land_use_layers_folder_xml(
                folder_title="exclude",
                links=exclude_layer_network_links,
                visible=getattr(lv, "exclude"),
            )
        )
    elif "exclude" in by_label:
        bundle_tail_blocks.append(
            _network_link_folder_xml(
                folder_label=by_label["exclude"][0],
                kml_href=by_label["exclude"][1],
                visible=getattr(lv, "exclude"),
            )
        )
    if include_layer_network_links:
        bundle_tail_blocks.append(
            _land_use_layers_folder_xml(
                folder_title="include",
                links=include_layer_network_links,
                visible=getattr(lv, "include"),
            )
        )
    elif "include" in by_label:
        bundle_tail_blocks.append(
            _network_link_folder_xml(
                folder_label=by_label["include"][0],
                kml_href=by_label["include"][1],
                visible=getattr(lv, "include"),
            )
        )
    for label, href, vis in reference_bundle_links:
        bundle_tail_blocks.append(
            _network_link_folder_xml(folder_label=label, kml_href=href, visible=vis)
        )
    if "aoi" in by_label:
        bundle_tail_blocks.append(
            _network_link_folder_xml(
                folder_label=by_label["aoi"][0],
                kml_href=by_label["aoi"][1],
                visible=getattr(lv, "aoi"),
            )
        )
    bundle_tail_xml = "\n".join(bundle_tail_blocks)

    v_pins, o_pins = _vis_open(visible=lv.pins)

    sites_folder = f"""    <Folder>
      <name>sites</name>
      <visibility>1</visibility>
      <open>1</open>
      <Folder>
        <name>viewsheds</name>
        <visibility>1</visibility>
        <open>1</open>
        <Folder>
          <name>raster</name>
          <visibility>{vo_ras}</visibility>
          <open>{oo_ras}</open>
{raster_xml}
        </Folder>
        <Folder>
          <name>polygon</name>
          <visibility>{v_poly}</visibility>
          <open>{o_poly}</open>
{polygon_xml}
        </Folder>
      </Folder>
{mesh_folder_xml}      <Folder>
        <name>pins</name>
        <visibility>{v_pins}</visibility>
        <open>{o_pins}</open>
{pins_xml}
      </Folder>
    </Folder>
"""

    body = "\n".join(part for part in (sites_folder, bundle_tail_xml) if part.strip())

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
  <Document>
    <name>{title_esc}</name>
    <Style id="site-center">
      <IconStyle>
        <scale>1.2</scale>
        <Icon>
          <href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href>
        </Icon>
        <hotSpot x="32" y="1" xunits="pixels" yunits="pixels"/>
      </IconStyle>
      <LabelStyle>
        <scale>0.9</scale>
      </LabelStyle>
    </Style>
{body}
  </Document>
</kml>
"""
