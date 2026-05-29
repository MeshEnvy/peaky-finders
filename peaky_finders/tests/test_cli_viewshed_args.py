"""Granular ``peaky viewshed`` flag wiring (Makefile uses ``--workspace-only --phase``)."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from peaky_finders.cli import _job_path_from_args, build_granular_viewshed_argument_parser, run_splat
from peaky_finders.sites_job import load_preset


def test_viewshed_workspace_only_and_phase_dest_on_namespace() -> None:
    """``--phase`` must set ``viewshed_phase`` (not default ``phase``) for :func:`run_splat`."""

    p = argparse.ArgumentParser(parents=[build_granular_viewshed_argument_parser()])
    p.add_argument("granular_viewshed_slug")
    args = p.parse_args(["hub", "--workspace-only", "--phase", "request"])
    assert args.viewshed_workspace_only is True
    assert args.viewshed_phase == "request"


def test_run_splat_rejects_without_workspace_only() -> None:
    ns = SimpleNamespace(
        granular_viewshed_slug="hub",
        viewshed_workspace_only=False,
        viewshed_phase="request",
        data_dir=None,
    )
    assert run_splat(ns) == 2


def test_job_path_from_args_uses_explicit_preset(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("simulation:\n  provider: splatter\n", encoding="utf-8")
    ns = SimpleNamespace(preset_path=str(cfg))
    assert _job_path_from_args(ns) == cfg.resolve()


def test_run_splat_uses_preset_path_arg(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    slug = next(iter(load_preset(sample).sites))
    ns = SimpleNamespace(
        granular_viewshed_slug=slug,
        viewshed_workspace_only=True,
        viewshed_phase="request",
        preset_path=str(sample),
        verbose=False,
    )
    assert run_splat(ns) == 0
