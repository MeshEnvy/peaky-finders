"""``peaky serve`` — local web UI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import peaky_finders

from pydantic import ValidationError

from peaky_finders.new_cli import discover_projects, scaffold_project, validate_project_slug
from peaky_finders.serve_html import landing_html, project_error_html, project_html
from peaky_finders.serve_goal_links import (
    evaluate_goal_site_prefetch_links,
    load_project_goal_links,
)
from peaky_finders.serve_goals import append_goal_to_preset, delete_goal_from_preset
from peaky_finders.serve_links import ServeLinksError, evaluate_site_pair_linked, load_project_site_links
from peaky_finders.serve_plss_mlrs import apply_plss_mlrs_from_loc_cache
from peaky_finders.serve_site_prefetch import ServeSitePrefetchError, load_site_placement_prefetch
from peaky_finders.serve_sites import append_planned_site_to_preset, delete_site_from_preset
from peaky_finders.serve_viewshed import (
    ServeViewshedError,
    ensure_coords_viewshed_overlay,
    ensure_coords_viewshed_png,
    ensure_site_viewshed_overlay,
    ensure_site_viewshed_png,
)
from peaky_finders.sites_job import GoalEntry, SiteEntry, load_preset_goals, load_preset_sites

SERVE_STATIC_DIR = Path(__file__).resolve().parent / "serve_static"
SERVE_RELOAD_CHILD_ENV = "PEAKY_SERVE_RELOAD_CHILD"
SERVE_RELOAD_POLL_S = 0.5

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
_API_PROJECT_VIEWSHED_META_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/([a-zA-Z][a-zA-Z0-9_-]*)/?$")
_API_PROJECT_VIEWSHED_PNG_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/viewsheds/([a-zA-Z][a-zA-Z0-9_-]*)/splat\.png$"
)
_API_PROJECT_LINK_PAIR_RE = re.compile(
    r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/links/([a-zA-Z][a-zA-Z0-9_-]*)/([a-zA-Z][a-zA-Z0-9_-]*)/?$"
)
_API_PROJECT_LINKS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/links/?$")
_API_PROJECT_GOAL_LINKS_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/goal-links/?$")


def resolve_serve_peaky_home() -> Path:
    """Runtime home for ``peaky serve`` (``PEAKY_HOME`` or ``~/.peaky``)."""
    raw = os.environ.get("PEAKY_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.peaky").expanduser().resolve()


def resolve_serve_projects_dir() -> Path:
    """Projects root for ``peaky serve`` (``PEAKY_PROJECTS`` or ``<home>/projects``)."""
    raw = os.environ.get("PEAKY_PROJECTS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return resolve_serve_peaky_home() / "projects"


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


def make_serve_handler(projects_dir: Path) -> type[BaseHTTPRequestHandler]:
    """Return an HTTP handler bound to *projects_dir*."""

    class ServeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/", "/index.html"):
                projects = discover_projects(projects_dir)
                self._send_html(landing_html(projects_dir, projects))
                return

            if path == "/api/projects":
                slugs = discover_projects(projects_dir)
                payload = json.dumps(
                    {"projects": [{"slug": s} for s in slugs], "root": str(projects_dir)}
                ).encode("utf-8")
                self._send_bytes(payload, "application/json")
                return

            api_match = _API_PROJECT_SITES_RE.match(path)
            if api_match:
                slug = api_match.group(1)
                project_dir = projects_dir / slug
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
                project_dir = projects_dir / slug
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
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    lat, lon = _parse_lat_lon_query(parsed.query)
                    site_map = _load_project_sites(project_dir)
                except (ValueError, ValidationError) as e:
                    payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                verbose = bool(getattr(self.server, "verbose", False))
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
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    lat, lon = _parse_lat_lon_query(parsed.query)
                except ValueError as e:
                    payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                verbose = bool(getattr(self.server, "verbose", False))
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
                project_dir = projects_dir / slug
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
                verbose = bool(getattr(self.server, "verbose", False))
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
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    site_map = _load_project_sites(project_dir)
                except (ValueError, ValidationError) as e:
                    payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                verbose = bool(getattr(self.server, "verbose", False))
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
                project_dir = projects_dir / slug
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
                verbose = bool(getattr(self.server, "verbose", False))
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

            viewshed_prefetch_png_match = _API_PROJECT_VIEWSHED_PREFETCH_PNG_RE.match(path)
            if viewshed_prefetch_png_match:
                slug = viewshed_prefetch_png_match.group(1)
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    lat, lon = _parse_lat_lon_query(parsed.query)
                except ValueError as e:
                    self.send_error(422)
                    return
                verbose = bool(getattr(self.server, "verbose", False))
                try:
                    png_path = ensure_coords_viewshed_png(project_dir, lat, lon, verbose=verbose)
                    body = png_path.read_bytes()
                except ServeViewshedError:
                    self.send_error(503)
                    return
                except OSError:
                    self.send_error(503)
                    return
                self._send_bytes(body, "image/png")
                return

            viewshed_prefetch_match = _API_PROJECT_VIEWSHED_PREFETCH_RE.match(path)
            if viewshed_prefetch_match:
                slug = viewshed_prefetch_match.group(1)
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    lat, lon = _parse_lat_lon_query(parsed.query)
                except ValueError as e:
                    payload = json.dumps(
                        {"slug": slug, "error": str(e)}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=422)
                    return
                verbose = bool(getattr(self.server, "verbose", False))
                try:
                    overlay = ensure_coords_viewshed_overlay(
                        slug,
                        project_dir,
                        lat,
                        lon,
                        verbose=verbose,
                    )
                except ServeViewshedError as e:
                    payload = json.dumps({"slug": slug, "error": str(e)}).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=503)
                    return
                payload = json.dumps({"project": slug, **overlay}).encode("utf-8")
                self._send_bytes(payload, "application/json")
                return

            viewshed_meta_match = _API_PROJECT_VIEWSHED_META_RE.match(path)
            if viewshed_meta_match:
                slug = viewshed_meta_match.group(1)
                site_slug = viewshed_meta_match.group(2)
                project_dir = projects_dir / slug
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
                verbose = bool(getattr(self.server, "verbose", False))
                try:
                    overlay = ensure_site_viewshed_overlay(
                        slug,
                        project_dir,
                        site_slug,
                        site_map[site_slug],
                        verbose=verbose,
                    )
                except ServeViewshedError as e:
                    payload = json.dumps(
                        {"slug": slug, "site": site_slug, "error": str(e)}
                    ).encode("utf-8")
                    self._send_bytes(payload, "application/json", status=503)
                    return
                payload = json.dumps({"project": slug, **overlay}).encode("utf-8")
                self._send_bytes(payload, "application/json")
                return

            viewshed_png_match = _API_PROJECT_VIEWSHED_PNG_RE.match(path)
            if viewshed_png_match:
                slug = viewshed_png_match.group(1)
                site_slug = viewshed_png_match.group(2)
                project_dir = projects_dir / slug
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
                verbose = bool(getattr(self.server, "verbose", False))
                try:
                    png_path = ensure_site_viewshed_png(
                        project_dir,
                        site_slug,
                        site_map[site_slug],
                        verbose=verbose,
                    )
                    body = png_path.read_bytes()
                except ServeViewshedError:
                    self.send_error(503)
                    return
                except OSError:
                    self.send_error(503)
                    return
                self._send_bytes(body, "image/png")
                return

            match = _PROJECT_PATH_RE.match(path)
            if match:
                slug = match.group(1)
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    sites = _load_project_sites(project_dir)
                    goals = _load_project_goals(project_dir)
                except (ValueError, ValidationError) as e:
                    self._send_html(project_error_html(slug, project_dir, str(e)), status=422)
                    return
                self._send_html(
                    project_html(
                        slug,
                        project_dir,
                        _serialize_project_sites(sites),
                        _serialize_project_goals(goals),
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

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)

            goals_match = _API_PROJECT_GOALS_RE.match(path)
            if goals_match:
                project_slug = goals_match.group(1)
                project_dir = projects_dir / project_slug
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
                project_dir = projects_dir / project_slug
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
                scaffold_project(slug, parent=projects_dir)
            except ValueError as e:
                projects = discover_projects(projects_dir)
                self._send_html(landing_html(projects_dir, projects, error=str(e)), status=400)
                return
            except FileExistsError as e:
                projects = discover_projects(projects_dir)
                self._send_html(landing_html(projects_dir, projects, error=str(e)), status=409)
                return
            except OSError as e:
                projects = discover_projects(projects_dir)
                self._send_html(landing_html(projects_dir, projects, error=str(e)), status=500)
                return

            self.send_response(303)
            self.send_header("Location", f"/p/{slug}/")
            self.end_headers()

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            site_match = _API_PROJECT_SITE_SLUG_RE.match(path)
            if site_match:
                project_slug = site_match.group(1)
                site_slug = site_match.group(2)
                preset_path = projects_dir / project_slug / "config.yaml"
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
                preset_path = projects_dir / project_slug / "config.yaml"
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

        def _send_html(self, body: bytes, *, status: int = 200) -> None:
            self._send_bytes(body, "text/html; charset=utf-8", status=status, extra_headers={"Cache-Control": "no-store"})

        def _send_bytes(
            self,
            body: bytes,
            content_type: str,
            status: int = 200,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            if getattr(self.server, "verbose", False):
                super().log_message(format, *args)

    return ServeHandler


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0 for Docker)",
    )
    p.add_argument("--port", type=int, default=8080, help="Listen port (default: 8080)")
    p.add_argument("--verbose", action="store_true", help="Log each HTTP request")
    p.add_argument(
        "--reload",
        action="store_true",
        help="Restart when peaky_finders Python source changes (dev)",
    )
    p.add_argument(
        "--no-reload",
        action="store_true",
        help="Run one process until interrupted (production)",
    )
    return p


def resolve_serve_reload_roots() -> list[Path]:
    """Source trees polled for ``--reload`` (package ``*.py`` + ``serve_static/``)."""
    roots = [Path(peaky_finders.__file__).resolve().parent]
    static_root = SERVE_STATIC_DIR.resolve()
    if static_root.is_dir():
        roots.append(static_root)
    return roots


def _is_serve_reload_child() -> bool:
    return os.environ.get(SERVE_RELOAD_CHILD_ENV, "").strip() == "1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reload_fingerprints(roots: list[Path]) -> dict[str, str]:
    static_root = SERVE_STATIC_DIR.resolve()
    out: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        if root == static_root:
            paths = (path for path in root.rglob("*") if path.is_file())
        else:
            paths = root.rglob("*.py")
        for path in paths:
            if path.is_file():
                out[str(path.resolve())] = _sha256_file(path)
    return out


def _reload_changed(before: dict[str, str], roots: list[Path]) -> bool:
    return _reload_fingerprints(roots) != before


def _serve_child_argv(host: str, port: int, *, verbose: bool) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "peaky_finders.peaky_cli",
        "serve",
        "--no-reload",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if verbose:
        argv.append("--verbose")
    return argv


def _terminate_serve_child(proc: subprocess.Popen[bytes], *, timeout_s: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _run_serve_once(
    host: str,
    port: int,
    *,
    verbose: bool,
    projects_dir: Path,
) -> HTTPServer:
    handler = make_serve_handler(projects_dir)
    server = HTTPServer((host, port), handler)
    server.allow_reuse_address = True
    server.verbose = verbose  # type: ignore[attr-defined]
    return server


def _supervise_serve_reload(
    host: str,
    port: int,
    *,
    verbose: bool,
    poll_s: float = SERVE_RELOAD_POLL_S,
) -> int:
    roots = [root for root in resolve_serve_reload_roots() if root.is_dir()]
    if not roots:
        print("serve: reload disabled — watch path missing", flush=True)
        return _run_serve_blocking(host, port, verbose=verbose, projects_dir=resolve_serve_projects_dir())

    print("serve: reload enabled", flush=True)
    for root in roots:
        print(f"serve: watching {root}", flush=True)

    child_env = {**os.environ, SERVE_RELOAD_CHILD_ENV: "1"}
    child_argv = _serve_child_argv(host, port, verbose=verbose)
    proc: subprocess.Popen[bytes] | None = None

    try:
        while True:
            proc = subprocess.Popen(child_argv, env=child_env)
            fingerprints = _reload_fingerprints(roots)
            print(f"serve: running http://{host}:{port}/", flush=True)

            while proc.poll() is None:
                time.sleep(poll_s)
                if _reload_changed(fingerprints, roots):
                    print("serve: source changed, restarting", flush=True)
                    _terminate_serve_child(proc)
                    break
            else:
                return int(proc.returncode or 0)
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        if proc is not None:
            _terminate_serve_child(proc)
        return 0


def _run_serve_blocking(
    host: str,
    port: int,
    *,
    verbose: bool,
    projects_dir: Path,
) -> int:
    server = _run_serve_once(host, port, verbose=verbose, projects_dir=projects_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        return 0
    finally:
        server.server_close()
    return 0


def run_serve(args: argparse.Namespace) -> int:
    host = str(args.host)
    port = int(args.port)
    verbose = bool(args.verbose)
    reload_enabled = bool(args.reload) and not bool(args.no_reload)

    projects_dir = resolve_serve_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    peaky_home = resolve_serve_peaky_home()

    print(f"serve: PEAKY_HOME={peaky_home}", flush=True)
    print(f"serve: projects={projects_dir}", flush=True)

    if reload_enabled and not _is_serve_reload_child():
        return _supervise_serve_reload(host, port, verbose=verbose)

    print(f"serve: starting http://{host}:{port}/", flush=True)
    return _run_serve_blocking(host, port, verbose=verbose, projects_dir=projects_dir)
