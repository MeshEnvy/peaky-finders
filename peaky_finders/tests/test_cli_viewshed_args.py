"""Granular ``peaky viewshed`` flag wiring (Makefile uses ``--workspace-only --phase``)."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from peaky_finders.cli import build_granular_viewshed_argument_parser, run_splat


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
