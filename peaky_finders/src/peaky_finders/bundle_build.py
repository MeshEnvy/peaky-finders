"""Build the AOI / land-use bundle: preset-listed GDB layers → ``build/clips`` + ``build/bundle``.

Per-layer clips and composites live under ``<preset>/build/clips``.
Each preset job writes ``<preset>/build/bundle/resolve.json`` pointing into ``build/clips``.
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
from peaky_finders.geometry_preview_png import write_wgs84_geodataframe_preview_png
from peaky_finders.sites_job import (
    BundleKmlOverlayStyles,
    BundleConfig,
    BundleReferenceLayerEntry,
    GdbLayerGroup,
    GdbLayerSpec,
    Preset,
    _gdb_layer_group_payload,
    _gdb_layer_group_sort_key,
    canonical_bundle_aoi_config_text,
    canonical_bundle_land_use_config_text,
    canonical_bundle_reference_config_text,
    load_preset,
    ogr_where_for_layer_spec,
    peaky_home,
    resolved_bundle_dir,
    resolved_preset_build_dir,
    resolved_preset_bundle_data_dir,
    resolved_preset_clips_dir,
    resolved_skadi_mirror_dir,
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
            "\n  list GDB layers with ogrinfo or pyogrio"
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
    """Legacy slug (AOI inputs only); preset bundle workspace uses a stable ``build/bundle/`` dir."""
    dd = Path(data_dir).expanduser().resolve()
    payload = aoi_inputs_fingerprint_body(pre, dd).encode("utf-8")
    return "aoi_" + hashlib.sha256(payload).hexdigest()[:16]


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
    """SHA-256 hex: AOI (v1) + land-use (v3) fingerprint bodies (logging / diagnostics only)."""
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


def list_gdb_input_files(root: Path) -> tuple[Path, ...]:
    """Sorted on-disk files that back a bundle vector dataset (``.gpkg`` file or FileGDB tree)."""
    root = root.expanduser().resolve()
    if root.is_file():
        return (root,)
    return tuple(
        path
        for path in sorted(root.rglob("*"), key=lambda p: p.as_posix())
        if path.is_file()
    )


def _file_tree_mtime_size_fingerprint(root: Path) -> str:
    """Stable multiline fingerprint: one ``relpath<TAB>mtime_ns<TAB>size`` per file under root."""
    root = root.resolve()
    lines: list[str] = []
    if root.is_file():
        st = root.stat()
        lines.append(f"{root.name}\t{st.st_mtime_ns}\t{st.st_size}")
        return "\n".join(lines) + "\n"
    for path in list_gdb_input_files(root):
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
            if not _ogr_layer_metadata_polygon_eligible(gt, resolved):
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


def _is_kml_path(resolved: Path) -> bool:
    return resolved.is_file() and resolved.suffix.lower() == ".kml"


def _ogr_layer_metadata_polygon_eligible(gt: str | None, resolved: Path) -> bool:
    """True when OGR metadata is polygon-like, or KML driver reports Unknown."""
    if gt and is_polygon_geometry_type(gt):
        return True
    return _is_kml_path(resolved) and (not gt or str(gt).lower() == "unknown")


@contextmanager
def openfilegdb_dataset_path(gdb_path: Path) -> Iterator[str]:

    """Yield a GDAL-readable dataset path (File GDB, unpacked GDB sibling, GeoPackage, or KML)."""
    gdb_path = gdb_path.expanduser().resolve()
    if not gdb_path.exists():
        raise FileNotFoundError(f"GDB path not found: {gdb_path}")
    if gdb_path.is_file() and gdb_path.suffix.lower() == ".gpkg":
        yield str(gdb_path)
        return
    if gdb_path.is_file() and gdb_path.suffix.lower() == ".kml":
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
        "Not a supported bundle vector dataset (File Geodatabase directory, .gpkg, or .kml): "
        f"{gdb_path}"
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
    """True for eligible slice KML paths under ``clips/eligible/layers``."""
    parts_lower = [p.lower() for p in kml_path.parts]
    for i in range(len(parts_lower) - 1):
        if parts_lower[i] != "eligible":
            continue
        if parts_lower[i + 1] == "layers":
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


def _write_geodataframe_preview_png(
    gdf_wgs84: gpd.GeoDataFrame,
    png_path: Path,
    *,
    kml_path: Path,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    """Flat-map PNG using the same fill/line semantics as ``bundle.kml_overlay`` (EPSG:3857 axes)."""
    role = _kml_overlay_role(kml_path, layer_label)
    if kml_overlay is None:
        face_aabbggrr = "66888888"
        line_aabbggrr = "ff666666"
        fill_polys = True
        line_w = 2.0
    else:
        spec = getattr(kml_overlay, role, None) or kml_overlay.default
        face_aabbggrr = spec.fill
        line_aabbggrr = spec.line
        fill_polys = spec.fill_polygons
        line_w = spec.line_width

    write_wgs84_geodataframe_preview_png(
        gdf_wgs84,
        png_path,
        face_aabbggrr=face_aabbggrr,
        line_aabbggrr=line_aabbggrr,
        fill_polygons=fill_polys,
        line_width=line_w,
    )


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


def list_polygon_layer_names(resolved: Path) -> list[str]:
    """Return sorted polygon-like OGR layer names for a bundle vector dataset."""
    resolved = resolved.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Vector dataset not found: {resolved}")
    with openfilegdb_dataset_path(resolved) as ds:
        names: list[str] = []
        for lyr, gt in _list_layers_pair_rows(ds):
            if _ogr_layer_metadata_polygon_eligible(gt, resolved):
                names.append(lyr)
    return sorted(set(names))


def gdb_layer_group_specs(g: GdbLayerGroup, data_dir: Path) -> list[GdbLayerSpec]:
    """Resolve explicit ``layers`` or all polygon-like layers when the list is empty."""
    if g.layers:
        return list(g.layers)
    resolved = resolve_land_use_gdb_path(data_dir, g.path)
    names = list_polygon_layer_names(resolved)
    if not names:
        raise ValueError(
            f"No polygon layers found for bundle path {g.path!r} ({resolved}); "
            "set layers explicitly in preset bundle.include / bundle.exclude."
        )
    return [GdbLayerSpec(name=n) for n in names]


def _clip_stem(preset_path: str, layer: str, where: str | None = None) -> str:
    """Basename stem for layer-job dirs from preset GDB path, layer name, and optional ``where``."""
    raw = Path(preset_path).as_posix()
    safe_path = _sanitize_path_component(raw.replace("/", "_"))
    safe_layer = _sanitize_path_component(layer)
    max_path, max_layer = 96, 80
    if len(safe_path) > max_path:
        safe_path = safe_path[-max_path:]
    if len(safe_layer) > max_layer:
        safe_layer = safe_layer[-max_layer:]
    parts = [safe_path, safe_layer]
    if where:
        safe_where = _sanitize_path_component(where)
        if len(safe_where) > 64:
            safe_where = safe_where[:64]
        parts.append(f"where_{safe_where}")
    return "__".join(parts)


def _flatten_gdb_layer_jobs(
    groups: list[GdbLayerGroup], data_dir: Path
) -> list[tuple[str, Path, str, str | None]]:
    jobs: list[tuple[str, Path, str, str | None]] = []
    for g in sorted(groups, key=lambda x: _gdb_layer_group_sort_key(x)):
        r = resolve_land_use_gdb_path(data_dir, g.path)
        for spec in sorted(gdb_layer_group_specs(g, data_dir), key=lambda z: z.name):
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
            if not _ogr_layer_metadata_polygon_eligible(gt, resolved):
                raise ValueError(
                    f"Exclude layer {preset_path}::{layer} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
        elif kind == "reference":
            if gt is None:
                raise ValueError(f"Reference layer {layer!r} not found in {resolved}")
            if not _ogr_layer_metadata_polygon_eligible(gt, resolved):
                raise ValueError(
                    f"Reference layer {preset_path}::{layer} has geometry {gt!r}; "
                    "only polygon-like layers are supported."
                )
        elif kind == "aoi":
            if gt is None:
                raise ValueError(f"AOI layer {layer!r} not found in {resolved}")
            if not _ogr_layer_metadata_polygon_eligible(gt, resolved):
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
    preset: Preset | None = None,
    data_dir: Path | None = None,
) -> tuple[Path, Path]:
    """``(bundle_workspace_dir, eligible_land_use_gpkg_path)`` via ``resolve.json`` when present."""
    _ = preset, data_dir
    bundle_dir = Path(cache_root).expanduser().resolve()
    return bundle_dir, bundle_eligible_land_use_gpkg(bundle_dir)


def bundle_directory_for_preset(
    *,
    preset_path: Path,
    data_dir: Path,
    cache_root: Path | None = None,
) -> Path | None:
    """Stable bundle cache dir for ``preset_path`` when it defines ``bundle.*``; else ``None``."""
    preset_path_resolved = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path_resolved)
    if preset.bundle is None:
        return None
    data_dir = Path(data_dir).expanduser().resolve()
    if cache_root is not None:
        cache_root_final = Path(cache_root).expanduser().resolve()
    else:
        cache_root_final = resolved_bundle_dir(preset_path=preset_path_resolved)
    bundle_dir, _ = bundle_paths(cache_root_final, preset=preset, data_dir=data_dir)
    return bundle_dir


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



def _write_clip_manifest(
    bundle_dir: Path, family: ClipFamily, *, items: list[dict[str, object]]
) -> None:
    family.dir(bundle_dir).mkdir(parents=True, exist_ok=True)
    path = family.manifest_path(bundle_dir)
    path.write_text(
        json.dumps({"format": family.manifest_format, "items": items}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )



def cached_gpkg_path_from_preset(
    *,
    preset_path: Path,
    cache_root: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Return planned eligible GPKG path for ``preset_path`` (``clips/eligible/eligible_land_use.gpkg``)."""
    p = Path(preset_path).expanduser().resolve()
    preset = load_preset(p)
    dd = (
        Path(data_dir).expanduser().resolve()
        if data_dir is not None
        else resolved_preset_bundle_data_dir(preset_path=p, preset=preset)
    )
    if cache_root is not None:
        bundles_root = Path(cache_root).expanduser().resolve()
    else:
        bundles_root = resolved_bundle_dir(preset_path=p)
    bundle_dir, gpkg = bundle_paths(bundles_root, preset=preset, data_dir=dd)
    from peaky_finders.bundle_clips import (
        bundle_resolve_path,
        plan_clip_build_result,
    )

    if bundle_resolve_path(bundle_dir).is_file():
        return bundle_eligible_land_use_gpkg(bundle_dir)
    plc = require_bundle_config(preset)
    planned = plan_clip_build_result(
        plc=plc, data_dir=dd, clips_root=resolved_preset_clips_dir(p)
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
            f"  eligible land-use GPKG not found; check bundle GDB paths in preset "
            f"({Path(preset_path).expanduser().resolve()})"
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

