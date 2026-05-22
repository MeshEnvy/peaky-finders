"""Build the AOI / land-use bundle: preset-listed GDB layers → global ``clips/`` cache + job workspace.

Per-layer clips and composites (aoi, include, exclude, eligible) live under
``<cache_base>/clips/``. Each preset job writes ``<cache_base>/bundles/<job_sha>/resolve.json``
pointing at those artifacts.
``bundle.kml_overlay`` is tracked separately so sidecar KML/PNG can refresh without a full GDB rebuild.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely import count_coordinates, make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.google_earth_polygon import orient_for_kml
from peaky_finders.plss_mlrs_fetch import maybe_refresh_plss_mlrs_for_bundle
from peaky_finders.sites_job import (
    BundleKmlOverlayStyles,
    BundleConfig,
    BundleReferenceLayerEntry,
    GdbLayerGroup,
    Preset,
    _gdb_layer_group_payload,
    canonical_bundle_aoi_config_text,
    canonical_bundle_land_use_config_text,
    canonical_bundle_reference_config_text,
    load_preset,
    ogr_where_for_layer_spec,
    peaky_home,
    resolved_bundle_cache_root,
    resolved_cache_base,
    resolved_splat_tile_cache_dir,
)

from peaky_finders.skadi_dem import (
    iter_skadi_tile_names_for_wgs84_bounds,
    prefetch_skadi_hgt_for_bounds_fatal,
)

def resolve_land_use_gdb_path(data_dir: Path, path_str: str) -> Path:
    """Resolve preset ``path`` relative to ``data_dir`` unless absolute."""
    p = Path(path_str.strip())
    if p.is_absolute():
        return p
    return (Path(data_dir).expanduser().resolve() / p).resolve()


def require_bundle_config(preset: Preset) -> BundleConfig:
    if preset.bundle is None:
        raise ValueError('Preset needs a top-level "bundle" object with aoi, include, and exclude.')
    pre = preset.bundle
    if not pre.aoi:
        raise ValueError(
            'Preset needs non-empty "bundle.aoi" (GDB path + polygon layers defining the AOI).'
        )
    if not pre.include:
        raise ValueError(
            'Preset needs non-empty "bundle.include". Layer names:'
            "\n  peaky inspect <path_to.gdb>"
            "\nthen edit the preset JSON bundle.include / bundle.exclude arrays."
        )
    return pre


def _unique_sorted_aoi_gdb_roots(pre: BundleConfig, data_dir: Path) -> list[Path]:
    seen: set[str] = set()
    roots: list[Path] = []
    for g in pre.aoi:
        r = resolve_land_use_gdb_path(data_dir, g.path)
        key = str(r.resolve())
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return sorted(roots, key=lambda p: str(p))


def _unique_sorted_gdb_roots(pre: BundleConfig, data_dir: Path) -> list[Path]:
    seen: set[str] = set()
    roots: list[Path] = []
    for g in pre.include + pre.exclude:
        r = resolve_land_use_gdb_path(data_dir, g.path)
        key = str(r.resolve())
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return sorted(roots, key=lambda p: str(p))


def _unique_sorted_include_gdb_roots(pre: BundleConfig, data_dir: Path) -> list[Path]:
    seen: set[str] = set()
    roots: list[Path] = []
    for g in pre.include:
        r = resolve_land_use_gdb_path(data_dir, g.path)
        key = str(r.resolve())
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return sorted(roots, key=lambda p: str(p))


def _unique_sorted_exclude_gdb_roots(pre: BundleConfig, data_dir: Path) -> list[Path]:
    seen: set[str] = set()
    roots: list[Path] = []
    for g in pre.exclude:
        r = resolve_land_use_gdb_path(data_dir, g.path)
        key = str(r.resolve())
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return sorted(roots, key=lambda p: str(p))


def land_use_inputs_fingerprint_body(pre: BundleConfig, data_dir: Path) -> str:
    """v3: canonical preset bundle land-use config + per-referenced-GDB file-tree fingerprints."""
    data_dir = Path(data_dir).expanduser().resolve()
    parts: list[str] = [
        "format=bundle_land_use_inputs/v3",
        "config",
        canonical_bundle_land_use_config_text(pre).rstrip("\n"),
        "gdb_roots",
    ]
    for root in _unique_sorted_gdb_roots(pre, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for bundle fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(_file_tree_mtime_size_fingerprint(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def aoi_inputs_fingerprint_body(pre: BundleConfig, data_dir: Path) -> str:
    """v1: AOI preset config + per-referenced-GDB file-tree fingerprints (AOI sources only)."""
    data_dir = Path(data_dir).expanduser().resolve()
    parts: list[str] = [
        "format=bundle_aoi_inputs/v1",
        "config",
        canonical_bundle_aoi_config_text(pre).rstrip("\n"),
        "gdb_roots",
    ]
    for root in _unique_sorted_aoi_gdb_roots(pre, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for AOI fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(_file_tree_mtime_size_fingerprint(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def bundle_aoi_inputs_digest(pre: BundleConfig, data_dir: Path) -> str:
    """SHA-256 hex of AOI fingerprint (preset AOI paths/layers + GDB file trees)."""
    dd = Path(data_dir).expanduser().resolve()
    body = aoi_inputs_fingerprint_body(pre, dd)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def bundle_aoi_dir_slug(pre: BundleConfig, data_dir: Path) -> str:
    """Legacy slug (AOI inputs); job dirs use :func:`bundle_job_dir_slug` instead."""
    dd = Path(data_dir).expanduser().resolve()
    payload = aoi_inputs_fingerprint_body(pre, dd).encode("utf-8")
    return "aoi_" + hashlib.sha256(payload).hexdigest()[:16]


def bundle_job_dir_slug(*, preset: Preset, data_dir: Path) -> str:
    """Filesystem directory name for a preset job workspace under ``bundles/``."""
    return bundle_cache_digest(preset=preset, data_dir=data_dir)


def bundle_land_use_inputs_digest(pre: BundleConfig, data_dir: Path) -> str:
    """SHA-256 hex of v3 land-use fingerprint (preset paths/layers + GDB file trees)."""
    dd = Path(data_dir).expanduser().resolve()
    body = land_use_inputs_fingerprint_body(pre, dd)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _unique_sorted_reference_gdb_roots(pre: BundleConfig, data_dir: Path) -> list[Path]:
    if not pre.reference:
        return []
    seen: set[str] = set()
    roots: list[Path] = []
    for ent in pre.reference:
        r = resolve_land_use_gdb_path(data_dir, ent.path)
        key = str(r.resolve())
        if key not in seen:
            seen.add(key)
            roots.append(r)
    return sorted(roots, key=lambda p: str(p))


def bundle_reference_inputs_fingerprint_body(pre: BundleConfig, data_dir: Path) -> str:
    """Canonical reference preset + GDB file-tree mtimes (independent of land-use cache digest)."""
    data_dir = Path(data_dir).expanduser().resolve()
    if not pre.reference:
        return (
            "format=bundle_reference_inputs/v1\nconfig\n"
            + json.dumps({"reference": []}, sort_keys=True)
            + "\n"
        )
    parts: list[str] = [
        "format=bundle_reference_inputs/v1",
        "config",
        canonical_bundle_reference_config_text(pre).rstrip("\n"),
        "gdb_roots",
    ]
    for root in _unique_sorted_reference_gdb_roots(pre, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for reference bundle fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(_file_tree_mtime_size_fingerprint(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def bundle_reference_inputs_digest(pre: BundleConfig, data_dir: Path) -> str:
    body = bundle_reference_inputs_fingerprint_body(pre, data_dir)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def bundle_kml_overlay_inputs_fingerprint_body(pre: BundleConfig) -> str:
    """Stable multiline body for hashing ``pre.kml_overlay`` (``null`` vs style JSON)."""
    payload = None if pre.kml_overlay is None else pre.kml_overlay.model_dump(mode="json")
    cfg = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"format=bundle_kml_overlay_inputs/v1\nconfig\n{cfg}\n"


def bundle_kml_overlay_inputs_digest(pre: BundleConfig) -> str:
    """SHA-256 hex for ``bundle.kml_overlay`` (sidecar regeneration; not part of land-use dir slug)."""
    body = bundle_kml_overlay_inputs_fingerprint_body(pre)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def bundle_cache_digest(*, preset: Preset, data_dir: Path | None = None) -> str:
    """SHA-256 hex: AOI (v1) + land-use (v3) fingerprint bodies."""
    dd = Path(data_dir).expanduser().resolve() if data_dir is not None else peaky_home() / "data"
    pre = require_bundle_config(preset)
    payload = aoi_inputs_fingerprint_body(pre, dd) + land_use_inputs_fingerprint_body(pre, dd)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bundle_log(verbose: bool, msg: str) -> None:
    if verbose:
        print(f"bundle: {msg}", flush=True)


def _bundle_progress_enabled(verbose: bool) -> bool:
    """Progress lines (flushed) during long OGR steps: ``--verbose`` or ``PEAKY_BUNDLE_PROGRESS``."""
    if verbose:
        return True
    v = os.environ.get("PEAKY_BUNDLE_PROGRESS", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _bundle_progress(verbose: bool, msg: str) -> None:
    if _bundle_progress_enabled(verbose):
        print(f"bundle: {msg}", flush=True)


def _file_tree_mtime_size_fingerprint(root: Path) -> str:
    """Stable multiline fingerprint: one ``relpath<TAB>mtime_ns<TAB>size`` per file under root."""
    root = root.resolve()
    lines: list[str] = []
    if root.is_file():
        st = root.stat()
        lines.append(f"{root.name}\t{st.st_mtime_ns}\t{st.st_size}")
        return "\n".join(lines) + "\n"
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        st = path.stat()
        lines.append(f"{rel}\t{st.st_mtime_ns}\t{st.st_size}")
    return "\n".join(lines) + ("\n" if lines else "")


def load_composite_aoi_polygon(pre: BundleConfig, data_dir: Path) -> BaseGeometry:
    """Union all ``bundle.aoi`` polygon layers to one geometry in EPSG:4326."""
    pieces: list[BaseGeometry] = []
    jobs = _flatten_gdb_layer_jobs(pre.aoi, data_dir)
    for preset_path, resolved, layer_name, where in jobs:
        if not resolved.exists():
            raise FileNotFoundError(f"AOI GDB not found: {resolved} (preset path {preset_path!r})")
        with openfilegdb_dataset_path(resolved) as ds:
            gt = _geom_type_for_layer(ds, layer_name)
            if gt is None:
                raise ValueError(f"AOI layer {layer_name!r} not found in {resolved}")
            if not is_polygon_geometry_type(gt):
                raise ValueError(
                    f"AOI layer {preset_path}::{layer_name} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
            rkw: dict[str, str] = {"layer": layer_name}
            if where:
                rkw["where"] = where
            raw = pyogrio.read_dataframe(ds, **rkw)
            gdf = gpd.GeoDataFrame(raw, geometry="geometry")
            if gdf.crs is None:
                raise ValueError(f"CRS missing on AOI {resolved}:{layer_name}")
            g_ll = gdf.to_crs("EPSG:4326")
            pieces.extend(g_ll.geometry.tolist())
    u = _sanitize_collection(pieces)
    if u.is_empty:
        raise ValueError("AOI union from bundle.aoi is empty")
    return u


def is_polygon_geometry_type(gt: str) -> bool:
    g = gt.lower().replace("_", "").replace("-", "").replace(" ", "")
    return "polygon" in g


@contextmanager
def openfilegdb_dataset_path(gdb_path: Path) -> Iterator[str]:
    """Yield a GDAL-readable dataset path (FileGDB folder, unpacked GDB sibling, or GeoPackage file)."""
    gdb_path = gdb_path.expanduser().resolve()
    if not gdb_path.exists():
        raise FileNotFoundError(f"GDB path not found: {gdb_path}")
    if gdb_path.is_file() and gdb_path.suffix.lower() == ".gpkg":
        yield str(gdb_path)
        return
    if gdb_path.is_dir() and gdb_path.name.lower().endswith(".gdb"):
        yield str(gdb_path)
        return
    if gdb_path.is_dir() and any(gdb_path.glob("*.gdbtable")):
        td = Path(tempfile.mkdtemp(prefix="openfilegdb_", dir=None))
        try:
            link = td / f"{gdb_path.name}.gdb"
            link.symlink_to(gdb_path, target_is_directory=True)
            yield str(link)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        return
    raise FileNotFoundError(
        f"Not a supported bundle vector dataset (File Geodatabase directory or .gpkg file): {gdb_path}"
    )


def _sanitize_collection(geoms: list[BaseGeometry]) -> BaseGeometry:
    cleaned: list[BaseGeometry] = []
    for g in geoms:
        if g is None or g.is_empty:
            continue
        gx = make_valid(g) if not g.is_valid else g
        if gx.is_empty:
            continue
        cleaned.append(gx)
    if not cleaned:
        return Polygon()
    u = unary_union(cleaned)
    return make_valid(u) if not u.is_valid else u


def _polygonal_area_only(geom: BaseGeometry) -> BaseGeometry:
    """Keep only polygon / multipolygon parts (drop points, linestrings, nested collections).

    Shapely :func:`~shapely.make_valid` and ``to_crs`` can yield :class:`~shapely.geometry.GeometryCollection`
    values that still contain stray points or nested collections. Those show up as pushpins when GDAL writes
    KML (many ``<Point>`` placemarks inside ``<MultiGeometry>``).
    """

    def collect_polys(g: BaseGeometry, acc: list[BaseGeometry]) -> None:
        if g is None or g.is_empty:
            return
        t = g.geom_type
        if t in ("Polygon", "MultiPolygon"):
            acc.append(g)
        elif t == "GeometryCollection":
            for g0 in g.geoms:
                collect_polys(g0, acc)
        # LineString, Point, LinearRing, etc. — ignore

    if geom is None or geom.is_empty:
        return Polygon()
    g = make_valid(geom) if not geom.is_valid else geom
    if g.is_empty:
        return Polygon()
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    polys: list[BaseGeometry] = []
    collect_polys(g, polys)
    if not polys:
        return Polygon()
    u = unary_union(polys)
    return make_valid(u) if not u.is_valid else u


def _gdf_coordinate_vertex_count(gdf: gpd.GeoDataFrame) -> int:
    """Total coordinate positions across geometry (all parts/rings; ring closure duplicated)."""
    if gdf.empty:
        return 0
    n = 0
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        n += int(count_coordinates(g))
    return n


def _kml_safe_name(label: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in label.strip())
    return s[:200] or "layer"


_KML_NS = "http://www.opengis.net/kml/2.2"
# Google Earth Pro stacks clamped polygons using gx:drawOrder (see splat_polygonize); bundle
# vector KML omitted this, which can leave fills missing under terrain/overlay compositing.
_GX_NS = "http://www.google.com/kml/ext/2.2"
_BUNDLE_GX_DRAW_ORDER_BY_ROLE: dict[str, int] = {
    "aoi": 5,
    "reference": 7,
    "include": 10,
    "exclude": 11,
    "eligible": 15,
    "default": 12,
}


def _kml_tag(local: str) -> str:
    return f"{{{_KML_NS}}}{local}"


def _kml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _explode_polygon_rows_for_google_earth(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One GDAL/KML feature per polygon part so Google Earth does not drop fills on large MultiGeometry."""
    if gdf.empty:
        return gdf
    out = gdf.explode("geometry", ignore_index=True)
    out = out[~out.geometry.is_empty & out.geometry.notna()]
    if out.empty:
        return out
    keep = out.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    return out.loc[keep].reset_index(drop=True)


