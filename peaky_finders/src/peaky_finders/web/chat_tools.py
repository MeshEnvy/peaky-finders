"""Map-aware tools for the Peaky web chat agent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.preset_io import chat_site_slug_for_pin, ensure_chat_site_in_preset
from peaky_finders.sites_job import peaky_projects_dir
from peaky_finders.web.chat_dem import (
    label_with_elevation_ft,
    looks_like_peak_label,
    query_project_dem_highest,
    snap_peak_to_local_dem,
)
from peaky_finders.web.geocode import GeocodeError, geocode_place_ranked
from peaky_finders.web.projects import project_geocode_aoi
from peaky_finders.web.viewshed_service import ensure_point_viewshed
from peaky_finders.web.viewshed_rasters import normalize_point_coords

ToolResult = dict[str, Any]
MapPinState = dict[str, Any]
EmitFn = Callable[[str, Any], None]

_LAT_LON = {
    "type": "object",
    "properties": {
        "lat": {"type": "number", "description": "WGS-84 latitude"},
        "lon": {"type": "number", "description": "WGS-84 longitude"},
    },
    "required": ["lat", "lon"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


WEB_TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "geocode_place",
        "Resolve a place name to WGS-84 coordinates (OpenStreetMap). Biases toward the active project AOI when set.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Place name, e.g. Charleston Peak, Nevada",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "query_project_dem_highest",
        (
            "Highest Skadi DEM elevation in the active project's AOI (or site extent). "
            "Required for questions about the tallest/highest peak or point in the project region — "
            "never answer peak height or ranking from memory."
        ),
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    ),
    _tool(
        "show_on_map",
        (
            "Drop a pin on the map, center the view, compute an RF viewshed overlay, and save the "
            "location to the project preset YAML as a planned site. Use for pin/viewshed requests — "
            "pass lat/lon/label from a prior query_project_dem_highest result or pin_id from map state; "
            "do not call query_project_dem_highest again just to place a pin."
        ),
        {
            "type": "object",
            "properties": {
                "pin_id": {
                    "type": "string",
                    "description": "Existing pin id from map state or a prior show_on_map result",
                },
                "lat": {"type": "number", "description": "WGS-84 latitude (required for new pins)"},
                "lon": {"type": "number", "description": "WGS-84 longitude (required for new pins)"},
                "label": {"type": "string", "description": "Pin label shown on the map"},
                "padding_deg": {
                    "type": "number",
                    "default": 0.08,
                    "description": "Map fit padding around the point in degrees",
                },
                "bbox": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional [west, south, east, north] from geocode_place",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    ),
]


@dataclass
class WebChatContext:
    project_slug: str | None
    emit: EmitFn | None = None
    geocode_viewbox: list[float] | None = None
    geocode_aoi: BaseGeometry | None = None
    geocode_calls: int = 0
    map_pins: list[MapPinState] | None = None

    @property
    def preset_path(self) -> Path | None:
        if not self.project_slug:
            return None
        cfg = peaky_projects_dir() / self.project_slug / "config.yaml"
        return cfg if cfg.is_file() else None


def _pin_id(label: str, lat: float, lon: float) -> str:
    raw = f"{label}:{lat:.6f}:{lon:.6f}".encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def normalize_map_pins(raw: list[MapPinState] | None) -> list[MapPinState]:
    out: list[MapPinState] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        pin_id = str(item.get("pin_id") or item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not pin_id:
            pin_id = _pin_id(label or f"{lat:.5f},{lon:.5f}", lat, lon)
        if pin_id in seen:
            continue
        seen.add(pin_id)
        pin: MapPinState = {
            "pin_id": pin_id,
            "label": label or f"{lat:.5f},{lon:.5f}",
            "lat": lat,
            "lon": lon,
        }
        if item.get("has_viewshed") is True:
            pin["has_viewshed"] = True
        site_slug = str(item.get("site_slug") or "").strip()
        if site_slug:
            pin["site_slug"] = site_slug
        out.append(pin)
    return out


def lookup_map_pin(pins: list[MapPinState], pin_id: str) -> MapPinState | None:
    needle = str(pin_id or "").strip()
    if not needle:
        return None
    for pin in pins:
        if str(pin.get("pin_id") or "") == needle:
            return pin
    return None


def remember_map_pin(
    ctx: WebChatContext,
    *,
    pin_id: str,
    label: str,
    lat: float,
    lon: float,
    site_slug: str,
    has_viewshed: bool,
) -> None:
    pins = normalize_map_pins(ctx.map_pins)
    updated = {
        "pin_id": pin_id,
        "label": label,
        "lat": lat,
        "lon": lon,
        "site_slug": site_slug,
        "has_viewshed": has_viewshed,
    }
    ctx.map_pins = [pin for pin in pins if str(pin.get("pin_id") or "") != pin_id] + [updated]


def format_map_pins_for_prompt(pins: list[MapPinState] | None) -> str:
    normalized = normalize_map_pins(pins)
    if not normalized:
        return "No chat-placed pins on the map yet."
    lines = [
        "Chat-placed pins currently on the map (use these exact coordinates or pin_id; never guess):"
    ]
    for pin in normalized:
        viewshed_note = ", viewshed shown" if pin.get("has_viewshed") else ""
        site_slug = str(pin.get("site_slug") or chat_site_slug_for_pin(str(pin["pin_id"])))
        lines.append(
            f"- pin_id={pin['pin_id']!r} site_slug={site_slug!r} label={pin['label']!r} "
            f"lat={float(pin['lat']):.6f} lon={float(pin['lon']):.6f}{viewshed_note}"
        )
    return "\n".join(lines)


def _apply_peak_dem_snap(
    ctx: WebChatContext,
    *,
    lat: float,
    lon: float,
    label: str,
    quality: str | None = None,
) -> tuple[float, float, str, dict[str, Any] | None]:
    if not ctx.project_slug:
        return lat, lon, label, None
    is_peak = quality == "peak" or looks_like_peak_label(label)
    if not is_peak:
        return lat, lon, label, None
    snap = snap_peak_to_local_dem(project_slug=ctx.project_slug, lat=lat, lon=lon)
    if snap is None:
        return lat, lon, label, None
    slat = float(snap["lat"])
    slon = float(snap["lon"])
    elev_ft = snap.get("elev_ft")
    new_label = label_with_elevation_ft(label, float(elev_ft) if elev_ft is not None else None)
    return slat, slon, new_label, snap


def _merge_peak_snap_into_geocode_hit(hit: dict[str, Any], snap: dict[str, Any]) -> None:
    hit["geocode_lat"] = hit["lat"]
    hit["geocode_lon"] = hit["lon"]
    hit["lat"] = float(snap["lat"])
    hit["lon"] = float(snap["lon"])
    hit["elev_m"] = snap.get("elev_m")
    hit["elev_ft"] = snap.get("elev_ft")
    hit["dem_snapped"] = bool(snap.get("dem_snapped"))
    hit["snap_shift_m"] = snap.get("shift_m")
    hit["snap_source"] = snap.get("source")


def dispatch_web_tool(name: str, args: dict[str, Any], *, ctx: WebChatContext) -> ToolResult:
    if name == "geocode_place":
        return _geocode_place(ctx, args)
    if name == "query_project_dem_highest":
        return _query_project_dem_highest(ctx)
    if name == "show_on_map":
        return _show_on_map(ctx, args)
    return {"error": f"unknown tool {name!r}"}


def _query_project_dem_highest(ctx: WebChatContext) -> ToolResult:
    if not ctx.project_slug:
        return {"error": "select a project before querying DEM elevations"}
    result = query_project_dem_highest(project_slug=ctx.project_slug)
    if "error" not in result and "lat" in result and "lon" in result:
        lat = float(result["lat"])
        lon = float(result["lon"])
        elev_ft = result.get("elev_ft")
        base_label = str(result.get("place_label") or "DEM highest point").strip()
        if elev_ft is not None:
            label = f"{base_label} ({int(elev_ft)} ft)"
        else:
            label = base_label
        pin_id = _pin_id(label, lat, lon)
        result["pin_id"] = pin_id
        result["label"] = label
        remember_map_pin(
            ctx,
            pin_id=pin_id,
            label=label,
            lat=lat,
            lon=lon,
            site_slug="",
            has_viewshed=False,
        )
        if ctx.emit:
            ctx.emit(
                "map.pin",
                layer_id="trials",
                id=pin_id,
                lat=lat,
                lon=lon,
                label=label,
            )
    return result


def _geocode_place(ctx: WebChatContext, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    if ctx.geocode_calls >= 2:
        return {
            "error": "geocode_place already called twice this turn; use the prior best result or ask the user to clarify",
        }
    ctx.geocode_calls += 1
    try:
        payload = geocode_place_ranked(
            query,
            viewbox=ctx.geocode_viewbox,
            aoi=ctx.geocode_aoi,
            project_slug=ctx.project_slug,
        )
    except GeocodeError as exc:
        return {"error": str(exc)}
    if not payload["results"]:
        return {"query": query, "results": [], "best": None, "error": "no matches", "hint": payload.get("hint")}
    best = payload.get("best")
    if isinstance(best, dict):
        orig_lat = float(best["lat"])
        orig_lon = float(best["lon"])
        lat, lon, label, snap = _apply_peak_dem_snap(
            ctx,
            lat=orig_lat,
            lon=orig_lon,
            label=str(best.get("display_name") or query),
            quality=str(best.get("quality") or ""),
        )
        if snap is not None:
            _merge_peak_snap_into_geocode_hit(best, snap)
            best["display_name"] = label
            for row in payload.get("results") or []:
                if (
                    isinstance(row, dict)
                    and float(row.get("lat", 0)) == orig_lat
                    and float(row.get("lon", 0)) == orig_lon
                ):
                    _merge_peak_snap_into_geocode_hit(row, snap)
                    row["display_name"] = label
                    break
    return payload


def _fit_bbox(lat: float, lon: float, *, padding_deg: float, place_bbox: list[float] | None) -> list[float]:
    if place_bbox and len(place_bbox) == 4:
        west, south, east, north = place_bbox
        if east > west and north > south:
            return [west, south, east, north]
    pad = max(0.01, float(padding_deg))
    return [lon - pad, lat - pad, lon + pad, lat + pad]


def _resolve_show_on_map_target(ctx: WebChatContext, args: dict[str, Any]) -> tuple[float, float, str, str] | ToolResult:
    pins = normalize_map_pins(ctx.map_pins)
    pin_id_arg = str(args.get("pin_id") or "").strip()
    if pin_id_arg:
        hit = lookup_map_pin(pins, pin_id_arg)
        if hit is None:
            return {"error": f"unknown pin_id {pin_id_arg!r}; use a pin from map state or place a new pin first"}
        label = str(args.get("label") or hit.get("label") or pin_id_arg).strip()
        return float(hit["lat"]), float(hit["lon"]), label, pin_id_arg

    try:
        lat = float(args["lat"])
        lon = float(args["lon"])
    except (KeyError, TypeError, ValueError):
        if pins:
            return {
                "error": (
                    "lat and lon are required for a new pin, or pass pin_id for an existing map pin"
                ),
                "map_pins": pins,
            }
        return {"error": "lat and lon are required numbers"}

    label = str(args.get("label") or f"{lat:.5f},{lon:.5f}").strip()
    return lat, lon, label, _pin_id(label, lat, lon)


def _show_on_map(ctx: WebChatContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.project_slug:
        return {"error": "select a project before showing locations on the map"}

    resolved = _resolve_show_on_map_target(ctx, args)
    if isinstance(resolved, dict):
        return resolved
    lat, lon, label, pin_id = resolved
    lat, lon = normalize_point_coords(lat, lon)

    peak_snap: dict[str, Any] | None = None
    if not str(args.get("pin_id") or "").strip():
        lat, lon, label, peak_snap = _apply_peak_dem_snap(ctx, lat=lat, lon=lon, label=label)
        if peak_snap is not None:
            pin_id = _pin_id(label, lat, lon)

    preset_path = ctx.preset_path
    if preset_path is None:
        return {"error": "project preset config.yaml not found"}

    padding_deg = float(args.get("padding_deg") or 0.08)
    place_bbox = args.get("bbox")
    bbox = _fit_bbox(lat, lon, padding_deg=padding_deg, place_bbox=place_bbox if isinstance(place_bbox, list) else None)

    if ctx.emit:
        ctx.emit(
            "map.pin",
            layer_id="trials",
            id=pin_id,
            lat=lat,
            lon=lon,
            label=label,
        )

    try:
        site_slug = ensure_chat_site_in_preset(
            preset_path,
            pin_id=pin_id,
            name=label,
            lat=lat,
            lon=lon,
        )
    except Exception as exc:
        if ctx.emit:
            ctx.emit("map.fit_bounds", bbox=bbox)
        return {
            "ok": False,
            "lat": lat,
            "lon": lon,
            "label": label,
            "pin_id": pin_id,
            "bbox": bbox,
            "preset_error": str(exc),
        }

    viewshed: dict[str, Any] | None = None
    try:
        viewshed = ensure_point_viewshed(
            project_slug=ctx.project_slug,
            lat=lat,
            lon=lon,
        )
        if ctx.emit:
            ctx.emit("map.viewshed", **viewshed)
            viewshed_bounds = viewshed.get("bounds")
            if isinstance(viewshed_bounds, list) and len(viewshed_bounds) == 4:
                ctx.emit("map.fit_bounds", bbox=viewshed_bounds)
            else:
                ctx.emit("map.fit_bounds", bbox=bbox)
    except Exception as exc:
        if ctx.emit:
            ctx.emit("map.fit_bounds", bbox=bbox)
        remember_map_pin(
            ctx,
            pin_id=pin_id,
            label=label,
            lat=lat,
            lon=lon,
            site_slug=site_slug,
            has_viewshed=False,
        )
        return {
        "ok": True,
        "lat": lat,
        "lon": lon,
        "label": label,
        "pin_id": pin_id,
        "site_slug": site_slug,
        "bbox": bbox,
        "saved_to_preset": True,
        "viewshed_error": str(exc),
        **({"peak_snap": peak_snap} if peak_snap else {}),
    }

    remember_map_pin(
        ctx,
        pin_id=pin_id,
        label=label,
        lat=lat,
        lon=lon,
        site_slug=site_slug,
        has_viewshed=True,
    )

    out = {
        "ok": True,
        "lat": lat,
        "lon": lon,
        "label": label,
        "pin_id": pin_id,
        "site_slug": site_slug,
        "bbox": bbox,
        "saved_to_preset": True,
        "viewshed": viewshed,
    }
    if peak_snap:
        out["peak_snap"] = peak_snap
    return out


def truncate_tool_result_for_stream(result: ToolResult, *, max_chars: int = 4000) -> ToolResult:
    text = json.dumps(result, default=str)
    if len(text) <= max_chars:
        return result
    return {"truncated": True, "preview": text[: max_chars - 3] + "..."}


def web_chat_context_for_project(
    project_slug: str | None,
    *,
    map_pins: list[MapPinState] | None = None,
) -> WebChatContext:
    geocode_viewbox = None
    geocode_aoi = None
    if project_slug:
        geocode_viewbox, geocode_aoi = project_geocode_aoi(project_slug)
    return WebChatContext(
        project_slug=project_slug,
        geocode_viewbox=geocode_viewbox,
        geocode_aoi=geocode_aoi,
        map_pins=normalize_map_pins(map_pins),
    )


def web_chat_system_prompt(
    *,
    project_slug: str | None,
    map_pins: list[MapPinState] | None = None,
) -> str:
    project_line = (
        f"Active project: {project_slug!r}."
        if project_slug
        else "No project selected yet."
    )
    return (
        "You are a helpful assistant for Peaky, a mesh radio site planning tool with an interactive map. "
        "Be concise, conversational, and friendly — talk to the user, not about them.\n\n"
        f"{project_line}\n\n"
        "Answer general questions (RF planning, how things work) directly in plain language. "
        "Do not narrate your reasoning, planning, or tool choices. Never say things like "
        "\"the user is asking\" or \"I should use a tool\".\n\n"
        "Peak and elevation facts (critical):\n"
        "- Never state which peak is highest/tallest or quote elevations from memory.\n"
        "- With an active project, call query_project_dem_highest once for "
        "\"highest peak\" / tallest-point questions — report tool lat/lon/elev_m/elev_ft/place_label/pin_id.\n"
        "- Pin or viewshed on that point: call show_on_map with those exact lat/lon (or pin_id) — "
        "never re-run query_project_dem_highest for placement.\n"
        "- Without a project, say you need a project selected (or offer to map a named peak via geocode).\n\n"
        "Map tools: place a pin, show a place, compute a viewshed. "
        "show_on_map always saves a planned site to the project YAML and computes its viewshed.\n"
        "Coordinate rules (critical):\n"
        "- Never recall or invent lat/lon from memory.\n"
        "- New places: geocode_place once, then show_on_map with the returned best lat/lon/label/bbox.\n"
        "- geocode_place snaps named peaks to the local Skadi DEM high point when project DEM tiles exist.\n"
        "- Follow-ups on an existing pin ('that one', 'there', 'add a viewshed'): use show_on_map with "
        "pin_id from map state below — do not pass new coordinates.\n"
        "- Copy coordinates exactly from tool results or map state; do not round or substitute.\n\n"
        f"{format_map_pins_for_prompt(map_pins)}\n\n"
        "Geocoding rules:\n"
        "- Call geocode_place once per new place.\n"
        "- With an active project, geocode_place biases toward the project AOI; use the returned best.\n"
        "- Use the returned best result unless quality is likely_street_not_peak.\n"
        "- Do not retry geocode with rephrased queries; ask the user to clarify instead.\n"
        "- For mountains/peaks without a project, include state in the query (e.g. 'Charleston Peak, Nevada').\n"
        "Confirm briefly what you placed.\n\n"
        "After reporting DEM highest-point facts, offer show_on_map only if they want it on the map."
    )
