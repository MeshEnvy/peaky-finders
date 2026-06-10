"""Smoke tests for build DAG (topo ordering, DEM sentinel)."""


from __future__ import annotations

import shutil
from pathlib import Path

from fixture_paths import PEAKY_TEST_HOME, SAMPLE_PROJECT_CONFIG
from peaky_finders.build_configure import configure_preset_build
from peaky_finders.build_executor import target_stale
from peaky_finders.build_graph import build_target_graph, dem_bulk_stamp_path, mesh_links_mtime_prereqs, topo_sort
from peaky_finders.preset_stamps import stamp_path, write_stamp
from peaky_finders.sites_job import (
    MeshConfig,
    load_preset,
    resolved_mesh_depth_enabled,
    resolved_mesh_pairwise_enabled,
)


def _sample_preset_with_mesh_coverage(tmp_path: Path, *, pairwise: bool, depth: bool) -> Path:
    proj = tmp_path / "sample-project"
    shutil.copytree(PEAKY_TEST_HOME / "projects" / "sample", proj)
    config_path = proj / "config.yaml"
    text = config_path.read_text(encoding="utf-8")
    block = (
        "mesh:\n"
        f"  pairwise: {'true' if pairwise else 'false'}\n"
        f"  depth: {'true' if depth else 'false'}\n"
    )
    patched = text.replace("\nsites:", f"\n{block}sites:", 1)
    config_path.write_text(patched, encoding="utf-8")
    return config_path


def test_mesh_coverage_disable_helpers() -> None:
    assert resolved_mesh_pairwise_enabled(None) is True
    assert resolved_mesh_depth_enabled(None) is True
    cfg = MeshConfig(pairwise=False, depth=True)
    assert resolved_mesh_pairwise_enabled(cfg) is False
    assert resolved_mesh_depth_enabled(cfg) is True


def test_build_plan_mesh_coverage_flags(tmp_path: Path, peaky_test_home: Path) -> None:
    pairwise_only = _sample_preset_with_mesh_coverage(tmp_path / "pairwise", pairwise=True, depth=False)
    depth_only = _sample_preset_with_mesh_coverage(tmp_path / "depth", pairwise=False, depth=True)
    neither = _sample_preset_with_mesh_coverage(tmp_path / "neither", pairwise=False, depth=False)

    pair_plan = configure_preset_build(preset_path=pairwise_only)
    assert pair_plan.mesh_pairs
    assert pair_plan.mesh_depth_complete is None
    assert pair_plan.mesh_links_kml is not None

    depth_plan = configure_preset_build(preset_path=depth_only)
    assert depth_plan.mesh_pairs == ()
    assert depth_plan.mesh_depth_complete is not None
    assert depth_plan.mesh_links_kml is not None

    off_plan = configure_preset_build(preset_path=neither)
    assert off_plan.mesh_pairs == ()
    assert off_plan.mesh_depth_complete is None
    assert off_plan.mesh_links_kml is not None


def test_mesh_links_tracks_topology_stamp(tmp_path: Path) -> None:
    preset_path = _sample_preset_with_mesh_coverage(tmp_path, pairwise=False, depth=False)
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)

    assert "mesh:links" in nodes
    assert "stamp:topology" in nodes["mesh:links"].depends_on
    topo_stamp = stamp_path(preset_path, "topology")
    assert topo_stamp.resolve() in mesh_links_mtime_prereqs(plan, preset)


def test_mesh_links_stale_after_site_list_change(tmp_path: Path) -> None:
    preset_path = _sample_preset_with_mesh_coverage(tmp_path, pairwise=False, depth=False)
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)
    links_node = nodes["mesh:links"]
    links_kml = plan.mesh_links_kml
    assert links_kml is not None

    links_kml.parent.mkdir(parents=True, exist_ok=True)
    links_kml.write_text("<kml/>", encoding="utf-8")
    write_stamp("topology", preset_path, quiet=True)

    assert target_stale(plan, preset, links_node)


def test_build_graph_depth_without_pairwise(tmp_path: Path, peaky_test_home: Path) -> None:
    preset_path = _sample_preset_with_mesh_coverage(tmp_path, pairwise=False, depth=True)
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)

    pair_ids = [k for k in nodes if k.startswith("mesh:pair:")]
    assert pair_ids == []
    assert "mesh:depth" in nodes
    depth_deps = set(nodes["mesh:depth"].depends_on)
    assert not any(d.startswith("mesh:pair:") for d in depth_deps)
    assert any(d.endswith(":footprint") for d in depth_deps)


def test_dem_bulk_stamp_and_topo_order(peaky_test_home: Path) -> None:


    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()

    preset = load_preset(preset_path)

    plan = configure_preset_build(preset_path=preset_path)




    nodes = build_target_graph(plan, preset)



    ids = topo_sort(nodes)




    assert "dem:bulk" in nodes


    dn = nodes["dem:bulk"]


    stamp = dem_bulk_stamp_path(plan).resolve()


    assert dn.outputs == (stamp,)



    elig_i = ids.index("bundle:eligible")


    dem_i = ids.index("dem:bulk")



    kmz_i = ids.index("kmz:out")


    assert elig_i < dem_i < kmz_i


    dem_like = [x for x in ids if x.startswith("dem:")]


    assert dem_like == ["dem:bulk"]


def test_viewshed_coverage_depends_on_request(peaky_test_home: Path) -> None:
    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)
    for ws in plan.viewshed_workspaces:
        coverage = nodes[f"viewshed:{ws.digest}:coverage"]
        request = nodes[f"viewshed:{ws.digest}:request"]
        assert coverage.depends_on == (f"viewshed:{ws.digest}:request",)
        assert request.outputs == (ws.request_json.resolve(),)
    ids = topo_sort(nodes)
    for ws in plan.viewshed_workspaces:
        assert ids.index(f"viewshed:{ws.digest}:request") < ids.index(f"viewshed:{ws.digest}:coverage")