def _kml_inject_ge_polygon_render_hints(kml_path: Path, *, layer_label: str) -> None:
    """Tag every ``Polygon`` with ``gx:drawOrder`` and ``clampToGround`` for Google Earth Pro."""
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

    role = _kml_overlay_role(kml_path, layer_label)
    order = _BUNDLE_GX_DRAW_ORDER_BY_ROLE.get(role, _BUNDLE_GX_DRAW_ORDER_BY_ROLE["default"])
    gx_draw_tag = f"{{{_GX_NS}}}drawOrder"
    for el in doc.iter():
        if _kml_local_name(el.tag) != "Polygon":
            continue
        for child in list(el):
            cname = _kml_local_name(child.tag)
            if cname == "altitudeMode" or child.tag == gx_draw_tag:
                el.remove(child)
        gx = ET.Element(gx_draw_tag)
        gx.text = str(int(order))
        el.insert(0, gx)
        am = ET.Element(t("altitudeMode"))
        am.text = "clampToGround"
        el.insert(1, am)

    if ns:
        ET.register_namespace("", ns)
    ET.register_namespace("gx", _GX_NS)
    tree.write(kml_path, encoding="utf-8", xml_declaration=True)


def _disk_path_under_clip_eligible_composite(kml_path: Path) -> bool:
    """Detect ``.../eligible/<composite_sha>/...`` clip-cache layout (eligible union or per-include slices).

    Paths use the same preset ``layer_label`` as include GDB clips — classify by filesystem location before
    ``include/…`` GDB path prefixes on the label.
    """
    parts_lower = [p.lower() for p in kml_path.parts]
    hex16 = frozenset("0123456789abcdef")
    for i in range(len(parts_lower) - 1):
        if parts_lower[i] != "eligible":
            continue
        seg = parts_lower[i + 1]
        if len(seg) == 16 and all(ch in hex16 for ch in seg):
            return True
    return False


def _kml_overlay_role(kml_path: Path, layer_label: str) -> str:
    """Preset ``kml_overlay`` role key: aoi, include, exclude, eligible, etc."""
    stem = kml_path.stem.lower()
    parts_lower = [p.lower() for p in kml_path.parts]
    lab = layer_label.strip().lower()
    gdb_path_prefix = lab.split("::", 1)[0].strip()
    # Resolve preset-path include/exclude before ``\"eligible\" in lab``, which matches layer
    # names like ``eligible_poly`` under ``include/...``.
    if (
        SUBDIR_EXCLUDE in parts_lower
        or stem == "exclude"
        or stem.startswith("exclude_")
        or gdb_path_prefix.startswith("exclude/")
        or "/exclude/" in gdb_path_prefix
    ):
        return "exclude"
    if _disk_path_under_clip_eligible_composite(kml_path):
        return "eligible"
    if (
        SUBDIR_INCLUDE in parts_lower
        or stem == "include"
        or stem.startswith("include_")
        or gdb_path_prefix.startswith("include/")
        or "/include/" in gdb_path_prefix
    ):
        return "include"
    if SUBDIR_ELIGIBLE_LAND_USE in parts_lower or stem in ("eligible", "eligible_land_use") or "eligible" in lab:
        return "eligible"
    if SUBDIR_AOI in parts_lower or stem == "aoi" or stem.startswith("aoi_") or lab.startswith("aoi:"):
        return "aoi"
    if SUBDIR_REFERENCE in parts_lower:
        return "reference"
    return "default"


_GDB_CLIP_PLACEMARK_NAME_COLUMNS = (
    "NAME",
    "Name",
    "name",
    "LABEL",
    "Label",
    "label",
    "TITLE",
    "Title",
    "title",
)


def _gdb_clip_placemark_display_title(row: pd.Series, layer_label: str) -> str:
    for col in _GDB_CLIP_PLACEMARK_NAME_COLUMNS:
        if col not in row.index:
            continue
        val = row[col]
        if val is None:
            continue
        try:
            if bool(pd.isna(val)):
                continue
        except (TypeError, ValueError):
            pass
        s = str(val).strip()
        if s:
            return s
    tail = layer_label.split("::")[-1].strip() if "::" in layer_label else layer_label.strip()
    return tail or "Feature"


def _gdb_clip_placemark_description(row: pd.Series, layer_label: str, *, heading: str) -> str:
    lines = [f"{heading}: {layer_label}"]
    for col in sorted(row.index, key=str):
        if col == "geometry":
            continue
        val = row[col]
        try:
            if bool(pd.isna(val)):
                continue
        except (TypeError, ValueError):
            pass
        lines.append(f"{col}: {val}")
    return "\n".join(lines)


