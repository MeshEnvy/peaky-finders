"""Tests for ``peaky generate-makefile`` / configure-style Makefile generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fixture_paths import PEAKY_TEST_HOME, SAMPLE_PROJECT_CONFIG
from peaky_finders.build_configure import ConfigureError, configure_preset_build
from peaky_finders.generate_makefile_cli import run_generate_makefile
from peaky_finders.makefile_gen import generate_makefile_text

_MINIMAL_SIM = """
simulation:
  provider: los
  radius_km: 10.0
  modem_presets:
    meshcore-us:
      frequency_mhz: 910.525
      bandwidth_khz: 62.5
      spreading_factor: 7
      coding_rate: 5
      implementation_margin_db: 3.0
      power_dbm: 22.0
      sensitivity_dbm: -121.0
  environment_presets:
    test-desert:
      climate: desert
      polarization: vertical
      clutter_height_m: 1.0
  modem: meshcore-us
  environment: test-desert
  transmitter:
    height_m: 2.0
    gain_dbi: 3.0
    loss_db: 2.0
  receiver:
    height_m: 2.0
    gain_dbi: 3.0
    loss_db: 2.0
display:
  colormap: plasma
  min_dbm: -130.0
  max_dbm: -80.0
"""


@pytest.fixture
def peaky_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.delenv("PEAKY_SHARE", raising=False)
    return PEAKY_TEST_HOME


def test_configure_viewshed_paths_without_bundle(peaky_env: Path, tmp_path: Path) -> None:
    preset_path = tmp_path / "solo.yaml"
    preset_path.write_text(
        _MINIMAL_SIM
        + """
sites:
  only:
    name: Only Site
    loc: [39.0, -119.0]
""",
        encoding="utf-8",
    )
    plan = configure_preset_build(preset_path=preset_path, make_root=tmp_path)
    assert plan.has_bundle is False
    assert len(plan.viewshed_workspaces) == 1
    ws = plan.viewshed_workspaces[0]
    assert ws.output_ppm.name == "output.ppm"
    assert ws.site_slugs == ("only",)
    assert ws.splat_png.name == "splat.png"
    assert ws.workdir.parent.name == "viewsheds"
    assert ws.workdir.name == "only"


def test_generate_makefile_default_make_root_is_preset_directory(
    peaky_env: Path, tmp_path: Path
) -> None:
    proj = tmp_path / "nevada-ish"
    proj.mkdir()
    p = proj / "config.yaml"
    p.write_text(
        _MINIMAL_SIM
        + """
sites:
  only:
    name: Hub
    loc: [39.5, -119.8]
""",
        encoding="utf-8",
    )
    body = generate_makefile_text(preset_path=p, make_root=None)
    assert "PRESET ?= config.yaml" in body.splitlines()


def test_configure_fails_when_bundle_gdb_missing(tmp_path: Path) -> None:
    preset_path = tmp_path / "bad-bundle.yaml"
    preset_path.write_text(
        _MINIMAL_SIM
        + """
bundle:
  inputs_root: data
  aoi:
    - path: aoi/missing.gdb
      layers:
        - name: boundary
  include:
    - path: include/missing.gdb
      layers:
        - name: inc_layer
  exclude: []
sites:
  only:
    name: Only Site
    loc: [39.0, -119.0]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigureError, match="GDB path not found"):
        configure_preset_build(preset_path=preset_path, make_root=tmp_path)


def test_generate_makefile_bundle_include_depends_on_composite_aoi(peaky_env: Path) -> None:
    """Include/exclude masking reads composite AOI GeoPackage (:func:`bundle_commands.run_bundle_clip`)."""

    text = generate_makefile_text(preset_path=SAMPLE_PROJECT_CONFIG, make_root=PEAKY_TEST_HOME)
    assert "BUNDLE_DIR ?=" not in text
    resolve_line = next(ln for ln in text.splitlines() if ln.startswith("BUNDLE_RESOLVE ?="))
    assert resolve_line.endswith("/bundle/resolve.json")
    assert len(resolve_line) < 80
    incl = next(ln for ln in text.splitlines() if ln.startswith("$(CLIP_INCLUDE_") and ": " in ln)
    assert "$(COMPOSITE_AOI)" in incl
    assert "$(PRESET)" in incl
    assert ".gdbtable" in incl or ".gpkg" in incl

    exclude_lines = [
        ln for ln in text.splitlines() if ln.startswith("$(CLIP_EXCLUDE_") and ": " in ln
    ]
    if exclude_lines:
        assert all("$(COMPOSITE_AOI)" in ln for ln in exclude_lines)


def test_generate_makefile_layer_job_paths_have_no_hash_suffix(peaky_env: Path) -> None:
    text = generate_makefile_text(preset_path=SAMPLE_PROJECT_CONFIG, make_root=PEAKY_TEST_HOME)
    clip_defs = [ln for ln in text.splitlines() if ln.startswith("CLIP_") and "?=" in ln]
    assert clip_defs
    hash_suffix = re.compile(r"__[0-9a-f]{8}/")
    for ln in clip_defs:
        path = ln.split("?=", 1)[1].strip()
        assert not hash_suffix.search(f"{path}/"), ln

    aoi_dep = next(ln for ln in text.splitlines() if ln.startswith("$(CLIP_AOI_") and ": " in ln)
    assert ".gdbtable" in aoi_dep or ".gpkg" in aoi_dep
    stamp_aoi = next(ln for ln in text.splitlines() if ln.startswith("$(STAMP_BUNDLE_AOI):"))
    assert ".gdbtable" in stamp_aoi or ".gpkg" in stamp_aoi


