"""Serve preset cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.serve_preset_cache import load_serve_project_context, reset_serve_preset_cache_for_tests


def test_serve_preset_cache_reuses_until_mtime_changes(tmp_path: Path) -> None:
    reset_serve_preset_cache_for_tests()
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    config = project_dir / "config.yaml"
    config.write_text(
        """
simulation:
  radius_km: 10
  raster_dimension: 256
  transmitter: {height_m: 10}
  receiver: {height_m: 2}
sites:
  hub:
    type: installed
    name: Hub
    loc: [39.5, -119.8]
""".strip(),
        encoding="utf-8",
    )
    ctx1 = load_serve_project_context(project_dir)
    ctx2 = load_serve_project_context(project_dir)
    assert ctx1 is ctx2
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    ctx3 = load_serve_project_context(project_dir)
    assert ctx3 is not ctx1
