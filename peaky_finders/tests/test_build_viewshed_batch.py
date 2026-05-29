"""Tests for build executor viewshed batch coalescing."""

from __future__ import annotations

from unittest.mock import patch

from peaky_finders.build_executor import _flush_viewshed_coverage_batch, _viewshed_coverage_digest


def test_viewshed_coverage_digest() -> None:
    assert _viewshed_coverage_digest("viewshed:abc123:coverage") == "abc123"
    assert _viewshed_coverage_digest("viewshed:abc123:request") is None
    assert _viewshed_coverage_digest("bundle:resolve") is None


def test_run_viewshed_batch_single_uses_splatter_run(peaky_test_home) -> None:
    from fixture_paths import SAMPLE_PROJECT_CONFIG
    from peaky_finders.build_configure import configure_preset_build
    from peaky_finders.sites_job import load_preset
    from peaky_finders.viewshed_batch import run_viewshed_batch

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    ws = plan.viewshed_workspaces[0]
    calls: list[str] = []

    with patch(
        "peaky_finders.viewshed_batch.run_splatter_site",
        side_effect=lambda **kwargs: calls.append("site") or 0,
    ), patch(
        "peaky_finders.viewshed_batch.run_splatter_batch",
        side_effect=lambda **kwargs: calls.append("batch") or 0,
    ), patch(
        "peaky_finders.viewshed_batch.vectorize_coverage_footprints_parallel",
        return_value=None,
    ):
        rc = run_viewshed_batch(
            preset=preset,
            viewshed_root=plan.viewsheds_root,
            workspaces=[ws],
            coverage_verbose=False,
        )

    assert rc == 0
    assert calls == ["site"]


def test_run_viewshed_batch_multi_uses_splatter_run_batch(peaky_test_home, tmp_path) -> None:
    from fixture_paths import SAMPLE_PROJECT_CONFIG
    from peaky_finders.build_configure import configure_preset_build
    from peaky_finders.sites_job import load_preset
    from peaky_finders.viewshed_batch import run_viewshed_batch

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    workspaces = plan.viewshed_workspaces[:2]
    batch_req = tmp_path / "request.json"
    batch_req.write_text("[]", encoding="utf-8")
    calls: list[str] = []

    with patch(
        "peaky_finders.viewshed_batch.run_splatter_site",
        side_effect=lambda **kwargs: calls.append("site") or 0,
    ), patch(
        "peaky_finders.viewshed_batch.run_splatter_batch",
        side_effect=lambda **kwargs: calls.append("batch") or 0,
    ), patch(
        "peaky_finders.viewshed_batch.write_batch_request_json",
        return_value=batch_req,
    ), patch(
        "peaky_finders.viewshed_batch.vectorize_coverage_footprints_parallel",
        return_value=None,
    ):
        rc = run_viewshed_batch(
            preset=preset,
            viewshed_root=plan.viewsheds_root,
            workspaces=workspaces,
            coverage_verbose=False,
        )

    assert rc == 0
    assert calls == ["batch"]


def test_flush_viewshed_coverage_batch_all_runnable(peaky_test_home) -> None:
    from fixture_paths import SAMPLE_PROJECT_CONFIG
    from peaky_finders.build_configure import configure_preset_build
    from peaky_finders.build_graph import build_target_graph
    from peaky_finders.sites_job import CoverageProvider, load_preset

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    assert preset.simulation.provider == CoverageProvider.LOS
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


def test_run_target_subgraph_one_viewshed_coverage_batch(peaky_test_home, monkeypatch) -> None:
    """Request phases finish before splatter run-batch so all workspaces batch together."""
    from fixture_paths import SAMPLE_PROJECT_CONFIG
    from peaky_finders.build_configure import configure_preset_build
    from peaky_finders.build_executor import _run_target_subgraph
    from peaky_finders.build_graph import build_target_graph, filter_subgraph, subgraph_roots_for
    from peaky_finders.sites_job import load_preset

    preset_path = SAMPLE_PROJECT_CONFIG.expanduser().resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes_all = build_target_graph(plan, preset)
    roots = subgraph_roots_for("viewsheds", plan, preset, nodes_all)
    subset = filter_subgraph(nodes_all, roots)
    batch_sizes: list[int] = []

    def fake_batch(**kwargs) -> int:
        batch_sizes.append(len(kwargs["workspaces"]))
        return 0

    monkeypatch.setattr(
        "peaky_finders.build_executor.run_viewshed_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "peaky_finders.build_executor.execute_target",
        lambda plan, preset, node, **kwargs: 0,
    )

    rc = _run_target_subgraph(
        plan=plan,
        preset=preset,
        subset=subset,
        force=True,
        dry_run=False,
        jobs=1,
        verbose=False,
        bundle_data_dir=None,
    )

    coverage_count = sum(1 for tid in subset if tid.endswith(":coverage"))
    assert rc == 0
    assert coverage_count >= 2
    assert batch_sizes == [coverage_count]
