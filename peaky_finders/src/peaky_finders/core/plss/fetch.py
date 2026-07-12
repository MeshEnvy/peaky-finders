"""CadNSDI PLSS lookup and per-preset loc cache (serve-only)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from peaky_finders.core.http import CADNSDI_HTTP_POOL, HttpPool
from peaky_finders.core.preset.io import read_preset_yaml_tree, update_preset_yaml_tree

CADNSDI_BASE = "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer"

PLSS_LOC_CACHE_FORMAT = "plss_loc_cache/v2"
PLSS_LOC_CACHE_SUBDIR = "plss"
PLSS_LOC_CACHE_BASENAME = "by_loc.json"


def loc_stamp(lat: float, lon: float) -> str:
    """Stable string for a site ``loc``; cache key for CadNSDI PLSS lookup."""
    return f"{lat:.6f},{lon:.6f}"


def plss_loc_cache_path(cache_base: Path) -> Path:
    return Path(cache_base).expanduser().resolve() / PLSS_LOC_CACHE_SUBDIR / PLSS_LOC_CACHE_BASENAME


def read_plss_loc_cache(cache_base: Path) -> dict[str, dict[str, str]]:
    path = plss_loc_cache_path(cache_base)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if raw.get("format") != PLSS_LOC_CACHE_FORMAT:
        return {}
    by_loc = raw.get("by_loc")
    if not isinstance(by_loc, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for loc_key, entry in by_loc.items():
        if not isinstance(loc_key, str) or not isinstance(entry, dict):
            continue
        plss = entry.get("plss")
        if isinstance(plss, str):
            out[loc_key] = {"plss": plss}
    return out


def write_plss_loc_cache(cache_base: Path, by_loc: dict[str, dict[str, str]]) -> None:
    path = plss_loc_cache_path(cache_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": PLSS_LOC_CACHE_FORMAT,
                "by_loc": {k: by_loc[k] for k in sorted(by_loc)},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def loc_plss_resolved(entry: dict[str, str]) -> bool:
    """True when a loc cache row has a prior CadNSDI lookup result."""
    return "plss" in entry


def _query(layer: int, lon: float, lat: float, *, http_pool: HttpPool | None = None) -> dict:
    params = urllib.parse.urlencode(
        {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    url = f"{CADNSDI_BASE}/{layer}/query?{params}"
    pool = http_pool or CADNSDI_HTTP_POOL
    with pool.request(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _attrs(feat: dict) -> dict:
    return feat.get("attributes") or {}


def plss_for_point(
    lon: float,
    lat: float,
    *,
    http_pool: HttpPool | None = None,
) -> str:
    """Return SECDIVID string for a WGS84 point (empty when not found)."""
    return _plss_for_point_impl(lon, lat, http_pool=http_pool)


def _plss_for_point_impl(
    lon: float,
    lat: float,
    *,
    http_pool: HttpPool | None = None,
) -> str:
    for layer in (2, 1):
        data = _query(layer, lon, lat, http_pool=http_pool)
        feats = data.get("features") or []
        if not feats:
            continue
        attrs = _attrs(feats[0])
        secdivid = attrs.get("SECDIVID") or attrs.get("secdivid")
        if isinstance(secdivid, str) and secdivid.strip():
            return secdivid.strip()
    return ""


def _list_site_slug_assignments(entries: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    import re

    out: list[tuple[str, dict[str, Any]]] = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        name = str(ent.get("name") or "").strip()
        base = re.sub(r"[^\w\s-]", "", name, flags=re.ASCII)
        base = re.sub(r"[\s_]+", "-", base.strip()).lower().strip("-") or "site"
        out.append((base, ent))
    return out


def apply_plss_on_preset(preset_path: Path, slug: str, plss: str | None) -> None:
    """Write PLSS onto a preset site/goal entry."""

    def mutator(_y: Any, root: dict[str, Any]) -> None:
        sites_raw = root.get("sites")
        if isinstance(sites_raw, dict):
            ent = sites_raw.get(slug)
            if isinstance(ent, dict):
                ent["plss"] = plss
                if "mlrs" in ent:
                    del ent["mlrs"]
            return
        if isinstance(sites_raw, list):
            for site_slug, ent in _list_site_slug_assignments(sites_raw):
                if site_slug == slug and isinstance(ent, dict):
                    ent["plss"] = plss
                    if "mlrs" in ent:
                        del ent["mlrs"]
                    break

    update_preset_yaml_tree(preset_path, mutator)
