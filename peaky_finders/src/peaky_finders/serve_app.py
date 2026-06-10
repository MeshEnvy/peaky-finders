"""Route dispatcher and WSGI app for ``peaky serve``."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import ParseResult, parse_qs, urlparse

from pydantic import ValidationError

from peaky_finders.new_cli import discover_projects, scaffold_project, validate_project_slug
from peaky_finders.serve_goal_links import (
    evaluate_goal_site_prefetch_links,
    load_project_goal_links,
)
from peaky_finders.serve_goals import append_goal_to_preset, delete_goal_from_preset
from peaky_finders.serve_html import landing_html, project_error_html, project_html
from peaky_finders.serve_links import ServeLinksError, evaluate_site_pair_linked, load_project_site_links
from peaky_finders.serve_plss_mlrs import apply_plss_mlrs_from_loc_cache
from peaky_finders.serve_site_prefetch import ServeSitePrefetchError, load_site_placement_prefetch
from peaky_finders.serve_simulation import update_viewshed_sim_to_preset
from peaky_finders.serve_sites import append_planned_site_to_preset, delete_site_from_preset
from peaky_finders.serve_events import get_serve_event_hub
from peaky_finders.serve_viewshed import (
    DRAFT_VIEWSHED_SLUG,
    ServeViewshedError,
    _preview_site_at,
    read_site_viewshed_png_if_ready,
    site_viewshed_overlay_if_ready,
)
from peaky_finders.serve_viewshed_jobs import warm_coords_viewshed, warm_site_viewshed
from peaky_finders.serve_viewshed_sim import ViewshedSimOverrides, parse_viewshed_sim_overrides
from peaky_finders.sites_job import GoalEntry, Preset, SiteEntry, load_preset, load_preset_goals, load_preset_sites

SERVE_STATIC_DIR = Path(__file__).resolve().parent / "serve_static"

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
_API_PROJECT_SITE_SLUG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_GOALS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/goals/?$")
_API_PROJECT_GOAL_SLUG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/goals/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_GOALS_PREFETCH_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/goals/prefetch/?$"
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
_API_PROJECT_GOAL_LINKS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/goal-links/?$")
_API_PROJECT_SIMULATION_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/simulation/?$")


def _parse_json_body(body: bytes) -> object:
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


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
    return load_preset_sites(project_dir / "config.yaml")


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
    return {
        "radius_km": float(sim.radius_km),
        "raster_dimension": int(sim.raster_dimension),
        "radius_km_min": 1,
        "radius_km_max": 100,
        "raster_dimension_min": 128,
        "raster_dimension_max": 4096,
    }


def _load_project_goals(project_dir: Path) -> dict[str, GoalEntry]:
    return load_preset_goals(project_dir / "config.yaml")


def _serialize_project_goals(goals: dict[str, GoalEntry]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for goal_slug, entry in sorted(goals.items()):
        row: dict[str, object] = {
            "slug": goal_slug,
            "name": entry.name,
            "lat": entry.lat,
            "lon": entry.lon,
            "kind": "goal",
        }
        if entry.elevation_m is not None:
            row["elevation_m"] = entry.elevation_m
        for key in ("description", "plss", "mlrs", "rationale"):
            val = getattr(entry, key)
            if val and str(val).strip():
                row[key] = str(val).strip()
        out.append(row)
    return out


def _serialize_project_sites(sites: dict[str, SiteEntry]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for site_slug, entry in sorted(sites.items()):
        row: dict[str, object] = {
            "slug": site_slug,
            "name": entry.name,
            "lat": entry.lat,
            "lon": entry.lon,
            "type": entry.type.value,
        }
        if entry.elevation_m is not None:
            row["elevation_m"] = entry.elevation_m
        for key in ("description", "plss", "mlrs", "rationale"):
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

        goals_match = _API_PROJECT_GOALS_RE.match(path)
        if goals_match:
            slug = goals_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                goals = _serialize_project_goals(_load_project_goals(project_dir))
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            payload = json.dumps({"slug": slug, "goals": goals}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        goals_prefetch_match = _API_PROJECT_GOALS_PREFETCH_RE.match(path)
        if goals_prefetch_match:
            slug = goals_prefetch_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                lat, lon = _parse_lat_lon_query(parsed_url.query)
                site_map = _load_project_sites(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                links = evaluate_goal_site_prefetch_links(
                    project_dir,
                    lat=lat,
                    lon=lon,
                    sites=site_map,
                    verbose=verbose,
                )
            except ServeLinksError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            features = []
            for row in links:
                site_slug = str(row["site"])
                site = site_map[site_slug]
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [lon, lat],
                                [float(site.lon), float(site.lat)],
                            ],
                        },
                        "properties": {"goal": "_draft", "site": site_slug, "captured": False},
                    }
                )
            payload = json.dumps(
                {
                    "project": slug,
                    "links": links,
                    "links_geojson": {"type": "FeatureCollection", "features": features},
                }
            ).encode("utf-8")
            self._send_bytes(payload, "application/json")
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
            verbose = bool(self.verbose)
            try:
                payload_obj = load_site_placement_prefetch(
                    project_dir,
                    lat,
                    lon,
                    verbose=verbose,
                )
            except ServeSitePrefetchError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
            payload = json.dumps({"project": slug, **payload_obj}).encode("utf-8")
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
            payload = json.dumps({"project": slug, **payload_obj}).encode("utf-8")
            self._send_bytes(payload, "application/json")
            return

        goal_links_match = _API_PROJECT_GOAL_LINKS_RE.match(path)
        if goal_links_match:
            slug = goal_links_match.group(1)
            project_dir = self.projects_dir / slug
            if not (project_dir / "config.yaml").is_file():
                self.send_error(404)
                return
            try:
                site_map = _load_project_sites(project_dir)
                goal_map = _load_project_goals(project_dir)
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            verbose = bool(self.verbose)
            try:
                payload_obj = load_project_goal_links(
                    project_dir,
                    goal_map,
                    site_map,
                    verbose=verbose,
                )
            except ServeLinksError as e:
                payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=503)
                return
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
                goals = preset.goals
            except (ValueError, ValidationError) as e:
                self._send_html(project_error_html(slug, project_dir, str(e)), status=422)
                return
            self._send_html(
                project_html(
                    slug,
                    project_dir,
                    _serialize_project_sites(sites),
                    _serialize_project_goals(goals),
                    simulation=_serialize_serve_simulation(preset),
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

        goals_match = _API_PROJECT_GOALS_RE.match(path)
        if goals_match:
            project_slug = goals_match.group(1)
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
            try:
                goal_slug = append_goal_to_preset(
                    preset_path,
                    name=name,
                    lat=lat,
                    lon=lon,
                )
                apply_plss_mlrs_from_loc_cache(preset_path, goal_slug, lat, lon)
                goal_map = _load_project_goals(project_dir)
                goal_entry = goal_map[goal_slug]
                goal_row = _serialize_project_goals({goal_slug: goal_entry})[0]
            except (ValueError, ValidationError) as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=422)
                return
            except OSError as e:
                payload = json.dumps({"slug": project_slug, "error": str(e)}).encode("utf-8")
                self._send_bytes(payload, "application/json", status=500)
                return
            payload = json.dumps(
                {"slug": project_slug, "goal": goal_row},
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
            try:
                site_slug = append_planned_site_to_preset(
                    preset_path,
                    name=name,
                    lat=lat,
                    lon=lon,
                )
                apply_plss_mlrs_from_loc_cache(preset_path, site_slug, lat, lon)
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

    def _do_delete(self, parsed: ParseResult) -> None:
        parsed_url = parsed
        path = parsed_url.path

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

        goal_match = _API_PROJECT_GOAL_SLUG_RE.match(path)
        if goal_match:
            project_slug = goal_match.group(1)
            goal_slug = goal_match.group(2)
            preset_path = self.projects_dir / project_slug / "config.yaml"
            if not preset_path.is_file():
                self.send_error(404)
                return
            try:
                deleted = delete_goal_from_preset(preset_path, goal_slug)
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
