"""Smoke tests for build DAG (topo ordering, DEM sentinel)."""


from __future__ import annotations

from pathlib import Path

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.build_configure import configure_preset_build
from peaky_finders.build_graph import build_target_graph, dem_bulk_stamp_path, topo_sort
from peaky_finders.sites_job import load_preset


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


