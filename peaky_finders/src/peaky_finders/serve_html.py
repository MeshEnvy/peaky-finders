"""HTML page builders for ``peaky serve``."""

from __future__ import annotations

import html
import json
from pathlib import Path

_FAVICON_HEAD = """\
  <link rel="icon" type="image/png" href="/favicon-96x96.png" sizes="96x96" />
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="shortcut icon" href="/favicon.ico" />
  <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
  <link rel="manifest" href="/site.webmanifest" />
  <meta name="theme-color" content="#111820" />"""

_BOOTSTRAP_HEAD = """\
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" \
integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
  <link rel="stylesheet" href="/static/app.css">"""

_BOOTSTRAP_FOOT = """\
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" \
integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>"""

_MAPLIBRE_HEAD = """\
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css" crossorigin="">"""


def html_page(title: str, body: str, *, wide: bool = False, extra_head: str = "") -> bytes:
    body_class = ' class="wide"' if wide else ""
    doc = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  {_FAVICON_HEAD}
  {_BOOTSTRAP_HEAD}
  {extra_head}
</head>
<body{body_class}>
{body}
{_BOOTSTRAP_FOOT}
</body>
</html>"""
    return doc.encode("utf-8")


def landing_html(projects_dir: Path, projects: list[str], *, error: str | None = None) -> bytes:
    err = ""
    if error:
        err = f'<div class="alert alert-danger" role="alert">{html.escape(error)}</div>'

    if projects:
        items = "\n".join(
            f'    <a class="list-group-item list-group-item-action" href="/p/{html.escape(slug)}/">'
            f"{html.escape(slug)}</a>"
            for slug in projects
        )
        project_block = f'<div class="list-group mb-4">\n{items}\n  </div>'
    else:
        project_block = '<p class="text-muted fst-italic mb-4">No projects yet — create one below.</p>'

    body = f"""<div class="container py-4 landing-container">
  {err}
  <h1 class="h2 mb-1">Peaky</h1>
  <p class="text-muted small mb-4">Projects in {html.escape(str(projects_dir))}</p>
  {project_block}
  <div class="card">
    <div class="card-body">
      <form method="post" action="/projects">
        <div class="mb-3">
          <label for="slug" class="form-label">New project</label>
          <input id="slug" name="slug" type="text" class="form-control" placeholder="my-region" required autofocus>
        </div>
        <button type="submit" class="btn btn-primary">Create empty template</button>
      </form>
    </div>
  </div>
</div>"""
    return html_page("Peaky", body)


def project_error_html(slug: str, project_dir: Path, message: str) -> bytes:
    body = f"""<div class="container py-4 landing-container">
  <a class="btn btn-link ps-0 mb-3" href="/">&larr; All projects</a>
  <h1 class="h2 mb-1">{html.escape(slug)}</h1>
  <p class="text-muted small mb-3">{html.escape(str(project_dir))}</p>
  <div class="alert alert-danger" role="alert">
    Could not load sites from config.yaml: {html.escape(message)}
  </div>