def test_generate_makefile_viewshed_prereqs_use_dem_files_not_phony_dem_tiles(peaky_env: Path) -> None:
    text = generate_makefile_text(preset_path=SAMPLE_PROJECT_CONFIG, make_root=PEAKY_TEST_HOME)
    request_line = next(
        ln for ln in text.splitlines() if ln.startswith("$(VIEWSHED_") and "_REQUEST):" in ln
    )
    ppm_line = next(
        ln for ln in text.splitlines() if ln.startswith("$(VIEWSHED_") and "_PPM):" in ln
    )
    assert "$(DEM_" in request_line or " dem-tiles" in request_line
    assert "$(DEM_" in ppm_line or " dem-tiles" in ppm_line
    assert "_REQUEST)" not in ppm_line
    phony_line = next(ln for ln in text.splitlines() if ln.startswith(".PHONY:"))
    assert "dem-tiles" not in phony_line


def test_generate_makefile_dem_targets_pass_tile(peaky_env: Path) -> None:
    text = generate_makefile_text(preset_path=SAMPLE_PROJECT_CONFIG, make_root=PEAKY_TEST_HOME)
    dem_defs = [ln for ln in text.splitlines() if ln.startswith("DEM_") and "?=" in ln]
    if dem_defs:
        dem_cmds = [
            ln
            for ln in text.splitlines()
            if "bundle dem" in ln and ln.startswith("\t")
        ]
        assert dem_cmds and all("--tile " in ln for ln in dem_cmds), "\n".join(dem_cmds)


def test_generate_makefile_mesh_lists_bundle_dem_workspace_rasters(peaky_env: Path) -> None:
    text = generate_makefile_text(preset_path=SAMPLE_PROJECT_CONFIG, make_root=PEAKY_TEST_HOME)
    mesh_pair_line = next(
        ln
        for ln in text.splitlines()
        if ln.startswith("$(MESH_PAIR_") and not ln.strip().startswith("\t")
    )
    assert "$(BUNDLE_RESOLVE)" in mesh_pair_line
    assert "$(STAMP_BUNDLE_KML_OVERLAY)" in mesh_pair_line
    assert "$(STAMP_BUNDLE_MESH_COVERAGE)" in mesh_pair_line
    assert "$(DEM_" in mesh_pair_line or " dem-tiles" in mesh_pair_line
    assert "_PNG)" in mesh_pair_line
    assert "_GPKG)" in mesh_pair_line


def test_generate_makefile_text_lists_file_targets(tmp_path: Path) -> None:
    preset_path = tmp_path / "solo.yaml"
    preset_path.write_text(
        _MINIMAL_SIM
        + """
sites:
  hub:
    name: Hub
    loc: [40.42619, -119.5]
  peer:
    name: Peer
    loc: [39.90951, -119.4]
""",
        encoding="utf-8",
    )
    text = generate_makefile_text(preset_path=preset_path, make_root=tmp_path)
    assert "VIEWSHED_" in text and "_PNG ?=" in text
    assert "_REQUEST ?=" in text and "_PPM ?=" in text
    assert "--workspace-only --phase request" in text
    assert "--workspace-only --phase docker" in text
    assert "--workspace-only --phase raster" in text
    assert "--workspace-only --phase footprint" in text
    assert "viewshed/hub:" in text
    assert "viewshed/peer:" in text
    assert "$(VIEWSHED_HUB_PNG) $(VIEWSHED_HUB_GPKG)" in text
    assert "$(KMZ):" in text
    assert "$(PLSS_CACHE):" in text
    assert "$(STAMP_TOPOLOGY)" in text.split("$(PLSS_CACHE):", 1)[1].split("\n", 1)[0]
    assert "MESH_PAIR_" not in text


def test_generate_makefile_omits_mesh_for_single_site(tmp_path: Path) -> None:
    preset_path = tmp_path / "solo.yaml"
    preset_path.write_text(
        _MINIMAL_SIM
        + """
sites:
  only:
    name: Only Site
    loc: [39.0, -119.0]
""",
        encoding="utf-8",
    )
    text = generate_makefile_text(preset_path=preset_path, make_root=tmp_path)
    assert "mesh pairwise" not in text
    assert "MESH_PAIR_" not in text


def test_run_generate_makefile_writes_default_path(tmp_path: Path) -> None:
    out_dir = tmp_path / "proj"
    out_dir.mkdir()
    preset = out_dir / "config.yaml"
    preset.write_text(
        _MINIMAL_SIM
        + """
sites:
  only:
    name: Only Site
    loc: [39.0, -119.0]
""",
        encoding="utf-8",
    )

    from argparse import Namespace

    args = Namespace(
        preset_yaml=preset,
        output=None,
        stdout=False,
        make_root=tmp_path,
        peaky_cmd="peaky",
    )
    assert run_generate_makefile(args) == 0
    makefile = out_dir / "Makefile"
    assert makefile.is_file()
    body = makefile.read_text(encoding="utf-8")
    assert "$(KMZ):" in body
    assert "VIEWSHED_" in body
