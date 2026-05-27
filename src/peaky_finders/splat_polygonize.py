"""Vector footprint from SPLAT ``output.ppm`` (native grid) + rotated LatLonBox."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import numpy as np
from affine import Affine
from PIL import Image
from rasterio import features
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

from peaky_finders.coverage_png import bbox_rotation_normalized, pixel_to_lat_lon
from peaky_finders.google_earth_polygon import orient_for_kml
from peaky_finders.sites_job import BundleKmlLayerStyle, DEFAULT_VIEWSHED_COVERAGE_KML_STYLE

SPLAT_GPKG_NAME = "splat.gpkg"
SPLAT_KML_NAME = "splat.kml"
SPLAT_OUTPUT_PPM_BASENAME = "output.ppm"

VIEWSHED_COVERAGE_KML_STYLE_ID = "peaky_viewshed_coverage"
MESH_PAIRWISE_KML_STYLE_ID = "peaky_mesh_pairwise"
MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID = "peaky_mesh_pairwise_eligible"
MESH_DEPTH_D1_KML_STYLE_ID = "peaky_mesh_depth_d1"
MESH_DEPTH_D2_KML_STYLE_ID = "peaky_mesh_depth_d2"
MESH_DEPTH_D3_KML_STYLE_ID = "peaky_mesh_depth_d3"
MESH_DEPTH_D5_KML_STYLE_ID = "peaky_mesh_depth_d5"
MESH_DEPTH_ELIGIBLE_D1_KML_STYLE_ID = "peaky_mesh_depth_eligible_d1"
MESH_DEPTH_ELIGIBLE_D2_KML_STYLE_ID = "peaky_mesh_depth_eligible_d2"
MESH_DEPTH_ELIGIBLE_D3_KML_STYLE_ID = "peaky_mesh_depth_eligible_d3"
MESH_DEPTH_ELIGIBLE_D5_KML_STYLE_ID = "peaky_mesh_depth_eligible_d5"

GX_NS = "http://www.google.com/kml/ext/2.2"
# GE stacks clamped overlays/polygons using gx:drawOrder; Places-folder order does not control it.
GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON = 0
# GroundOverlay rasters share the same stack band as viewshed footprint polygons (below mesh).
GX_DRAW_ORDER_VIEWSHED_RASTER = GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON
GX_DRAW_ORDER_MESH_DEPTH_D1 = 40
GX_DRAW_ORDER_MESH_DEPTH_D2 = 41
GX_DRAW_ORDER_MESH_DEPTH_D3 = 42
GX_DRAW_ORDER_MESH_DEPTH_D5 = 43
GX_DRAW_ORDER_MESH_PAIRWISE = 50
GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D1 = 52
GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D2 = 53
GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D3 = 54
GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D5 = 55
GX_DRAW_ORDER_MESH_PAIRWISE_ELIGIBLE = 60
GX_DRAW_ORDER_MESH_SITE_TO_SITE = 70

# Bump when host-side KML emission changes but geometry/DEM inputs are unchanged (icons,
# inject_peaky_polygon_kml_style, gx:drawOrder constants, placemark templates, etc.).
# Included in flat KML cache fingerprints — not in overlap.gpkg / dem_peak JSON caches.
PEAKY_KML_EMIT_VERSION = 2


def _local_kml_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def inject_peaky_polygon_kml_style(
    kml_path: Path,
    *,
    style_id: str,
    spec: BundleKmlLayerStyle,
    gx_draw_order: int | None = None,
) -> None:
    """Replace GDAL default placemark styling using preset ``BundleKmlLayerStyle`` (matches bundle_build rules)."""
    try:
        tree = ET.parse(kml_path)
    except ET.ParseError:
        return
    root = tree.getroot()
    if root.tag.startswith("{"):
        ns, local = root.tag[1:].split("}", 1)
    else:
        ns, local = "", root.tag
    if local != "kml":
        return

    def t(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    doc = root.find(t("Document"))
    if doc is None:
        return

    draw_order_tag = f"{{{GX_NS}}}drawOrder"
    altitude_tag = t("altitudeMode")
    if gx_draw_order is not None:
        for el in doc.iter():
            if _local_kml_tag(el.tag) != "Polygon":
                continue
            for child in list(el):
                if child.tag in (draw_order_tag, altitude_tag):
                    el.remove(child)
            go = ET.Element(draw_order_tag)
            go.text = str(int(gx_draw_order))
            el.insert(0, go)
            am = ET.Element(altitude_tag)
            am.text = "clampToGround"
            el.insert(1, am)

    style_tag = t("Style")
    style_map_tag = t("StyleMap")
    su_tag = t("styleUrl")
    for prior in list(doc):
        if prior.tag == style_tag and (prior.get("id") or "") == style_id:
            doc.remove(prior)

    outline_on = spec.line_width > 0
    st = ET.Element(style_tag)
    st.set("id", style_id)
    ls = ET.SubElement(st, t("LineStyle"))
    ET.SubElement(ls, t("color")).text = spec.line
    ET.SubElement(ls, t("width")).text = f"{spec.line_width:g}"
    ps = ET.SubElement(st, t("PolyStyle"))
    ET.SubElement(ps, t("fill")).text = "1" if spec.fill_polygons else "0"
    ET.SubElement(ps, t("outline")).text = "1" if outline_on else "0"
    ET.SubElement(ps, t("color")).text = spec.fill if spec.fill_polygons else "00000000"
    if not spec.fill_polygons:
        ic = ET.SubElement(st, t("IconStyle"))
        ET.SubElement(ic, t("color")).text = spec.line
        ET.SubElement(ic, t("scale")).text = "0.45"
        href = ET.SubElement(ET.SubElement(ic, t("Icon")), t("href"))
        href.text = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    doc.insert(0, st)

    for pm in doc.iter(t("Placemark")):
        if not any(_local_kml_tag(ch.tag) == "Polygon" for ch in pm.iter()):
            continue
        for child in list(pm):
            if child.tag in (style_tag, style_map_tag):
                pm.remove(child)
        for child in list(pm):
            if child.tag == su_tag:
                pm.remove(child)
        su = ET.Element(su_tag)
        su.text = f"#{style_id}"
        pm.insert(0, su)

    if ns:
        ET.register_namespace("", ns)
    if gx_draw_order is not None:
        ET.register_namespace("gx", GX_NS)
    tree.write(kml_path, encoding="utf-8", xml_declaration=True)


def coverage_mask_from_splat_ppm_rgb(rgb: np.ndarray) -> np.ndarray:
    """Binary mask (1=covered): any pixel that is not pure white.

    Matches :func:`peaky_finders.splat_ppm_to_png.write_splat_png_from_ppm`: SPLAT marks
    no-signal areas as ``(255, 255, 255)`` in common outputs (see ``WritePPMSS`` in
    ``vendor/meshtastic-site-planner/splat/splat.cpp``, e.g. ``fprintf(..., 255,255,255)``
    when ``ngs`` and below contour). Non-white pixels (RF tint, terrain shading, sea,
    etc.) are treated like the KMZ overlay—opaque in ``splat.png``.
    """
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("Expected RGB image array")
    ch = rgb[..., :3].astype(np.uint8, copy=False)
    no_coverage = np.all(ch == 255, axis=2)
    covered = ~no_coverage
    return covered.astype(np.uint8)


def coverage_mask_from_rgba(arr: np.ndarray) -> np.ndarray:
    """Binary mask (1=covered, 0=no RF); aligned with ``point_in_png_coverage``."""
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("Expected RGB or RGBA image array")
    r = arr[..., 0].astype(np.uint8)
    g = arr[..., 1].astype(np.uint8)
    b = arr[..., 2].astype(np.uint8)
    white = (r == 255) & (g == 255) & (b == 255)
    if arr.shape[2] >= 4:
        a = arr[..., 3].astype(np.uint8)
        covered = (a > 127) & (~white)
    else:
        covered = ~white
    return covered.astype(np.uint8)


def _pixels_to_wgs84(geom: Polygon | MultiPolygon, *, width: int, height: int, bbox: dict[str, float]):
    north, south, east, west, rotation_deg = bbox_rotation_normalized(bbox)

    def mapper_x_y(col: float, row: float) -> tuple[float, float]:
        lat, lon = pixel_to_lat_lon(
            col,
            row,
            width=width,
            height=height,
            north=north,
            south=south,
            east=east,
            west=west,
            rotation_deg=rotation_deg,
        )
        return lon, lat

    from shapely.ops import transform as shapely_transform

    return shapely_transform(mapper_x_y, geom)


def polygonize_mask(mask: np.ndarray) -> Polygon | MultiPolygon | None:
    """Merge contiguous covered pixels into one or more polygons (pixel CRS)."""
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    transform = Affine.identity()
    pieces: list[Polygon] = []
    for geom_dict, val in features.shapes(mask, mask=(mask == 1), transform=transform, connectivity=8):
        if int(val) != 1:
            continue
        g = shape(geom_dict)
        if g.is_empty:
            continue
        if isinstance(g, Polygon):
            pieces.append(g)
        elif isinstance(g, MultiPolygon):
            pieces.extend(g.geoms)

    if not pieces:
        return None
    merged = unary_union(pieces)
    if merged.is_empty:
        return None
    if isinstance(merged, (Polygon, MultiPolygon)):
        return merged
    return None


def write_coverage_polygons(
    *,
    ppm_path: Path,
    bbox: dict[str, float],
    out_gpkg: Path,
    out_kml: Path,
    polygon_style: BundleKmlLayerStyle | None = None,
) -> bool:
    """Write footprint GPKG + vector KML from ``output.ppm`` and SPLAT bbox.

    Uses the native PPM grid (full SPLAT resolution), not ``splat.png`` (which may be
    downscaled for GroundOverlay texture limits). The footprint mask is non-white PPM pixels
    only—the same rule as :func:`coverage_mask_from_splat_ppm_rgb` / ``splat.png`` opacity.

    ``polygon_style`` defaults to :data:`DEFAULT_VIEWSHED_COVERAGE_KML_STYLE`; preset overrides via
    ``bundle.kml_overlay.viewshed_coverage``.

    Returns True if at least one covered pixel existed and files were written.
    """
    ppm_path = Path(ppm_path)
    out_gpkg = Path(out_gpkg)
    out_kml = Path(out_kml)
    style = polygon_style if polygon_style is not None else DEFAULT_VIEWSHED_COVERAGE_KML_STYLE

    with Image.open(ppm_path) as im:
        rgb = np.asarray(im.convert("RGB"))

    h, w = rgb.shape[0], rgb.shape[1]
    mask = coverage_mask_from_splat_ppm_rgb(rgb)
    geom_px = polygonize_mask(mask)
    if geom_px is None:
        return False

    geom_ll = _pixels_to_wgs84(geom_px, width=w, height=h, bbox=bbox)
    geom_ll = orient_for_kml(geom_ll)

    out_gpkg.parent.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"geometry": [geom_ll]}, crs="EPSG:4326")
    gdf.to_file(out_gpkg, driver="GPKG", layer="coverage")
    gdf.to_file(out_kml, driver="KML")
    inject_peaky_polygon_kml_style(
        out_kml,
        style_id=VIEWSHED_COVERAGE_KML_STYLE_ID,
        spec=style,
        gx_draw_order=GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
    )

    return True