def _kml_enrich_gdb_clip_placemarks(
    kml_path: Path, g: gpd.GeoDataFrame, *, layer_label: str, description_heading: str
) -> None:
    """Set polygon ``Placemark`` ``name`` / ``description`` for bundle vector sidecars."""
    if g.empty:
        return
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

    placemarks = [el for el in doc.iter(t("Placemark"))]
    if len(placemarks) != len(g):
        return
    name_tag = t("name")
    desc_tag = t("description")
    for pm, (_, row) in zip(placemarks, g.iterrows(), strict=True):
        title = _gdb_clip_placemark_display_title(row, layer_label)
        body = _gdb_clip_placemark_description(row, layer_label, heading=description_heading)
        name_el = pm.find(name_tag)
        if name_el is None:
            name_el = ET.Element(name_tag)
            pm.insert(0, name_el)
        name_el.text = title
        desc_el = pm.find(desc_tag)
        if desc_el is None:
            desc_el = ET.SubElement(pm, desc_tag)
        desc_el.text = body

    if ns:
        ET.register_namespace("", ns)
    ET.register_namespace("gx", _GX_NS)
    tree.write(kml_path, encoding="utf-8", xml_declaration=True)


def _kml_build_style_element(
    style_id: str,
    line_aabbggrr: str,
    fill_aabbggrr: str,
    *,
    polygon_fill: bool,
    line_width: float = 2.0,
    ns: str = _KML_NS,
) -> ET.Element:
    def t(local: str) -> str:
        return f"{{{ns}}}{local}" if ns else local

    outline_on = line_width > 0
    st = ET.Element(t("Style"))
    st.set("id", style_id)
    ls = ET.SubElement(st, t("LineStyle"))
    ET.SubElement(ls, t("color")).text = line_aabbggrr
    ET.SubElement(ls, t("width")).text = f"{line_width:g}"
    ps = ET.SubElement(st, t("PolyStyle"))
    ET.SubElement(ps, t("fill")).text = "1" if polygon_fill else "0"
    ET.SubElement(ps, t("outline")).text = "1" if outline_on else "0"
    ET.SubElement(ps, t("color")).text = fill_aabbggrr if polygon_fill else "00000000"
    if not polygon_fill:
        ic = ET.SubElement(st, t("IconStyle"))
        ET.SubElement(ic, t("color")).text = line_aabbggrr
        ET.SubElement(ic, t("scale")).text = "0.45"
        href = ET.SubElement(ET.SubElement(ic, t("Icon")), t("href"))
        href.text = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
    return st


