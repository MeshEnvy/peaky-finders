"""Flat-map PNG previews for WGS84 geometries (EPSG:3857 axes; debug / cache sidecars)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry


def _aabbggrr_to_rgba_mpl(hex8: str) -> tuple[float, float, float, float]:
    """KML ``aabbggrr`` → matplotlib ``(r, g, b, a)`` in 0..1."""
    s = hex8.strip().lower()
    if len(s) != 8:
        raise ValueError(f"Expected 8 hex digits (aabbggrr); got {hex8!r}")
    aa = int(s[0:2], 16) / 255.0
    bb = int(s[2:4], 16) / 255.0
    gg = int(s[4:6], 16) / 255.0
    rr = int(s[6:8], 16) / 255.0
    return (rr, gg, bb, aa)


def write_wgs84_geometry_preview_png(
    geom: BaseGeometry,
    png_path: Path,
    *,
    face_aabbggrr: str = "66888888",
    line_aabbggrr: str = "ff666666",
    fill_polygons: bool = True,
    line_width: float = 2.0,
) -> None:
    """Write a single-geometry preview; no-op if geometry is null or empty."""
    if geom is None or getattr(geom, "is_empty", True):
        return

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    g = gdf.to_crs("EPSG:3857")
    face = _aabbggrr_to_rgba_mpl(face_aabbggrr)
    edge = _aabbggrr_to_rgba_mpl(line_aabbggrr)

    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 11), dpi=120)

    try:
        types = set(g.geom_type.astype(str))
        point_only = types <= {"Point"}
        if point_only:
            ax.scatter(g.geometry.x, g.geometry.y, s=12, c=[edge[:3]], alpha=max(edge[3], 0.35))
        else:
            if fill_polygons and face[3] > 0:
                fc: tuple[float, float, float, float] | str = face
            else:
                fc = (0.0, 0.0, 0.0, 0.0)
            ec: tuple[float, float, float, float] | str = edge[:4] if line_width > 0 else "none"
            g.plot(ax=ax, facecolor=fc, edgecolor=ec, linewidth=0.25 if line_width > 0 else 0)
        xmin, ymin, xmax, ymax = g.total_bounds
        xpad = max((xmax - xmin) * 0.02, 500.0)
        ypad = max((ymax - ymin) * 0.02, 500.0)
        ax.set_xlim(xmin - xpad, xmax + xpad)
        ax.set_ylim(ymin - ypad, ymax + ypad)
        ax.set_aspect("equal")
        ax.axis("off")
        fig.savefig(
            png_path,
            dpi=144,
            bbox_inches="tight",
            pad_inches=0.05,
            facecolor="white",
        )
    finally:
        plt.close(fig)
