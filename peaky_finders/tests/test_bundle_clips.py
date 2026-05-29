"""Clip cache fingerprints and path planning."""

from __future__ import annotations

import re
from pathlib import Path

from fixture_paths import PEAKY_TEST_HOME, SAMPLE_PROJECT_CONFIG

from peaky_finders.bundle_build import _clip_stem, aoi_inputs_fingerprint_body, require_bundle_config
from peaky_finders.bundle_clips import (
    clip_layer_fingerprint_body,
    clip_layer_sha,
    plan_clip_build_result,
    plan_clip_layer_jobs,
    reference_entry_fingerprint_body,
    reference_entry_sha,
    resolved_clips_cache_root,
)
from peaky_finders.sites_job import BundleConfig, GeneralOverlayEntry, Preset, SimulationConfig, load_preset


def _minimal_preset() -> Preset:
    return Preset(
        simulation=SimulationConfig.model_validate({"radius_km": 10.0}),
        display={},
        sites={
            "hub": {
                "name": "Hub",
                "loc": [40.0, -119.0],
            }
        },
        maps=_minimal_preset_maps(),
        bundle=BundleConfig(),
    )


def _minimal_bundle_config() -> BundleConfig:
    return BundleConfig()


def _minimal_preset_maps() -> list:
    from peaky_finders.sites_job import ProjectMapEntry

    return [
        ProjectMapEntry(
            id="test_aoi",
            name="Test AOI",
            description="",
            type="aoi",
            path="aoi/test.gdb",
            layers=["boundary"],
        ),
        ProjectMapEntry(
            id="test_include",
            name="Test Include",
            description="",
            type="include",
            path="include/test.gdb",
            layers=["inc_layer"],
        ),
    ]


def test_clip_stem_is_stable_without_content_hash() -> None:
    stem = _clip_stem("include/SMA_WM.gdb", "Land_Status_Dis", None)
    assert stem == "include_SMA_WM_gdb__Land_Status_Dis"
    assert "__" in stem
    assert not re.search(r"__[0-9a-f]{8}$", stem)

    with_where = _clip_stem("include/foo.gdb", "layer_a", "STATUS = 'ACTIVE'")
    assert with_where.startswith("include_foo_gdb__layer_a__where_")


def test_plan_clip_layer_jobs_lists_gdb_input_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "aoi" / "test.gdb").mkdir(parents=True)
    (data / "aoi" / "test.gdb" / "boundary.gdbtable").write_bytes(b"a")
    (data / "include" / "test.gdb").mkdir(parents=True)
    (data / "include" / "test.gdb" / "inc_layer.gdbtable").write_bytes(b"b")
    clips = resolved_clips_cache_root(tmp_path)
    preset = _minimal_preset()
    layers, _ = plan_clip_layer_jobs(preset=preset, data_dir=data, clips_root=clips)
    aoi = next(layer for layer in layers if layer.role == "aoi")
    assert aoi.gpkg == clips / "layer_jobs" / "aoi" / "aoi_test_gdb__boundary" / "clip.gpkg"
    assert (data / "aoi" / "test.gdb" / "boundary.gdbtable") in aoi.input_files


def test_clip_layer_sha_stable_for_same_inputs() -> None:
    mask = "format=bundle_aoi_inputs/v1\nconfig\n{}\n"
    body = clip_layer_fingerprint_body(
        role="include",
        preset_path="include/test.gdb",
        resolved=Path("/data/include/test.gdb"),
        layer="inc_layer",
        where=None,
        mask_body=mask,
        gdb_tree="boundary.gdbtable\t1\t100\n",
    )
    assert clip_layer_sha(body) == clip_layer_sha(body)


def test_clip_layer_sha_changes_with_mask() -> None:
    base = dict(
        role="include",
        preset_path="include/test.gdb",
        resolved=Path("/data/include/test.gdb"),
        layer="inc_layer",
        where=None,
        gdb_tree="t\n",
    )
    a = clip_layer_sha(clip_layer_fingerprint_body(**base, mask_body="mask-a\n"))
    b = clip_layer_sha(clip_layer_fingerprint_body(**base, mask_body="mask-b\n"))
    assert a != b


def test_reference_entry_sha_stable() -> None:
    ent = GeneralOverlayEntry(
        id="districts",
        path="reference/foo.gdb",
        layers=["layer_a"],
    )
    body = reference_entry_fingerprint_body(
        ent,
        Path("/data"),
        aoi_mask_sha="abc123",
        gdb_tree="x.gdbtable\t1\t2\n",
    )
    assert reference_entry_sha(body) == reference_entry_sha(body)


def test_plan_clip_build_result_paths_under_clips_root(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "aoi" / "test.gdb").mkdir(parents=True)
    (data / "aoi" / "test.gdb" / "x.gdbtable").write_bytes(b"")
    (data / "include" / "test.gdb").mkdir(parents=True)
    (data / "include" / "test.gdb" / "y.gdbtable").write_bytes(b"")
    clips = resolved_clips_cache_root(tmp_path)
    preset = _minimal_preset()
    planned = plan_clip_build_result(preset=preset, data_dir=data, clips_root=clips)
    assert planned.eligible_gpkg == clips / "eligible" / "eligible_land_use.gpkg"
    assert len(planned.aoi_sha) == 16
    assert len(planned.eligible_sha) == 16


def test_plan_with_sample_project_preset() -> None:
    preset = load_preset(SAMPLE_PROJECT_CONFIG)
    require_bundle_config(preset)
    data_dir = SAMPLE_PROJECT_CONFIG.parent / "data"
    clips = resolved_clips_cache_root(PEAKY_TEST_HOME / "share")
    planned = plan_clip_build_result(preset=preset, data_dir=data_dir, clips_root=clips)
    mask = aoi_inputs_fingerprint_body(preset, data_dir)
    assert mask.startswith("format=bundle_aoi_inputs/v1")
    assert planned.eligible_gpkg.parent.name == "eligible"