</div>"""
    return html_page(slug, body, wide=True)


def project_html(
    slug: str,
    project_dir: Path,
    sites: list[dict[str, object]],
    goals: list[dict[str, object]] | None = None,
    *,
    simulation: dict[str, object] | None = None,
) -> bytes:
    site_count = len(sites)
    goal_count = len(goals or [])
    site_noun = "site" if site_count == 1 else "sites"
    goal_noun = "goal" if goal_count == 1 else "goals"
    peaky_config = json.dumps(
        {
            "slug": slug,
            "sites": sites,
            "goals": goals or [],
            "simulation": simulation or {},
        }
    )
    body = f"""<header class="project-toolbar border-bottom">
  <div class="container-fluid py-2 px-3">
    <div class="d-flex flex-wrap align-items-center gap-2 gap-md-3">
      <a class="btn btn-sm btn-outline-secondary" href="/">&larr; Projects</a>
      <div class="d-flex align-items-center gap-2 me-auto">
        <h1 class="h5 mb-0">{html.escape(slug)}</h1>
        <span id="site-count-badge" class="badge text-bg-secondary">{site_count} {site_noun}</span>
        <span id="goal-count-badge" class="badge text-bg-secondary">{goal_count} {goal_noun}</span>
      </div>
      <div class="d-flex flex-wrap align-items-center gap-2 gap-md-3">
        <button type="button" id="entity-panel-toggle" class="btn btn-sm btn-outline-secondary" title="Sites and goals" aria-expanded="false" aria-controls="entity-panel">Sites</button>
        <div class="d-flex align-items-center gap-2">
          <label for="basemap" class="form-label mb-0 small text-muted">Map</label>
          <select id="basemap" class="form-select form-select-sm" style="width: auto;" title="Base map">
            <option value="street">Street</option>
            <option value="topo">Topo</option>
            <option value="satellite">Satellite</option>
          </select>
        </div>
        <div class="form-check mb-0">
          <input id="show-links" class="form-check-input" type="checkbox" checked>
          <label class="form-check-label small" for="show-links">Site links</label>
        </div>
        <div class="form-check mb-0">
          <input id="show-goal-links" class="form-check-input" type="checkbox" checked>
          <label class="form-check-label small" for="show-goal-links">Goal links</label>
        </div>
        <button type="button" id="viewshed-sim-open" class="btn btn-sm btn-outline-secondary"
          data-bs-toggle="modal" data-bs-target="#viewshed-sim-modal" title="Viewshed radius and resolution">
          <span id="viewshed-sim-summary" class="font-monospace">60 km · 500 px</span>
        </button>
        <div class="d-flex align-items-center gap-2">
          <label for="viewshed-opacity" class="form-label mb-0 small text-muted">Opacity</label>
          <input id="viewshed-opacity" type="range" class="form-range" min="0" max="100" value="75"
            style="width: 5.5rem;" title="Viewshed opacity">
        </div>
        <span class="text-muted small d-none d-md-inline">Tilt for 3D terrain</span>
      </div>
    </div>
    <p class="text-muted small mb-0 mt-1 d-md-none">{html.escape(str(project_dir))}</p>
  </div>
