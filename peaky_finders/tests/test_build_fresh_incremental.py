"""Digest-first incremental build staleness (add-site without clip churn)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.build_configure import configure_preset_build
from peaky_finders.build_executor import target_stale
from peaky_finders.build_fresh_checks import bundle_resolve_fresh
from peaky_finders.build_graph import (
    build_target_graph,
    pairwise_target_id,
    viewshed_mtime_prereqs,
)
from peaky_finders.bundle_clips import write_bundle_resolve
from peaky_finders.mesh_pairwise_store import write_cached_pair_overlap_geometry
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import load_preset
from peaky_finders.viewshed_workspace import viewshed_workspace_digest


def _touch_future(path: Path) -> None:
    p = path.expanduser().resolve()
    t = time.time() + 60.0
    os.utime(p, (t, t))


def _composite_sha(plan, role: str) -> str:
    for c in plan.composites:
        if c.role == role:
            return c.sha
    raise KeyError(role)


def _write_bundle_resolve_for_plan(plan) -> None:
    write_bundle_resolve(
        plan.bundle_dir,
        clips_root=plan.clips_root,
        aoi_sha=_composite_sha(plan, "aoi"),
        include_sha=_composite_sha(plan, "include"),
        exclude_sha=_composite_sha(plan, "exclude"),
        eligible_sha=_composite_sha(plan, "eligible"),
        reference={ref.entry_id: ref.sha for ref in plan.references},
    )


def test_build_graph_viewshed_mtime_prereqs_exclude_preset_and_resolve(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    preset_f = preset_path.resolve()
    resolve_f = plan.bundle_resolve.resolve()

    for ws in plan.viewshed_workspaces:
        mq = viewshed_mtime_prereqs(plan, ws)
        assert preset_f not in mq
        assert resolve_f not in mq


def test_bundle_resolve_fresh_when_preset_yaml_touched(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)

    _write_bundle_resolve_for_plan(plan)
    _touch_future(preset_path)

    assert target_stale(plan, preset, nodes["bundle:resolve"]) is False


def test_bundle_resolve_stale_when_clip_sha_changes(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)

    _write_bundle_resolve_for_plan(plan)
    rp = plan.bundle_resolve.resolve()
    assert bundle_resolve_fresh(
        rp,
        clips_root_expected=plan.clips_root,
        aoi_sha=_composite_sha(plan, "aoi"),
        include_sha=_composite_sha(plan, "include"),
        exclude_sha=_composite_sha(plan, "exclude"),
        eligible_sha=_composite_sha(plan, "eligible"),
        reference={ref.entry_id: ref.sha for ref in plan.references},
    )

    assert not bundle_resolve_fresh(
        rp,
        clips_root_expected=plan.clips_root,
        aoi_sha="deadbeef",
        include_sha=_composite_sha(plan, "include"),
        exclude_sha=_composite_sha(plan, "exclude"),
        eligible_sha=_composite_sha(plan, "eligible"),
        reference={ref.entry_id: ref.sha for ref in plan.references},
    )


def test_viewshed_request_fresh_when_preset_yaml_touched(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)

    ws = plan.viewshed_workspaces[0]
    slug = ws.site_slugs[0]
    site = preset.sites[slug]
    req = preset_to_request(preset, float(site.lat), float(site.lon))
    assert viewshed_workspace_digest(request=req) == ws.digest

    ws.request_json.parent.mkdir(parents=True, exist_ok=True)
    ws.request_json.write_text(req.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    _touch_future(preset_path)

    node = nodes[f"viewshed:{ws.digest}:request"]
    assert target_stale(plan, preset, node) is False


def test_mesh_depth_fresh_when_preset_yaml_touched(peaky_test_home: Path) -> None:
    import numpy as np
    from rasterio.transform import from_bounds

    from peaky_finders.mesh_depth_store import write_cached_mesh_depth_bands, write_mesh_depth_grid

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    if plan.mesh_depth_complete is None:
        pytest.skip("sample preset has no mesh depth target")

    nodes = build_target_graph(plan, preset)
    sdir = plan.mesh_depth_complete.parent
    vds = []
    slugs = []
    for slug in sorted(preset.sites.keys()):
        site = preset.sites[slug]
        vd = viewshed_workspace_digest(request=preset_to_request(preset, float(site.lat), float(site.lon)))
        vds.append(vd)
        slugs.append(slug)

    write_cached_mesh_depth_bands(
        set_dir=sdir,
        max_raster_dimension=4096,
        viewshed_digests=vds,
        site_slugs=slugs,
        bands_wgs84={},
    )
    write_mesh_depth_grid(
        set_dir=sdir,
        acc=np.zeros((8, 8), dtype=np.uint32),
        transform=from_bounds(-120, 39, -119, 40, 8, 8),
        bounds=(-120.0, 39.0, -119.0, 40.0),
    )
    _touch_future(preset_path)

    assert target_stale(plan, preset, nodes["mesh:depth"]) is False


def test_mesh_pair_fresh_when_preset_yaml_touched(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    if not plan.mesh_pairs:
        pytest.skip("sample preset has no mesh pairs")

    nodes = build_target_graph(plan, preset)
    pair = plan.mesh_pairs[0]
    vd_a = viewshed_workspace_digest(
        request=preset_to_request(preset, float(preset.sites[pair.slug_a].lat), float(preset.sites[pair.slug_a].lon))
    )
    vd_b = viewshed_workspace_digest(
        request=preset_to_request(preset, float(preset.sites[pair.slug_b].lat), float(preset.sites[pair.slug_b].lon))
    )

    write_cached_pair_overlap_geometry(
        pair_dir=pair.complete_json.parent,
        vd_a=vd_a,
        vd_b=vd_b,
        overlap_wgs84=None,
    )
    _touch_future(preset_path)

    tid = pairwise_target_id(pair.slug_a, pair.slug_b)
    assert target_stale(plan, preset, nodes[tid]) is False
