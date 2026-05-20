"""Convert SPLAT output.ppm to KMZ-ready splat.png (no-RF pixels → transparent)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image


def overlay_dimensions_capped(width: int, height: int, max_edge_px: int) -> tuple[int, int]:
    """Return ``(w, h)`` fitting inside ``max_edge_px``, preserving aspect ratio.

    SPLAT-HD emits very large PPMS (~14k wide). Google Earth often maps GroundOverlay
    rasters onto a GPU texture capped around 4–8k; oversize PNGs routinely show bogus
    opaque white slabs while the tinted coverage still renders.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")
    if max_edge_px < 1:
        raise ValueError("max_edge_px must be positive")
    if max(width, height) <= max_edge_px:
        return width, height
    scale = max_edge_px / max(width, height)
    nw = max(1, int(round(width * scale)))
    nh = max(1, int(round(height * scale)))
    return nw, nh


def overlay_max_edge_from_env() -> int | None:
    """``PEAKY_SPLAT_OVERLAY_MAX_EDGE`` (default ``8192``); ``0`` = no resizing."""
    raw = os.environ.get("PEAKY_SPLAT_OVERLAY_MAX_EDGE", "8192").strip()
    if raw in ("", "none", "None"):
        return 8192
    try:
        n = int(raw)
    except ValueError:
        return 8192
    return None if n < 1 else max(n, 32)


def write_splat_png_from_ppm(*, ppm_path: Path, png_path: Path) -> None:
    """No-RF (white SPLAT pixels) → transparent RGBA; optional downscale for Google Earth overlay limits."""
    with Image.open(ppm_path) as img:
        rgb_u8 = np.asarray(img.convert("RGB"), dtype=np.uint8)
    no_coverage = np.all(rgb_u8 == 255, axis=2)
    rgba = np.zeros((*rgb_u8.shape[:2], 4), dtype=np.uint8)
    rgba[~no_coverage, 0:3] = rgb_u8[~no_coverage]
    rgba[~no_coverage, 3] = 255
    overlay = Image.fromarray(rgba, "RGBA")

    edge = overlay_max_edge_from_env()
    if edge is not None:
        w, h = overlay.size
        nw, nh = overlay_dimensions_capped(w, h, edge)
        if (nw, nh) != (w, h):
            overlay = overlay.resize((nw, nh), Image.Resampling.LANCZOS)

    overlay.save(png_path, format="PNG", optimize=True)