</header>
<div class="map-shell">
  <aside id="entity-panel" class="entity-panel card shadow" hidden aria-label="Sites and goals">
    <div class="entity-panel__header card-header py-2 px-2 border-bottom-0">
      <ul class="nav nav-tabs card-header-tabs" role="tablist">
        <li class="nav-item" role="presentation">
          <button type="button" class="nav-link active" id="entity-tab-sites" data-entity-tab="sites" role="tab" aria-selected="true">Sites</button>
        </li>
        <li class="nav-item" role="presentation">
          <button type="button" class="nav-link" id="entity-tab-goals" data-entity-tab="goals" role="tab" aria-selected="false">Goals</button>
        </li>
      </ul>
    </div>
    <div class="entity-panel__body card-body p-0 d-flex flex-column">
      <div id="entity-panel-sites-pane" class="entity-panel__pane flex-grow-1" role="tabpanel">
        <div id="entity-panel-sites-list" class="entity-panel__list"></div>
        <div class="entity-panel__footer border-top p-2">
          <button type="button" id="entity-panel-add-site" class="btn btn-sm btn-outline-primary w-100" title="Add site">+ Site</button>
        </div>
      </div>
      <div id="entity-panel-goals-pane" class="entity-panel__pane flex-grow-1" hidden role="tabpanel">
        <div id="entity-panel-goals-list" class="entity-panel__list"></div>
        <div class="entity-panel__footer border-top p-2">
          <button type="button" id="entity-panel-add-goal" class="btn btn-sm btn-outline-primary w-100" title="Add goal">+ Goal</button>
        </div>
      </div>
    </div>
  </aside>
  <div id="map"></div>
  <div id="pin-load-overlays" class="pin-load-overlays" aria-hidden="true"></div>
  <aside id="site-panel" class="site-panel card shadow" hidden>
    <div class="card-body">
      <div id="site-panel-view">
        <div class="site-panel__header d-flex align-items-start justify-content-between gap-2 mb-3">
          <h2 class="site-panel__title h6 mb-0" id="site-panel-name"></h2>
          <button type="button" class="btn-close btn-close-sm" id="site-panel-close" title="Close" aria-label="Close"></button>
        </div>
        <span class="site-panel__badge badge mb-3" id="site-panel-type"></span>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase">Coordinates</span>
          <p class="site-panel__value small mb-0" id="site-panel-coords"></p>
        </div>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase">Elevation</span>
          <p class="site-panel__value small mb-0" id="site-panel-elevation"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-plss-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">PLSS</span>
          <p class="site-panel__value small mb-0" id="site-panel-plss"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-mlrs-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">MLRS</span>
          <p class="site-panel__value small mb-0" id="site-panel-mlrs"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-desc-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">Description</span>
          <p class="site-panel__value small mb-0" id="site-panel-desc"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-rationale-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">Rationale</span>
          <p class="site-panel__value small mb-0" id="site-panel-rationale"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-links-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">Linked sites</span>
          <ul class="site-panel__links small mb-0 ps-3" id="site-panel-links"></ul>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-goal-links-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">Linked repeaters</span>
          <ul class="site-panel__links small mb-0 ps-3" id="site-panel-goal-links"></ul>
        </div>
        <div class="site-panel__viewshed form-check border-top pt-2 mt-2" id="site-panel-viewshed-section">
          <input class="form-check-input" type="checkbox" id="site-panel-viewshed" checked>
          <label class="form-check-label small" for="site-panel-viewshed">Show viewshed</label>
          <span class="site-panel__viewshed-hint small text-muted ms-1" id="site-panel-viewshed-hint"></span>
        </div>
      </div>
      <div id="site-panel-create" hidden>
        <div class="site-panel__header d-flex align-items-start justify-content-between gap-2 mb-3">
          <h2 class="site-panel__title h6 mb-0" id="site-panel-create-title">New site</h2>
          <button type="button" class="btn-close btn-close-sm" id="site-panel-create-close" title="Close" aria-label="Close"></button>
        </div>
        <span class="site-panel__badge badge site-panel__badge--planned mb-3" id="site-panel-create-badge">planned</span>
        <div class="site-panel__section mb-2">
          <label class="site-panel__label small text-muted text-uppercase" for="site-panel-create-name">Name</label>
          <input id="site-panel-create-name" type="text" class="form-control form-control-sm" required>
        </div>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase">Slug</span>
          <p class="site-panel__value small mb-0 font-monospace text-muted" id="site-panel-slug-preview"></p>
        </div>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase">Coordinates</span>
          <p class="site-panel__value small mb-0" id="site-panel-create-coords"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-create-plss-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">PLSS</span>
          <p class="site-panel__value small mb-0" id="site-panel-create-plss"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-create-mlrs-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase">MLRS</span>
          <p class="site-panel__value small mb-0" id="site-panel-create-mlrs"></p>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-create-links-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase" id="site-panel-create-links-label">Linked sites</span>
          <ul class="site-panel__links small mb-0 ps-3" id="site-panel-create-links"></ul>
        </div>
        <div class="site-panel__viewshed form-check border-top pt-2 mt-2" id="site-panel-create-viewshed-section">
          <input class="form-check-input" type="checkbox" id="site-panel-create-viewshed" checked>
          <label class="form-check-label small" for="site-panel-create-viewshed">Show viewshed</label>
          <span class="site-panel__viewshed-hint small text-muted ms-1" id="site-panel-create-viewshed-hint"></span>
        </div>
        <div id="site-panel-create-error" class="alert alert-danger py-2 small mb-2" role="alert" hidden></div>
        <div class="site-panel__create-actions d-flex gap-2 mt-3">
          <button type="button" id="site-panel-create-save" class="btn btn-primary btn-sm">Save</button>
          <button type="button" id="site-panel-create-cancel" class="btn btn-outline-secondary btn-sm">Cancel</button>
        </div>
      </div>
    </div>
  </aside>
</div>
<div class="modal fade" id="viewshed-sim-modal" tabindex="-1" aria-labelledby="viewshed-sim-modal-label" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="viewshed-sim-modal-label">Viewshed grid</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body">
        <p class="small text-muted mb-3">Tune coverage radius and raster size. Visible viewsheds reload when you apply.</p>
        <div class="mb-3">
          <div class="d-flex justify-content-between align-items-center mb-1">
            <label for="viewshed-sim-radius-km" class="form-label small mb-0">Radius (km)</label>
            <span id="viewshed-sim-radius-km-value" class="small font-monospace text-muted">60</span>
          </div>
          <input id="viewshed-sim-radius-km" type="range" class="form-range" min="1" max="100" value="60">
        </div>
        <div>
          <div class="d-flex justify-content-between align-items-center mb-1">
            <label for="viewshed-sim-raster-dimension" class="form-label small mb-0">Resolution (px)</label>
            <span id="viewshed-sim-raster-dimension-value" class="small font-monospace text-muted">500</span>
          </div>
          <input id="viewshed-sim-raster-dimension" type="range" class="form-range" min="128" max="4096" value="500">
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Cancel</button>
        <button type="button" id="viewshed-sim-apply" class="btn btn-primary btn-sm">Apply</button>
      </div>
    </div>
  </div>
</div>
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js" crossorigin=""></script>
<script>window.PEAKY_PROJECT = {peaky_config};</script>
<script src="/static/project-map.js"></script>"""
    return html_page(slug, body, wide=True, extra_head=_MAPLIBRE_HEAD)
