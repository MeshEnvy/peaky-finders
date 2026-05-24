"""Pairwise SPLAT footprint intersections (plain overlap and overlap ∩ eligible land use)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import xml.etree.ElementTree as ET


from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path
from typing import Sequence
from xml.sax.saxutils import escape as xml_escape

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.google_earth_polygon import orient_for_kml
from peaky_finders.mesh_pairwise_store import (
    DEM_PEAK_ELIGIBLE_JSON,
    DEM_PEAK_PLAIN_JSON,
    pairwise_overlap_geometry_digest_sha256,
    resolved_mesh_pairwise_pair_dir,
    write_cached_pair_overlap_geometry,
    write_cached_pairwise_dem_peak,
    write_cached_pairwise_flat_kml,
)
from peaky_finders.path_labels import mesh_pairwise_rel_dir
from peaky_finders.pairwise_dem_peak import global_max_skadi_elevation_in_polygon
from peaky_finders.sites_job import (
    DEFAULT_MESH_PAIRWISE_ELIGIBLE_PEAK_PIN_STYLE,
    DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE,
    BundleKmlLayerStyle,
    mesh_pairwise_eligible_kml_arcname,
    mesh_pairwise_kml_arcname,
)
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_MESH_PAIRWISE,
    GX_DRAW_ORDER_MESH_PAIRWISE_ELIGIBLE,
    MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID,
    MESH_PAIRWISE_KML_STYLE_ID,
    PEAKY_KML_EMIT_VERSION,
    inject_peaky_polygon_kml_style,
)


def _bundle_layer_style_fingerprint(style: BundleKmlLayerStyle) -> str:
    return hashlib.sha256(style.model_dump_json().encode("utf-8")).hexdigest()


def _pairwise_flat_kml_plain_inputs_fingerprint(
    *,
    bundle_kml_overlay_digest: str,
    polygon_union_stable_digest: str,
    skadi_dem_peak_enabled: bool,
    dem_peak_llz: tuple[float, float, float] | None,
    link_polygon_style: BundleKmlLayerStyle,
    pairwise_peak_pin_style: BundleKmlLayerStyle,
    mesh_pairwise_gx_draw_order: int,
) -> str:
    dem = None if dem_peak_llz is None else [float(dem_peak_llz[0]), float(dem_peak_llz[1]), float(dem_peak_llz[2])]
    body = {
        "bundle_kml_overlay_digest": bundle_kml_overlay_digest,
        "dem_peak_llz": dem,
        "gx_draw_order": mesh_pairwise_gx_draw_order,
        "kml_emit_version": PEAKY_KML_EMIT_VERSION,
        "link_polygon_style_digest": _bundle_layer_style_fingerprint(link_polygon_style),
        "pairwise_peak_pin_style_digest": _bundle_layer_style_fingerprint(pairwise_peak_pin_style),
        "polygon_union_stable_digest": polygon_union_stable_digest,
        "skadi_dem_peak_enabled": skadi_dem_peak_enabled,
        "style_id_mesh_pairwise": MESH_PAIRWISE_KML_STYLE_ID,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _pairwise_flat_kml_eligible_inputs_fingerprint(
    *,
    bundle_kml_overlay_digest: str,
    bundle_land_use_inputs_digest: str,
    clipped_polygon_union_stable_digest: str,
    skadi_dem_peak_enabled: bool,
    dem_peak_llz: tuple[float, float, float] | None,
    eligible_polygon_style: BundleKmlLayerStyle,
    eligible_peak_pin_style: BundleKmlLayerStyle,
    mesh_eligible_gx_draw_order: int,
) -> str:
    dem = None if dem_peak_llz is None else [float(dem_peak_llz[0]), float(dem_peak_llz[1]), float(dem_peak_llz[2])]
    body = {
        "bundle_kml_overlay_digest": bundle_kml_overlay_digest,
        "bundle_land_use_inputs_digest": bundle_land_use_inputs_digest,
        "clipped_polygon_union_stable_digest": clipped_polygon_union_stable_digest,
        "dem_peak_llz": dem,
        "eligible_peak_pin_style_digest": _bundle_layer_style_fingerprint(eligible_peak_pin_style),
        "eligible_polygon_style_digest": _bundle_layer_style_fingerprint(eligible_polygon_style),
        "gx_draw_order": mesh_eligible_gx_draw_order,
        "kml_emit_version": PEAKY_KML_EMIT_VERSION,
        "skadi_dem_peak_enabled": skadi_dem_peak_enabled,
        "style_id_mesh_pairwise_eligible": MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


KML_NS = "http://www.opengis.net/kml/2.2"


def _pairwise_worker_cap(requested: int | None, *, total_pairs: int) -> int:
    """Thread count for pairwise phase: capped by pair count; default CPU heuristic ≤24."""

    if requested is not None:
        w = requested
    else:
        cpu = os.cpu_count() or 8
        w = max(1, min(24, cpu))
    return max(1, min(int(w), total_pairs))


def _polygonal_parts_only(geom: BaseGeometry) -> BaseGeometry:
    """Keep Polygon/MultiPolygon only; strip points/lines/collections per GDAL KML quirks."""

    def collect_polys(g: BaseGeometry, acc: list[BaseGeometry]) -> None:
        if g is None or g.is_empty:
            return
        t = g.geom_type
        if t in ("Polygon", "MultiPolygon"):
            acc.append(g)
        elif t == "GeometryCollection":
            for g0 in g.geoms:
                collect_polys(g0, acc)

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


_ELIGIBLE_LU_PREFIX = "Eligible land use union"
_ELIGIBLE_UNION_CHUNK = 2048


def _unary_union_batches(parts: list[BaseGeometry], *, log_prefix: str) -> BaseGeometry | None:
    """Cascaded ``unary_union`` with progress logs (single huge union can be silent for tens of minutes)."""

    stage: list[BaseGeometry] = list(parts)
    level = 0

    while len(stage) > 1:
        level += 1
        t0 = time.monotonic()
        nxt: list[BaseGeometry] = []

        for bi in range(0, len(stage), _ELIGIBLE_UNION_CHUNK):
            chunk = stage[bi : bi + _ELIGIBLE_UNION_CHUNK]
            nxt.append(unary_union(chunk))

        dt = time.monotonic() - t0

        print(
            f"{log_prefix}: union pass {level}: {len(stage)} part(s) → {len(nxt)} in {dt:.1f}s",
            flush=True,
        )

        stage = nxt

    return stage[0]


def _compute_eligible_land_use_union(
    eligible_gpkg: Path,
    *,
    layer: str = "eligible_land_use",
) -> BaseGeometry | None:
    """Union all polygons from bundle eligible land-use GeoPackage (layer matches bundle_build output)."""

    lp = _ELIGIBLE_LU_PREFIX
    ep = Path(eligible_gpkg)

    if not ep.is_file():
        return None

    t_read = time.monotonic()

    print(f"{lp}: reading {ep.resolve()} …", flush=True)

    gdf = gpd.read_file(ep, layer=layer)

    dt_r = time.monotonic() - t_read

    n_rows = len(gdf)

    n_geom = int(gdf.geometry.notna().sum())

    print(f"{lp}: loaded {n_rows} row(s), {n_geom} non-null geometries in {dt_r:.1f}s", flush=True)

    if gdf.empty or not gdf.geometry.notna().any():
        return None

    pieces = list(gdf.geometry.dropna())

    if not pieces:

        return None

    print(
        f"{lp}: cascading unary_union ({len(pieces)} parts, chunk={_ELIGIBLE_UNION_CHUNK}; may take a while) …",
        flush=True,
    )

    t_u = time.monotonic()

    u = _unary_union_batches(pieces, log_prefix=lp)

    print(f"{lp}: unary_union total {time.monotonic() - t_u:.1f}s", flush=True)

    if u is None or u.is_empty:

        return None

    u = make_valid(u) if not u.is_valid else u

    if u.is_empty:
        return None

    print(f"{lp}: extracting polygonal parts …", flush=True)

    out = _polygonal_parts_only(u)

    print(f"{lp}: polygonal union ready ({out.geom_type})", flush=True)



    return out


def read_eligible_land_use_union(
    eligible_gpkg: Path,
    *,
    layer: str = "eligible_land_use",
    cache_root: Path | None = None,
    eligible_sha: str | None = None,
) -> BaseGeometry | None:
    """Union eligible land-use polygons, optionally persisted under preset ``eligible_union`` cache."""
    ep = Path(eligible_gpkg).expanduser().resolve()
    if not ep.is_file():
        return None

    if cache_root is not None:
        from peaky_finders.eligible_union_store import (
            eligible_union_complete_matches,
            eligible_union_digest,
            load_cached_eligible_union,
            resolved_eligible_union_data_dir,
            write_cached_eligible_union,
        )

        cdig = eligible_union_digest(eligible_gpkg=ep, eligible_sha=eligible_sha)
        cdir = resolved_eligible_union_data_dir(cache_root)
        print(f"{_ELIGIBLE_LU_PREFIX}: digest {cdig[:12]}… → cache {cdir.resolve()}", flush=True)
        if eligible_union_complete_matches(
            cache_dir=cdir,
            cache_digest=cdig,
            eligible_sha=eligible_sha,
            source_gpkg=ep,
        ):
            print(f"{_ELIGIBLE_LU_PREFIX}: cache hit", flush=True)
            return load_cached_eligible_union(cache_dir=cdir)

        union = _compute_eligible_land_use_union(ep, layer=layer)
        print(f"{_ELIGIBLE_LU_PREFIX}: writing GPKG preview + metadata …", flush=True)
        write_cached_eligible_union(
            cache_dir=cdir,
            cache_digest=cdig,
            eligible_sha=eligible_sha,
            source_gpkg=ep,
            union_wgs84=union,
        )

        print(f"{_ELIGIBLE_LU_PREFIX}: cache written", flush=True)

        return union

    return _compute_eligible_land_use_union(ep, layer=layer)


def _read_coverage_footprint_epsg3857(gpkg: Path) -> BaseGeometry | None:
    """SPLAT coverage footprint projected to EPSG:3857, or ``None`` if missing/empty/unreadable."""
    p = Path(gpkg)
    if not p.is_file():
        return None
    ll = read_coverage_footprint(p)
    if ll is None or ll.is_empty:
        return None
    ll = make_valid(ll) if not ll.is_valid else ll
    if ll.is_empty:
        return None
    return gpd.GeoDataFrame(geometry=[ll], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _intersection_metric_geometries(ga: BaseGeometry | None, gb: BaseGeometry | None) -> BaseGeometry | None:
    """EPSG:3857 intersection of two footprint polygons."""
    if ga is None or ga.is_empty or gb is None or gb.is_empty:
        return None
    inter = ga.intersection(gb)
    if inter is None or inter.is_empty:
        return None
    inter = make_valid(inter) if not inter.is_valid else inter
    return None if inter.is_empty else inter


def _coverage_pair_intersection_metric(gpkg_a: Path, gpkg_b: Path) -> BaseGeometry | None:
    """EPSG:3857 intersection of two SPLAT footprint polygons."""
    ga = _read_coverage_footprint_epsg3857(gpkg_a)
    gb = _read_coverage_footprint_epsg3857(gpkg_b)
    return _intersection_metric_geometries(ga, gb)


def _metric_intersection_to_kml_polygon(metric_geom: BaseGeometry) -> BaseGeometry | None:
    ll = gpd.GeoDataFrame(geometry=[metric_geom], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
    ll = _polygonal_parts_only(ll)
    if ll.is_empty:
        return None
    return orient_for_kml(ll)


def _clip_plain_link_overlap_to_eligible(
    *,
    plain_link_ll: BaseGeometry,
    eligible_ll: BaseGeometry | None,
) -> BaseGeometry | None:
    """``plain_link_overlap`` ∩ eligible in WGS-84 (eligible link overlap *reuses* plain link geometry)."""
    if eligible_ll is None or eligible_ll.is_empty:
        return None
    elig = make_valid(eligible_ll) if not eligible_ll.is_valid else eligible_ll
    if elig.is_empty:
        return None
    clipped = plain_link_ll.intersection(elig)
    if clipped is None or clipped.is_empty:
        return None
    clipped = make_valid(clipped) if not clipped.is_valid else clipped
    if clipped.is_empty:
        return None
    clipped = _polygonal_parts_only(clipped)
    if clipped.is_empty:
        return None
    return orient_for_kml(clipped)


def compute_pair_overlap_geometry(gpkg_a: Path, gpkg_b: Path) -> BaseGeometry | None:
    """Intersection footprint of ``gpkg_a`` and ``gpkg_b`` projected to WGS-84 polygons only."""
    inter_m = _coverage_pair_intersection_metric(gpkg_a, gpkg_b)
    if inter_m is None:
        return None
    return _metric_intersection_to_kml_polygon(inter_m)


def compute_pair_eligible_overlap_geometry(
    gpkg_a: Path,
    gpkg_b: Path,
    eligible_ll: BaseGeometry | None,
) -> BaseGeometry | None:
    """Link overlap clipped to eligible land use (same as displayed link ∩ eligible in WGS-84)."""
    plain_ll = compute_pair_overlap_geometry(gpkg_a, gpkg_b)
    if plain_ll is None or plain_ll.is_empty:
        return None
    return _clip_plain_link_overlap_to_eligible(plain_link_ll=plain_ll, eligible_ll=eligible_ll)


def _polygons_flat(oriented_geom: BaseGeometry) -> list[Polygon]:
    if oriented_geom is None or oriented_geom.is_empty:
        return []
    if isinstance(oriented_geom, Polygon):
        return [oriented_geom]
    if isinstance(oriented_geom, MultiPolygon):
        return list(oriented_geom.geoms)
    return []


def _linear_ring_coord_text(ring_coords: object) -> str:
    pts: list[str] = []
    for xy in ring_coords:
        lon = float(xy[0])
        lat = float(xy[1])
        pts.append(f"{lon:.8f},{lat:.8f},0")
    return " ".join(pts)


_GOOGLE_EARTH_PUSHPIN_HREF = "http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png"


def _append_dem_peak_placemark(
    doc: ET.Element,
    t,
    *,
    lon: float,
    lat: float,
    z_m: float,
    pin_style: BundleKmlLayerStyle,
) -> None:
    pm = ET.SubElement(doc, t("Placemark"))
    ET.SubElement(pm, t("name")).text = xml_escape(f"{z_m:.0f} m")
    desc = ET.SubElement(pm, t("description"))
    desc.text = xml_escape(f"{lon:.8f}°, {lat:.8f}°")
    st = ET.SubElement(pm, t("Style"))
    ic = ET.SubElement(st, t("IconStyle"))
    ET.SubElement(ic, t("color")).text = pin_style.line
    ET.SubElement(ic, t("scale")).text = "1.0"
    href = ET.SubElement(ET.SubElement(ic, t("Icon")), t("href"))
    href.text = _GOOGLE_EARTH_PUSHPIN_HREF
    hs = ET.SubElement(ic, t("hotSpot"))
    hs.set("x", "20")
    hs.set("y", "2")
    hs.set("xunits", "pixels")
    hs.set("yunits", "pixels")
    pt = ET.SubElement(pm, t("Point"))
    ET.SubElement(pt, t("coordinates")).text = f"{lon:.8f},{lat:.8f},0"


def _append_polygon_under(parent_el: ET.Element, poly: Polygon) -> None:
    def t(local: str) -> str:
        return f"{{{KML_NS}}}{local}"

    pel = ET.SubElement(parent_el, t("Polygon"))
    obr = ET.SubElement(ET.SubElement(pel, t("outerBoundaryIs")), t("LinearRing"))
    ET.SubElement(obr, t("coordinates")).text = _linear_ring_coord_text(poly.exterior.coords)
    for hole in poly.interiors:
        ibr = ET.SubElement(ET.SubElement(pel, t("innerBoundaryIs")), t("LinearRing"))
        ET.SubElement(ibr, t("coordinates")).text = _linear_ring_coord_text(hole.coords)


def _write_flat_pair_overlap_kml_base(
    out_kml: Path,
    *,
    title: str,
    polygons: Sequence[Polygon],
    dem_peak_llz: tuple[float, float, float] | None = None,
    dem_peak_pin_style: BundleKmlLayerStyle | None = None,
) -> None:
    out_kml.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("", KML_NS)

    def t(local: str) -> str:
        return f"{{{KML_NS}}}{local}"

    root = ET.Element(t("kml"))
    doc = ET.SubElement(root, t("Document"))
    ET.SubElement(doc, t("name")).text = title

    pm = ET.SubElement(doc, t("Placemark"))
    ET.SubElement(pm, t("name")).text = title

    if len(polygons) == 1:
        _append_polygon_under(pm, polygons[0])
    else:
        mg = ET.SubElement(pm, t("MultiGeometry"))
        for poly in polygons:
            _append_polygon_under(mg, poly)

    if dem_peak_llz is not None and dem_peak_pin_style is not None:
        lon, lat, z_m = dem_peak_llz
        _append_dem_peak_placemark(
            doc, t, lon=lon, lat=lat, z_m=z_m, pin_style=dem_peak_pin_style
        )

    ET.ElementTree(root).write(out_kml, encoding="utf-8", xml_declaration=True)


def write_pairwise_link_overlap_kml_pairs(
    *,
    footprints: Sequence[tuple[Path, str, str]],
    emit_plain: bool,
    link_scratch_dir: Path | None,
    link_polygon_style: BundleKmlLayerStyle | None,
    emit_eligible: bool,
    eligible_scratch_dir: Path | None,
    eligible_polygon_style: BundleKmlLayerStyle | None,
    eligible_ll: BaseGeometry | None,
    dem_mirror_root: Path | None = None,
    emit_dem_peak_pins: bool = True,
    pairwise_peak_pin_style: BundleKmlLayerStyle | None = None,
    eligible_peak_pin_style: BundleKmlLayerStyle | None = None,
    pairwise_overlap_workers: int | None = None,
    geometry_cache_root: Path | None = None,
    slug_to_viewshed_digest: Mapping[str, str] | None = None,
    bundle_kml_overlay_digest: str | None = None,
    bundle_land_use_inputs_digest: str | None = None,
) -> tuple[list[tuple[str, Path, str]], list[tuple[str, Path, str]]]:
    """Pairwise link overlap plus optional reuse as plain ∩ eligible. One footprint read and one A∩B per pair.

    With ``pairwise_overlap_workers`` > 1 (or default CPU cap), pair work runs in a thread pool so
    geometry + DEM sampling can overlap; Skadi tile decode stays in-process LRU.

    Plain footprint∩geometry may be persisted under ``geometry_cache_root`` keyed by unordered
    viewshed workspace digests (via ``slug_to_viewshed_digest``). Skadi DEM peak sampling and stitched
    flat pairwise KML are written to that cache whenever configured; downstream runs still regenerate
    scratch KML copies under ``*_scratch_dirs``.
    """

    if emit_plain and (link_scratch_dir is None or link_polygon_style is None):
        raise ValueError("emit_plain requires link_scratch_dir and link_polygon_style")
    if emit_eligible and (
        eligible_scratch_dir is None
        or eligible_polygon_style is None
        or eligible_ll is None
        or eligible_ll.is_empty
    ):
        raise ValueError(
            "emit_eligible requires non-empty eligible_ll, scratch_dir, and polygon_style",
        )

    usable: list[tuple[Path, str, str]] = []
    for p, slug, name in footprints:
        pp = Path(p)
        if pp.is_file():
            usable.append((pp.resolve(), slug, name.strip() or slug))

    if len(usable) < 2:
        return [], []

    scratch_link = Path(link_scratch_dir) if link_scratch_dir is not None else None
    scratch_eligible = Path(eligible_scratch_dir) if eligible_scratch_dir is not None else None
    if scratch_link is not None:
        scratch_link.mkdir(parents=True, exist_ok=True)
    if scratch_eligible is not None:
        scratch_eligible.mkdir(parents=True, exist_ok=True)

    metrics = [_read_coverage_footprint_epsg3857(p) for p, _, _ in usable]

    total_pairs = len(usable) * (len(usable) - 1) // 2
    if emit_plain and emit_eligible:
        tag = "link + eligible overlap"
    elif emit_eligible:
        tag = "eligible link overlap"
    else:
        tag = "link overlap"
    print(f"{tag.capitalize()}: {total_pairs} pair(s)...", flush=True)

    want_eligible = emit_eligible and eligible_ll is not None and not eligible_ll.is_empty
    plain_peak_style = pairwise_peak_pin_style or DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE
    elig_peak_style = eligible_peak_pin_style or DEFAULT_MESH_PAIRWISE_ELIGIBLE_PEAK_PIN_STYLE

    pair_tasks = [
        (idx, pair[0], pair[1])
        for idx, pair in enumerate(combinations(range(len(usable)), 2), start=1)
    ]
    thread_cap = _pairwise_worker_cap(pairwise_overlap_workers, total_pairs=len(pair_tasks))
    if thread_cap > 1:
        print(f"  Pairwise parallelism: {thread_cap} threads", flush=True)
    print_lock: threading.Lock | None = threading.Lock() if thread_cap > 1 else None

    pairwise_geom_locks: dict[str, threading.Lock] = {}
    pairwise_geom_locks_mu = threading.Lock()

    def _pairwise_geom_lock(pair_key: str) -> threading.Lock:
        with pairwise_geom_locks_mu:
            if pair_key not in pairwise_geom_locks:
                pairwise_geom_locks[pair_key] = threading.Lock()
            return pairwise_geom_locks[pair_key]

    def _pair_log_prefix() -> str:
        if emit_plain and emit_eligible:
            return "  overlap pair"
        if emit_eligible:
            return "  eligible overlap"
        return "  link overlap"

    def log_pair_progress(pair_idx: int, label_a: str, label_b: str) -> None:
        msg = f"{_pair_log_prefix()} [{pair_idx}/{total_pairs}] {label_a} ↔ {label_b}..."
        if print_lock is not None:
            with print_lock:
                print(msg, flush=True)
        else:
            print(msg, flush=True)

    def log_pair_done(pair_idx: int, label_a: str, label_b: str, detail: str) -> None:
        msg = f"{_pair_log_prefix()} [{pair_idx}/{total_pairs}] {label_a} ↔ {label_b}: {detail}"
        if print_lock is not None:
            with print_lock:
                print(msg, flush=True)
        else:
            print(msg, flush=True)

    def emit_one_pair(
        pair_idx: int,
        i: int,
        j: int,
    ) -> tuple[tuple[str, Path, str] | None, tuple[str, Path, str] | None]:
        gpkg_a, slug_a, label_a = usable[i]
        gpkg_b, slug_b, label_b = usable[j]
        log_pair_progress(pair_idx, label_a, label_b)
        notes: list[str] = []

        def _finish_pair(
            result: tuple[tuple[str, Path, str] | None, tuple[str, Path, str] | None],
        ) -> tuple[tuple[str, Path, str] | None, tuple[str, Path, str] | None]:
            detail = ", ".join(notes) if notes else "done"
            log_pair_done(pair_idx, label_a, label_b, detail)
            return result

        use_geom_cache = (
            geometry_cache_root is not None
            and slug_to_viewshed_digest is not None
            and slug_a in slug_to_viewshed_digest
            and slug_b in slug_to_viewshed_digest
        )

        vd_a_: str | None = None
        vd_b_: str | None = None
        pdir: Path | None = None
        pair_lock_key = mesh_pairwise_rel_dir(slug_a, slug_b)

        plain_geom: BaseGeometry | None
        if use_geom_cache:
            assert geometry_cache_root is not None and slug_to_viewshed_digest is not None
            vd_a_ = slug_to_viewshed_digest[slug_a]
            vd_b_ = slug_to_viewshed_digest[slug_b]
            pdir = resolved_mesh_pairwise_pair_dir(
                slug_a=slug_a, slug_b=slug_b, cache_root=geometry_cache_root,
            )
            with _pairwise_geom_lock(pair_lock_key):
                plain_geom = compute_pair_overlap_geometry(gpkg_a, gpkg_b)
                write_cached_pair_overlap_geometry(
                    pair_dir=pdir,
                    vd_a=vd_a_,
                    vd_b=vd_b_,
                    overlap_wgs84=plain_geom,
                )
            notes.append("geom computed")
        else:
            inter_m = _intersection_metric_geometries(metrics[i], metrics[j])
            if inter_m is None:
                notes.append("no intersection")
                return _finish_pair((None, None))
            plain_geom = _metric_intersection_to_kml_polygon(inter_m)
            notes.append("geom computed (no cache)")

        if plain_geom is None or plain_geom.is_empty:
            notes.append("no overlap")
            return _finish_pair((None, None))

        paired_name = f"{label_a} <-> {label_b}"
        out_plain: tuple[str, Path, str] | None = None
        out_elig: tuple[str, Path, str] | None = None

        if emit_plain and plain_geom is not None and not plain_geom.is_empty:
            polys = _polygons_flat(plain_geom)
            if polys and scratch_link is not None and link_polygon_style is not None:
                dem_peak_llz = None
                if emit_dem_peak_pins and dem_mirror_root is not None:
                    if use_geom_cache and pdir is not None and pair_lock_key is not None:
                        peak_path = pdir / DEM_PEAK_PLAIN_JSON
                        with _pairwise_geom_lock(pair_lock_key):
                            dem_peak_llz = global_max_skadi_elevation_in_polygon(
                                plain_geom, dem_mirror_root
                            )
                            write_cached_pairwise_dem_peak(
                                peak_path,
                                peak_llz=dem_peak_llz,
                            )
                            notes.append("dem plain sampled")
                    else:
                        dem_peak_llz = global_max_skadi_elevation_in_polygon(
                            plain_geom, dem_mirror_root
                        )
                        notes.append("dem plain computed (no geom cache)")
                arc = mesh_pairwise_kml_arcname(slug_a, slug_b)
                out_kml = scratch_link / arc.rsplit("/", 1)[-1]
                poly_u_plain = unary_union(polys)
                plain_stable_d = pairwise_overlap_geometry_digest_sha256(poly_u_plain)
                skadi_on = bool(emit_dem_peak_pins and dem_mirror_root is not None)
                fp_plain: str | None = None
                can_cache_plain_kml = (
                    bundle_kml_overlay_digest is not None
                    and use_geom_cache
                    and pdir is not None
                    and pair_lock_key is not None
                )
                if can_cache_plain_kml:
                    fp_plain = _pairwise_flat_kml_plain_inputs_fingerprint(
                        bundle_kml_overlay_digest=bundle_kml_overlay_digest,
                        polygon_union_stable_digest=plain_stable_d,
                        skadi_dem_peak_enabled=skadi_on,
                        dem_peak_llz=dem_peak_llz if skadi_on else None,
                        link_polygon_style=link_polygon_style,
                        pairwise_peak_pin_style=plain_peak_style,
                        mesh_pairwise_gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE,
                    )
                    with _pairwise_geom_lock(pair_lock_key):
                        _write_flat_pair_overlap_kml_base(
                            out_kml,
                            title=paired_name,
                            polygons=polys,
                            dem_peak_llz=dem_peak_llz,
                            dem_peak_pin_style=plain_peak_style if dem_peak_llz is not None else None,
                        )
                        inject_peaky_polygon_kml_style(
                            out_kml,
                            style_id=MESH_PAIRWISE_KML_STYLE_ID,
                            spec=link_polygon_style,
                            gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE,
                        )
                        write_cached_pairwise_flat_kml(
                            pair_dir=pdir,
                            role="plain",
                            fingerprint=fp_plain,
                            source_kml=out_kml,
                        )
                    notes.append("plain kml written")
                else:
                    _write_flat_pair_overlap_kml_base(
                        out_kml,
                        title=paired_name,
                        polygons=polys,
                        dem_peak_llz=dem_peak_llz,
                        dem_peak_pin_style=plain_peak_style if dem_peak_llz is not None else None,
                    )
                    inject_peaky_polygon_kml_style(
                        out_kml,
                        style_id=MESH_PAIRWISE_KML_STYLE_ID,
                        spec=link_polygon_style,
                        gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE,
                    )
                    notes.append("plain kml written")
                out_plain = (paired_name, out_kml, arc)

        if want_eligible:
            clipped = (
                None
                if plain_geom is None or plain_geom.is_empty
                else _clip_plain_link_overlap_to_eligible(
                    plain_link_ll=plain_geom, eligible_ll=eligible_ll
                )
            )
            if clipped is not None and not clipped.is_empty:
                polye = _polygons_flat(clipped)
                if (
                    polye
                    and scratch_eligible is not None
                    and eligible_polygon_style is not None
                ):
                    dem_elig_peak = None
                    if emit_dem_peak_pins and dem_mirror_root is not None:
                        if use_geom_cache and pdir is not None and pair_lock_key is not None:
                            dg_elig = pairwise_overlap_geometry_digest_sha256(clipped)
                            elig_peak_path = pdir / DEM_PEAK_ELIGIBLE_JSON
                            with _pairwise_geom_lock(pair_lock_key):
                                dem_elig_peak = global_max_skadi_elevation_in_polygon(
                                    clipped, dem_mirror_root
                                )
                                write_cached_pairwise_dem_peak(
                                    elig_peak_path,
                                    peak_llz=dem_elig_peak,
                                    geometry_digest=dg_elig,
                                )
                                notes.append("dem eligible sampled")
                        else:
                            dem_elig_peak = global_max_skadi_elevation_in_polygon(
                                clipped, dem_mirror_root
                            )
                            notes.append("dem eligible computed (no geom cache)")
                    earc = mesh_pairwise_eligible_kml_arcname(slug_a, slug_b)
                    eout = scratch_eligible / earc.rsplit("/", 1)[-1]
                    poly_u_elig = unary_union(polye)
                    elig_stable_d = pairwise_overlap_geometry_digest_sha256(poly_u_elig)
                    sk_elig_on = bool(emit_dem_peak_pins and dem_mirror_root is not None)
                    fp_elig: str | None = None
                    can_cache_elig_kml = (
                        bundle_kml_overlay_digest is not None
                        and bundle_land_use_inputs_digest is not None
                        and use_geom_cache
                        and pdir is not None
                        and pair_lock_key is not None
                    )
                    if can_cache_elig_kml:
                        fp_elig = _pairwise_flat_kml_eligible_inputs_fingerprint(
                            bundle_kml_overlay_digest=bundle_kml_overlay_digest,
                            bundle_land_use_inputs_digest=bundle_land_use_inputs_digest,
                            clipped_polygon_union_stable_digest=elig_stable_d,
                            skadi_dem_peak_enabled=sk_elig_on,
                            dem_peak_llz=dem_elig_peak if sk_elig_on else None,
                            eligible_polygon_style=eligible_polygon_style,
                            eligible_peak_pin_style=elig_peak_style,
                            mesh_eligible_gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE_ELIGIBLE,
                        )
                        with _pairwise_geom_lock(pair_lock_key):
                            _write_flat_pair_overlap_kml_base(
                                eout,
                                title=paired_name,
                                polygons=polye,
                                dem_peak_llz=dem_elig_peak,
                                dem_peak_pin_style=elig_peak_style if dem_elig_peak is not None else None,
                            )
                            inject_peaky_polygon_kml_style(
                                eout,
                                style_id=MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID,
                                spec=eligible_polygon_style,
                                gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE_ELIGIBLE,
                            )
                            write_cached_pairwise_flat_kml(
                                pair_dir=pdir,
                                role="eligible",
                                fingerprint=fp_elig,
                                source_kml=eout,
                            )
                        notes.append("eligible kml written")
                    else:
                        _write_flat_pair_overlap_kml_base(
                            eout,
                            title=paired_name,
                            polygons=polye,
                            dem_peak_llz=dem_elig_peak,
                            dem_peak_pin_style=elig_peak_style if dem_elig_peak is not None else None,
                        )
                        inject_peaky_polygon_kml_style(
                            eout,
                            style_id=MESH_PAIRWISE_ELIGIBLE_KML_STYLE_ID,
                            spec=eligible_polygon_style,
                            gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE_ELIGIBLE,
                        )
                        notes.append("eligible kml written")
                    out_elig = (paired_name, eout, earc)

        return _finish_pair((out_plain, out_elig))

    if thread_cap == 1:
        pair_results = [emit_one_pair(*t) for t in pair_tasks]
    else:
        with ThreadPoolExecutor(max_workers=thread_cap) as ex:
            pair_results = list(ex.map(lambda t: emit_one_pair(*t), pair_tasks))

    emitted_plain: list[tuple[str, Path, str]] = []
    emitted_eligible: list[tuple[str, Path, str]] = []
    for p_row, e_row in pair_results:
        if p_row is not None:
            emitted_plain.append(p_row)
        if e_row is not None:
            emitted_eligible.append(e_row)

    return emitted_plain, emitted_eligible


def write_pairwise_link_overlap_layers(
    *,
    footprints: Sequence[tuple[Path, str, str]],
    scratch_dir: Path,
    polygon_style: BundleKmlLayerStyle,
    dem_mirror_root: Path | None = None,
    emit_dem_peak_pins: bool = True,
    pairwise_peak_pin_style: BundleKmlLayerStyle | None = None,
    pairwise_overlap_workers: int | None = None,
    geometry_cache_root: Path | None = None,
    slug_to_viewshed_digest: Mapping[str, str] | None = None,
) -> list[tuple[str, Path, str]]:
    """Pairwise footprint ∩ footprint → flat KML under ``sites/mesh/coverage/pairwise/``."""
    emitted, _ = write_pairwise_link_overlap_kml_pairs(
        footprints=footprints,
        emit_plain=True,
        link_scratch_dir=scratch_dir,
        link_polygon_style=polygon_style,
        emit_eligible=False,
        eligible_scratch_dir=None,
        eligible_polygon_style=None,
        eligible_ll=None,
        dem_mirror_root=dem_mirror_root,
        emit_dem_peak_pins=emit_dem_peak_pins,
        pairwise_peak_pin_style=pairwise_peak_pin_style,
        eligible_peak_pin_style=None,
        pairwise_overlap_workers=pairwise_overlap_workers,
        geometry_cache_root=geometry_cache_root,
        slug_to_viewshed_digest=slug_to_viewshed_digest,
    )
    return emitted


def write_pairwise_eligible_link_overlap_layers(
    *,
    footprints: Sequence[tuple[Path, str, str]],
    scratch_dir: Path,
    polygon_style: BundleKmlLayerStyle,
    eligible_ll: BaseGeometry | None,
    dem_mirror_root: Path | None = None,
    emit_dem_peak_pins: bool = True,
    eligible_peak_pin_style: BundleKmlLayerStyle | None = None,
    pairwise_overlap_workers: int | None = None,
    geometry_cache_root: Path | None = None,
    slug_to_viewshed_digest: Mapping[str, str] | None = None,
) -> list[tuple[str, Path, str]]:
    """Plain link ∩ eligible (same geometry as pairwise link overlap clipped in WGS-84)."""

    if eligible_ll is None or eligible_ll.is_empty:
        return []

    _, emitted = write_pairwise_link_overlap_kml_pairs(
        footprints=footprints,
        emit_plain=False,
        link_scratch_dir=None,
        link_polygon_style=None,
        emit_eligible=True,
        eligible_scratch_dir=scratch_dir,
        eligible_polygon_style=polygon_style,
        eligible_ll=eligible_ll,
        dem_mirror_root=dem_mirror_root,
        emit_dem_peak_pins=emit_dem_peak_pins,
        pairwise_peak_pin_style=None,
        eligible_peak_pin_style=eligible_peak_pin_style,
        pairwise_overlap_workers=pairwise_overlap_workers,
        geometry_cache_root=geometry_cache_root,
        slug_to_viewshed_digest=slug_to_viewshed_digest,
    )
    return emitted
