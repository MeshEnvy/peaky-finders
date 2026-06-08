"""``peaky serve`` — local web UI."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import peaky_finders

from pydantic import ValidationError

from peaky_finders.new_cli import discover_projects, scaffold_project, validate_project_slug
from peaky_finders.sites_job import SiteEntry, load_preset_sites

_PROJECT_PATH_RE = re.compile(r"^/p/([a-zA-Z][a-zA-Z0-9_-]*)/?$")
_API_PROJECT_SITES_RE = re.compile(r"^/api/p/([a-zA-Z][a-zA-Z0-9_-]*)/sites/?$")


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


def _parse_form_body(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {k: (v[0] if v else "") for k, v in parsed.items()}


def _html_page(title: str, body: str, *, wide: bool = False, extra_head: str = "") -> bytes:
    body_layout = (
        "body.wide { max-width: none; margin: 0; padding: 0; }\n"
        "    body.wide .page-header { max-width: 56rem; margin: 0 auto; padding: 1.5rem 1rem 0; }\n"
        "    body.wide #map { width: 100%; height: calc(100vh - 7rem); min-height: 20rem; "
        "border-top: 1px solid #d8dce3; }\n"
        "    .site-count {{ color: #555; font-size: 0.9rem; margin-bottom: 0.75rem; }}\n"
        "    .leaflet-tooltip.site-label {{ background: #fff; border: 1px solid #4a6cf7; "
        "border-radius: 0.25rem; padding: 0.15rem 0.45rem; font-weight: 600; font-size: 0.8rem; "
        "box-shadow: 0 1px 3px rgba(0,0,0,0.12); color: #1a1a1a; }}\n"
        "    .leaflet-tooltip.site-label::before {{ border-top-color: #4a6cf7; }}\n"
        "    .leaflet-control-layers {{ font-size: 0.85rem; }}"
        if wide
        else ""
    )
    body_class = ' class="wide"' if wide else ""
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  {extra_head}
  <style>
    :root {{ font-family: system-ui, sans-serif; line-height: 1.5; color: #1a1a1a; background: #f6f7f9; }}
    body {{ max-width: 42rem; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ font-size: 1.5rem; margin: 0 0 0.25rem; }}
    .meta {{ color: #555; font-size: 0.9rem; margin-bottom: 1.5rem; }}
    ul {{ list-style: none; padding: 0; margin: 0 0 2rem; }}
    li {{ margin: 0.5rem 0; }}
    a.project {{ display: block; padding: 0.75rem 1rem; background: #fff; border: 1px solid #d8dce3;
                 border-radius: 0.5rem; text-decoration: none; color: inherit; }}
    a.project:hover {{ border-color: #4a6cf7; }}
    .empty {{ color: #666; font-style: italic; }}
    form {{ background: #fff; border: 1px solid #d8dce3; border-radius: 0.5rem; padding: 1rem; }}
    label {{ display: block; font-weight: 600; margin-bottom: 0.35rem; }}
    input[type=text] {{ width: 100%; box-sizing: border-box; padding: 0.5rem; margin-bottom: 0.75rem;
                        border: 1px solid #ccc; border-radius: 0.35rem; }}
    button {{ padding: 0.5rem 1rem; background: #4a6cf7; color: #fff; border: 0; border-radius: 0.35rem;
              cursor: pointer; font-weight: 600; }}
    button:hover {{ background: #3a57d7; }}
    .error {{ color: #b00020; margin-bottom: 1rem; }}
    .back {{ display: inline-block; margin-bottom: 1rem; }}
    {body_layout}
  </style>
</head>
<body{body_class}>
{body}
</body>
</html>"""
    return doc.encode("utf-8")


def _landing_html(projects_dir: Path, projects: list[str], *, error: str | None = None) -> bytes:
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    if projects:
        items = "\n".join(
            f'  <li><a class="project" href="/p/{html.escape(slug)}/">{html.escape(slug)}</a></li>'
            for slug in projects
        )
        project_block = f"<ul>\n{items}\n</ul>"
    else:
        project_block = '<p class="empty">No projects yet — create one below.</p>'

    body = f"""{err}
<h1>Peaky</h1>
<p class="meta">Projects in {html.escape(str(projects_dir))}</p>
{project_block}
<form method="post" action="/projects">
  <label for="slug">New project</label>
  <input id="slug" name="slug" type="text" placeholder="my-region" required autofocus>
  <button type="submit">Create empty template</button>
</form>"""
    return _html_page("Peaky", body)


def _load_project_sites(project_dir: Path) -> dict[str, SiteEntry]:
    return load_preset_sites(project_dir / "config.yaml")