def _kml_inject_filled_overlay_style(
    kml_path: Path,
    *,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    """Post-process GDAL/libkml KML: add ``Style`` + ``styleUrl`` from preset ``bundle.kml_overlay``.

    Handles both the OGR-KML driver (xmlns-prefixed elements) and libkml driver (no xmlns,
    nested ``Document/Document``) so injection works regardless of which driver GDAL chose.
    Without this, libkml output silently dropped every ``styleUrl`` (placemarks rendered with
    Google Earth defaults: thin red outline, no fill).
    """
    if kml_overlay is None:
        return
    role = _kml_overlay_role(kml_path, layer_label)
    spec = getattr(kml_overlay, role, None) or kml_overlay.default
    style_id = f"peaky_kml_{role}"
    try:
        tree = ET.parse(kml_path)
    except ET.ParseError:
        return
    root = tree.getroot()
    # Detect runtime namespace: '{ns}local' (OGR-KML) or plain 'local' (libkml-rewritten).
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
    # Idempotent: remove any prior peaky_kml_* Style we previously injected so re-running
    # the injector on a partially-processed cache file doesn't accumulate duplicates.
    style_tag = t("Style")
    for prior in list(doc):
        if prior.tag == style_tag and (prior.get("id") or "").startswith("peaky_kml_"):
            doc.remove(prior)
    doc.insert(
        0,
        _kml_build_style_element(
            style_id,
            spec.line,
            spec.fill,
            polygon_fill=spec.fill_polygons,
            line_width=spec.line_width,
            ns=ns,
        ),
    )
    style_map_tag = t("StyleMap")
    su_tag = t("styleUrl")
    for pm in doc.iter(t("Placemark")):
        # GDAL KML emits outline-only inline <Style>; it overrides injected styleUrl, so strip it.
        for child in list(pm):
            if child.tag in (style_tag, style_map_tag):
                pm.remove(child)
        for child in list(pm):
            if child.tag == su_tag:
                pm.remove(child)
        su = ET.Element(su_tag)
        su.text = f"#{style_id}"
        pm.insert(0, su)
    # Re-register the parsed default namespace so write emits ``<kml xmlns="…">`` rather
    # than ``ns0:`` prefixes everywhere (Google Earth tolerates both, but the former is
    # what every KML tool expects).
    if ns:
        ET.register_namespace("", ns)
    tree.write(kml_path, encoding="utf-8", xml_declaration=True)


def _aabbggrr_to_rgba_mpl(hex8: str) -> tuple[float, float, float, float]:
    """KML ``aabbggrr`` → matplotlib ``(r, g, b, a)`` in 0..1."""
    s = hex8.strip().lower()
    if len(s) != 8:
        raise ValueError(f"Expected 8 hex digits (aabbggrr); got {hex8!r}")
    aa = int(s[0:2], 16) / 255.0
    bb = int(s[2:4], 16) / 255.0
    gg = int(s[4:6], 16) / 255.0
    rr = int(s[6:8], 16) / 255.0
    return (rr, gg, bb, aa)


def _write_geodataframe_preview_png(
    gdf_wgs84: gpd.GeoDataFrame,
    png_path: Path,
    *,
    kml_path: Path,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    """Flat-map PNG using the same fill/line semantics as ``bundle.kml_overlay`` (EPSG:3857 axes)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = gdf_wgs84.to_crs("EPSG:3857")
    role = _kml_overlay_role(kml_path, layer_label)
    if kml_overlay is None:
        face = _aabbggrr_to_rgba_mpl("66888888")
        edge = _aabbggrr_to_rgba_mpl("ff666666")
        fill_polys = True
        line_w = 2.0
    else:
        spec = getattr(kml_overlay, role, None) or kml_overlay.default
        face = _aabbggrr_to_rgba_mpl(spec.fill)
        edge = _aabbggrr_to_rgba_mpl(spec.line)
        fill_polys = spec.fill_polygons
        line_w = spec.line_width

    types = set(g.geom_type.astype(str))
    point_only = types <= {"Point"}
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 11), dpi=120)

    try:
        if point_only:
            g.plot(ax=ax, color=edge[:3], alpha=max(edge[3], 0.35), markersize=5, linewidth=0)
        else:
            if fill_polys and face[3] > 0:
                fc: tuple[float, float, float, float] | str = face
            else:
                fc = (0.0, 0.0, 0.0, 0.0)
            ec: tuple[float, float, float, float] | str = edge[:4] if line_w > 0 else "none"
            g.plot(ax=ax, facecolor=fc, edgecolor=ec, linewidth=0.25 if line_w > 0 else 0)
        xmin, ymin, xmax, ymax = g.total_bounds
        xpad = max((xmax - xmin) * 0.02, 500.0)
        ypad = max((ymax - ymin) * 0.02, 500.0)
        ax.set_xlim(xmin - xpad, xmax + xpad)
        ax.set_ylim(ymin - ypad, ymax + ypad)
        ax.set_aspect("equal")
        ax.axis("off")
        fig.savefig(
            png_path,
            dpi=144,
            bbox_inches="tight",
            pad_inches=0.05,
            facecolor="white",
        )
    finally:
        plt.close(fig)


def _write_geodataframe_kml(
    gdf: gpd.GeoDataFrame,
    kml_path: Path,
    *,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None = None,
) -> None:
    """Write one KML (WGS84) for Google Earth and a matching ``<same-stem>.png`` map (EPSG:3857 plot)."""

    if gdf.empty or gdf.geometry.is_empty.all():
        return
    g = gdf.to_crs("EPSG:4326") if gdf.crs is not None else gdf
    g = g.assign(geometry=g.geometry.map(orient_for_kml))
    g = _explode_polygon_rows_for_google_earth(g)
    if g.empty:
        return
    kml_path.parent.mkdir(parents=True, exist_ok=True)
    # Some GDAL builds route driver="KML" to libkml, which APPENDS to an existing file
    # instead of overwriting (placemarks duplicate, file grows on every bundle rebuild).
    kml_path.unlink(missing_ok=True)
    g.to_file(kml_path, driver="KML", layer=_kml_safe_name(layer_label))
    _kml_inject_filled_overlay_style(kml_path, layer_label=layer_label, kml_overlay=kml_overlay)
    _kml_inject_ge_polygon_render_hints(kml_path, layer_label=layer_label)
    role_ll = _kml_overlay_role(kml_path, layer_label)
    if role_ll == "exclude":
        _kml_enrich_gdb_clip_placemarks(
            kml_path, g, layer_label=layer_label, description_heading="Exclusion layer"
        )
    elif role_ll == "include":
        _kml_enrich_gdb_clip_placemarks(
            kml_path, g, layer_label=layer_label, description_heading="Inclusion layer"
        )
    elif role_ll == "eligible" and kml_path.parent.name.lower() == "layers":
        _kml_enrich_gdb_clip_placemarks(
            kml_path,
            g,
            layer_label=layer_label,
            description_heading="Eligible land (after exclusions)",
        )
    _write_geodataframe_preview_png(
        g,
        kml_path.with_suffix(".png"),
        kml_path=kml_path,
        layer_label=layer_label,
        kml_overlay=kml_overlay,
    )


def _write_geodataframe_gpkg_and_kml(
    gdf: gpd.GeoDataFrame,
    gpkg_path: Path,
    *,
    gpkg_layer: str,
    layer_label: str,
    gpkg_mode: str = "w",
    kml_path: Path | None = None,
    write_sidecar_kml: bool = True,
    kml_overlay: BundleKmlOverlayStyles | None = None,
) -> None:
    """Write one GeoPackage layer and optionally a sidecar KML (default: same basename as ``gpkg_path``)."""
    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(gpkg_path, driver="GPKG", layer=gpkg_layer, mode=gpkg_mode)
    if write_sidecar_kml:
        kml = kml_path if kml_path is not None else gpkg_path.with_suffix(".kml")
        _write_geodataframe_kml(gdf, kml, layer_label=layer_label, kml_overlay=kml_overlay)


def _list_layers_pair_rows(gdb_path: Path | str) -> list[tuple[str, str]]:
    mat = pyogrio.list_layers(str(gdb_path))
    if mat.ndim == 1:
        return [(str(mat[0]), str(mat[1]))]
    return [(str(r[0]), str(r[1])) for r in mat]


def _geom_type_for_layer(ds: str, layer_name: str) -> str | None:
    for lyr, gt in _list_layers_pair_rows(ds):
        if lyr == layer_name:
            return gt
    return None


def _clip_stem(
    preset_path: str, resolved_gdb: Path, layer: str, where: str | None = None
) -> str:
    """Basename stem for ``{aoi|include|exclude}_{stem}.gpkg`` (readable source id + short hash).

    ``preset_path`` is the preset's GDB path (e.g. ``include/SMA_WM.gdb``). A short digest keeps
    stems unique if sanitization collapses different paths or layers.
    """
    raw = Path(preset_path).as_posix()
    safe_path = _sanitize_path_component(raw.replace("/", "_"))
    safe_layer = _sanitize_path_component(layer)
    max_path, max_layer = 96, 80
    if len(safe_path) > max_path:
        safe_path = safe_path[-max_path:]
    if len(safe_layer) > max_layer:
        safe_layer = safe_layer[-max_layer:]
    digest = hashlib.sha256(
        f"{preset_path}\0{layer}\0{resolved_gdb.resolve()!s}\0{where or ''}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{safe_path}__{safe_layer}__{digest}"


def _flatten_gdb_layer_jobs(
    groups: list[GdbLayerGroup], data_dir: Path
) -> list[tuple[str, Path, str, str | None]]:
    jobs: list[tuple[str, Path, str, str | None]] = []
    for g in sorted(
        groups,
        key=lambda x: (
            x.path,
            tuple(
                (s.name, ogr_where_for_layer_spec(s) or "")
                for s in sorted(x.layers, key=lambda z: z.name)
            ),
        ),
    ):
        r = resolve_land_use_gdb_path(data_dir, g.path)
        for spec in sorted(g.layers, key=lambda z: z.name):
            w = ogr_where_for_layer_spec(spec)
            jobs.append((g.path, r, spec.name, w))
    jobs.sort(key=lambda t: (t[0], t[2], t[3] or ""))
    return jobs


def _read_and_clip_gdb_layer_to_aoi(
    preset_path: str,
    resolved: Path,
    layer: str,
    aoi_poly_4326: BaseGeometry,
    *,
    kind: Literal["include", "exclude", "aoi", "reference"],
    where: str | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Read one GDB layer and clip to AOI (polygon in WGS84) in the layer's CRS.

    Returns ``(full_gdf, clipped_gdf)``. ``clipped_gdf`` uses the same columns/CRS as ``full_gdf``
    and may be empty (never drops column schema). Exclude and AOI layers must be polygon-like.
    """
    with openfilegdb_dataset_path(resolved) as ds:
        gt = _geom_type_for_layer(ds, layer)
        if kind == "exclude":
            if gt is None:
                raise ValueError(f"Exclude layer {layer!r} not found in {resolved}")
            if not is_polygon_geometry_type(gt):
                raise ValueError(
                    f"Exclude layer {preset_path}::{layer} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
        elif kind == "reference":
            if gt is None:
                raise ValueError(f"Reference layer {layer!r} not found in {resolved}")
            if not is_polygon_geometry_type(gt):
                raise ValueError(
                    f"Reference layer {preset_path}::{layer} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
        elif kind == "aoi":
            if gt is None:
                raise ValueError(f"AOI layer {layer!r} not found in {resolved}")
            if not is_polygon_geometry_type(gt):
                raise ValueError(
                    f"AOI layer {preset_path}::{layer} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
        elif gt is None:
            raise ValueError(f"Layer {layer!r} not found in GDB {resolved}")

        rkw: dict[str, str] = {"layer": layer}
        if where:
            rkw["where"] = where
        raw = pyogrio.read_dataframe(ds, **rkw)
        gdf = gpd.GeoDataFrame(raw, geometry="geometry")
        if gdf.crs is None:
            raise ValueError(f"CRS missing on {resolved}:{layer}")
        mask_ll = gpd.GeoDataFrame(geometry=[make_valid(aoi_poly_4326)], crs="EPSG:4326")
        mask_native = mask_ll.to_crs(gdf.crs)
        clipped = gdf.clip(mask_native)
        if clipped.empty:
            clipped = gpd.GeoDataFrame(columns=gdf.columns, crs=gdf.crs)
        return gdf, clipped


def build_eligible_land_use_gdf(
    include_union: gpd.GeoDataFrame,
    exclude_union: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """``include_union \\ exclude_union`` in EPSG:3857; stored as one EPSG:4326 geometry row."""
    empty = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
    if include_union.empty or include_union.geometry.is_empty.iloc[0]:
        return empty
    inc_ll = make_valid(include_union.geometry.iloc[0])
    if inc_ll.is_empty:
        return empty

    inc_3857 = gpd.GeoDataFrame(geometry=[inc_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    ig = make_valid(inc_3857.geometry.iloc[0])
    if ig.is_empty:
        return empty

    exc_ll = Polygon()
    if not exclude_union.empty and not exclude_union.geometry.is_empty.iloc[0]:
        exc_ll = make_valid(exclude_union.geometry.iloc[0])

    if exc_ll.is_empty:
        clean = _polygonal_area_only(inc_ll)
        if clean.is_empty:
            return empty
        return gpd.GeoDataFrame(geometry=[clean], crs="EPSG:4326")

    exc_3857 = gpd.GeoDataFrame(geometry=[exc_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    eg = make_valid(exc_3857.geometry.iloc[0])
    if eg.is_empty:
        clean = _polygonal_area_only(inc_ll)
        if clean.is_empty:
            return empty
        return gpd.GeoDataFrame(geometry=[clean], crs="EPSG:4326")

    diff = ig.difference(eg)
    diff = make_valid(diff) if not diff.is_valid else diff
    if diff.is_empty:
        return empty
    diff = _polygonal_area_only(diff)
    if diff.is_empty:
        return empty
    elig_3857 = gpd.GeoDataFrame(geometry=[diff], crs="EPSG:3857")
    elig_ll = elig_3857.to_crs("EPSG:4326")
    geom_ll = _polygonal_area_only(make_valid(elig_ll.geometry.iloc[0]))
    if geom_ll.is_empty:
        return empty
    return gpd.GeoDataFrame(geometry=[geom_ll], crs="EPSG:4326")


def eligible_land_slice_from_include_clip_gpkg(
    eligible_geom_ll: BaseGeometry,
    include_clip_gpkg: Path,
) -> gpd.GeoDataFrame:
    """Eligible land attributable to one include clip: ``eligible ∩ clip`` projected like the global eligible op.

    The global eligible area is ``(∪ include) \\\\ ∪ exclude`` (:func:`build_eligible_land_use_gdf`).
    Returned polygons are subsets of ``eligible_geom_ll`` (after exclusions): each feature lies within that
    include clip footprint and within the aggregated eligible polygon.
    """
    empty_ll = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    if eligible_geom_ll is None or eligible_geom_ll.is_empty:
        return empty_ll

    igpkg = Path(include_clip_gpkg).expanduser().resolve()
    if not igpkg.is_file():
        return empty_ll

    inc = gpd.read_file(igpkg)
    if inc.empty:
        return empty_ll
    if inc.crs is None:
        raise ValueError(f"CRS missing on include clip for eligible slice KML build: {igpkg}")
    inc_3857 = inc.to_crs("EPSG:3857")
    clip_u = _sanitize_collection(list(inc_3857.geometry))
    clip_u = make_valid(clip_u) if not clip_u.is_valid else clip_u
    if clip_u.is_empty:
        return empty_ll

    elig_ll = make_valid(eligible_geom_ll)
    elig_3857 = gpd.GeoDataFrame(geometry=[elig_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    eg3857 = make_valid(elig_3857.geometry.iloc[0])
    if eg3857.is_empty:
        return empty_ll

    raw = eg3857.intersection(clip_u)
    raw = make_valid(raw) if not raw.is_valid else raw
    if raw.is_empty:
        return empty_ll

    clipped = _polygonal_area_only(raw)
    if clipped.is_empty:
        return empty_ll
    slice_ll = gpd.GeoDataFrame(geometry=[clipped], crs="EPSG:3857").to_crs("EPSG:4326")
    geom_out = make_valid(slice_ll.geometry.iloc[0])
    if geom_out.is_empty:
        return empty_ll
    return gpd.GeoDataFrame(geometry=[geom_out], crs="EPSG:4326")


def _kml_label_aoi_clip(path: str, layer: str) -> str:
    return f"aoi:{path}::{layer}"


def _kml_label_gdb_job(path: str, layer: str) -> str:
    return f"{path}::{layer}"


GPKG_FILENAME = "eligible_land_use.gpkg"
CLIP_MANIFEST_BASENAME = "clip_manifest.json"
SUBDIR_AOI = "aoi"
SUBDIR_INCLUDE = "include"
SUBDIR_EXCLUDE = "exclude"
SUBDIR_ELIGIBLE_LAND_USE = "eligible_land_use"
ELIGIBLE_LAND_USE_LAYER = "eligible_land_use"
ELIGIBLE_MANIFEST_FORMAT = "bundle_eligible_land_use_manifest/v1"
KML_OVERLAY_DIGEST_BASENAME = "bundle_kml_overlay.sha256"
SUBDIR_REFERENCE = "reference"
REFERENCE_DIGEST_BASENAME = "reference_inputs.sha256"
REFERENCE_GPKG_LAYER = "reference"

def bundle_eligible_land_use_gpkg(bundle_dir: Path) -> Path:
    from peaky_finders.bundle_clips import bundle_resolve_path, eligible_gpkg_from_bundle_dir

    if bundle_resolve_path(bundle_dir).is_file():
        return eligible_gpkg_from_bundle_dir(bundle_dir)
    return bundle_dir / SUBDIR_ELIGIBLE_LAND_USE / GPKG_FILENAME


@dataclass(frozen=True, slots=True)
class ClipFamily:
    """Clip pieces + ``clip_manifest.json`` under ``bundle_dir/{subdir}/``; final ``{final_gpkg_basename}``."""

    key: str
    subdir: str
    clip_filename_prefix: str
    manifest_format: str
    kml_label: Callable[[str, str], str]
    final_gpkg_basename: str
    manifest_requires_non_empty_items: bool = False

    def dir(self, bundle_dir: Path) -> Path:
        return bundle_dir / self.subdir

    def manifest_path(self, bundle_dir: Path) -> Path:
        return self.dir(bundle_dir) / CLIP_MANIFEST_BASENAME

    def clip_glob_gpkg(self) -> str:
        return f"{self.clip_filename_prefix}*.gpkg"

    def clip_glob_kml(self) -> str:
        return f"{self.clip_filename_prefix}*.kml"

    def clip_glob_png(self) -> str:
        return f"{self.clip_filename_prefix}*.png"

    def final_gpkg_path(self, bundle_dir: Path) -> Path:
        return self.dir(bundle_dir) / self.final_gpkg_basename


CLIP_AOI = ClipFamily(
    key="aoi",
    subdir=SUBDIR_AOI,
    clip_filename_prefix="aoi_",
    manifest_format="bundle_aoi_clip_manifest/v3",
    kml_label=_kml_label_aoi_clip,
    final_gpkg_basename="aoi.gpkg",
    manifest_requires_non_empty_items=True,
)
CLIP_INCLUDE = ClipFamily(
    key="include",
    subdir=SUBDIR_INCLUDE,
    clip_filename_prefix="include_",
    manifest_format="bundle_include_clip_manifest/v4",
    kml_label=_kml_label_gdb_job,
    final_gpkg_basename="include.gpkg",
)
CLIP_EXCLUDE = ClipFamily(
    key="exclude",
    subdir=SUBDIR_EXCLUDE,
    clip_filename_prefix="exclude_",
    manifest_format="bundle_exclude_clip_manifest/v4",
    kml_label=_kml_label_gdb_job,
    final_gpkg_basename="exclude.gpkg",
)


def bundle_paths(
    cache_root: Path,
    *,
    preset: Preset,
    data_dir: Path,
) -> tuple[Path, Path]:
    """``(bundle_job_dir, eligible_land_use_gpkg_path)`` via ``resolve.json`` when present."""
    job_slug = bundle_job_dir_slug(preset=preset, data_dir=data_dir)
    bundle_dir = Path(cache_root) / job_slug
    return bundle_dir, bundle_eligible_land_use_gpkg(bundle_dir)


def bundle_directory_for_preset(
    *,
    preset_path: Path,
    data_dir: Path,
    cache_root: Path | None = None,
) -> Path | None:
    """Stable bundle cache dir for ``preset_path`` when it defines ``bundle.*``; else ``None``.

    Mirrors :func:`ensure_land_use_bundle` layout resolution (does not ensure the bundle exists).
    """
    preset_path_resolved = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path_resolved)
    if preset.bundle is None:
        return None
    data_dir = Path(data_dir).expanduser().resolve()
    if cache_root is not None:
        cache_root_final = Path(cache_root).expanduser().resolve()
    else:
        cache_root_final = resolved_bundle_cache_root(cli_bundle_cache_root=None)
    bundle_dir, _ = bundle_paths(cache_root_final, preset=preset, data_dir=data_dir)
    return bundle_dir


def _remove_family_outputs(bundle_dir: Path, family: ClipFamily) -> None:
    d = family.dir(bundle_dir)
    if d.is_dir():
        for p in d.glob(family.clip_glob_gpkg()):
            p.unlink(missing_ok=True)
        for p in d.glob(family.clip_glob_kml()):
            p.unlink(missing_ok=True)
        for p in d.glob(family.clip_glob_png()):
            p.unlink(missing_ok=True)
        family.manifest_path(bundle_dir).unlink(missing_ok=True)
        fp = family.final_gpkg_path(bundle_dir)
        fp.unlink(missing_ok=True)
        fp.with_suffix(".kml").unlink(missing_ok=True)
        fp.with_suffix(".png").unlink(missing_ok=True)


def _clear_bundle_family_dirs(bundle_dir: Path) -> None:
    for fam in (CLIP_AOI, CLIP_INCLUDE, CLIP_EXCLUDE):
        _remove_family_outputs(bundle_dir, fam)
        d = fam.dir(bundle_dir)
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    elig = bundle_dir / SUBDIR_ELIGIBLE_LAND_USE
    if elig.is_dir():
        shutil.rmtree(elig, ignore_errors=True)


def _unlink_legacy_bundle_artifacts(bundle_dir: Path) -> None:
    for name in (
        "aoi_snap.gpkg",
        "aoi_clip_manifest.json",
        "include_clip_manifest.json",
        "exclude_clip_manifest.json",
        "aoi_union.gpkg",
        "include_union.gpkg",
        "exclude_union.gpkg",
        "eligible.gpkg",
        "eligible.kml",
        "eligible.png",
        "eligible_land_use.kml",
    ):
        (bundle_dir / name).unlink(missing_ok=True)
    for stem in ("aoi_union", "include_union", "exclude_union", "eligible"):
        for suf in (".gpkg", ".kml", ".png"):
            (bundle_dir / f"{stem}{suf}").unlink(missing_ok=True)
    (bundle_dir / "summits_aoi.gpkg").unlink(missing_ok=True)
    (bundle_dir / "summits_aoi.kml").unlink(missing_ok=True)
    (bundle_dir / "summits_aoi.png").unlink(missing_ok=True)
    (bundle_dir / "summits.gpkg").unlink(missing_ok=True)
    (bundle_dir / "summits.kml").unlink(missing_ok=True)
    (bundle_dir / "summits.png").unlink(missing_ok=True)


def _unlink_bundle_outputs_for_rebuild(bundle_dir: Path) -> None:
    """Scrub per-job bundle workspace (clips live under ``<cache>/clips/``, not here)."""
    _clear_bundle_family_dirs(bundle_dir)
    _unlink_legacy_bundle_artifacts(bundle_dir)
    (bundle_dir / KML_OVERLAY_DIGEST_BASENAME).unlink(missing_ok=True)
    from peaky_finders.bundle_clips import bundle_resolve_path

    bundle_resolve_path(bundle_dir).unlink(missing_ok=True)
    ref_dir = bundle_dir / SUBDIR_REFERENCE
    if ref_dir.is_dir():
        shutil.rmtree(ref_dir, ignore_errors=True)


def _sanitize_path_component(name: str) -> str:
    out: list[str] = []
    for ch in name:
        out.append(ch if ch.isalnum() else "_")
    s = "".join(out).strip("_")
    return s[:120] if s else "x"


def reference_entry_kml_stem(entry_id: str) -> str:
    """Sanitized basename stem for ``reference/<stem>.{gpkg,kml}``."""
    s = _sanitize_path_component(entry_id.strip())
    return s[:120] if s else "ref"


def _overlay_for_reference_entry(
    entry: BundleReferenceLayerEntry,
    plc: BundleConfig,
) -> BundleKmlOverlayStyles | None:
    if plc.kml_overlay is not None:
        base = plc.kml_overlay
        ref_style = entry.style or base.reference or base.default
        return base.model_copy(update={"reference": ref_style})
    if entry.style is None:
        return None
    st = entry.style
    return BundleKmlOverlayStyles(default=st, reference=st)


def _union_include_merged(include_dir: Path, clip_entries: list[dict[str, object]]) -> gpd.GeoDataFrame:
    pieces: list[BaseGeometry] = []
    for ent in sorted(clip_entries, key=lambda d: str(d.get("file", ""))):
        fn = ent.get("file")
        if not isinstance(fn, str):
            continue
        p = include_dir / fn
        if not p.is_file():
            raise FileNotFoundError(f"Missing clipped include: {p.name}")
        gdf = gpd.read_file(p)
        if gdf.empty:
            continue
        g3857 = gdf.to_crs("EPSG:3857")
        pieces.extend(g3857.geometry.tolist())
    u = _sanitize_collection(pieces)
    g_union = gpd.GeoDataFrame(geometry=[u], crs="EPSG:3857")
    return g_union.to_crs("EPSG:4326")


def _write_clip_manifest(
    bundle_dir: Path, family: ClipFamily, *, items: list[dict[str, object]]
) -> None:
    family.dir(bundle_dir).mkdir(parents=True, exist_ok=True)
    path = family.manifest_path(bundle_dir)
    path.write_text(
        json.dumps({"format": family.manifest_format, "items": items}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_clip_manifest_item_summaries(bundle_dir: Path, family: ClipFamily) -> list[str]:
    """Sorted ``file (path::layer)`` lines from a clip manifest, or empty if missing/invalid."""
    mp = family.manifest_path(bundle_dir)
    if not mp.is_file():
        return []
    try:
        raw = json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    rows: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("file")
        ps = it.get("path")
        ly = it.get("layer")
        if isinstance(fn, str) and isinstance(ps, str) and isinstance(ly, str):
            rows.append(f"{fn}  ({ps}::{ly})")
    rows.sort()
    return rows


def _verbose_log_clip_merge(
    verbose: bool,
    *,
    op: str,
    bundle_dir: Path,
    family: ClipFamily,
    output_paths: str,
) -> None:
    if not verbose:
        return
    _bundle_log(verbose, f"{op}: manifest {family.subdir}/{CLIP_MANIFEST_BASENAME}")
    inputs = _read_clip_manifest_item_summaries(bundle_dir, family)
    if not inputs:
        _bundle_log(verbose, f"{op}:   in:  (no rows in manifest)")
    else:
        for line in inputs:
            _bundle_log(verbose, f"{op}:   in:  {line}")
    _bundle_log(verbose, f"{op}:   out: {output_paths}")


def _clear_clip_family_artifacts(bundle_dir: Path, family: ClipFamily) -> None:
    _remove_family_outputs(bundle_dir, family)


def _clip_manifest_ok(bundle_dir: Path, family: ClipFamily, raw: object) -> bool:
    if not isinstance(raw, dict) or raw.get("format") != family.manifest_format:
        return False
    items = raw.get("items")
    if not isinstance(items, list):
        return False
    if family.manifest_requires_non_empty_items and not items:
        return False
    prefix = family.clip_filename_prefix
    fdir = family.dir(bundle_dir)
    for it in items:
        if not isinstance(it, dict):
            return False
        fn = it.get("file")
        if not isinstance(fn, str) or not fn.startswith(prefix) or not (fdir / fn).is_file():
            return False
        if not isinstance(it.get("path"), str) or not isinstance(it.get("layer"), str):
            return False
    return True


def _clip_manifest_items_with_stats(
    bundle_dir: Path,
    family: ClipFamily,
    items: list[object],
    *,
    kml_overlay: BundleKmlOverlayStyles | None = None,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    fdir = family.dir(bundle_dir)
    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("file")
        path_str = it.get("path")
        layer_str = it.get("layer")
        if not isinstance(fn, str) or not isinstance(path_str, str) or not isinstance(layer_str, str):
            continue
        path = fdir / fn
        if not path.is_file():
            raise FileNotFoundError(f"{family.key} clip missing during manifest refresh: {fn}")
        gdf = gpd.read_file(path)
        _write_geodataframe_kml(
            gdf,
            path.with_suffix(".kml"),
            layer_label=family.kml_label(path_str, layer_str),
            kml_overlay=kml_overlay,
        )
        out.append(
            {
                "file": fn,
                "path": path_str,
                "layer": layer_str,
                "features_clipped": len(gdf),
                "vertices_clipped": _gdf_coordinate_vertex_count(gdf),
            }
        )
    out.sort(key=lambda d: str(d["file"]))
    return out


def _union_wgs84_from_clip_manifest_items(family_dir: Path, items: list[object]) -> gpd.GeoDataFrame:
    pieces_ll: list[BaseGeometry] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("file")
        if not isinstance(fn, str):
            continue
        gdf = gpd.read_file(family_dir / fn)
        if gdf.empty:
            continue
        pieces_ll.extend(gdf.geometry.tolist())
    u_ll = _sanitize_collection(pieces_ll if pieces_ll else [])
    return gpd.GeoDataFrame(geometry=[u_ll], crs="EPSG:4326")


def _write_family_final_polygon(
    gdf: gpd.GeoDataFrame,
    bundle_dir: Path,
    family: ClipFamily,
    *,
    gpkg_layer: str,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    family.dir(bundle_dir).mkdir(parents=True, exist_ok=True)
    fp = family.final_gpkg_path(bundle_dir)
    _write_geodataframe_gpkg_and_kml(
        gdf, fp, gpkg_layer=gpkg_layer, layer_label=layer_label, kml_overlay=kml_overlay
    )


def load_or_build_clipped_aoi_union(
    pre: BundleConfig,
    data_dir: Path,
    bundle_dir: Path,
    *,
    verbose: bool = False,
) -> BaseGeometry:
    """Build ``aoi/aoi_*.gpkg`` + ``aoi/clip_manifest.json``; write ``aoi/aoi.gpkg``; return AOI (EPSG:4326)."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    aoi_dir = CLIP_AOI.dir(bundle_dir)
    manifest_path = CLIP_AOI.manifest_path(bundle_dir)
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if _clip_manifest_ok(bundle_dir, CLIP_AOI, raw) and isinstance(raw, dict):
            items_obj = raw["items"]
            if not isinstance(items_obj, list):
                items_obj = []
            refreshed = _clip_manifest_items_with_stats(
                bundle_dir, CLIP_AOI, items_obj, kml_overlay=pre.kml_overlay
            )
            _write_clip_manifest(bundle_dir, CLIP_AOI, items=refreshed)
            _bundle_log(
                verbose,
                f"aoi: updated clip manifest ({len(refreshed)} file(s), {SUBDIR_AOI}/{CLIP_MANIFEST_BASENAME})",
            )
            g_union = _union_wgs84_from_clip_manifest_items(aoi_dir, refreshed)
            if g_union.empty or g_union.geometry.is_empty.iloc[0]:
                raise ValueError("AOI union from clip manifest is empty")
            geom = make_valid(g_union.geometry.iloc[0])
            if geom.is_empty:
                raise ValueError("AOI union geometry is empty")
            _write_family_final_polygon(
                gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326"),
                bundle_dir,
                CLIP_AOI,
                gpkg_layer="aoi",
                layer_label="aoi",
                kml_overlay=pre.kml_overlay,
            )
            return geom

    _bundle_log(verbose, "aoi: rebuilding clip GeoPackages from bundle.aoi …")
    _clear_clip_family_artifacts(bundle_dir, CLIP_AOI)
    aoi_poly = load_composite_aoi_polygon(pre, data_dir)
    aoi_poly = make_valid(aoi_poly)
    if aoi_poly.is_empty:
        raise ValueError("AOI union from bundle.aoi is empty")

    jobs = _flatten_gdb_layer_jobs(pre.aoi, data_dir)
    clip_entries: list[dict[str, object]] = []
    n_aoi = len(jobs)
    for aidx, (preset_path, resolved, layer_name, where) in enumerate(jobs, start=1):
        if not resolved.exists():
            raise FileNotFoundError(f"AOI GDB not found: {resolved} (preset path {preset_path!r})")
        _bundle_progress(verbose, f"aoi [{aidx}/{n_aoi}] read+clip {preset_path}::{layer_name} …")
        gdf, clipped = _read_and_clip_gdb_layer_to_aoi(
            preset_path, resolved, layer_name, aoi_poly, kind="aoi", where=where
        )
        clipped_ll = clipped.to_crs("EPSG:4326")
        stem = _clip_stem(preset_path, resolved, layer_name, where)
        fname = f"aoi_{stem}.gpkg"
        out = aoi_dir / fname
        _bundle_progress(
            verbose,
            f"aoi [{aidx}/{n_aoi}] write {fname} (gpkg+kml, {len(clipped_ll):,} features) …",
        )
        _write_geodataframe_gpkg_and_kml(
            clipped_ll,
            out,
            gpkg_layer="features",
            layer_label=CLIP_AOI.kml_label(preset_path, layer_name),
            kml_overlay=pre.kml_overlay,
        )
        _bundle_log(
            verbose,
            f"aoi {preset_path}::{layer_name}: {len(gdf):,} features → {len(clipped):,} clipped → {fname}",
        )
        clip_entries.append(
            {
                "file": fname,
                "path": preset_path,
                "layer": layer_name,
                "features_clipped": len(clipped_ll),
                "vertices_clipped": _gdf_coordinate_vertex_count(clipped_ll),
                **({"where": where} if where else {}),
            }
        )

    clip_entries.sort(key=lambda d: str(d["file"]))
    _write_clip_manifest(bundle_dir, CLIP_AOI, items=clip_entries)
    _bundle_log(
        verbose,
        f"aoi: wrote {SUBDIR_AOI}/{CLIP_MANIFEST_BASENAME} ({len(clip_entries)} layer(s))",
    )
    _bundle_progress(verbose, "aoi: merging clip GeoPackages to AOI union …")
    g_out = _union_wgs84_from_clip_manifest_items(aoi_dir, clip_entries)
    if g_out.empty or g_out.geometry.is_empty.iloc[0]:
        raise ValueError("AOI union after clip is empty")
    geom_final = make_valid(g_out.geometry.iloc[0])
    _bundle_progress(verbose, "aoi: writing final aoi.gpkg + kml …")
    _write_family_final_polygon(
        gpd.GeoDataFrame(geometry=[geom_final], crs="EPSG:4326"),
        bundle_dir,
        CLIP_AOI,
        gpkg_layer="aoi",
        layer_label="aoi",
        kml_overlay=pre.kml_overlay,
    )
    return geom_final


def load_or_build_clipped_include_union(
    include_groups: list[GdbLayerGroup],
    data_dir: Path,
    aoi_poly_4326: BaseGeometry,
    bundle_dir: Path,
    *,
    kml_overlay: BundleKmlOverlayStyles | None = None,
    verbose: bool = False,
) -> gpd.GeoDataFrame:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    inc_dir = CLIP_INCLUDE.dir(bundle_dir)
    jobs = _flatten_gdb_layer_jobs(include_groups, data_dir)
    clip_entries: list[dict[str, object]] = []
    n_inc = len(jobs)
    for iidx, (preset_path, resolved, layer, where) in enumerate(jobs, start=1):
        if not resolved.exists():
            raise FileNotFoundError(f"Include GDB not found: {resolved} (preset path {preset_path!r})")
        stem = _clip_stem(preset_path, resolved, layer, where)
        out = inc_dir / f"include_{stem}.gpkg"
        if out.is_file():
            gchk = gpd.read_file(out)
            _bundle_log(verbose, f"include reuse {preset_path}::{layer} → {out.name}")
            clip_entries.append(
                {
                    "file": out.name,
                    "layer": layer,
                    "path": preset_path,
                    "features_clipped": len(gchk),
                    "vertices_clipped": _gdf_coordinate_vertex_count(gchk),
                    **({"where": where} if where else {}),
                }
            )
            _write_geodataframe_kml(
                gchk,
                out.with_suffix(".kml"),
                layer_label=CLIP_INCLUDE.kml_label(preset_path, layer),
                kml_overlay=kml_overlay,
            )
            continue
        _bundle_log(verbose, f"include clip {preset_path}::{layer} from {resolved} …")
        _bundle_progress(verbose, f"include [{iidx}/{n_inc}] read+clip {preset_path}::{layer} …")
        gdf, clipped = _read_and_clip_gdb_layer_to_aoi(
            preset_path, resolved, layer, aoi_poly_4326, kind="include", where=where
        )
        clipped_3857 = clipped.to_crs("EPSG:3857")
        _bundle_log(
            verbose,
            f"include {preset_path}::{layer}: {len(gdf):,} features → {len(clipped):,} clipped → {out.name}",
        )
        _bundle_progress(
            verbose,
            f"include [{iidx}/{n_inc}] write {out.name} (gpkg+kml, {len(clipped_3857):,} features) …",
        )
        _write_geodataframe_gpkg_and_kml(
            clipped_3857,
            out,
            gpkg_layer="features",
            layer_label=CLIP_INCLUDE.kml_label(preset_path, layer),
            kml_overlay=kml_overlay,
        )
        clip_entries.append(
            {
                "file": out.name,
                "layer": layer,
                "path": preset_path,
                "features_clipped": len(clipped_3857),
                "vertices_clipped": _gdf_coordinate_vertex_count(clipped_3857),
                **({"where": where} if where else {}),
            }
        )

    clip_entries.sort(key=lambda d: str(d["file"]))
    _bundle_progress(verbose, f"include: merge union from {len(clip_entries)} clip file(s) …")
    include_union = _union_include_merged(inc_dir, clip_entries)
    _write_clip_manifest(bundle_dir, CLIP_INCLUDE, items=clip_entries)
    _bundle_log(
        verbose,
        f"include: wrote {SUBDIR_INCLUDE}/{CLIP_MANIFEST_BASENAME} ({len(clip_entries)} layer(s))",
    )
    if not include_union.empty and not include_union.geometry.is_empty.iloc[0]:
        g0 = include_union.geometry.iloc[0]
        _bundle_log(
            verbose,
            f"include union (4326): valid={g0.is_valid} empty={g0.is_empty} geom_type={g0.geom_type}",
        )
    else:
        _bundle_log(verbose, "include union (4326): empty")
    _bundle_progress(verbose, "include: writing final include.gpkg + kml …")
    _write_family_final_polygon(
        include_union,
        bundle_dir,
        CLIP_INCLUDE,
        gpkg_layer="include",
        layer_label="include",
        kml_overlay=kml_overlay,
    )
    return include_union


def load_or_build_clipped_exclude_union(
    exclude_groups: list[GdbLayerGroup],
    data_dir: Path,
    aoi_poly_4326: BaseGeometry,
    bundle_dir: Path,
    *,
    kml_overlay: BundleKmlOverlayStyles | None = None,
    verbose: bool = False,
) -> gpd.GeoDataFrame:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    exc_dir = CLIP_EXCLUDE.dir(bundle_dir)
    manifest_path = CLIP_EXCLUDE.manifest_path(bundle_dir)
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if _clip_manifest_ok(bundle_dir, CLIP_EXCLUDE, raw) and isinstance(raw, dict):
            items_obj = raw["items"]
            if not isinstance(items_obj, list):
                items_obj = []
            refreshed = _clip_manifest_items_with_stats(
                bundle_dir, CLIP_EXCLUDE, items_obj, kml_overlay=kml_overlay
            )
            _write_clip_manifest(bundle_dir, CLIP_EXCLUDE, items=refreshed)
            _bundle_log(
                verbose,
                f"exclude: updated clip manifest ({len(refreshed)} file(s), {SUBDIR_EXCLUDE}/{CLIP_MANIFEST_BASENAME})",
            )
            out = _union_wgs84_from_clip_manifest_items(exc_dir, refreshed)
            if not out.empty and not out.geometry.is_empty.iloc[0]:
                ex = out.geometry.iloc[0]
                _bundle_log(
                    verbose,
                    f"exclude union (4326): valid={ex.is_valid} empty={ex.is_empty} geom_type={ex.geom_type}",
                )
            else:
                _bundle_log(verbose, "exclude union (4326): empty")
            _write_family_final_polygon(
                out,
                bundle_dir,
                CLIP_EXCLUDE,
                gpkg_layer="exclude",
                layer_label="exclude",
                kml_overlay=kml_overlay,
            )
            return out

    _bundle_log(verbose, "exclude: rebuilding AOI clips from preset-listed GDB layers…")
    _clear_clip_family_artifacts(bundle_dir, CLIP_EXCLUDE)
    items: list[dict[str, object]] = []
    pieces_ll: list[BaseGeometry] = []

    if not exclude_groups:
        _write_clip_manifest(bundle_dir, CLIP_EXCLUDE, items=[])
        _bundle_log(verbose, "exclude: empty list (no exclusions)")
        empty_ex = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
        _write_family_final_polygon(
            empty_ex,
            bundle_dir,
            CLIP_EXCLUDE,
            gpkg_layer="exclude",
            layer_label="exclude",
            kml_overlay=kml_overlay,
        )
        return empty_ex

    jobs = _flatten_gdb_layer_jobs(exclude_groups, data_dir)
    n_exc = len(jobs)
    for jidx, (preset_path, resolved, layer_name, where) in enumerate(jobs, start=1):
        if not resolved.exists():
            raise FileNotFoundError(f"Exclude GDB not found: {resolved} (preset path {preset_path!r})")
        _bundle_progress(verbose, f"exclude [{jidx}/{n_exc}] read+clip {preset_path}::{layer_name} …")
        gdf, clipped = _read_and_clip_gdb_layer_to_aoi(
            preset_path, resolved, layer_name, aoi_poly_4326, kind="exclude", where=where
        )
        if clipped.empty:
            _bundle_log(
                verbose,
                f"exclude {preset_path}::{layer_name}: {len(gdf):,} features → clip empty (skip file)",
            )
            continue
        clipped_ll = clipped.to_crs("EPSG:4326")
        stem = _clip_stem(preset_path, resolved, layer_name, where)
        fname = f"exclude_{stem}.gpkg"
        out = exc_dir / fname
        _bundle_progress(
            verbose,
            f"exclude [{jidx}/{n_exc}] write {fname} (gpkg+kml, {len(clipped_ll):,} features) …",
        )
        _write_geodataframe_gpkg_and_kml(
            clipped_ll,
            out,
            gpkg_layer="features",
            layer_label=CLIP_EXCLUDE.kml_label(preset_path, layer_name),
            kml_overlay=kml_overlay,
        )
        items.append(
            {
                "file": fname,
                "path": preset_path,
                "layer": layer_name,
                "features_clipped": len(clipped),
                "vertices_clipped": _gdf_coordinate_vertex_count(clipped_ll),
                **({"where": where} if where else {}),
            }
        )
        pieces_ll.extend(clipped_ll.geometry.tolist())
        _bundle_log(
            verbose,
            f"exclude {preset_path}::{layer_name}: {len(gdf):,} native → {len(clipped):,} clipped → {fname}",
        )

    items.sort(key=lambda d: str(d["file"]))
    _write_clip_manifest(bundle_dir, CLIP_EXCLUDE, items=items)
    _bundle_log(verbose, f"exclude: wrote manifest with {len(items)} non-empty clip file(s)")
    _bundle_progress(verbose, f"exclude: unary_union of {len(pieces_ll):,} clipped part(s) …")
    u_ll = _sanitize_collection(pieces_ll if pieces_ll else [])
    g_out = gpd.GeoDataFrame(geometry=[u_ll], crs="EPSG:4326")
    if not g_out.empty and not g_out.geometry.is_empty.iloc[0]:
        ex = g_out.geometry.iloc[0]
        _bundle_log(
            verbose,
            f"exclude union (4326): valid={ex.is_valid} empty={ex.is_empty} geom_type={ex.geom_type}",
        )
    _bundle_progress(verbose, "exclude: writing final exclude.gpkg + kml …")
    _write_family_final_polygon(
        g_out,
        bundle_dir,
        CLIP_EXCLUDE,
        gpkg_layer="exclude",
        layer_label="exclude",
        kml_overlay=kml_overlay,
    )
    return g_out


def aoi_polygon_for_bundle_job(
    plc: BundleConfig,
    data_dir: Path,
    bundle_dir: Path,
    *,
    verbose: bool = False,
) -> BaseGeometry:
    """AOI polygon (EPSG:4326) from clips composite when ``resolve.json`` exists."""
    from peaky_finders.bundle_clips import (
        bundle_resolve_path,
        composite_union_gpkg,
        read_bundle_resolve,
    )

    if bundle_resolve_path(bundle_dir).is_file():
        resolve = read_bundle_resolve(bundle_dir)
        clips_root = Path(str(resolve["clips_root"])).resolve()
        gpkg = composite_union_gpkg(clips_root, "aoi", str(resolve["aoi"]))
        gdf = gpd.read_file(gpkg, layer="aoi")
        if gdf.empty or gdf.geometry.is_empty.iloc[0]:
            raise ValueError("AOI composite geometry is empty")
        return make_valid(gdf.geometry.iloc[0])
    return load_or_build_clipped_aoi_union(plc, data_dir, bundle_dir, verbose=verbose)


def refresh_bundle_kml_sidecars(
    *,
    plc: BundleConfig,
    data_dir: Path,
    bundle_dir: Path,
    eligible_gpkg: Path,
    verbose: bool = False,
) -> None:
    """Rewrite clip/composite KML/PNG after ``bundle.kml_overlay`` change."""
    from peaky_finders.bundle_clips import (
        bundle_resolve_path,
        plan_clip_build_result,
        read_bundle_resolve,
        refresh_clip_kml_sidecars,
        resolved_clips_cache_root,
    )

    data_dir = Path(data_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()
    rp = bundle_resolve_path(bundle_dir)
    ref_map_raw: dict[str, Any] | None = None
    if rp.is_file():
        resolve = read_bundle_resolve(bundle_dir)
        clips_root = Path(str(resolve["clips_root"])).resolve()
        raw_ref = resolve.get("reference")
        ref_map_raw = raw_ref if isinstance(raw_ref, dict) else None
    else:
        # Without ``resolve.json`` (deleted / interrupted write): derive sibling ``clips/`` like ``ensure_land_use_bundle``.
        cache_base = bundle_dir.parent.parent
        clips_root = resolved_clips_cache_root(cache_base)

    mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
    result = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    refresh_clip_kml_sidecars(
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=plc.kml_overlay,
        result=result,
        verbose_log=((lambda m: _bundle_log(verbose, m)) if verbose else None),
    )
    if isinstance(ref_map_raw, dict) and ref_map_raw:
        from peaky_finders.bundle_clips import refresh_reference_clip_kml_sidecars

        refresh_reference_clip_kml_sidecars(
            plc=plc,
            clips_root=clips_root,
            reference_shas={str(k): str(v) for k, v in ref_map_raw.items()},
            verbose_log=((lambda m: _bundle_log(verbose, m)) if verbose else None),
        )


def ensure_reference_bundle_layers(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
    aoi_sha: str,
    force: bool,
    verbose: bool = False,
) -> dict[str, str]:
    """Build or reuse ``clips/reference/<sha>/`` entries; return id → sha map."""
    from peaky_finders.bundle_clips import ensure_reference_clip_cache

    vlog = (lambda m: _bundle_log(verbose, m)) if verbose else None
    plog = (lambda m: _bundle_progress(verbose, m)) if verbose else None
    return ensure_reference_clip_cache(
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        aoi_sha=aoi_sha,
        kml_overlay=plc.kml_overlay,
        force=force,
        verbose_log=vlog,
        progress_log=plog,
    )


def cached_gpkg_path_from_preset(
    *,
    preset_path: Path,
    cache_root: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Return planned eligible GPKG path for ``preset_path`` (``clips/eligible/<sha>/…``)."""
    p = Path(preset_path).expanduser().resolve()
    preset = load_preset(p)
    dd = Path(data_dir).expanduser().resolve() if data_dir is not None else peaky_home() / "data"
    if cache_root is not None:
        bundles_root = Path(cache_root).expanduser().resolve()
    else:
        bundles_root = resolved_bundle_cache_root(cli_bundle_cache_root=None)
    bundle_dir, gpkg = bundle_paths(bundles_root, preset=preset, data_dir=dd)
    from peaky_finders.bundle_clips import (
        bundle_resolve_path,
        plan_clip_build_result,
        resolved_clips_cache_root,
    )

    if bundle_resolve_path(bundle_dir).is_file():
        return bundle_eligible_land_use_gpkg(bundle_dir)
    cache_base = bundles_root.parent
    plc = require_bundle_config(preset)
    planned = plan_clip_build_result(
        plc=plc, data_dir=dd, clips_root=resolved_clips_cache_root(cache_base)
    )
    return planned.eligible_gpkg


def require_cached_gpkg(
    *,
    preset_path: Path,
    cache_root: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Resolve cached ``eligible_land_use/eligible_land_use.gpkg`` or raise ``FileNotFoundError`` with a fix hint."""
    gpkg = cached_gpkg_path_from_preset(
        preset_path=preset_path, cache_root=cache_root, data_dir=data_dir
    )
    if not gpkg.is_file():
        hint = (
            f'  poetry run peaky render "{Path(preset_path).expanduser().resolve()}"'
        )
        raise FileNotFoundError(
            "No AOI bundle cache for this preset's inputs. Run:\n" + hint
        )
    return gpkg


def effective_skadi_prefetch_workers(dem_workers_override: int | None) -> int:
    """``--dem-workers`` if set; else 16."""
    if dem_workers_override is not None:
        return max(1, dem_workers_override)
    return 16


def _final_kml_folder_role(folder_name: str) -> str:
    k = folder_name.strip().lower()
    if k == ELIGIBLE_LAND_USE_LAYER or k == "eligible":
        return "eligible"
    if k in ("aoi", "include", "exclude"):
        return k
    return "default"


def _kml_inject_final_bundle_styles(kml_path: Path, kml_overlay: BundleKmlOverlayStyles | None) -> None:
    if kml_overlay is None:
        return
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

    style_tag = t("Style")
    for prior in list(doc):
        if prior.tag == style_tag and (prior.get("id") or "").startswith("peaky_kml_final_"):
            doc.remove(prior)

    style_map_tag = t("StyleMap")
    su_tag = t("styleUrl")
    added_style: set[str] = set()

    for folder in doc.findall(f".//{t('Folder')}"):
        name_el = folder.find(t("name"))
        if name_el is None or not name_el.text:
            continue
        role = _final_kml_folder_role(name_el.text)
        spec = getattr(kml_overlay, role, None) or kml_overlay.default
        style_id = f"peaky_kml_final_{role}"
        if style_id not in added_style:
            added_style.add(style_id)
            doc.insert(
                0,
                _kml_build_style_element(
                    style_id,
                    spec.line,
                    spec.fill,
                    polygon_fill=spec.fill_polygons,
                    line_width=spec.line_width,
                    ns=ns,
                ),
            )
        for pm in folder.findall(f".//{t('Placemark')}"):
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
    tree.write(kml_path, encoding="utf-8", xml_declaration=True)


def _ogr2ogr_to_libkml(
    src_gpkg: Path,
    dst_kml: Path,
    *,
    layers: list[str] | None = None,
    human_name: str = "KML export",
) -> None:
    """Write LIBKML; ``layers`` if set limits which GPKG layers are copied (same order as listed)."""
    ogr = shutil.which("ogr2ogr")
    if not ogr:
        raise RuntimeError(f"ogr2ogr not found on PATH (install GDAL) — required for {human_name}")
    dst_kml.parent.mkdir(parents=True, exist_ok=True)
    dst_kml.unlink(missing_ok=True)
    cmd = [ogr, "-f", "LIBKML", "-overwrite", str(dst_kml), str(src_gpkg)]
    if layers:
        cmd.extend(layers)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ogr2ogr failed writing {human_name}\n" + (proc.stderr or proc.stdout or "").strip()
        )


def ensure_land_use_bundle(
    *,
    preset_path: Path,
    data_dir: Path,
    cache_root: Path | None,
    force: bool,
    verbose: bool = False,
    prefetch_dem: bool = True,
    dem_workers: int | None = None,
    no_plss_fetch: bool = False,
) -> tuple[Path, bool]:
    """Build or reuse land-use bundle. Returns ``(eligible_land_use_gpkg_path, reused_cache)``."""
    data_dir = Path(data_dir).expanduser().resolve()

    preset_path_resolved = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path_resolved)
    plc = require_bundle_config(preset)
    digest = bundle_cache_digest(preset=preset, data_dir=data_dir)
    if cache_root is not None:
        cache_root_final = Path(cache_root).expanduser().resolve()
    else:
        cache_root_final = resolved_bundle_cache_root(cli_bundle_cache_root=None)
    cache_base = cache_root_final.parent
    from peaky_finders.bundle_clips import (
        ensure_bundle_clip_cache,
        plan_clip_build_result,
        resolved_clips_cache_root,
        write_bundle_resolve,
    )

    clips_root = resolved_clips_cache_root(cache_base)
    bundle_dir, gpkg_path = bundle_paths(cache_root_final, preset=preset, data_dir=data_dir)
    mask_body = aoi_inputs_fingerprint_body(plc, data_dir)

    _bundle_log(verbose, f"preset={Path(preset_path).name} data_dir={data_dir}")
    _bundle_log(verbose, f"bundles_root={cache_root_final}")
    _bundle_log(verbose, f"clips_root={clips_root}")
    _bundle_log(verbose, f"bundle_dir={bundle_dir}")
    _bundle_log(verbose, f"digest combined_sha256={digest}")
    kml_overlay_digest = bundle_kml_overlay_inputs_digest(plc)
    _bundle_log(verbose, f"kml_overlay_sha256={kml_overlay_digest}")

    bundle_dir.mkdir(parents=True, exist_ok=True)

    maybe_refresh_plss_mlrs_for_bundle(
        preset_path=preset_path_resolved,
        cache_base=resolved_cache_base(),
        preset=preset,
        force_all=force,
        skip_network=no_plss_fetch,
        verbose_log=((lambda m: _bundle_log(verbose, m)) if verbose else None),
    )
    preset = load_preset(preset_path_resolved)

    planned = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips_root)
    reused_cache = planned.eligible_gpkg.is_file() and not force
    eligible_bounds: tuple[float, float, float, float] | None = None
    eligible_gpkg: Path

    vlog = (lambda m: _bundle_log(verbose, m)) if verbose else None
    plog = (lambda m: _bundle_progress(verbose, m)) if verbose else None

    if reused_cache:
        overlay_path = bundle_dir / KML_OVERLAY_DIGEST_BASENAME
        stored = overlay_path.read_text(encoding="utf-8").strip() if overlay_path.is_file() else ""
        if stored != kml_overlay_digest:
            _bundle_log(
                verbose,
                "cache hit (eligible GPKG unchanged): bundle.kml_overlay changed — refreshing sidecar KML and PNG …",
            )
            refresh_bundle_kml_sidecars(
                plc=plc,
                data_dir=data_dir,
                bundle_dir=bundle_dir,
                eligible_gpkg=planned.eligible_gpkg,
                verbose=verbose,
            )
            overlay_path.write_text(kml_overlay_digest + "\n", encoding="utf-8")
        else:
            _bundle_log(verbose, f"cache hit: reuse {planned.eligible_gpkg} (use --force to rebuild bundle)")
        eligible_gpkg = planned.eligible_gpkg
        clip_meta = planned
    else:
        _bundle_log(verbose, "building bundle: clips/composites missing or --force set …")
        if force:
            _bundle_log(verbose, "--force: clearing per-job bundle workspace …")
            _unlink_bundle_outputs_for_rebuild(bundle_dir)

        clip_result = ensure_bundle_clip_cache(
            plc=plc,
            data_dir=data_dir,
            clips_root=clips_root,
            mask_body=mask_body,
            kml_overlay=plc.kml_overlay,
            force=force,
            verbose_log=vlog,
            progress_log=plog,
        )
        eligible_gpkg = clip_result.eligible_gpkg
        clip_meta = clip_result
        _bundle_log(verbose, f"done: {eligible_gpkg}")

    ref_shas = ensure_reference_bundle_layers(
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        aoi_sha=clip_meta.aoi_sha,
        force=force,
        verbose=verbose,
    )
    write_bundle_resolve(
        bundle_dir,
        clips_root=clips_root,
        aoi_sha=clip_meta.aoi_sha,
        include_sha=clip_meta.include_sha,
        exclude_sha=clip_meta.exclude_sha,
        eligible_sha=clip_meta.eligible_sha,
        reference=ref_shas,
    )
    (bundle_dir / KML_OVERLAY_DIGEST_BASENAME).write_text(kml_overlay_digest + "\n", encoding="utf-8")
    loaded = gpd.read_file(eligible_gpkg, layer=ELIGIBLE_LAND_USE_LAYER)
    if not loaded.empty and not loaded.geometry.is_empty.iloc[0]:
        eligible_bounds = tuple(map(float, loaded.total_bounds))
        if not reused_cache:
            _bundle_log(
                verbose,
                f"eligible WGS84 bounds (minx,miny,maxx,maxy): {eligible_bounds}",
            )

    splat_tile_dir = resolved_splat_tile_cache_dir()

    if prefetch_dem:
        if eligible_bounds is None:
            print("bundle: dem prefetch skipped (empty eligible land geometry)", flush=True)
            _bundle_log(verbose, "dem prefetch skipped — empty geometry")
        else:
            prefetch_workers = effective_skadi_prefetch_workers(dem_workers)
            minx, miny, maxx, maxy = eligible_bounds
            bbox_tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
            n_cover = len(bbox_tiles)
            _bundle_log(
                verbose,
                f"dem prefetch Skadi: bbox={(minx, miny, maxx, maxy)} bbox_tiles={n_cover} workers={prefetch_workers}",
            )
            skipped, dl = prefetch_skadi_hgt_for_bounds_fatal(
                minx=minx,
                miny=miny,
                maxx=maxx,
                maxy=maxy,
                splat_tile_cache_dir=splat_tile_dir,
                max_workers=prefetch_workers,
                verbose_log=(lambda msg: _bundle_log(verbose, msg)) if verbose else None,
            )
            print(
                "bundle: dem prefetch finished "
                f"(bbox_tiles={n_cover} fetched={dl} skipped_present={skipped} mirror_dir={splat_tile_dir})",
                flush=True,
            )

    return eligible_gpkg, reused_cache
