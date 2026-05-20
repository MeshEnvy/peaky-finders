"""Clip cache fingerprints and path planning."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.bundle_build import aoi_inputs_fingerprint_body, require_bundle_config
from peaky_finders.bundle_clips import (
    clip_layer_fingerprint_body,
    clip_layer_sha,
    plan_clip_build_result,
    reference_entry_fingerprint_body,
    reference_entry_sha,
    resolved_clips_cache_root,
)
from peaky_finders.sites_job import BundleReferenceLayerEntry
from peaky_finders.sites_job import BundleConfig, load_preset


def _minimal_bundle_config() -> BundleConfig:
    return BundleConfig.model_validate(
        {
            "aoi": [
                {
                    "path": "aoi/test.gdb",
                    "layers": [{"name": "boundary"}],
                }
            ],
            "include": [
                {
                    "path": "include/test.gdb",
                    "layers": [{"name": "inc_layer"}],
                }
            ],
            "exclude": [],
        }
    )


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
    ent = BundleReferenceLayerEntry(
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
    plc = _minimal_bundle_config()
    planned = plan_clip_build_result(plc=plc, data_dir=data, clips_root=clips)
    assert planned.eligible_gpkg == clips / "eligible" / planned.eligible_sha / "eligible_land_use.gpkg"
    assert len(planned.aoi_sha) == 16
    assert len(planned.eligible_sha) == 16


def test_plan_with_repo_nevada_preset() -> None:
    repo = Path(__file__).resolve().parents[1]
    nevada = repo / "projects" / "nevada" / "config.yaml"
    if not nevada.is_file():
        nevada = repo / "nevada.yaml"
    if not nevada.is_file():
        return
    preset = load_preset(nevada)
    plc = require_bundle_config(preset)
    data_dir = (
        (nevada.parent / "data").resolve()
        if preset.bundle is not None and preset.bundle.inputs_root
        else repo / "data"
    )
    clips = resolved_clips_cache_root(repo / ".cache")
    planned = plan_clip_build_result(plc=plc, data_dir=data_dir, clips_root=clips)
    mask = aoi_inputs_fingerprint_body(plc, data_dir)
    assert mask.startswith("format=bundle_aoi_inputs/v1")
    assert planned.eligible_gpkg.parent.name == planned.eligible_sha
