"""Shared helpers for ArcGIS Map Viewer web maps (REST FeatureServer / MapServer layers)."""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import geopandas as gpd

DEFAULT_UA = "Mozilla/5.0 (compatible; peaky_finders/webmap_arcgis)"


def strip_layer_base_url(layer_url: str) -> str:
    return layer_url.split("?", 1)[0].rstrip("/")


def walk_arcgis_feature_layers(
    webmap: dict[str, Any],
    *,
    include_tables: bool = False,
    skip_hidden: bool = False,
    title_contains: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten ``operationalLayers`` (and optionally ``tables``) to ArcGIS feature layer dicts."""

    def walk(items: list[Any]) -> Iterator[dict[str, Any]]:
        for item in items:
            if not isinstance(item, dict):
                continue
            lt = item.get("layerType")
            if lt == "GroupLayer":
                yield from walk(list(item.get("layers") or []))
                continue
            if lt == "ArcGISFeatureLayer" and item.get("url"):
                yield item

    ops = list(walk(list(webmap.get("operationalLayers") or [])))
    if include_tables:
        ops.extend(walk(list(webmap.get("tables") or [])))

    out: list[dict[str, Any]] = []
    subs = title_contains or []
    for layer in ops:
        if skip_hidden and layer.get("visibility") is False:
            continue
        url = layer.get("url")
        if not isinstance(url, str):
            continue
        if "FeatureServer" not in url and "MapServer" not in url:
            continue
        title = layer.get("title")
        if not isinstance(title, str) or not title.strip():
            title = Path(url).name or url
        title = title.strip()
        if subs and not all(s in title for s in subs):
            continue
        out.append(layer)
    return out


def layer_definition_where(layer: dict[str, Any]) -> str:
    ld = layer.get("layerDefinition") or {}
    expr = ld.get("definitionExpression")
    if isinstance(expr, str) and expr.strip():
        return expr
    return "1=1"


def fetch_json(url: str, *, timeout_s: float, user_agent: str = DEFAULT_UA) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def fetch_layer_metadata(layer_url: str, *, timeout_s: float, user_agent: str = DEFAULT_UA) -> dict[str, Any]:
    base = strip_layer_base_url(layer_url)
    return fetch_json(f"{base}?f=json", timeout_s=timeout_s, user_agent=user_agent)


def arcgis_geometry_exclude_eligible(geometry_type: str | None) -> bool:
    """True if Peaky ``bundle.exclude`` can consume the layer (polygon-like only)."""
    if not geometry_type:
        return False
    g = geometry_type.lower().replace("_", "").replace("-", "")
    if "point" in g or "multipoint" in g:
        return False
    if "polyline" in g or "linestring" in g:
        return False
    return "polygon" in g or "envelope" in g


def stable_layer_slug(title: str, url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip()).strip("._")[:48]
    return f"{safe}__{h}"


def iter_arcgis_geojson_features(
    layer_url: str,
    *,
    where: str,
    page_size: int,
    timeout_s: float,
    max_features: int | None,
    user_agent: str = DEFAULT_UA,
) -> Iterator[dict[str, Any]]:
    base = strip_layer_base_url(layer_url)
    offset = 0
    emitted = 0
    while True:
        if max_features is not None and emitted >= max_features:
            break
        take = page_size
        if max_features is not None:
            take = min(page_size, max_features - emitted)
        qs = urllib.parse.urlencode(
            {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
                "returnZ": "false",
                "returnM": "false",
                "f": "geojson",
                "outSR": "4326",
                "resultRecordCount": str(take),
                "resultOffset": str(offset),
            }
        )
        qurl = f"{base}/query?{qs}"
        try:
            data = fetch_json(qurl, timeout_s=timeout_s, user_agent=user_agent)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise RuntimeError(f"query failed ({qurl}): {e}") from e

        if data.get("error"):
            raise RuntimeError(f"ArcGIS error for {base}: {data.get('error')}")

        feats = data.get("features") if isinstance(data, dict) else None
        if not feats:
            break

        for feat in feats:
            if max_features is not None and emitted >= max_features:
                return
            yield feat
            emitted += 1

        props = data.get("properties") if isinstance(data.get("properties"), dict) else {}
        exceeded = bool(props.get("exceededTransferLimit"))
        if not exceeded and len(feats) < take:
            break
        offset += len(feats)


def geojson_features_to_gdf(features: list[dict[str, Any]]) -> gpd.GeoDataFrame | None:
    if not features:
        return None
    fc = {"type": "FeatureCollection", "features": features}
    raw = json.dumps(fc).encode("utf-8")
    try:
        return gpd.read_file(io.BytesIO(raw))
    except Exception:
        return None


def sanitize_filename(title: str, idx: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._") or "layer"
    return f"{idx:03d}_{safe[:72]}"
