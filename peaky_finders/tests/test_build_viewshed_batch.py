"""Tests for build executor viewshed batch coalescing."""

from __future__ import annotations

from unittest.mock import patch

from peaky_finders.build_executor import _flush_viewshed_coverage_batch, _viewshed_coverage_digest


def test_viewshed_coverage_digest() -> None:
    assert _viewshed_coverage_digest("viewshed:abc123:coverage") == "abc123"
    assert _viewshed_coverage_digest("viewshed:abc123:request") is None
    assert _viewshed_coverage_digest("bundle:resolve") is None


def test_flush_viewshed_coverage_batch_all_runnable(peaky_test_home) -> None:
    from fixture_paths import SAMPLE_PROJECT_CONFIG
    from peaky_finders.build_configure import configure_preset_build
    from peaky_finders.build_graph import build_target_graph
    from peaky_finders.sites_job import CoverageProvider, load_preset

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    assert preset.simulation.provider == CoverageProvider.SPLATTER
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)
    coverage_ids = sorted(tid for tid in nodes if tid.endswith(":coverage"))
    assert len(coverage_ids) >= 2
    pending = set(coverage_ids)
    subset = {tid: nodes[tid] for tid in pending}
    seen: list[int] = []

    def fake_batch(**kwargs) -> int:
        seen.append(len(kwargs["workspaces"]))
        return 0

    with patch(
        "peaky_finders.build_executor.run_viewshed_batch",
        side_effect=fake_batch,
    ):
        codes = _flush_viewshed_coverage_batch(
            plan=plan,
            preset=preset,
            pending=pending,
            subset=subset,
            force=True,
            verbose=False,
            jobs=8,
        )

    assert len(codes) == len(coverage_ids)
    assert seen == [len(coverage_ids)]
