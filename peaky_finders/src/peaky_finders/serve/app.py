"""Route dispatcher and WSGI app for ``peaky serve``."""

from __future__ import annotations

import json
import re
import threading
import time
import base64
import binascii
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, parse_qs, urlparse

from pydantic import ValidationError

from peaky_finders.core.project.scaffold import discover_projects, scaffold_project, validate_project_slug
from peaky_finders.serve.link_jobs import warm_project_site_links
from peaky_finders.serve.home import (
    create_environment_preset,
    create_modem_preset,
    delete_environment_preset,
    delete_modem_preset,
    get_environment_catalog_payload,
    get_home_simulation_payload,
    get_modem_catalog_payload,
    patch_environment_preset,
    patch_home_simulation,
    patch_modem_preset,
)
from peaky_finders.serve.html import home_settings_modal_html, landing_html, project_error_html, project_html
from peaky_finders.serve.links import (
    ServeLinksError,
    compute_single_site_links,
    evaluate_site_pair_linked,
    load_project_site_links,
)
from peaky_finders.serve.plss import apply_plss_from_loc_cache
from peaky_finders.serve.site_prefetch import ServeSitePrefetchError, load_site_placement_prefetch
from peaky_finders.serve.simulation import (
    get_project_simulation_payload,
    patch_project_simulation,
    update_viewshed_sim_to_preset,
)
from peaky_finders.serve.sites import (
    append_planned_site_to_preset,
    bulk_merge_site_tags_in_preset_rows,
    delete_site_from_preset,
    import_sites_to_preset,
    patch_site_tags_in_preset,
    update_site_in_preset,
)
from peaky_finders.serve.kml_import import (
    KmlPointSite,
    parse_kml_point_placemarks,
    parse_kmz_point_placemarks,
    serialize_kml_point,
)
from peaky_finders.core.preset import LandLayerEntry, LandSidebar, LandSidebarFolder, load_preset
from peaky_finders.serve.land import (
    add_land_source,
    delete_land_source,
    list_land_payload,
    parse_land_layer_entries,
    patch_land_sidebar,
    patch_land_source,
    read_layer_geojson_bytes,
)
from peaky_finders.serve.land_import import (
    ensure_layer_preview_geojson,
    list_data_gdbs,
    list_field_values,
    list_gdb_layers,
    list_layer_fields,
    resolve_land_gdb_path,
    serialize_layer_info,
)
from peaky_finders.serve.events import get_serve_event_hub
from peaky_finders.serve.preset_cache import load_serve_project_context, patch_serve_project_site_tags
from peaky_finders.serve.viewshed import (
    DRAFT_VIEWSHED_SLUG,
    ServeViewshedError,
    _preview_site_at,
    read_site_viewshed_png_if_ready,
    site_viewshed_overlay_if_ready,
)
from peaky_finders.serve.viewshed_jobs import warm_coords_viewshed, warm_site_viewshed
from peaky_finders.serve.project_warm_scheduler import (
    PRIORITY_INTERACTIVE,
    PRIORITY_VIEWPORT,
    ensure_project_warm,
    invalidate_site_coverage,
    schedule_bump_project_priorities,
)
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides, parse_viewshed_sim_overrides
from peaky_finders.core.preset import (
    Preset,
    SiteEntry,
    load_preset,
)

SERVE_STATIC_DIR = Path(__file__).resolve().parent / "static"

_SERVE_STATIC_FILES: dict[str, tuple[str, str]] = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon-96x96.png": ("favicon-96x96.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
    "/web-app-manifest-192x192.png": ("web-app-manifest-192x192.png", "image/png"),
    "/web-app-manifest-512x512.png": ("web-app-manifest-512x512.png", "image/png"),
}

_STATIC_MIME: dict[str, str] = {
    ".css": "text/css",
    ".js": "application/javascript",
}

_PROJECT_PATH_RE = re.compile(r"^/p/([a-zA-Z][a-zA-Z0-9_-]*)/?$")
_API_PROJECT_SITES_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/?$")
_API_PROJECT_SITES_IMPORT_PREVIEW_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/import/preview/?$"
)
_API_PROJECT_SITES_IMPORT_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/import/?$"
)
_API_PROJECT_SITES_TAGS_BULK_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/tags/bulk/?$"
)
_API_PROJECT_LAND_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/?$")
_API_PROJECT_LAND_DATA_GDBS_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/data-gdbs/?$"
)
_API_PROJECT_LAND_IMPORT_PREVIEW_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/import/preview/?$"
)
_API_PROJECT_LAND_IMPORT_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/import/?$"
)
_API_PROJECT_LAND_IMPORT_PREVIEW_FIELDS_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/import/preview/fields/?$"
)
_API_PROJECT_LAND_IMPORT_PREVIEW_VALUES_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/import/preview/values/?$"
)
_API_PROJECT_LAND_PREVIEW_GEOJSON_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/preview/geojson/?$"
)
_API_PROJECT_LAND_SIDEBAR_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/sidebar/?$"
)
_API_PROJECT_LAND_SOURCE_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/sources/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_LAND_LAYER_GEOJSON_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/land/sources/([a-zA-Z][a-zA-Z0-9_-]*)/layers/([^/]+)/geojson/?$"
)
_API_PROJECT_SITE_SLUG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_SITES_PREFETCH_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/prefetch/?$"
)
_API_PROJECT_VIEWSHED_PREFETCH_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/prefetch/?$"
)
_API_PROJECT_VIEWSHED_PREFETCH_PNG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/prefetch/splat\.png$"
)
_API_PROJECT_EVENTS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/events/?$")
_API_PROJECT_VIEWSHED_WARM_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/([a-zA-Z][a-zA-Z0-9_-]*)/warm/?$"
)
_API_PROJECT_VIEWSHED_PREFETCH_WARM_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/prefetch/warm/?$"
)
_API_PROJECT_VIEWSHED_META_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/([a-zA-Z][a-zA-Z0-9_-]*)/?$")
_API_PROJECT_VIEWSHED_PNG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/([a-zA-Z][a-zA-Z0-9_-]*)/splat\.png$"
)
_API_PROJECT_LINK_PAIR_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/links/([a-zA-Z][a-zA-Z0-9_-]*)/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_LINKS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/links/?$")
_API_PROJECT_LINKS_WARM_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/links/warm/?$")
_API_PROJECT_SITE_LINKS_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/([a-zA-Z][a-zA-Z0-9_-]*)/links/?$"
)
_API_PROJECT_WARM_PRIORITIES_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/warm/priorities/?$"
)
_API_PROJECT_SIMULATION_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/simulation/?$")
_API_HOME_SIMULATION_RE = re.compile(r"^/api/home/simulation/?$")
_API_HOME_MODEMS_RE = re.compile(r"^/api/home/modems/?$")
_API_HOME_MODEM_NAME_RE = re.compile(r"^/api/home/modems/([a-zA-Z][a-zA-Z0-9_-]+)/?$")
_API_HOME_ENVIRONMENTS_RE = re.compile(r"^/api/home/environments/?$")
_API_HOME_ENVIRONMENT_NAME_RE = re.compile(r"^/api/home/environments/([a-zA-Z][a-zA-Z0-9_-]+)/?$")


