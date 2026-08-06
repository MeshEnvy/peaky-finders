"""Goal-seek candidate API."""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.serve.seek import (
    _collect_reachable_site_rows,
    _goal_finish_eligible,
    _goal_hop_eligible,
    _goal_in_viewshed,
    _goal_reachable,
    _parse_bbox,
    _parse_exclude_points,
    _parse_exclude_slugs,
    resolve_seek_peak_bin_size_m,
)
from peaky_finders.serve.seek import SeekPoint

_COVERING_FP = box(-120.0, 39.0, -119.0, 41.0)

_SEEK_PROJECT_YAML = """
simulation:
  provider: splatter
  radius_km: 100.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
display:
  colormap: plasma
  min_dbm: -130.0
  max_dbm: -80.0
seek:
  peak_bin_size_m: 1500
  max_candidates: 4
sites:
  start:
    name: Start
    loc: [40.0, -119.5]
  goal:
    name: Goal
    loc: [40.5, -119.0]
  relay:
    name: Relay
    loc: [40.1, -119.4]
links: []
land:
  sources:
    blm:
      path: data/test.gdb
      layers:
        - name: public
          role: include
""".strip()


def _write_seek_project(project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(_SEEK_PROJECT_YAML + "\n", encoding="utf-8")
    return project_dir


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def _poll_seek_result(host: str, port: int, slug: str, *, timeout_s: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", f"/api/p/{slug}/seek/scan-progress")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") == "done" and body.get("result"):
            return body
        if body.get("status") == "error":
            raise AssertionError(body.get("error") or "seek failed")
        time.sleep(0.02)
    raise AssertionError("timed out waiting for seek result")


def test_parse_bbox_and_exclude() -> None:
    assert _parse_bbox("-120,39,-119,40") == (-120.0, 39.0, -119.0, 40.0)
    pts = _parse_exclude_points("40.1,-119.2;40.2,-119.3")
    assert len(pts) == 2
    assert pts[0].lat == 40.1
    assert _parse_exclude_slugs("start;goal") == {"start", "goal"}


def test_collect_reachable_site_rows(tmp_path: Path) -> None:
    from peaky_finders.core.preset import load_preset

    project_dir = _write_seek_project(tmp_path / "site-rows")
    preset = load_preset(project_dir / "config.yaml")
    eligible = box(-120.0, 39.0, -119.0, 41.0)
    footprint = box(-120.0, 39.0, -119.0, 41.0)
    rows = _collect_reachable_site_rows(
        preset=preset,
        eligible=eligible,
        footprint=footprint,
        from_lat=40.0,
        from_lon=-119.5,
        goal_lat=40.5,
        goal_lon=-119.0,
        exclude=[],
        exclude_slugs={"start", "goal"},
    )
    assert len(rows) == 1
    assert rows[0][0] == "relay"


def test_resolve_seek_peak_bin_size_m() -> None:
    from peaky_finders.core.preset.model import SeekConfig

    cfg = SeekConfig(peak_bin_size_m=1500, max_candidates=48)
    assert resolve_seek_peak_bin_size_m(cfg, None) == 1500.0
    assert resolve_seek_peak_bin_size_m(cfg, 800.0) == 800.0
    assert resolve_seek_peak_bin_size_m(cfg, 200.0) == 500.0
    assert resolve_seek_peak_bin_size_m(cfg, 3000.0) == 1500.0


def test_goal_reachable_helper(tmp_path: Path) -> None:
    from peaky_finders.core.preset import load_preset

    project_dir = _write_seek_project(tmp_path / "goal-check")
    preset = load_preset(project_dir / "config.yaml")
    eligible = box(-120.0, 39.0, -119.0, 41.0)
    footprint = box(-120.0, 39.0, -119.0, 41.0)
    assert _goal_reachable(
        preset=preset,
        eligible=eligible,
        footprint=footprint,
        from_lat=40.0,
        from_lon=-119.5,
        goal_lat=40.5,
        goal_lon=-119.0,
        exclude=[],
    )
    assert not _goal_reachable(
        preset=preset,
        eligible=eligible,
        footprint=footprint,
        from_lat=40.0,
        from_lon=-119.5,
        goal_lat=41.5,
        goal_lon=-119.0,
        exclude=[],
    )


def test_goal_hop_eligible_without_viewshed(tmp_path: Path) -> None:
    from peaky_finders.core.preset import load_preset

    project_dir = _write_seek_project(tmp_path / "goal-hop")
    preset = load_preset(project_dir / "config.yaml")
    eligible = box(-120.0, 39.0, -119.0, 41.0)
    footprint = box(-119.8, 39.8, -119.6, 40.2)
    goal_lat, goal_lon = 40.5, -119.0
    assert _goal_hop_eligible(
        preset=preset,
        eligible=eligible,
        from_lat=40.0,
        from_lon=-119.5,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        exclude=[],
    )
    assert not _goal_in_viewshed(eligible, footprint, goal_lat, goal_lon)
    assert not _goal_reachable(
        preset=preset,
        eligible=eligible,
        footprint=footprint,
        from_lat=40.0,
        from_lon=-119.5,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        exclude=[],
    )


def test_goal_finish_eligible_near_prior_hop(tmp_path: Path) -> None:
    from peaky_finders.core.preset import load_preset

    project_dir = _write_seek_project(tmp_path / "goal-finish")
    preset = load_preset(project_dir / "config.yaml")
    eligible = box(-120.0, 39.0, -119.0, 41.0)
    goal_lat, goal_lon = 40.5, -119.0
    from_lat, from_lon = 40.0, -119.5
    # Prior hop sitting ~50 m from the goal site — blocks anonymous peak picks, not Finish.
    exclude = [SeekPoint(lat=goal_lat + 0.0002, lon=goal_lon)]
    assert _goal_finish_eligible(
        preset,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
    )
    assert not _goal_hop_eligible(
        preset=preset,
        eligible=eligible,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        exclude=exclude,
    )


def test_load_seek_candidates_mocked_direct(tmp_path: Path) -> None:
    from unittest.mock import patch

    project_dir = _write_seek_project(tmp_path / "sample-direct")
    peaks = [(-119.4, 40.05, 2100.0), (-119.35, 40.08, 2200.0)]
    with (
        patch("peaky_finders.serve.seek.load_or_build_eligible_geometry") as mock_elig,
        patch("peaky_finders.serve.seek.load_or_build_eligible_peaks") as mock_peaks,
        patch("peaky_finders.serve.seek.get_viewshed_engine") as mock_engine,
        patch("peaky_finders.serve.seek.splatter_session") as mock_session,
        patch("peaky_finders.serve.seek.mutual_hop_batch") as mock_rf,
    ):
        from peaky_finders.serve.seek import load_seek_candidates

        mock_elig.return_value = (_COVERING_FP, "abc123")
        mock_peaks.return_value = (peaks, {"cache": "hit", "build_ms": None, "n_peaks": len(peaks)})
        inst = mock_engine.return_value
        inst.read_coords_footprint.return_value = _COVERING_FP
        mock_rf.return_value = [True, True, True, False]
        mock_session.return_value.ensure_tiles_for_points.return_value = None
        body = load_seek_candidates(
            project_dir,
            from_lat=40.0,
            from_lon=-119.5,
            goal_lat=40.5,
            goal_lon=-119.0,
            bbox="-120,39,-119,41",
            exclude_slugs_raw="start;goal",
            peak_bin_size_m=750.0,
        )
        assert body["meta"]["n_site_candidates"] == 1


def test_api_seek_candidates_mocked(tmp_path: Path) -> None:
    project_dir = _write_seek_project(tmp_path / "sample")
    server, host, port, _thread = _start_server(tmp_path)
    try:
        peaks = [(-119.4, 40.05, 2100.0), (-119.35, 40.08, 2200.0)]
        with (
            patch("peaky_finders.serve.seek.load_or_build_eligible_geometry") as mock_elig,
            patch("peaky_finders.serve.seek.load_or_build_eligible_peaks") as mock_peaks,
            patch("peaky_finders.serve.seek.get_viewshed_engine") as mock_engine,
            patch("peaky_finders.serve.seek.splatter_session") as mock_session,
            patch("peaky_finders.serve.seek.mutual_hop_batch") as mock_rf,
        ):
            mock_elig.return_value = (_COVERING_FP, "abc123")
            mock_peaks.return_value = (peaks, {"cache": "hit", "build_ms": None, "n_peaks": len(peaks)})
            inst = mock_engine.return_value
            inst.read_coords_footprint.return_value = _COVERING_FP
            mock_rf.return_value = [True, True, True, False]
            mock_session.return_value.ensure_tiles_for_points.return_value = None

            qs = (
                "from_lat=40.0&from_lon=-119.5&goal_lat=40.5&goal_lon=-119.0"
                "&bbox=-120,39,-119,41&peak_bin_size_m=750"
                "&exclude_slugs=start;goal"
            )
            conn = HTTPConnection(host, port, timeout=5)
            conn.request("GET", f"/api/p/sample/seek/candidates?{qs}")
            resp = conn.getresponse()
            kickoff = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 202
            assert kickoff["project"] == "sample"
            assert kickoff["status"] == "pending"
            assert isinstance(kickoff["gen"], int)
            polled = _poll_seek_result(host, port, "sample")
            body = {"project": polled["project"], **polled["result"]}
            assert body["meta"]["peak_bin_size_m"] == 750.0
            assert body["meta"]["n_site_candidates"] == 1
            assert body["meta"]["site_candidate_slugs"] == ["relay"]
            mock_peaks.assert_called_once()
            assert mock_peaks.call_args.kwargs["bin_size_m"] == 750.0
            site_lines = [
                f for f in body["lines"]["features"] if f["properties"].get("site_slug") == "relay"
            ]
            assert len(site_lines) == 1
            site_candidates = [
                f for f in body["candidates"]["features"] if f["properties"].get("site_slug") == "relay"
            ]
            assert len(site_candidates) == 1
            assert len(body["candidates"]["features"]) <= 6
            assert body["meta"]["goal_finish_eligible"] is True
            assert body["meta"]["goal_hop_eligible"] is True
            assert body["meta"]["goal_in_viewshed"] is True
            assert body["meta"]["goal_reachable"] is True
            assert body["meta"]["goal_rf_viable"] is True
            goal_candidates = [
                f for f in body["candidates"]["features"] if f["properties"].get("is_goal")
            ]
            assert len(goal_candidates) == 1
            assert body["goal_line"]["geometry"]["type"] == "LineString"
    finally:
        server.shutdown()


def test_api_seek_missing_include_layers(tmp_path: Path) -> None:
    project_dir = _write_seek_project(tmp_path / "sample")
    server, host, port, _thread = _start_server(tmp_path)
    try:
        with patch("peaky_finders.serve.seek.load_or_build_eligible_geometry") as mock_elig:
            from peaky_finders.serve.eligible_land import EligibleLandError

            mock_elig.side_effect = EligibleLandError(
                "Seek requires at least one land layer with role: include"
            )
            qs = (
                "from_lat=40.0&from_lon=-119.5&goal_lat=40.5&goal_lon=-119.0"
                "&bbox=-120,39,-119,41"
            )
            conn = HTTPConnection(host, port, timeout=5)
            conn.request("GET", f"/api/p/sample/seek/candidates?{qs}")
            resp = conn.getresponse()
            kickoff = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 202
            deadline = time.monotonic() + 5.0
            error_body = None
            while time.monotonic() < deadline:
                conn = HTTPConnection(host, port, timeout=5)
                conn.request("GET", "/api/p/sample/seek/scan-progress")
                poll = conn.getresponse()
                body = json.loads(poll.read().decode("utf-8"))
                if body.get("status") == "error":
                    error_body = body
                    break
                time.sleep(0.02)
            assert error_body is not None
            assert error_body["error_status"] == 422
            assert "include" in error_body["error"]
    finally:
        server.shutdown()