def _serialize_project_sites(sites: dict[str, SiteEntry]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for site_slug, entry in sorted(sites.items()):
        out.append(
            {
                "slug": site_slug,
                "name": entry.name,
                "lat": entry.lat,
                "lon": entry.lon,
                "type": entry.type.value,
            }
        )
    return out


def _project_error_html(slug: str, project_dir: Path, message: str) -> bytes:
    body = f"""<div class="page-header">
  <a class="back" href="/">&larr; All projects</a>
  <h1>{html.escape(slug)}</h1>
  <p class="meta">{html.escape(str(project_dir))}</p>
  <p class="error">Could not load sites from config.yaml: {html.escape(message)}</p>
</div>"""
    return _html_page(slug, body, wide=True)


def _project_html(slug: str, project_dir: Path, sites: dict[str, SiteEntry]) -> bytes:
    serialized = _serialize_project_sites(sites)
    sites_json = json.dumps(serialized)
    site_count = len(serialized)
    site_noun = "site" if site_count == 1 else "sites"
    extra_head = (
        '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" '
        'integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">'
    )
    body = f"""<div class="page-header">
  <a class="back" href="/">&larr; All projects</a>
  <h1>{html.escape(slug)}</h1>
  <p class="meta">{html.escape(str(project_dir))}</p>
  <p class="site-count">{site_count} {site_noun}</p>
</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<script>
(function () {{
  const sites = {sites_json};
  const street = L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }});
  const topo = L.tileLayer("https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png", {{
    maxZoom: 17,
    attribution: 'Map: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> '
      + '(<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
  }});
  const satellite = L.tileLayer(
    "https://server.arcgis.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}",
    {{
      maxZoom: 19,
      attribution: 'Tiles &copy; <a href="https://www.esri.com/">Esri</a>',
    }}
  );
  const map = L.map("map", {{ scrollWheelZoom: true, layers: [street] }});
  L.control.layers({{ Street: street, Topo: topo, Satellite: satellite }}).addTo(map);

  const bounds = [];
  for (const site of sites) {{
    const marker = L.marker([site.lat, site.lon]).addTo(map);
    marker.bindTooltip(site.name, {{
      permanent: true,
      direction: "top",
      offset: [0, -28],
      className: "site-label",
    }});
    bounds.push([site.lat, site.lon]);
  }}

  if (bounds.length === 1) {{
    map.setView(bounds[0], 10);
  }} else if (bounds.length > 1) {{
    map.fitBounds(bounds, {{ padding: [48, 48] }});
  }} else {{
    map.setView([39.5, -98.35], 4);
  }}
}})();
</script>"""
    return _html_page(slug, body, wide=True, extra_head=extra_head)


def make_serve_handler(projects_dir: Path) -> type[BaseHTTPRequestHandler]:
    """Return an HTTP handler bound to *projects_dir*."""

    class ServeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/", "/index.html"):
                projects = discover_projects(projects_dir)
                self._send_html(_landing_html(projects_dir, projects))
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

            match = _PROJECT_PATH_RE.match(path)
            if match:
                slug = match.group(1)
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                try:
                    sites = _load_project_sites(project_dir)
                except (ValueError, ValidationError) as e:
                    self._send_html(_project_error_html(slug, project_dir, str(e)), status=422)
                    return
                self._send_html(_project_html(slug, project_dir, sites))
                return

            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/projects":
                self.send_error(404)
                return

            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            fields = _parse_form_body(body)
            slug = fields.get("slug", "").strip()

            try:
                validate_project_slug(slug)
                scaffold_project(slug, parent=projects_dir)
            except ValueError as e:
                projects = discover_projects(projects_dir)
                self._send_html(_landing_html(projects_dir, projects, error=str(e)), status=400)
                return
            except FileExistsError as e:
                projects = discover_projects(projects_dir)
                self._send_html(_landing_html(projects_dir, projects, error=str(e)), status=409)
                return
            except OSError as e:
                projects = discover_projects(projects_dir)
                self._send_html(_landing_html(projects_dir, projects, error=str(e)), status=500)
                return

            self.send_response(303)
            self.send_header("Location", f"/p/{slug}/")
            self.end_headers()

        def _send_html(self, body: bytes, *, status: int = 200) -> None:
            self._send_bytes(body, "text/html; charset=utf-8", status=status)

        def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
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


def resolve_serve_reload_watch_dirs() -> list[Path]:
    """Directories polled for ``--reload`` (installed ``peaky_finders`` package tree)."""
    return [Path(peaky_finders.__file__).resolve().parent]


def _py_file_mtimes(root: Path) -> dict[str, float]:
    if not root.is_dir():
        return {}
    out: dict[str, float] = {}
    for path in root.rglob("*.py"):
        if path.is_file():
            out[str(path.resolve())] = path.stat().st_mtime
    return out


def _reload_snapshots(watch_dirs: list[Path]) -> dict[Path, dict[str, float]]:
    return {root: _py_file_mtimes(root) for root in watch_dirs}


def _reload_detected(before: dict[Path, dict[str, float]], watch_dirs: list[Path]) -> bool:
    return any(_py_file_mtimes(root) != before[root] for root in watch_dirs)


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


def _stop_serve_server(server: HTTPServer, thread: threading.Thread | None) -> None:
    server.shutdown()
    if thread is not None:
        thread.join(timeout=10)
    server.server_close()


def _run_serve_with_reload(
    host: str,
    port: int,
    *,
    verbose: bool,
    projects_dir: Path,
    watch_dirs: list[Path],
    poll_s: float = 0.5,
) -> int:
    existing = [d for d in watch_dirs if d.is_dir()]
    if not existing:
        print("serve: reload disabled — watch path missing", flush=True)
        return _run_serve_blocking(host, port, verbose=verbose, projects_dir=projects_dir)

    print("serve: reload enabled", flush=True)
    for watch_dir in existing:
        print(f"serve: watching {watch_dir}", flush=True)

    server: HTTPServer | None = None
    thread: threading.Thread | None = None
    try:
        while True:
            snapshots = _reload_snapshots(existing)
            server = _run_serve_once(host, port, verbose=verbose, projects_dir=projects_dir)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f"serve: running http://{host}:{port}/", flush=True)

            while thread.is_alive():
                time.sleep(poll_s)
                if _reload_detected(snapshots, existing):
                    print("serve: source changed, restarting", flush=True)
                    _stop_serve_server(server, thread)
                    server = None
                    thread = None
                    break
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        if server is not None:
            _stop_serve_server(server, thread)
        return 0
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

    if reload_enabled:
        return _run_serve_with_reload(
            host,
            port,
            verbose=verbose,
            projects_dir=projects_dir,
            watch_dirs=resolve_serve_reload_watch_dirs(),
        )

    print(f"serve: starting http://{host}:{port}/", flush=True)
    return _run_serve_blocking(host, port, verbose=verbose, projects_dir=projects_dir)
