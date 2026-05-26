"""Gather persisted mesh-layer KML paths under preset ``build/mesh`` for aggregate KMZ assembly."""

from __future__ import annotations

import itertools
from pathlib import Path

from peaky_finders.mesh_coverage_depth import MESH_DEPTH_BANDS, MESH_DEPTH_DISPLAY, mesh_depth_visibility_field
from peaky_finders.mesh_depth_store import mesh_depth_stitched_flat_kml_path, resolved_mesh_depth_set_dir, resolved_mesh_depth_slice_dir
from peaky_finders.mesh_pairwise_store import (
    PAIRWISE_FLAT_ELIG_KML,
    PAIRWISE_FLAT_PLAIN_KML,
    resolved_mesh_pairwise_pair_dir,
)
from peaky_finders.path_labels import mesh_depth_network_rel_dir
from peaky_finders.sites_job import (
    Preset,
    mesh_coverage_depth_eligible_site_kml_arcname,
    mesh_coverage_depth_site_kml_arcname,
    mesh_pairwise_eligible_kml_arcname,
    mesh_pairwise_kml_arcname,
)


def collect_mesh_kml_for_aggregate_kmz(
    *,
    job: Preset,
    pairwise_geom_root: Path,
    mesh_depth_root: Path,
    slug_to_viewshed_digest: dict[str, str],
    max_raster_dimension: int,
) -> tuple[
    list[tuple[str, str, Path, str, str]],
    list[tuple[str, str, Path, str, str]],
    list[tuple[str, Path, str]],
    list[tuple[str, Path, str]],
]:
    """Return ``(mesh_depth_plain, mesh_depth_elig, pairwise_plain, pairwise_elig)`` rows for zip + doc.kml."""

    plain_depth: list[tuple[str, str, Path, str, str]] = []
    elig_depth: list[tuple[str, str, Path, str, str]] = []
    pairwise_plain: list[tuple[str, Path, str]] = []
    pairwise_elig: list[tuple[str, Path, str]] = []

    rel = mesh_depth_network_rel_dir(max_raster_dimension=max_raster_dimension)
    set_dir = resolved_mesh_depth_set_dir(rel_label=rel, cache_root=mesh_depth_root)

    slug_order = sorted(job.sites.keys())
    for slug in slug_order:
        vd = slug_to_viewshed_digest.get(slug)
        ent = job.sites[slug]
        title_nl = ent.name.strip() or slug
        for band in MESH_DEPTH_BANDS:
            if vd is None:
                continue
            slice_dir = resolved_mesh_depth_slice_dir(set_dir=set_dir, band=band, site_vd=vd)
            kp = mesh_depth_stitched_flat_kml_path(slice_dir, role="plain", slug=slug)
            if kp.is_file():
                arc = mesh_coverage_depth_site_kml_arcname(band, slug)
                plain_depth.append(
                    (band, title_nl, kp, arc, mesh_depth_visibility_field(band, eligible=False))
                )

            kp_e = mesh_depth_stitched_flat_kml_path(slice_dir, role="eligible", slug=slug)
            if kp_e.is_file():
                earc = mesh_coverage_depth_eligible_site_kml_arcname(band, slug)
                elig_depth.append(
                    (band, title_nl, kp_e, earc, mesh_depth_visibility_field(band, eligible=True))
                )

    for sa, sb in itertools.combinations(slug_order, 2):
        pdir = resolved_mesh_pairwise_pair_dir(slug_a=sa, slug_b=sb, cache_root=pairwise_geom_root)
        pp = pdir / PAIRWISE_FLAT_PLAIN_KML
        if pp.is_file():
            arc_p = mesh_pairwise_kml_arcname(sa, sb)
            name_a = job.sites[sa].name.strip() or sa
            name_b = job.sites[sb].name.strip() or sb
            pairwise_plain.append((f"{name_a} <-> {name_b}", pp, arc_p))
        pe = pdir / PAIRWISE_FLAT_ELIG_KML
        if pe.is_file():
            arc_e = mesh_pairwise_eligible_kml_arcname(sa, sb)
            name_a = job.sites[sa].name.strip() or sa
            name_b = job.sites[sb].name.strip() or sb
            pairwise_elig.append((f"{name_a} <-> {name_b}", pe, arc_e))

    return plain_depth, elig_depth, pairwise_plain, pairwise_elig
