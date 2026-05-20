"""Flat KML cache fingerprints invalidate when PEAKY_KML_EMIT_VERSION bumps."""

from __future__ import annotations

import pytest

from peaky_finders.link_overlap import _pairwise_flat_kml_plain_inputs_fingerprint
from peaky_finders.sites_job import (
    DEFAULT_MESH_PAIRWISE_KML_STYLE,
    DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE,
)
from peaky_finders.splat_polygonize import GX_DRAW_ORDER_MESH_PAIRWISE, PEAKY_KML_EMIT_VERSION


def _plain_fp() -> str:
    return _pairwise_flat_kml_plain_inputs_fingerprint(
        bundle_kml_overlay_digest="overlay_test",
        polygon_union_stable_digest="abc123",
        skadi_dem_peak_enabled=False,
        dem_peak_llz=None,
        link_polygon_style=DEFAULT_MESH_PAIRWISE_KML_STYLE,
        pairwise_peak_pin_style=DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE,
        mesh_pairwise_gx_draw_order=GX_DRAW_ORDER_MESH_PAIRWISE,
    )


def test_pairwise_flat_kml_fingerprint_changes_with_kml_emit_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fp_a = _plain_fp()
    monkeypatch.setattr(
        "peaky_finders.link_overlap.PEAKY_KML_EMIT_VERSION",
        PEAKY_KML_EMIT_VERSION + 1,
    )
    fp_b = _plain_fp()
    assert fp_a != fp_b