def _parse_json_body(body: bytes) -> object:
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _parse_kml_import_payload(raw: dict[str, object]) -> tuple[list[KmlPointSite], int]:
    """Parse KML or KMZ base64 from a JSON import/preview body."""
    kmz_b64 = raw.get("kmz_b64")
    if kmz_b64 is not None and str(kmz_b64).strip():
        try:
            data = base64.b64decode(str(kmz_b64), validate=True)
        except (ValueError, binascii.Error) as e:
            raise ValueError(f"invalid kmz_b64: {e}") from e
        return parse_kmz_point_placemarks(data)

    kml = raw.get("kml")
    if kml is not None and str(kml).strip():
        return parse_kml_point_placemarks(str(kml).encode("utf-8"))

    raise ValueError("kml or kmz_b64 required")


def _coerce_import_point(item: object, *, index: int) -> KmlPointSite:
    if not isinstance(item, dict):
        raise ValueError(f"points[{index}] must be an object")
    try:
        lat = float(item["lat"])
        lon = float(item["lon"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"points[{index}] requires lat and lon") from e
    name = str(item.get("name", "")).strip() or "Unnamed site"
    if item.get("elevation_m") is not None:
        raise ValueError(f"points[{index}].elevation_m is removed")
    return KmlPointSite(name=name, lat=lat, lon=lon)


def _parse_import_sites_body(raw: dict[str, object]) -> tuple[list[KmlPointSite], int]:
    """Parse explicit import points or fall back to KML/KMZ payload."""
    points_raw = raw.get("points")
    if points_raw is not None:
        if not isinstance(points_raw, list):
            raise ValueError("points must be a list")
        if not points_raw:
            raise ValueError("points must not be empty")
        sites = [_coerce_import_point(item, index=i) for i, item in enumerate(points_raw)]
        return sites, 0
    return _parse_kml_import_payload(raw)


def _parse_land_path_field(raw: Mapping[str, object]) -> str:
    path = raw.get("path")
    if path is None or not str(path).strip():
        raise ValueError("path is required")
    return str(path).strip().replace("\\", "/")


def _parse_land_layers_field(raw: Mapping[str, object]) -> list[LandLayerEntry]:
    layers_raw = raw.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise ValueError("layers must be a non-empty list")

    styles_raw = raw.get("layer_styles")
    if styles_raw is None:
        styles_raw = raw.get("layerStyles")

    merged: list[Any] = []
    for item in layers_raw:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                continue
            entry: dict[str, Any] = {"name": name}
            if isinstance(styles_raw, dict) and name in styles_raw:
                entry["style"] = styles_raw[name]
            merged.append(entry)
        else:
            merged.append(item)

    return parse_land_layer_entries(merged)


def _parse_land_layer_entry_body(raw: Mapping[str, object], *, layer_name: str) -> LandLayerEntry:
    payload: dict[str, Any] = {"name": layer_name}
    for src, dst in (
        ("id", "id"),
        ("role", "role"),
        ("label_field", "label_field"),
        ("labelField", "label_field"),
        ("style_field", "style_field"),
        ("styleField", "style_field"),
        ("include", "include"),
        ("exclude", "exclude"),
        ("style", "style"),
    ):
        if src in raw and raw[src] is not None:
            payload[dst] = raw[src]
    return LandLayerEntry.model_validate(payload)


def _parse_land_sidebar_body(raw: Mapping[str, object]) -> LandSidebar:
    folders_raw = raw.get("folders")
    if folders_raw is None:
        folders_raw = []
    if not isinstance(folders_raw, list):
        raise ValueError("folders must be a list")
    folders: list[LandSidebarFolder] = []
    for item in folders_raw:
        if not isinstance(item, dict):
            raise ValueError("each folder must be an object")
        folder_id = item.get("id")
        label = item.get("label")
        if folder_id is None or not str(folder_id).strip():
            raise ValueError("folder id is required")
        if label is None or not str(label).strip():
            raise ValueError("folder label is required")
        sources_raw = item.get("sources")
        if sources_raw is None:
            sources_raw = []
        if not isinstance(sources_raw, list):
            raise ValueError("folder sources must be a list")
        folders.append(
            LandSidebarFolder(
                id=str(folder_id).strip(),
                label=str(label).strip(),
                sources=[str(s).strip() for s in sources_raw if str(s).strip()],
            )
        )
    unfiled_raw = raw.get("unfiledSources")
    if unfiled_raw is None:
        unfiled_raw = raw.get("unfiled_sources")
    if unfiled_raw is None:
        unfiled_raw = []
    if not isinstance(unfiled_raw, list):
        raise ValueError("unfiledSources must be a list")
    return LandSidebar(
        folders=folders,
        unfiled_sources=[str(s).strip() for s in unfiled_raw if str(s).strip()],
    )


def _parse_form_body(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def _read_serve_static(url_path: str) -> tuple[bytes, str] | None:
    if not url_path.startswith("/static/"):
        return None
    rel = url_path.removeprefix("/static/")
    if not rel or any(part == ".." for part in Path(rel).parts):
        return None
    suffix = Path(rel).suffix.lower()
    content_type = _STATIC_MIME.get(suffix)
    if content_type is None:
        return None
    file_path = (SERVE_STATIC_DIR / rel).resolve()
    try:
        file_path.relative_to(SERVE_STATIC_DIR.resolve())
    except ValueError:
        return None
    if not file_path.is_file():
        return None
    return file_path.read_bytes(), content_type


def _load_project_sites(project_dir: Path) -> dict[str, SiteEntry]:
    return load_serve_project_context(project_dir).sites


def _parse_lat_lon_query(query: str) -> tuple[float, float]:
    qs = parse_qs(query)
    try:
        lat = float(qs.get("lat", [""])[0])
        lon = float(qs.get("lon", [""])[0])
    except (IndexError, TypeError, ValueError) as e:
        raise ValueError(f"lat and lon query params required: {e}") from e
    return lat, lon


def _parse_viewshed_sim_query(query: str) -> ViewshedSimOverrides:
    try:
        return parse_viewshed_sim_overrides(query)
    except ValueError as e:
        raise ValueError(str(e)) from e


def _serialize_serve_simulation(preset: Preset) -> dict[str, object]:
    sim = preset.simulation
    tx = sim.transmitter if isinstance(sim.transmitter, dict) else {}
    return {
        "radius_km": float(sim.radius_km),
        "raster_dimension": int(sim.raster_dimension),
        "radius_km_min": 1,
        "radius_km_max": 100,
        "raster_dimension_min": 128,
        "raster_dimension_max": 4096,
        "transmitter": {
            "height_m": float(tx.get("height_m", 2.0) or 2.0),
        },
    }


def _serialize_project_sites(sites: dict[str, SiteEntry]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for site_slug, entry in sorted(sites.items()):
        row: dict[str, object] = {
            "slug": site_slug,
            "name": entry.name,
            "lat": entry.lat,
            "lon": entry.lon,
            "tags": list(entry.tags),
        }
        if entry.height_m is not None:
            row["height_m"] = entry.height_m
        for key in ("description", "plss"):
            val = getattr(entry, key)
            if val and str(val).strip():
                row[key] = str(val).strip()
        out.append(row)
    return out


@dataclass
class ServeResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes = b""
    body_iter: Iterable[bytes] | None = None


class ServeDispatcher:
    def __init__(self, projects_dir: Path, *, verbose: bool):
        self.projects_dir = projects_dir
        self.verbose = verbose
        self._response: ServeResponse | None = None

    def dispatch(self, method: str, path: str, query: str, body: bytes) -> ServeResponse:
        self._response = None
        url = path + (f"?{query}" if query else "")
        parsed = urlparse(url)
        if method == "GET":
            self._do_get(parsed)
        elif method == "POST":
            self._do_post(parsed, body)
        elif method == "PATCH":
            self._do_patch(parsed, body)
        elif method == "DELETE":
            self._do_delete(parsed)
        else:
            self.send_error(405)
        if self._response is None:
            return ServeResponse(500, [("Content-Type", "text/plain")], b"internal error")
        return self._response

    def _send_html(self, body: bytes, *, status: int = 200) -> None:
        self._send_bytes(
            body,
            "text/html; charset=utf-8",
            status=status,
            extra_headers={"Cache-Control": "no-store"},
        )

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        headers: list[tuple[str, str]] = [("Content-Type", content_type)]
        if extra_headers:
            headers.extend(list(extra_headers.items()))
        headers.append(("Content-Length", str(len(body))))
        self._response = ServeResponse(status=status, headers=headers, body=body)

    def _send_sse_stream(self, project_slug: str) -> None:
        headers: list[tuple[str, str]] = [
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Cache-Control", "no-cache"),
            ("X-Accel-Buffering", "no"),
        ]
        self._response = ServeResponse(
            status=200,
            headers=headers,
            body_iter=get_serve_event_hub().subscribe(project_slug),
        )

    def send_error(self, status: int) -> None:
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = "Error"
        body = f"{status} {phrase}\n".encode("utf-8")
        self._send_bytes(body, "text/plain; charset=utf-8", status=status)

    def _redirect(self, location: str) -> None:
        self._response = ServeResponse(
            status=303,
            headers=[("Location", location), ("Content-Length", "0")],
            body=b"",
        )

    def _do_get(self, parsed: ParseResult) -> None:
        parsed_url = parsed
        path = parsed_url.path

        if path in ("/", "/index.html"):
            projects = discover_projects(self.projects_dir)
            self._send_html(landing_html(self.projects_dir, projects))
            return

        if path == "/api/projects":
            slugs = discover_projects(self.projects_dir)
            payload = json.dumps(
                {"projects": [{"slug": s} for s in slugs], "root": str(self.projects_dir)}
            ).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        if _API_HOME_SIMULATION_RE.match(path):
            try:
                payload = json.dumps(get_home_simulation_payload(), sort_keys=True).encode("utf-8")
            except (ValueError, OSError) as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            self._send_bytes(payload, "application/json")
            return

        if _API_HOME_MODEMS_RE.match(path):
            try:
                payload = json.dumps(get_modem_catalog_payload(), sort_keys=True).encode("utf-8")
            except (ValueError, OSError) as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            self._send_bytes(payload, "application/json")
            return

        home_modem_match = _API_HOME_MODEM_NAME_RE.match(path)
        if home_modem_match:
            name = home_modem_match.group(1)
            try:
                catalog = get_modem_catalog_payload()
            except (ValueError, OSError) as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            preset = catalog.get("presets", {}).get(name)
            if preset is None:
                self.send_error(404)
                return
            payload = json.dumps({"name": name, "preset": preset}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        if _API_HOME_ENVIRONMENTS_RE.match(path):
            try:
                payload = json.dumps(get_environment_catalog_payload(), sort_keys=True).encode("utf-8")
            except (ValueError, OSError) as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            self._send_bytes(payload, "application/json")
            return

        home_env_match = _API_HOME_ENVIRONMENT_NAME_RE.match(path)
        if home_env_match:
            name = home_env_match.group(1)
            try:
                catalog = get_environment_catalog_payload()
            except (ValueError, OSError) as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            preset = catalog.get("presets", {}).get(name)
            if preset is None:
                self.send_error(404)
                return
            payload = json.dumps({"name": name, "preset": preset}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        api_match = _API_PROJECT_SITES_RE.match(path)
        if api_match:
            slug = api_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                sites = _serialize_project_sites(_load_project_sites(project_dir))
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps({"slug": slug, "sites": sites}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        land_match = _API_PROJECT_LAND_RE.match(path)
        if land_match:
            slug = land_match.group(1)
            preset_path = self.projects_dir / slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                payload_obj = list_land_payload(preset_path)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps({"slug": slug, **payload_obj}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        land_gdbs_match = _API_PROJECT_LAND_DATA_GDBS_RE.match(path)
        if land_gdbs_match:
            slug = land_gdbs_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            paths = list_data_gdbs(project_dir)
            payload = json.dumps({"slug": slug, "paths": paths}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        land_preview_fields_match = _API_PROJECT_LAND_IMPORT_PREVIEW_FIELDS_RE.match(path)
        if land_preview_fields_match:
            slug = land_preview_fields_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            qs = parse_qs(parsed_url.query)
            rel_path = str(qs.get("path", [""])[0]).strip()
            layer = str(qs.get("layer", [""])[0]).strip()
            if not rel_path or not layer:
                payload = json.dumps(
                    {"slug": slug, "error": "path and layer query parameters are required"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                gdb_path = resolve_land_gdb_path(project_dir, rel_path)
                fields_payload = list_layer_fields(gdb_path, layer)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps({"slug": slug, "path": rel_path, **fields_payload}, sort_keys=True).encode(
                "utf-8"
            )
            self._send_bytes(payload, "application/json")
            return

        land_preview_values_match = _API_PROJECT_LAND_IMPORT_PREVIEW_VALUES_RE.match(path)
        if land_preview_values_match:
            slug = land_preview_values_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            qs = parse_qs(parsed_url.query)
            rel_path = str(qs.get("path", [""])[0]).strip()
            layer = str(qs.get("layer", [""])[0]).strip()
            field = str(qs.get("field", [""])[0]).strip()
            if not rel_path or not layer or not field:
                payload = json.dumps(
                    {"slug": slug, "error": "path, layer, and field query parameters are required"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                gdb_path = resolve_land_gdb_path(project_dir, rel_path)
                values_payload = list_field_values(gdb_path, layer, field)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": slug, "path": rel_path, "layer": layer, **values_payload},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        land_preview_geojson_match = _API_PROJECT_LAND_PREVIEW_GEOJSON_RE.match(path)
        if land_preview_geojson_match:
            slug = land_preview_geojson_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            qs = parse_qs(parsed_url.query)
            rel_path = str(qs.get("path", [""])[0]).strip()
            layer = str(qs.get("layer", [""])[0]).strip()
            if not rel_path or not layer:
                payload = json.dumps(
                    {"slug": slug, "error": "path and layer query parameters are required"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                gdb_path = resolve_land_gdb_path(project_dir, rel_path)
                geojson = ensure_layer_preview_geojson(project_dir, gdb_path, layer)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": slug, "path": rel_path, "layer": layer, "geojson": geojson},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        land_layer_geojson_match = _API_PROJECT_LAND_LAYER_GEOJSON_RE.match(path)
        if land_layer_geojson_match:
            slug = land_layer_geojson_match.group(1)
            source_id = land_layer_geojson_match.group(2)
            layer = land_layer_geojson_match.group(3)
            preset_path = self.projects_dir / slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                body, digest = read_layer_geojson_bytes(preset_path, source_id, layer)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            self._send_bytes(
                body,
                "application/geo+json",
                extra_headers={"X-Peaky-Digest": digest},
            )
            return

        sites_prefetch_match = _API_PROJECT_SITES_PREFETCH_RE.match(path)
        if sites_prefetch_match:
            slug = sites_prefetch_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                lat, lon = _parse_lat_lon_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            qs = parse_qs(parsed_url.query)
            exclude_site = str(qs.get("exclude_site", [""])[0]).strip() or None
            verbose = bool(self.verbose)
            try:
                payload_obj = load_site_placement_prefetch(
                    project_dir,
                    lat,
                    lon,
                    exclude_site_slug=exclude_site,
                    verbose=verbose,
                )
            except ServeSitePrefetchError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            payload = json.dumps({"project": slug, **payload_obj}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        site_links_match = _API_PROJECT_SITE_LINKS_RE.match(path)
        if site_links_match:
            slug = site_links_match.group(1)
            site_slug = site_links_match.group(2)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "site": site_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                result = compute_single_site_links(project_dir, site_slug, site_map)
            except ServeLinksError as e:
                payload = json.dumps({"slug": slug, "site": site_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": slug, "site": site_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            # Queue anything missing at interactive priority so a re-fetch completes.
            pending = [site_slug] if not result.get("center_footprint") else []
            pending += list(result.get("missing") or [])
            if pending:
                schedule_bump_project_priorities(
                    slug,
                    project_dir,
                    pending,
                    priority=PRIORITY_INTERACTIVE,
                    verbose=bool(self.verbose),
                )
            payload = json.dumps({"project": slug, **result}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        link_pair_match = _API_PROJECT_LINK_PAIR_RE.match(path)
        if link_pair_match:
            slug = link_pair_match.group(1)
            site_a = link_pair_match.group(2)
            site_b = link_pair_match.group(3)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps(
                    {"slug": slug, "a": site_a, "b": site_b, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                result = evaluate_site_pair_linked(
                    project_dir,
                    site_a,
                    site_b,
                    site_map,
                    verbose=verbose,
                )
            except ServeLinksError as e:
                payload = json.dumps(
                    {"slug": slug, "a": site_a, "b": site_b, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            payload = json.dumps({"project": slug, **result}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        project_sim_match = _API_PROJECT_SIMULATION_RE.match(path)
        if project_sim_match:
            project_slug = project_sim_match.group(1)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                payload_obj = get_project_simulation_payload(preset_path)
            except (ValueError, ValidationError, OSError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps({"slug": project_slug, **payload_obj}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        links_match = _API_PROJECT_LINKS_RE.match(path)
        if links_match:
            slug = links_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                payload_obj = load_project_site_links(
                    project_dir,
                    site_map,
                    verbose=verbose,
                )
            except ServeLinksError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            ensure_project_warm(
                slug,
                project_dir,
                site_map,
                verbose=verbose,
            )
            payload = json.dumps({"project": slug, **payload_obj}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        events_match = _API_PROJECT_EVENTS_RE.match(path)
        if events_match:
            slug = events_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
                ensure_project_warm(slug, project_dir, site_map, verbose=bool(self.verbose))
            except (ValueError, ValidationError):
                pass
            self._send_sse_stream(slug)
            return

        viewshed_prefetch_png_match = _API_PROJECT_VIEWSHED_PREFETCH_PNG_RE.match(path)
        if viewshed_prefetch_png_match:
            slug = viewshed_prefetch_png_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                lat, lon = _parse_lat_lon_query(parsed_url.query)
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            site = _preview_site_at(lat, lon)
            png_path = read_site_viewshed_png_if_ready(
                project_dir,
                site,
                site_slug=DRAFT_VIEWSHED_SLUG,
                sim_overrides=sim_overrides,
            )
            if png_path is None:
                self.send_error(404)
                return
            try:
                body = png_path.read_bytes()
            except OSError:
                self.send_error(503)
                return
            self._send_bytes(body, "image/png")
            return

        viewshed_prefetch_match = _API_PROJECT_VIEWSHED_PREFETCH_RE.match(path)
        if viewshed_prefetch_match:
            slug = viewshed_prefetch_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                lat, lon = _parse_lat_lon_query(parsed_url.query)
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps(
                    {"slug": slug, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            site = _preview_site_at(lat, lon)
            overlay = site_viewshed_overlay_if_ready(
                slug,
                project_dir,
                DRAFT_VIEWSHED_SLUG,
                site,
                sim_overrides=sim_overrides,
            )
            if overlay is None:
                self.send_error(404)
                return
            payload = json.dumps({"project": slug, **overlay, "lat": lat, "lon": lon}).encode(
                "utf-8"
            )
            self._send_bytes(payload, "application/json")
            return

        viewshed_meta_match = _API_PROJECT_VIEWSHED_META_RE.match(path)
        if viewshed_meta_match:
            slug = viewshed_meta_match.group(1)
            site_slug = viewshed_meta_match.group(2)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "site": site_slug, "error": str(e)}).encode(
                    "utf-8"
                )
                self._send_bytes(payload, "application/json", status=422)
                return
            if site_slug not in site_map:
                self.send_error(404)
                return
            try:
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps(
                    {"slug": slug, "site": site_slug, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            overlay = site_viewshed_overlay_if_ready(
                slug,
                project_dir,
                site_slug,
                site_map[site_slug],
                sim_overrides=sim_overrides,
            )
            if overlay is None:
                warm_site_viewshed(
                    slug,
                    project_dir,
                    site_slug,
                    site_map[site_slug],
                    sim_overrides=sim_overrides,
                    priority=PRIORITY_INTERACTIVE,
                )
                self.send_error(404)
                return
            payload = json.dumps({"project": slug, **overlay}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        viewshed_png_match = _API_PROJECT_VIEWSHED_PNG_RE.match(path)
        if viewshed_png_match:
            slug = viewshed_png_match.group(1)
            site_slug = viewshed_png_match.group(2)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError):
                self.send_error(404)
                return
            if site_slug not in site_map:
                self.send_error(404)
                return
            try:
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps(
                    {"slug": slug, "site": site_slug, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            png_path = read_site_viewshed_png_if_ready(
                project_dir,
                site_map[site_slug],
                site_slug=site_slug,
                sim_overrides=sim_overrides,
            )
            if png_path is None:
                self.send_error(404)
                return
            try:
                body = png_path.read_bytes()
            except OSError:
                self.send_error(503)
                return
            self._send_bytes(body, "image/png")
            return

        match = _PROJECT_PATH_RE.match(path)
        if match:
            slug = match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                preset = load_preset(project_dir / "config.yaml")
                sites = preset.sites
            except (ValueError, ValidationError) as e:
                self._send_html(project_error_html(slug, project_dir, str(e)), status=422)
                return
            land_payload = list_land_payload(project_dir / "config.yaml")
            self._send_html(
                project_html(
                    slug,
                    project_dir,
                    _serialize_project_sites(sites),
                    simulation=_serialize_serve_simulation(preset),
                    land=land_payload["sources"],
                    land_data_gdbs=list_data_gdbs(project_dir),
                    land_aoi_digest=land_payload.get("aoiDigest"),
                    land_sidebar=land_payload.get("sidebar"),
                )
            )
            return

        static_asset = _read_serve_static(path)
        if static_asset is not None:
            body, content_type = static_asset
            try:
                self._send_bytes(body, content_type)
            except OSError:
                self.send_error(503)
            return

        static = _SERVE_STATIC_FILES.get(path)
        if static is not None:
            filename, content_type = static
            file_path = SERVE_STATIC_DIR / filename
            if file_path.is_file():
                try:
                    self._send_bytes(file_path.read_bytes(), content_type)
                except OSError:
                    self.send_error(503)
                return

        self.send_error(404)

    def _do_post(self, parsed: ParseResult, body: bytes) -> None:
        parsed_url = parsed
        path = parsed_url.path

        if _API_HOME_MODEMS_RE.match(path):
            try:
                raw = self._parse_entity_patch_body(body)
                result = create_modem_preset(raw)
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(result, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=201)
            return

        if _API_HOME_ENVIRONMENTS_RE.match(path):
            try:
                raw = self._parse_entity_patch_body(body)
                result = create_environment_preset(raw)
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(result, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=201)
            return

        links_warm_match = _API_PROJECT_LINKS_WARM_RE.match(path)
        if links_warm_match:
            slug = links_warm_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            priority_slugs: list[str] | None = None
            if body.strip():
                try:
                    raw = _parse_json_body(body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raw = None
                if isinstance(raw, dict):
                    slugs_raw = raw.get("priority_slugs")
                    if isinstance(slugs_raw, list):
                        priority_slugs = [str(s) for s in slugs_raw if str(s).strip()]
            verbose = bool(self.verbose)
            result = warm_project_site_links(
                slug,
                project_dir,
                site_map,
                verbose=verbose,
                priority_slugs=priority_slugs,
            )
            status = 200 if result.get("status") == "ready" else 202
            payload = json.dumps(result).encode("utf-8")
            self._send_bytes(payload, "application/json", status=status)
            return

        warm_priorities_match = _API_PROJECT_WARM_PRIORITIES_RE.match(path)
        if warm_priorities_match:
            slug = warm_priorities_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps({"slug": slug, "error": f"invalid JSON: {e}"}).encode(
                    "utf-8"
                )
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            slugs_raw = raw.get("slugs")
            if not isinstance(slugs_raw, list):
                payload = json.dumps(
                    {"slug": slug, "error": "slugs must be a list of strings"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            slugs_list = [str(s).strip() for s in slugs_raw if str(s).strip()]
            priority_raw = raw.get("priority", PRIORITY_VIEWPORT)
            try:
                priority = int(priority_raw)
            except (TypeError, ValueError):
                priority = PRIORITY_VIEWPORT
            try:
                result = schedule_bump_project_priorities(
                    slug,
                    project_dir,
                    slugs_list,
                    priority=priority,
                    verbose=bool(self.verbose),
                )
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps({"project": slug, **result}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=202)
            return

        # Prefetch warm must precede per-site warm — otherwise ``prefetch`` matches as a site slug.
        viewshed_prefetch_warm_match = _API_PROJECT_VIEWSHED_PREFETCH_WARM_RE.match(path)
        if viewshed_prefetch_warm_match:
            slug = viewshed_prefetch_warm_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                lat, lon = _parse_lat_lon_query(parsed_url.query)
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                result = warm_coords_viewshed(
                    slug,
                    project_dir,
                    lat,
                    lon,
                    sim_overrides=sim_overrides,
                    verbose=verbose,
                )
            except ServeViewshedError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            status = 200 if result.get("status") == "ready" else 202
            payload = json.dumps(result).encode("utf-8")
            self._send_bytes(payload, "application/json", status=status)
            return

        viewshed_warm_match = _API_PROJECT_VIEWSHED_WARM_RE.match(path)
        if viewshed_warm_match:
            slug = viewshed_warm_match.group(1)
            site_slug = viewshed_warm_match.group(2)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "site": site_slug, "error": str(e)}).encode(
                    "utf-8"
                )
                self._send_bytes(payload, "application/json", status=422)
                return
            if site_slug not in site_map:
                self.send_error(404)
                return
            try:
                sim_overrides = _parse_viewshed_sim_query(parsed_url.query)
            except ValueError as e:
                payload = json.dumps(
                    {"slug": slug, "site": site_slug, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                result = warm_site_viewshed(
                    slug,
                    project_dir,
                    site_slug,
                    site_map[site_slug],
                    sim_overrides=sim_overrides,
                    verbose=verbose,
                )
            except ServeViewshedError as e:
                payload = json.dumps(
                    {"slug": slug, "site": site_slug, "error": str(e)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            status = 200 if result.get("status") == "ready" else 202
            payload = json.dumps(result).encode("utf-8")
            self._send_bytes(payload, "application/json", status=status)
            return

        simulation_match = _API_PROJECT_SIMULATION_RE.match(path)
        if simulation_match:
            project_slug = simulation_match.group(1)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                radius_km = float(raw["radius_km"])
                raster_dimension = int(float(raw["raster_dimension"]))
            except (KeyError, TypeError, ValueError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"radius_km and raster_dimension required: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                simulation = update_viewshed_sim_to_preset(
                    preset_path,
                    radius_km=radius_km,
                    raster_dimension=raster_dimension,
                )
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "simulation": simulation},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_preview_geojson_post_match = _API_PROJECT_LAND_PREVIEW_GEOJSON_RE.match(path)
        if land_preview_geojson_post_match:
            project_slug = land_preview_geojson_post_match.group(1)
            project_dir = self.projects_dir / project_slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                rel_path = _parse_land_path_field(raw)
                layer = str(raw.get("layer", "")).strip()
                if not layer:
                    raise ValueError("layer is required")
                gdb_path = resolve_land_gdb_path(project_dir, rel_path)
                entry = _parse_land_layer_entry_body(raw, layer_name=layer)
                geojson = ensure_layer_preview_geojson(project_dir, gdb_path, layer, entry=entry)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {
                    "slug": project_slug,
                    "path": rel_path,
                    "layer": layer,
                    "geojson": geojson,
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_import_preview_match = _API_PROJECT_LAND_IMPORT_PREVIEW_RE.match(path)
        if land_import_preview_match:
            project_slug = land_import_preview_match.group(1)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                rel_path = _parse_land_path_field(raw)
                gdb_path = resolve_land_gdb_path(project_dir, rel_path)
                layers = list_gdb_layers(gdb_path)
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            if not layers:
                payload = json.dumps(
                    {"slug": project_slug, "error": "no layers found in GDB"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps(
                {
                    "slug": project_slug,
                    "path": rel_path,
                    "layers": [serialize_layer_info(layer) for layer in layers],
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_import_match = _API_PROJECT_LAND_IMPORT_RE.match(path)
        if land_import_match:
            project_slug = land_import_match.group(1)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                rel_path = _parse_land_path_field(raw)
                layer_entries = _parse_land_layers_field(raw)
                label_raw = raw.get("label")
                label = str(label_raw).strip() if label_raw is not None else None
                source_id_raw = raw.get("id")
                source_id = str(source_id_raw).strip() if source_id_raw is not None else None
                source = add_land_source(
                    preset_path,
                    path=rel_path,
                    layers=layer_entries,
                    label=label or None,
                    source_id=source_id or None,
                )
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "source": source},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=201)
            return

        import_preview_match = _API_PROJECT_SITES_IMPORT_PREVIEW_RE.match(path)
        if import_preview_match:
            project_slug = import_preview_match.group(1)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                parsed_sites, skipped = _parse_kml_import_payload(raw)
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not parsed_sites:
                payload = json.dumps(
                    {"slug": project_slug, "error": "no Point placemarks found in KML/KMZ"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps(
                {
                    "slug": project_slug,
                    "points": [serialize_kml_point(site) for site in parsed_sites],
                    "skipped": skipped,
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        tags_bulk_match = _API_PROJECT_SITES_TAGS_BULK_RE.match(path)
        if tags_bulk_match:
            project_slug = tags_bulk_match.group(1)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            slugs_raw = raw.get("slugs")
            if not isinstance(slugs_raw, list) or not slugs_raw:
                payload = json.dumps(
                    {"slug": project_slug, "error": "slugs must be a non-empty list of strings"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            slugs_list = [str(s) for s in slugs_raw]
            add_tags_raw = raw.get("add_tags")
            remove_tags_raw = raw.get("remove_tags")
            add_tags_list: list[str] | None = None
            remove_tags_list: list[str] | None = None
            if add_tags_raw is not None:
                if not isinstance(add_tags_raw, list):
                    payload = json.dumps(
                        {"slug": project_slug, "error": "add_tags must be a list of strings"}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                add_tags_list = [str(t) for t in add_tags_raw]
            if remove_tags_raw is not None:
                if not isinstance(remove_tags_raw, list):
                    payload = json.dumps(
                        {"slug": project_slug, "error": "remove_tags must be a list of strings"}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                remove_tags_list = [str(t) for t in remove_tags_raw]
            try:
                site_rows = bulk_merge_site_tags_in_preset_rows(
                    preset_path,
                    slugs=slugs_list,
                    add_tags=add_tags_list,
                    remove_tags=remove_tags_list,
                )
                patch_serve_project_site_tags(
                    project_dir,
                    {str(row["slug"]): row["tags"] for row in site_rows},
                )
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {
                    "slug": project_slug,
                    "sites": site_rows,
                    "updated": len(site_rows),
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        import_match = _API_PROJECT_SITES_IMPORT_RE.match(path)
        if import_match:
            project_slug = import_match.group(1)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            tags_raw = raw.get("tags")
            if not isinstance(tags_raw, list) or not tags_raw:
                payload = json.dumps(
                    {"slug": project_slug, "error": "tags must be a non-empty list of strings"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            tags_list = [str(t) for t in tags_raw]
            try:
                parsed_sites, skipped = _parse_import_sites_body(raw)
                new_slugs = import_sites_to_preset(
                    preset_path,
                    sites=parsed_sites,
                    tags=tags_list,
                )
                for slug, site in zip(new_slugs, parsed_sites, strict=True):
                    apply_plss_from_loc_cache(preset_path, slug, site.lat, site.lon)
                site_map = _load_project_sites(project_dir)
                site_rows = [
                    _serialize_project_sites({slug: site_map[slug]})[0]
                    for slug in new_slugs
                    if slug in site_map
                ]
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {
                    "slug": project_slug,
                    "sites": site_rows,
                    "imported": len(site_rows),
                    "skipped": skipped,
                },
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=201)
            return

        sites_match = _API_PROJECT_SITES_RE.match(path)
        if sites_match:
            project_slug = sites_match.group(1)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            name = str(raw.get("name", ""))
            try:
                lat = float(raw["lat"])
                lon = float(raw["lon"])
            except (KeyError, TypeError, ValueError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"lat and lon required: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            tags_raw = raw.get("tags")
            if tags_raw is None:
                tags: list[str] | None = None
            elif isinstance(tags_raw, list):
                tags = [str(t) for t in tags_raw]
            else:
                payload = json.dumps(
                    {"slug": project_slug, "error": "tags must be a list of strings"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                site_slug = append_planned_site_to_preset(
                    preset_path,
                    name=name,
                    lat=lat,
                    lon=lon,
                    tags=tags,
                )
                apply_plss_from_loc_cache(preset_path, site_slug, lat, lon)
                site_map = _load_project_sites(project_dir)
                site_entry = site_map[site_slug]
                site_row = _serialize_project_sites({site_slug: site_entry})[0]
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "site": site_row},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=201)
            return

        if path != "/projects":
            self.send_error(404)
            return

        fields = _parse_form_body(body)
        slug = fields.get("slug", "").strip()

        try:
            validate_project_slug(slug)
            scaffold_project(slug, parent=self.projects_dir)
        except ValueError as e:
            projects = discover_projects(self.projects_dir)
            self._send_html(landing_html(self.projects_dir, projects, error=str(e)), status=400)
            return
        except FileExistsError as e:
            projects = discover_projects(self.projects_dir)
            self._send_html(landing_html(self.projects_dir, projects, error=str(e)), status=409)
            return
        except OSError as e:
            projects = discover_projects(self.projects_dir)
            self._send_html(landing_html(self.projects_dir, projects, error=str(e)), status=500)
            return

        self._redirect(f"/p/{slug}/")

    def _parse_entity_patch_body(self, body: bytes) -> dict[str, object]:
        try:
            raw = _parse_json_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"invalid JSON: {e}") from e
        if not isinstance(raw, dict):
            raise ValueError("body must be a JSON object")
        return raw

    def _do_patch(self, parsed: ParseResult, body: bytes) -> None:
        parsed_url = parsed
        path = parsed_url.path

        if _API_HOME_SIMULATION_RE.match(path):
            try:
                raw = self._parse_entity_patch_body(body)
                result = patch_home_simulation(raw)
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(result, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        home_modem_match = _API_HOME_MODEM_NAME_RE.match(path)
        if home_modem_match:
            name = home_modem_match.group(1)
            try:
                raw = self._parse_entity_patch_body(body)
                result = patch_modem_preset(name, raw)
            except KeyError:
                self.send_error(404)
                return
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(result, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        home_env_match = _API_HOME_ENVIRONMENT_NAME_RE.match(path)
        if home_env_match:
            name = home_env_match.group(1)
            try:
                raw = self._parse_entity_patch_body(body)
                result = patch_environment_preset(name, raw)
            except KeyError:
                self.send_error(404)
                return
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(result, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        project_sim_match = _API_PROJECT_SIMULATION_RE.match(path)
        if project_sim_match:
            project_slug = project_sim_match.group(1)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = self._parse_entity_patch_body(body)
                result = patch_project_simulation(preset_path, raw)
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps({"slug": project_slug, **result}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        site_match = _API_PROJECT_SITE_SLUG_RE.match(path)
        if site_match:
            project_slug = site_match.group(1)
            site_slug = site_match.group(2)
            project_dir = self.projects_dir / project_slug
            preset_path = project_dir / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = self._parse_entity_patch_body(body)
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            name = raw.get("name")
            name_str = str(name).strip() if name is not None else None
            lat_raw = raw.get("lat")
            lon_raw = raw.get("lon")
            lat: float | None = None
            lon: float | None = None
            if lat_raw is not None or lon_raw is not None:
                try:
                    lat = float(lat_raw)  # type: ignore[arg-type]
                    lon = float(lon_raw)  # type: ignore[arg-type]
                except (TypeError, ValueError) as e:
                    payload = json.dumps(
                        {"slug": project_slug, "error": f"lat and lon required: {e}"}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
            if raw.get("type") is not None:
                payload = json.dumps(
                    {"slug": project_slug, "error": "type is removed; use tags"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            tags_raw = raw.get("tags")
            tags_list: list[str] | None = None
            if tags_raw is not None:
                if not isinstance(tags_raw, list):
                    payload = json.dumps(
                        {"slug": project_slug, "error": "tags must be a list of strings"}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                tags_list = [str(t) for t in tags_raw]
            height_kw: dict[str, object] = {}
            if "height_m" in raw:
                height_raw = raw.get("height_m")
                if height_raw is None:
                    height_kw["height_m"] = None
                else:
                    try:
                        height_kw["height_m"] = float(height_raw)
                    except (TypeError, ValueError) as e:
                        payload = json.dumps(
                            {"slug": project_slug, "error": f"height_m must be a number: {e}"}
                        ).encode("utf-8")
                        self._send_bytes(payload, "application/json", status=422)
                        return
            try:
                tags_only = (
                    tags_list is not None
                    and name_str is None
                    and lat is None
                    and lon is None
                    and "height_m" not in raw
                )
                if tags_only:
                    site_row = patch_site_tags_in_preset(preset_path, site_slug, tags_list)
                    patch_serve_project_site_tags(project_dir, {site_slug: tags_list})
                else:
                    updated_slug = update_site_in_preset(
                        preset_path,
                        site_slug,
                        name=name_str,
                        lat=lat,
                        lon=lon,
                        tags=tags_list,
                        **height_kw,
                    )
                    if lat is not None and lon is not None:
                        apply_plss_from_loc_cache(preset_path, updated_slug, lat, lon)
                    site_map = _load_project_sites(project_dir)
                    site_entry = site_map[updated_slug]
                    site_row = _serialize_project_sites({updated_slug: site_entry})[0]
                    if lat is not None and lon is not None or "height_m" in raw:
                        invalidate_site_coverage(
                            project_slug,
                            project_dir,
                            updated_slug,
                            site_entry,
                        )
            except ValueError as e:
                if "not found" in str(e):
                    self.send_error(404)
                    return
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "site": site_row},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_sidebar_match = _API_PROJECT_LAND_SIDEBAR_RE.match(path)
        if land_sidebar_match:
            project_slug = land_sidebar_match.group(1)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = _parse_json_body(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                payload = json.dumps(
                    {"slug": project_slug, "error": f"invalid JSON: {e}"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            if not isinstance(raw, dict):
                payload = json.dumps(
                    {"slug": project_slug, "error": "body must be a JSON object"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                sidebar = _parse_land_sidebar_body(raw)
                result = patch_land_sidebar(preset_path, sidebar)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps(
                {"slug": project_slug, "sidebar": result},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_source_match = _API_PROJECT_LAND_SOURCE_RE.match(path)
        if land_source_match:
            project_slug = land_source_match.group(1)
            source_id = land_source_match.group(2)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                raw = self._parse_entity_patch_body(body)
            except ValueError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            layers_raw = raw.get("layers")
            label_raw = raw.get("label")
            layers_list: list[LandLayerEntry] | None = None
            label_str: str | None = None
            if layers_raw is not None:
                if not isinstance(layers_raw, list):
                    payload = json.dumps(
                        {"slug": project_slug, "error": "layers must be a list"}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                layers_list = _parse_land_layers_field({"layers": layers_raw})
            if label_raw is not None:
                label_str = str(label_raw).strip()
            if layers_list is None and label_raw is None:
                payload = json.dumps(
                    {"slug": project_slug, "error": "layers or label required"}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            try:
                source = patch_land_source(
                    preset_path,
                    source_id,
                    layers=layers_list,
                    label=label_str,
                )
            except ValueError as e:
                if "unknown land source" in str(e):
                    self.send_error(404)
                    return
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "source": source},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        self.send_error(404)

    def _do_delete(self, parsed: ParseResult) -> None:
        parsed_url = parsed
        path = parsed_url.path

        home_modem_match = _API_HOME_MODEM_NAME_RE.match(path)
        if home_modem_match:
            name = home_modem_match.group(1)
            try:
                delete_modem_preset(name)
            except KeyError:
                self.send_error(404)
                return
            except ReferenceError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=409)
                return
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps({"deleted": name}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        home_env_match = _API_HOME_ENVIRONMENT_NAME_RE.match(path)
        if home_env_match:
            name = home_env_match.group(1)
            try:
                delete_environment_preset(name)
            except KeyError:
                self.send_error(404)
                return
            except ReferenceError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=409)
                return
            except ValueError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps({"deleted": name}, sort_keys=True).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        land_source_match = _API_PROJECT_LAND_SOURCE_RE.match(path)
        if land_source_match:
            project_slug = land_source_match.group(1)
            source_id = land_source_match.group(2)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                delete_land_source(preset_path, source_id)
            except ValueError as e:
                if "unknown land source" in str(e):
                    self.send_error(404)
                    return
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "deleted": source_id},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        site_match = _API_PROJECT_SITE_SLUG_RE.match(path)
        if site_match:
            project_slug = site_match.group(1)
            site_slug = site_match.group(2)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                deleted = delete_site_from_preset(preset_path, site_slug)
            except ValueError as e:
                if "not found" in str(e):
                    self.send_error(404)
                    return
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "deleted": deleted},
                sort_keys=True,
            ).encode("utf-8")
            self._send_bytes(payload, "application/json", status=200)
            return

        self.send_error(404)


class _ServeMetrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_requests = 0
        self.request_seq = 0


def make_serve_wsgi_app(
    projects_dir: Path,
    *,
    verbose: bool,
    request_log: bool,
) -> Callable[[dict[str, object], Callable[[str, list[tuple[str, str]]], object]], list[bytes]]:
    metrics = _ServeMetrics()

    def app(
        environ: dict[str, object],
        start_response: Callable[[str, list[tuple[str, str]]], object],
    ) -> list[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/") or "/")
        query = str(environ.get("QUERY_STRING", "") or "")
        client = str(environ.get("REMOTE_ADDR", "?") or "?")

        content_length_raw = str(environ.get("CONTENT_LENGTH", "0") or "0")
        try:
            length = int(content_length_raw)
        except ValueError:
            length = 0

        body = b""
        input_stream = environ.get("wsgi.input")
        if hasattr(input_stream, "read") and length > 0:
            body = input_stream.read(length)  # type: ignore[call-arg]
            if not isinstance(body, bytes):
                body = bytes(body)

        with metrics.lock:
            metrics.request_seq += 1
            req_id = metrics.request_seq
            metrics.active_requests += 1
            active = metrics.active_requests

        t0 = time.monotonic()
        if request_log:
            print(
                f"serve: req [{req_id}] start active={active} client={client}",
                flush=True,
            )

        dispatcher = ServeDispatcher(projects_dir, verbose=verbose)
        response = dispatcher.dispatch(method, path, query, body)

        with metrics.lock:
            metrics.active_requests = max(0, metrics.active_requests - 1)
            active = metrics.active_requests

        if request_log:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            url = path + (f"?{query}" if query else "")
            print(
                f"serve: req [{req_id}] end {method} {url} -> {response.status} "
                f"({elapsed_ms:.0f}ms) active={active} client={client}",
                flush=True,
            )

        try:
            phrase = HTTPStatus(response.status).phrase
        except ValueError:
            phrase = "Unknown"
        status_line = f"{response.status} {phrase}"

        headers = list(response.headers)
        if response.body_iter is not None:
            start_response(status_line, headers)
            return response.body_iter

        if not any(k.lower() == "content-length" for k, _ in headers):
            headers.append(("Content-Length", str(len(response.body))))
        start_response(status_line, headers)
        return [response.body]

    return app


__all__ = [
    "SERVE_STATIC_DIR",
    "ServeDispatcher",
    "ServeResponse",
    "make_serve_wsgi_app",
]
