"""Footprint overlap-count (depth) bands for aggregate KMZ mesh layers."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Sequence

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import from_bounds
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.google_earth_polygon import orient_for_kml
from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.link_overlap import (
    _clip_plain_link_overlap_to_eligible,
    _pairwise_worker_cap,
    _polygonal_parts_only,
    _polygons_flat,
    _write_flat_pair_overlap_kml_base,
    _read_coverage_footprint_epsg3857,
)
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_MESH_DEPTH_D1,
    GX_DRAW_ORDER_MESH_DEPTH_D2,
    GX_DRAW_ORDER_MESH_DEPTH_D3,
    GX_DRAW_ORDER_MESH_DEPTH_D5,
    GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D1,
    GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D2,
    GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D3,
    GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D5,
    MESH_DEPTH_D1_KML_STYLE_ID,
    MESH_DEPTH_D2_KML_STYLE_ID,
    MESH_DEPTH_D3_KML_STYLE_ID,
    MESH_DEPTH_D5_KML_STYLE_ID,
    MESH_DEPTH_ELIGIBLE_D1_KML_STYLE_ID,
    MESH_DEPTH_ELIGIBLE_D2_KML_STYLE_ID,
    MESH_DEPTH_ELIGIBLE_D3_KML_STYLE_ID,
    MESH_DEPTH_ELIGIBLE_D5_KML_STYLE_ID,
    PEAKY_KML_EMIT_VERSION,
    inject_peaky_polygon_kml_style,
)
from peaky_finders.mesh_depth_cache import (
    mesh_depth_set_digest,
    resolved_mesh_depth_set_dir,
    resolved_mesh_depth_slice_dir,
    try_read_cached_mesh_depth_bands,
    try_read_cached_mesh_depth_flat_kml,
    try_read_cached_mesh_depth_slice,
    write_cached_mesh_depth_bands,
    write_cached_mesh_depth_flat_kml,
    write_cached_mesh_depth_slice,
)
from peaky_finders.mesh_pairwise_cache import pairwise_overlap_geometry_digest_sha256
from peaky_finders.sites_job import (
    BundleKmlLayerStyle,
    BundleKmlOverlayStyles,
    mesh_coverage_depth_eligible_site_kml_arcname,
    mesh_coverage_depth_site_kml_arcname,
    resolved_mesh_depth_band_kml_style,
)

MESH_DEPTH_BANDS: tuple[str, ...] = ("d1_unique", "d2_pair", "d3_quad", "d5_plus")

MESH_DEPTH_DISPLAY: dict[str, str] = {
    "d1_unique": "Coverage depth: 1 site",
    "d2_pair": "Coverage depth: 2 sites",
    "d3_quad": "Coverage depth: 3–4 sites",
    "d5_plus": "Coverage depth: 5+ sites",
}

_PLAIN_STYLE: dict[str, tuple[str, int]] = {
    "d1_unique": (MESH_DEPTH_D1_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_D1),
    "d2_pair": (MESH_DEPTH_D2_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_D2),
    "d3_quad": (MESH_DEPTH_D3_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_D3),
    "d5_plus": (MESH_DEPTH_D5_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_D5),
}
_ELIG_STYLE: dict[str, tuple[str, int]] = {
    "d1_unique": (MESH_DEPTH_ELIGIBLE_D1_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D1),
    "d2_pair": (MESH_DEPTH_ELIGIBLE_D2_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D2),
    "d3_quad": (MESH_DEPTH_ELIGIBLE_D3_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D3),
    "d5_plus": (MESH_DEPTH_ELIGIBLE_D5_KML_STYLE_ID, GX_DRAW_ORDER_MESH_DEPTH_ELIGIBLE_D5),
}


def mesh_depth_visibility_field(band: str, *, eligible: bool) -> str:
    """``KmzDocumentLayerVisibility`` field name for this band."""
    if eligible:
        return f"mesh_depth_eligible_{band}"
    return f"mesh_depth_{band}"


def compute_footprint_depth_bands_wgs84(
    gpkg_paths: Sequence[Path],
    *,
    max_raster_dimension: int,
    raster_workers: int | None = None,
) -> dict[str, BaseGeometry]:
    """Count overlapping footprints on an EPSG:3857 grid; return WGS-84 polygonal bands (may be empty)."""
    metric: list[BaseGeometry] = []
    for p in gpkg_paths:
        g = _read_coverage_footprint_epsg3857(Path(p))
        if g is not None and not g.is_empty:
            g = make_valid(g) if not g.is_valid else g
            if not g.is_empty:
                metric.append(g)

    if len(metric) < 2:
        return {}

    bounds = unary_union(metric).bounds
    minx, miny, maxx, maxy = bounds
    width_m = maxx - minx
    height_m = maxy - miny
    if width_m <= 0 or height_m <= 0:
        return {}

    max_dim = max(1, int(max_raster_dimension))
    if width_m >= height_m:
        cols = max_dim
        rows = max(1, int(round(max_dim * height_m / width_m)))
    else:
        rows = max_dim
        cols = max(1, int(round(max_dim * width_m / height_m)))

    print(f"  depth grid {cols}×{rows} px (~{width_m / 1000.0:.1f}×{height_m / 1000.0:.1f} km extent)…", flush=True)
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)
    n_m = len(metric)
    raster_cap = _pairwise_worker_cap(raster_workers, total_pairs=n_m)

    def _depth_raster_plane(gm: BaseGeometry) -> np.ndarray:
        return features.rasterize(
            [(gm, 1)],
            out_shape=(rows, cols),
            transform=transform,
            fill=0,
            dtype=np.uint16,
        )

    if raster_cap > 1:
        print(
            f"  depth footprint raster parallelism: {raster_cap} threads ({n_m} layers)…",
            flush=True,
        )
        with ThreadPoolExecutor(max_workers=raster_cap) as ex:
            stacks = list(ex.map(_depth_raster_plane, metric))
        acc = np.zeros((rows, cols), dtype=np.uint32)
        for layer in stacks:
            acc += layer.astype(np.uint32, copy=False)
    else:
        acc = np.zeros((rows, cols), dtype=np.uint32)
        for i, g in enumerate(metric, start=1):
            print(f"  depth rasterize footprint {i}/{n_m}…", flush=True)
            layer = features.rasterize(
                [(g, 1)],
                out_shape=(rows, cols),
                transform=transform,
                fill=0,
                dtype=np.uint16,
            )
            acc += layer.astype(np.uint32, copy=False)

    out: dict[str, BaseGeometry] = {}
    band_masks: list[tuple[str, np.ndarray]] = [
        ("d1_unique", (acc == 1)),
        ("d2_pair", (acc == 2)),
        ("d3_quad", (acc >= 3) & (acc <= 4)),
        ("d5_plus", (acc >= 5)),
    ]
    for band, sel in band_masks:
        m = sel.astype(np.uint8)
        if not np.any(m):
            print(f"  depth band {band}: empty, skip", flush=True)
            continue
        print(f"  depth polygonize {band}…", flush=True)
        pieces: list[BaseGeometry] = []
        for geom, val in features.shapes(m, mask=m, transform=transform, connectivity=8):
            if int(val) == 1:
                pieces.append(shape(geom))
        if not pieces:
            continue
        print(f"  depth union {band}: {len(pieces)} piece(s)…", flush=True)
        merged = unary_union(pieces)
        merged = make_valid(merged) if not merged.is_valid else merged
        if merged.is_empty:
            continue
        print(f"  depth reproject {band} → WGS84…", flush=True)
        ll = gpd.GeoDataFrame(geometry=[merged], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
        ll = make_valid(ll) if not ll.is_valid else ll
        if ll.is_empty:
            continue
        polys = _polygons_flat(orient_for_kml(ll))
        if not polys:
            continue
        u = unary_union(polys)
        u = make_valid(u) if not u.is_valid else u
        if u.is_empty:
            continue
        out[band] = u
        print(f"  depth band {band}: ok", flush=True)
    return out


def _mesh_depth_viewshed_rows_for_cache(
    usable: list[tuple[Path, str, str]],
    slug_to_viewshed_digest: dict[str, str] | None,
) -> list[str] | None:
    if slug_to_viewshed_digest is None:
        return None
    out: list[str] = []
    for _p, slug, _ in usable:
        vd = slug_to_viewshed_digest.get(slug)
        if vd is None:
            return None
        out.append(vd)
    return out


def _mesh_depth_layer_style_digest(style: BundleKmlLayerStyle) -> str:
    return hashlib.sha256(style.model_dump_json().encode("utf-8")).hexdigest()


def _mesh_depth_flat_plain_fingerprint(
    *,
    bundle_kml_overlay_digest: str,
    band: str,
    site_slug: str,
    slice_polygon_union_stable_digest: str,
    doc_title: str,
    polygon_style_digest: str,
    mesh_depth_polygon_style_id: str,
    gx_draw_order: int,
) -> str:
    body = {
        "band": band,
        "bundle_kml_overlay_digest": bundle_kml_overlay_digest,
        "doc_title": doc_title,
        "gx_draw_order": gx_draw_order,
        "kml_emit_version": PEAKY_KML_EMIT_VERSION,
        "mesh_depth_polygon_style_id": mesh_depth_polygon_style_id,
        "polygon_style_digest": polygon_style_digest,
        "site_slug": site_slug,
        "slice_polygon_union_stable_digest": slice_polygon_union_stable_digest,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _mesh_depth_flat_eligible_fingerprint(
    *,
    bundle_kml_overlay_digest: str,
    bundle_land_use_inputs_digest: str,
    band: str,
    site_slug: str,
    eligible_clip_union_stable_digest: str,
    doc_title_eligible: str,
    polygon_style_digest: str,
    mesh_depth_eligible_polygon_style_id: str,
    gx_draw_order: int,
) -> str:
    body = {
        "band": band,
        "bundle_kml_overlay_digest": bundle_kml_overlay_digest,
        "bundle_land_use_inputs_digest": bundle_land_use_inputs_digest,
        "doc_title_eligible": doc_title_eligible,
        "eligible_clip_polygon_union_stable_digest": eligible_clip_union_stable_digest,
        "gx_draw_order": gx_draw_order,
        "kml_emit_version": PEAKY_KML_EMIT_VERSION,
        "mesh_depth_eligible_polygon_style_id": mesh_depth_eligible_polygon_style_id,
        "polygon_style_digest": polygon_style_digest,
        "site_slug": site_slug,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def write_mesh_depth_kml_layers(
    *,
    footprints: Sequence[tuple[Path, str, str]],
    kml_overlay: BundleKmlOverlayStyles | None,
    scratch_depth_dir: Path,
    scratch_depth_eligible_dir: Path | None,
    eligible_ll: BaseGeometry | None,
    max_raster_dimension: int,
    mesh_depth_workers: int | None = None,
    geometry_cache_root: Path | None = None,
    slug_to_viewshed_digest: dict[str, str] | None = None,
    force_mesh_depth_geometry: bool = False,
    bundle_kml_overlay_digest: str | None = None,
    bundle_land_use_inputs_digest: str | None = None,
) -> tuple[list[tuple[str, str, Path, str, str]], list[tuple[str, str, Path, str, str]]]:
    """Write per-site depth KMLs under ``depth/{band}/{slug}.kml`` (and eligible mirror).

    Returns two lists of ``(band, display_title, disk_path, kmz_arcname, visibility_field)``.
    Each row is one site's footprint ∩ that depth band (Google Earth: toggle per site per band).
    Per-footprint rasterize and per-site band slices use :class:`~concurrent.futures.ThreadPoolExecutor`
    when ``mesh_depth_workers`` (or heuristic default) yields more than one thread.

    When ``geometry_cache_root`` and ``slug_to_viewshed_digest`` are set, WGS84 band and per-site
    slice geometry persist under ``<cache_root>/<set_digest>/`` (see :mod:`peaky_finders.mesh_depth_cache`).
    With ``bundle_kml_overlay_digest`` set, plain stitched mesh-depth flat KML is cached next to each
    slice; with ``bundle_land_use_inputs_digest`` as well (non-``None``), eligible stitched KML is cached.
    """
    usable_paths = [Path(p).resolve() for p, _, _ in footprints if Path(p).is_file()]
    if len(usable_paths) < 2:
        return [], []

    usable: list[tuple[Path, str, str]] = [
        (Path(p).resolve(), slug, name.strip() or slug)
        for p, slug, name in footprints
        if Path(p).is_file()
    ]
    vd_row = _mesh_depth_viewshed_rows_for_cache(usable, slug_to_viewshed_digest)
    use_cache = geometry_cache_root is not None and vd_row is not None
    set_dir: Path | None = None
    if use_cache:
        sdig = mesh_depth_set_digest(
            viewshed_digests=vd_row,
            max_raster_dimension=max_raster_dimension,
        )
        set_dir = resolved_mesh_depth_set_dir(set_digest=sdig, cache_root=geometry_cache_root)

    bands_ll: dict[str, BaseGeometry]
    if use_cache and set_dir is not None:
        if not force_mesh_depth_geometry:
            kind_b, hit_b = try_read_cached_mesh_depth_bands(
                set_dir,
                expected_max_raster=max_raster_dimension,
                expected_viewshed_digests=vd_row,
            )
            if kind_b == "hit" and hit_b is not None:
                bands_ll = hit_b
                print(
                    f"Mesh coverage depth: bands cache hit ({len(usable_paths)} footprint(s))…",
                    flush=True,
                )
            else:
                print(f"Mesh coverage depth: raster {len(usable_paths)} footprint(s)…", flush=True)
                bands_ll = compute_footprint_depth_bands_wgs84(
                    usable_paths,
                    max_raster_dimension=max_raster_dimension,
                    raster_workers=mesh_depth_workers,
                )
                write_cached_mesh_depth_bands(
                    set_dir=set_dir,
                    max_raster_dimension=max_raster_dimension,
                    viewshed_digests=vd_row,
                    bands_wgs84=bands_ll,
                )
        else:
            print(f"Mesh coverage depth: raster {len(usable_paths)} footprint(s)…", flush=True)
            bands_ll = compute_footprint_depth_bands_wgs84(
                usable_paths,
                max_raster_dimension=max_raster_dimension,
                raster_workers=mesh_depth_workers,
            )
            write_cached_mesh_depth_bands(
                set_dir=set_dir,
                max_raster_dimension=max_raster_dimension,
                viewshed_digests=vd_row,
                bands_wgs84=bands_ll,
            )
    else:
        print(f"Mesh coverage depth: raster {len(usable_paths)} footprint(s)…", flush=True)
        bands_ll = compute_footprint_depth_bands_wgs84(
            usable_paths,
            max_raster_dimension=max_raster_dimension,
            raster_workers=mesh_depth_workers,
        )

    scratch_depth_dir = Path(scratch_depth_dir)
    scratch_depth_dir.mkdir(parents=True, exist_ok=True)
    want_eligible = (
        scratch_depth_eligible_dir is not None
        and eligible_ll is not None
        and not eligible_ll.is_empty
    )
    if want_eligible:
        Path(scratch_depth_eligible_dir).mkdir(parents=True, exist_ok=True)

    plain_out: list[tuple[str, str, Path, str, str]] = []
    elig_out: list[tuple[str, str, Path, str, str]] = []
    vis_field = mesh_depth_visibility_field

    n_sites = len(usable)
    print(f"  depth per-site KMLs ({n_sites} sites, {len(MESH_DEPTH_BANDS)} bands)…", flush=True)
    slice_cap = _pairwise_worker_cap(mesh_depth_workers, total_pairs=n_sites)
    slice_log_lock: threading.Lock | None = threading.Lock() if slice_cap > 1 else None
    if slice_cap > 1:
        print(f"  depth site-slice parallelism: {slice_cap} threads …", flush=True)

    site_inputs = [
        (si, gpkg, slug, folder_name)
        for si, (gpkg, slug, folder_name) in enumerate(usable, start=1)
    ]

    Row = tuple[str, str, Path, str, str]
    SliceKind = Literal["from_cache", "computed"]

    mesh_depth_slice_locks: dict[str, threading.Lock] = {}
    mesh_depth_slice_locks_mu = threading.Lock()

    def _mesh_depth_slice_lock(band_key: str, site_vd: str) -> threading.Lock:
        key = f"{band_key}\0{site_vd}"
        with mesh_depth_slice_locks_mu:
            if key not in mesh_depth_slice_locks:
                mesh_depth_slice_locks[key] = threading.Lock()
            return mesh_depth_slice_locks[key]

    def compute_oriented_plain(gpkg: Path, band_geom: BaseGeometry) -> BaseGeometry | None:
        fp = read_coverage_footprint(gpkg)
        if fp is None or fp.is_empty:
            return None
        fp0 = make_valid(fp) if not fp.is_valid else fp
        if fp0.is_empty:
            return None
        inter = fp0.intersection(band_geom)
        if inter is None or inter.is_empty:
            return None
        inter_m = make_valid(inter) if not inter.is_valid else inter
        if inter_m.is_empty:
            return None
        part = _polygonal_parts_only(inter_m)
        if part.is_empty:
            return None
        return orient_for_kml(part)

    for band in MESH_DEPTH_BANDS:
        band_geom = bands_ll.get(band)
        if band_geom is None or band_geom.is_empty:
            continue
        band_dir = scratch_depth_dir / band
        band_dir.mkdir(parents=True, exist_ok=True)
        elig_band_dir: Path | None = None
        if want_eligible and scratch_depth_eligible_dir is not None:
            elig_band_dir = Path(scratch_depth_eligible_dir) / band
            elig_band_dir.mkdir(parents=True, exist_ok=True)

        def slice_one_site(
            inp: tuple[int, Path, str, str],
        ) -> tuple[Row | None, Row | None, SliceKind]:
            si, gpkg, slug, folder_name = inp
            oriented: BaseGeometry | None = None
            sk: SliceKind

            if use_cache and set_dir is not None and vd_row is not None:
                site_vd = vd_row[si - 1]
                slice_dir = resolved_mesh_depth_slice_dir(
                    set_dir=set_dir, band=band, site_vd=site_vd
                )
                lock = _mesh_depth_slice_lock(band, site_vd)
                with lock:
                    if force_mesh_depth_geometry:
                        kind_s, geo_hit = ("miss", None)
                    else:
                        kind_s, geo_hit = try_read_cached_mesh_depth_slice(slice_dir)
                    if kind_s == "miss":
                        oriented = compute_oriented_plain(gpkg, band_geom)
                        write_cached_mesh_depth_slice(
                            slice_dir=slice_dir,
                            band=band,
                            site_vd=site_vd,
                            slice_wgs84=oriented,
                        )
                        sk = "computed"
                    elif kind_s == "empty":
                        return None, None, "from_cache"
                    else:
                        oriented = geo_hit
                        sk = "from_cache"
            else:
                oriented = compute_oriented_plain(gpkg, band_geom)
                sk = "computed"

            if oriented is None:
                return None, None, sk
            polys = _polygons_flat(oriented)
            if not polys:
                return None, None, sk

            slice_cache_dir: Path | None = None
            site_vd_for_lock = ""
            if use_cache and set_dir is not None and vd_row is not None:
                site_vd_for_lock = vd_row[si - 1]
                slice_cache_dir = resolved_mesh_depth_slice_dir(
                    set_dir=set_dir, band=band, site_vd=site_vd_for_lock
                )

            title_nl = folder_name
            doc_title = f"{folder_name} — {MESH_DEPTH_DISPLAY[band]}"
            arc = mesh_coverage_depth_site_kml_arcname(band, slug)
            out_kml = band_dir / arc.rsplit("/", 1)[-1]
            style_plain = resolved_mesh_depth_band_kml_style(kml_overlay, band, eligible=False)
            sid, gx = _PLAIN_STYLE[band]
            fp_plain: str | None = None
            plain_hit = False
            if bundle_kml_overlay_digest is not None and slice_cache_dir is not None:
                slice_stable_plain = pairwise_overlap_geometry_digest_sha256(unary_union(polys))
                fp_plain = _mesh_depth_flat_plain_fingerprint(
                    bundle_kml_overlay_digest=bundle_kml_overlay_digest,
                    band=band,
                    doc_title=doc_title,
                    gx_draw_order=gx,
                    mesh_depth_polygon_style_id=sid,
                    polygon_style_digest=_mesh_depth_layer_style_digest(style_plain),
                    site_slug=slug,
                    slice_polygon_union_stable_digest=slice_stable_plain,
                )
                lock_plain = _mesh_depth_slice_lock(band, site_vd_for_lock)
                with lock_plain:
                    plain_hit = try_read_cached_mesh_depth_flat_kml(
                        slice_dir=slice_cache_dir,
                        role="plain",
                        slug=slug,
                        fingerprint=fp_plain,
                        dest_kml=out_kml,
                    )

            if not plain_hit:
                _write_flat_pair_overlap_kml_base(out_kml, title=doc_title, polygons=polys)
                inject_peaky_polygon_kml_style(
                    out_kml,
                    style_id=sid,
                    spec=style_plain,
                    gx_draw_order=gx,
                )
                if fp_plain is not None and slice_cache_dir is not None:
                    lock_w = _mesh_depth_slice_lock(band, site_vd_for_lock)
                    with lock_w:
                        write_cached_mesh_depth_flat_kml(
                            slice_dir=slice_cache_dir,
                            role="plain",
                            slug=slug,
                            fingerprint=fp_plain,
                            source_kml=out_kml,
                        )
            plain_row: Row = (band, title_nl, out_kml, arc, vis_field(band, eligible=False))

            eligible_row: Row | None = None
            if want_eligible and elig_band_dir is not None and eligible_ll is not None:
                clipped = _clip_plain_link_overlap_to_eligible(
                    plain_link_ll=oriented,
                    eligible_ll=eligible_ll,
                )
                if clipped is not None and not clipped.is_empty:
                    polye = _polygons_flat(clipped)
                    if polye:
                        eligible_doc_title = f"{doc_title} (eligible)"
                        earc = mesh_coverage_depth_eligible_site_kml_arcname(band, slug)
                        epath = elig_band_dir / earc.rsplit("/", 1)[-1]
                        style_elig = resolved_mesh_depth_band_kml_style(
                            kml_overlay, band, eligible=True,
                        )
                        esid, egx = _ELIG_STYLE[band]
                        fp_elig: str | None = None
                        elig_hit = False
                        if (
                            bundle_kml_overlay_digest is not None
                            and bundle_land_use_inputs_digest is not None
                            and slice_cache_dir is not None
                        ):
                            elig_stable = pairwise_overlap_geometry_digest_sha256(
                                unary_union(polye),
                            )
                            fp_elig = _mesh_depth_flat_eligible_fingerprint(
                                bundle_kml_overlay_digest=bundle_kml_overlay_digest,
                                bundle_land_use_inputs_digest=bundle_land_use_inputs_digest,
                                band=band,
                                doc_title_eligible=eligible_doc_title,
                                eligible_clip_union_stable_digest=elig_stable,
                                gx_draw_order=egx,
                                mesh_depth_eligible_polygon_style_id=esid,
                                polygon_style_digest=_mesh_depth_layer_style_digest(style_elig),
                                site_slug=slug,
                            )
                            lock_e = _mesh_depth_slice_lock(band, site_vd_for_lock)
                            with lock_e:
                                elig_hit = try_read_cached_mesh_depth_flat_kml(
                                    slice_dir=slice_cache_dir,
                                    role="eligible",
                                    slug=slug,
                                    fingerprint=fp_elig,
                                    dest_kml=epath,
                                )

                        if not elig_hit:
                            _write_flat_pair_overlap_kml_base(
                                epath,
                                title=eligible_doc_title,
                                polygons=polye,
                            )
                            inject_peaky_polygon_kml_style(
                                epath,
                                style_id=esid,
                                spec=style_elig,
                                gx_draw_order=egx,
                            )
                            if fp_elig is not None and slice_cache_dir is not None:
                                lock_ew = _mesh_depth_slice_lock(band, site_vd_for_lock)
                                with lock_ew:
                                    write_cached_mesh_depth_flat_kml(
                                        slice_dir=slice_cache_dir,
                                        role="eligible",
                                        slug=slug,
                                        fingerprint=fp_elig,
                                        source_kml=epath,
                                    )
                        eligible_row = (band, title_nl, epath, earc, vis_field(band, eligible=True))

            return plain_row, eligible_row, sk

        if slice_cap <= 1:
            band_rows = [slice_one_site(inp) for inp in site_inputs]
        else:
            with ThreadPoolExecutor(max_workers=slice_cap) as ex:
                band_rows = list(ex.map(slice_one_site, site_inputs))

        n_from_cache = sum(1 for _pr, _er, sk in band_rows if sk == "from_cache")
        n_computed = sum(1 for _pr, _er, sk in band_rows if sk == "computed")
        print(
            f"  depth slice {band}: {n_from_cache} cached, {n_computed} computed (≤{n_sites} sites)…",
            flush=True,
        )
        if n_computed > 0:
            for inp, (_pr, _er, sk) in zip(site_inputs, band_rows, strict=True):
                if sk != "computed":
                    continue
                si, _gpkg, _slug, folder_name = inp
                if slice_log_lock is not None:
                    with slice_log_lock:
                        print(f"    {band} [{si}/{n_sites}] {folder_name}…", flush=True)
                else:
                    print(f"    {band} [{si}/{n_sites}] {folder_name}…", flush=True)

        for pr, er, _sk in band_rows:
            if pr is not None:
                plain_out.append(pr)
            if er is not None:
                elig_out.append(er)

    return plain_out, elig_out
