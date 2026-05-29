"""DEBUG=1 wiring for Peaky Web verbose tracing."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from peaky_finders.web.settings import debug_enabled, log_debug_mode_at_boot, stderr_verbose_log
from peaky_finders.web.viewshed_service import _run_site_viewshed_build


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_debug_enabled_truth_table(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: bool,
) -> None:
    if value is None:
        monkeypatch.delenv("DEBUG", raising=False)
    else:
        monkeypatch.setenv("DEBUG", value)
    assert debug_enabled() is expected


def test_stderr_verbose_log_when_debug(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DEBUG", raising=False)
    assert stderr_verbose_log() is None

    monkeypatch.setenv("DEBUG", "1")
    vlog = stderr_verbose_log()
    assert vlog is not None
    vlog("hello debug")
    assert capsys.readouterr().out == "hello debug\n"


def test_log_debug_mode_at_boot(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DEBUG", raising=False)
    log_debug_mode_at_boot()
    assert capsys.readouterr().out == ""

    monkeypatch.setenv("DEBUG", "1")
    log_debug_mode_at_boot()
    assert capsys.readouterr().out == "peaky web: DEBUG=1 (verbose operation logging)\n"


def test_site_viewshed_build_verbose_when_debug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEBUG", "1")
    workdir = tmp_path / "abc"
    workdir.mkdir()
    (workdir / "splat.png").write_bytes(b"x")
    captured: list[bool] = []

    with patch(
        "peaky_finders.web.viewshed_service.run_viewshed_workspace",
        side_effect=lambda **kwargs: captured.append(bool(kwargs.get("coverage_verbose"))),
    ), patch(
        "peaky_finders.web.viewshed_service.preset_path_for_project",
        return_value=Path("/tmp/config.yaml"),
    ), patch(
        "peaky_finders.web.viewshed_service.load_preset",
    ) as load_preset, patch(
        "peaky_finders.web.viewshed_service._viewsheds_root_for_preset",
        return_value=tmp_path,
    ), patch(
        "peaky_finders.web.viewshed_service.resolved_viewshed_workdir_for_coords",
        return_value=workdir,
    ):
        preset = load_preset.return_value
        preset.sites = {"foo": SimpleNamespace(name="Foo", lat=39.5, lon=-115.5)}
        _run_site_viewshed_build(project_slug="demo", site_slug="foo")

    assert captured == [True]


def test_run_maps_rebuild_receives_verbose_log_when_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "1")
    from peaky_finders.web import maps_build_scheduler as sched

    captured: dict[str, object] = {}

    def fake_run_maps_rebuild(slug: str, *, verbose_log=None, progress_log=None):
        captured["slug"] = slug
        captured["verbose_log"] = verbose_log
        captured["progress_log"] = progress_log

    with patch.object(sched, "run_maps_rebuild", side_effect=fake_run_maps_rebuild), patch.object(
        sched, "_notify_build_status"
    ), patch.object(sched, "publish_layer_phase"), patch.object(sched, "publish_catalog_refresh"):
        sched._run_clips_job("nevada")

    assert captured["slug"] == "nevada"
    assert captured["verbose_log"] is not None
    assert captured["progress_log"] is not None
    captured["verbose_log"]("maps verbose")  # type: ignore[operator]


def test_maintenance_planner_logs_unavailable_mesh_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEBUG", "1")
    from peaky_finders.web import maps_build_scheduler as sched
    from types import SimpleNamespace

    preset = SimpleNamespace(bundle=SimpleNamespace(mesh_coverage=object()))

    with patch.object(sched, "clips_need_rebuild", return_value=False), patch.object(
        sched, "mesh_need_rebuild", return_value=False
    ), patch.object(sched, "auto_rebuild_enabled", return_value=True), patch.object(
        sched, "_preset_path", return_value=Path("/tmp/nevada/config.yaml")
    ), patch.object(sched, "load_preset", return_value=preset), patch.object(
        sched, "resolved_mesh_pairwise_enabled", return_value=False
    ), patch.object(sched, "resolved_mesh_depth_enabled", return_value=False), patch.object(
        sched, "_notify_build_status"
    ):
        sched._maintenance_planner("nevada")

    out = capsys.readouterr().out
    assert "mesh=unavailable (pairwise and depth disabled in preset)" in out


def test_maintenance_planner_logs_current_artifacts_when_debug(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEBUG", "1")
    from peaky_finders.web import maps_build_scheduler as sched
    from types import SimpleNamespace

    preset = SimpleNamespace(bundle=SimpleNamespace(mesh_coverage=object()))

    with patch.object(sched, "clips_need_rebuild", return_value=False), patch.object(
        sched, "mesh_need_rebuild", return_value=False
    ), patch.object(sched, "auto_rebuild_enabled", return_value=True), patch.object(
        sched, "_preset_path", return_value=Path("/tmp/nevada/config.yaml")
    ), patch.object(sched, "load_preset", return_value=preset), patch.object(
        sched, "resolved_mesh_pairwise_enabled", return_value=True
    ), patch.object(sched, "resolved_mesh_depth_enabled", return_value=False), patch.object(
        sched, "_notify_build_status"
    ):
        sched._maintenance_planner("nevada")

    out = capsys.readouterr().out
    assert "maps maintenance (nevada): scan start" in out
    assert "clips=current" in out
    assert "mesh=current" in out
    assert "nothing to rebuild" in out


def test_schedule_all_projects_maps_maintenance_logs_when_debug(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEBUG", "1")
    from peaky_finders.web import maps_build_scheduler as sched

    with patch("peaky_finders.web.projects.list_projects", return_value=[{"slug": "nevada"}]), patch.object(
        sched, "schedule_maps_maintenance"
    ) as schedule:
        sched.schedule_all_projects_maps_maintenance()

    schedule.assert_called_once_with("nevada")
    out = capsys.readouterr().out
    assert "boot maps maintenance: start: 1 project(s)" in out
    assert "boot maps maintenance: planners queued" in out


def test_enrich_all_project_sites_quiet_without_debug(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DEBUG", raising=False)
    from peaky_finders.web.projects import enrich_all_project_sites

    with patch(
        "peaky_finders.web.projects._iter_project_config_paths",
        return_value=[("demo", Path("/tmp/demo/config.yaml"))],
    ), patch(
        "peaky_finders.web.projects.enrich_all_preset_sites",
        return_value=(0, 0),
    ):
        enrich_all_project_sites()

    assert capsys.readouterr().out == ""


def test_enrich_all_project_sites_verbose_when_debug(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DEBUG", "1")
    from peaky_finders.web.projects import enrich_all_project_sites

    log_fps: list[object] = []

    def capture_enrich(cfg, *, allow_network_plss=True, http_pool=None, log_fp=None):
        log_fps.append(log_fp)
        return (1, 2)

    with patch(
        "peaky_finders.web.projects._iter_project_config_paths",
        return_value=[("demo", Path("/tmp/demo/config.yaml"))],
    ), patch(
        "peaky_finders.web.projects.enrich_all_preset_sites",
        side_effect=capture_enrich,
    ):
        enrich_all_project_sites()

    out = capsys.readouterr().out
    assert "boot site enrich: start: 1 project(s)" in out
    assert "boot site enrich: done:" in out
    assert log_fps == [sys.stderr]
