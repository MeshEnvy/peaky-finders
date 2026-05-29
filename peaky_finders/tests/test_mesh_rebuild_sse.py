"""Mesh rebuild SSE callbacks (per pair / per depth band)."""

from __future__ import annotations

from unittest.mock import patch

from peaky_finders.path_labels import mesh_pairwise_rel_dir
from peaky_finders.web.maps_build_scheduler import _mesh_rebuild_sse_callbacks


def test_mesh_rebuild_sse_callbacks_publish_layer_phase() -> None:
    published: list[tuple[str, str, str]] = []

    def capture(slug: str, *, layer_id: str, phase: str) -> None:
        published.append((slug, layer_id, phase))

    on_pair, on_band = _mesh_rebuild_sse_callbacks("nevada")
    with patch("peaky_finders.web.maps_build_scheduler.publish_layer_phase", side_effect=capture):
        on_pair("site_a", "site_b")
        on_band("d2_pair")

    pair_id = f"mesh_pair:{mesh_pairwise_rel_dir('site_a', 'site_b')}"
    assert published == [
        ("nevada", pair_id, "built"),
        ("nevada", "mesh_depth:d2_pair", "built"),
    ]
