"""``peaky serve`` — local web UI."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from peaky_finders.new_cli import discover_projects, scaffold_project, validate_project_slug

_PROJECT_PATH_RE = re.compile(r"^/p/([a-zA-Z][a-zA-Z0-9_-]*)/?$")


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


def _html_page(title: str, body: str) -> bytes:
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
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
  </style>
</head>
<body>
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


def _project_html(slug: str, project_dir: Path) -> bytes:
    body = f"""<a class="back" href="/">&larr; All projects</a>
<h1>{html.escape(slug)}</h1>
<p class="meta">{html.escape(str(project_dir))}</p>
<p>Project workspace — CLI and build UI coming soon.</p>"""
    return _html_page(slug, body)


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

            match = _PROJECT_PATH_RE.match(path)
            if match:
                slug = match.group(1)
                project_dir = projects_dir / slug
                if not (project_dir / "config.yaml").is_file():
                    self.send_error(404)
                    return
                self._send_html(_project_html(slug, project_dir))
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
    return p


def run_serve(args: argparse.Namespace) -> int:
    host = str(args.host)
    port = int(args.port)
    verbose = bool(args.verbose)

    projects_dir = resolve_serve_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    peaky_home = resolve_serve_peaky_home()

    print(f"serve: PEAKY_HOME={peaky_home}", flush=True)
    print(f"serve: projects={projects_dir}", flush=True)
    print(f"serve: starting http://{host}:{port}/", flush=True)

    handler = make_serve_handler(projects_dir)
    server = HTTPServer((host, port), handler)
    server.verbose = verbose  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        return 0
    finally:
        server.server_close()
    return 0
