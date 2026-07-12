"""KML bounds helpers for splatter viewshed workspaces."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_lat_lon_box(kml_bytes: bytes) -> dict[str, float]:
    root = ET.fromstring(kml_bytes)
    box_el = None
    for el in root.iter():
        if _local_tag(el.tag) == "LatLonBox":
            box_el = el
            break
    if box_el is None:
        raise ValueError("No LatLonBox found in KML")
    values: dict[str, float] = {}
    for child in box_el:
        name = _local_tag(child.tag)
        if child.text is None:
            continue
        if name in ("north", "south", "east", "west", "rotation"):
            values[name] = float(child.text.strip())
    for k in ("north", "south", "east", "west"):
        if k not in values:
            raise ValueError(f"LatLonBox missing {k}")
    return values


def point_splat_output_kml_at_png(output_kml: Path, *, raster_basename: str = "splat.png") -> None:
    """Rewrite SPLAT's GroundOverlay raster ``<href>output.ppm`` → PNG."""
    raw = output_kml.read_text(encoding="utf-8")
    if not re.search(r"<href>\s*output\.ppm\s*</href>", raw):
        if re.search(rf"<href>\s*{re.escape(raster_basename)}\s*</href>", raw):
            return
        raise ValueError(f"No SPLAT <href>output.ppm</href> and no PNG href in {output_kml}")
    patched = re.sub(r"<href>\s*output\.ppm\s*</href>", f"<href>{raster_basename}</href>", raw)
    output_kml.write_text(patched, encoding="utf-8")


def load_bounds_from_manifest(path: Path) -> dict[str, float] | None:
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    bbox = data.get("bbox")
    if not bbox:
        return None
    out = {k: float(bbox[k]) for k in ("north", "south", "east", "west")}
    if "rotation" in bbox and bbox["rotation"] is not None:
        out["rotation"] = float(bbox["rotation"])
    return out
