"""Web project discovery and context API."""

from __future__ import annotations

from peaky_finders.web.projects import project_context

from fixture_paths import PEAKY_TEST_HOME


def test_project_context_includes_site_name(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    ctx = project_context("sample")
    by_slug = {s["slug"]: s for s in ctx["sites"]}
    assert by_slug["hub"]["name"] == "Hub Site"
    assert by_slug["peer-a"]["name"] == "Peer A"
