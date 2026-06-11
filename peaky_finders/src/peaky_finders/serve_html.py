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

_GEAR_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" aria-hidden="true">
  <path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z"/>
  <path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319z"/>
</svg>"""

_HOME_SETTINGS_GEAR = f"""\
<button type="button" id="home-settings-open" class="btn btn-sm btn-outline-secondary d-inline-flex align-items-center" \
data-bs-toggle="modal" data-bs-target="#home-settings-modal" title="Settings" aria-label="Settings">
  {_GEAR_SVG}
</button>"""

_CLIMATE_OPTIONS = (
    "equatorial",
    "continental_subtropical",
    "maritime_subtropical",
    "desert",
    "continental_temperate",
    "maritime_temperate_land",
    "maritime_temperate_sea",
)


def _sim_inline_field(override_key: str, label: str, control_html: str, *, title: str = "") -> str:
    key = html.escape(override_key)
    title_attr = f' title="{html.escape(title)}"' if title else ""
    return f"""\
                  <div class="home-sim-field home-sim-field--inline" data-override-key="{key}">
                    <label class="home-sim-inline-label"{title_attr}>{html.escape(label)}</label>
                    {control_html}
                    <button type="button" class="btn btn-link btn-sm py-0 px-1 home-sim-reset" \
data-override-key="{key}" hidden>Reset</button>
                  </div>"""


def _sim_chain_group(title: str, fields_html: str) -> str:
    return f"""\
              <div class="col-md-6">
                <div class="home-sim-chain">
                  <div class="home-sim-chain__title">{html.escape(title)}</div>
                  <div class="home-sim-chain__fields">
{fields_html}
                  </div>
                </div>
              </div>"""


def _sim_reset_row(override_key: str, label: str, control_html: str, *, compact: bool = False) -> str:
    key = html.escape(override_key)
    col = "col-md-4 home-sim-field--compact" if compact else "col-md-6"
    return f"""\
              <div class="home-sim-field {col}" data-override-key="{key}">
                <div class="d-flex justify-content-between align-items-center mb-1 gap-2">
                  <span class="form-label small mb-0">{html.escape(label)}</span>
                  <button type="button" class="btn btn-link btn-sm py-0 px-1 home-sim-reset" \
data-override-key="{key}" hidden>Reset</button>
                </div>
                {control_html}
              </div>"""


def home_settings_modal_html(*, on_project_map: bool = False) -> str:
    project_hint = ""
    if on_project_map:
        project_hint = (
            '<p class="small text-muted mb-3" id="home-settings-project-hint">'
            "Simulation values show this project's effective settings. "
            "Highlighted fields override <code>$PEAKY_HOME</code> defaults; "
            "<strong>Reset</strong> removes the project override."
            "</p>"
        )
    else:
        project_hint = (
            '<p class="small text-muted mb-3" id="home-settings-project-hint">'
            "Simulation tab edits global defaults in <code>$PEAKY_HOME/config.yaml</code>. "
            "Open a project map to override per project."
            "</p>"
        )
    climate_opts = "\n".join(
        f'              <option value="{html.escape(c)}">{html.escape(c.replace("_", " "))}</option>'
        for c in _CLIMATE_OPTIONS
    )
    return f"""\
<div class="modal fade" id="home-settings-modal" tabindex="-1" aria-labelledby="home-settings-modal-label" aria-hidden="true">
  <div class="modal-dialog modal-xl modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="home-settings-modal-label">Settings</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body">
        <div id="home-settings-error" class="alert alert-danger py-2 small mb-3" role="alert" hidden></div>
        {project_hint}
        <ul class="nav nav-tabs mb-3" id="home-settings-tabs" role="tablist">
          <li class="nav-item" role="presentation">
            <button class="nav-link active" id="home-tab-simulation" data-bs-toggle="tab" \
data-bs-target="#home-pane-simulation" type="button" role="tab">Simulation</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="home-tab-modems" data-bs-toggle="tab" \
data-bs-target="#home-pane-modems" type="button" role="tab">Modems</button>
          </li>
          <li class="nav-item" role="presentation">
            <button class="nav-link" id="home-tab-environments" data-bs-toggle="tab" \
data-bs-target="#home-pane-environments" type="button" role="tab">Environments</button>
          </li>
        </ul>
        <div class="tab-content">
          <div class="tab-pane fade show active" id="home-pane-simulation" role="tabpanel">
            <div class="row g-3">
              {_sim_reset_row("modem", "Modem preset", '<select id="home-sim-modem" class="form-select form-select-sm"></select>')}
              {_sim_reset_row("environment", "Environment preset", '<select id="home-sim-environment" class="form-select form-select-sm"></select>')}
              <div class="home-sim-field col-md-6" data-override-key="radius_km">
                <div class="d-flex justify-content-between align-items-center mb-1">
                  <span class="form-label small mb-0">Radius (km)</span>
                  <div class="d-flex align-items-center gap-2">
                    <span id="home-sim-radius-km-value" class="small font-monospace text-muted home-sim-field__value-label">50</span>
                    <button type="button" class="btn btn-link btn-sm py-0 home-sim-reset" \
data-override-key="radius_km" hidden>Reset</button>
                  </div>
                </div>
                <input id="home-sim-radius-km" type="range" class="form-range" min="1" max="100" value="50">
              </div>
              <div class="home-sim-field col-md-6" data-override-key="raster_dimension">
                <div class="d-flex justify-content-between align-items-center mb-1">
                  <span class="form-label small mb-0">Resolution (px)</span>
                  <div class="d-flex align-items-center gap-2">
                    <span id="home-sim-raster-dimension-value" class="small font-monospace text-muted home-sim-field__value-label">500</span>
                    <button type="button" class="btn btn-link btn-sm py-0 home-sim-reset" \
data-override-key="raster_dimension" hidden>Reset</button>
                  </div>
                </div>
                <input id="home-sim-raster-dimension" type="range" class="form-range" min="128" max="4096" value="500">
              </div>
              {_sim_chain_group(
                  "Transmitter",
                  _sim_inline_field(
                      "transmitter.height_m",
                      "Height",
                      '<input id="home-sim-tx-height" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Height (m)",
                  )
                  + _sim_inline_field(
                      "transmitter.gain_dbi",
                      "Gain",
                      '<input id="home-sim-tx-gain" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Gain (dBi)",
                  )
                  + _sim_inline_field(
                      "transmitter.loss_db",
                      "Loss",
                      '<input id="home-sim-tx-loss" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Loss (dB)",
                  ),
              )}
              {_sim_chain_group(
                  "Receiver",
                  _sim_inline_field(
                      "receiver.height_m",
                      "Height",
                      '<input id="home-sim-rx-height" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Height (m)",
                  )
                  + _sim_inline_field(
                      "receiver.gain_dbi",
                      "Gain",
                      '<input id="home-sim-rx-gain" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Gain (dBi)",
                  )
                  + _sim_inline_field(
                      "receiver.loss_db",
                      "Loss",
                      '<input id="home-sim-rx-loss" type="number" step="0.1" class="form-control form-control-sm">',
                      title="Loss (dB)",
                  ),
              )}
              {_sim_reset_row("max_workers.splatter", "Splatter workers", '<input id="home-sim-splatter-workers" type="number" min="1" step="1" class="form-control form-control-sm home-sim-field--narrow">')}
            </div>
          </div>
          <div class="tab-pane fade" id="home-pane-modems" role="tabpanel">
            <div class="home-settings-split row g-3">
              <div class="col-md-4">
                <div class="d-flex flex-wrap gap-2 mb-2">
                  <button type="button" id="home-modem-add" class="btn btn-outline-secondary btn-sm">Add</button>
                  <button type="button" id="home-modem-duplicate" class="btn btn-outline-secondary btn-sm">Duplicate</button>
                  <button type="button" id="home-modem-delete" class="btn btn-outline-danger btn-sm">Delete</button>
                </div>
                <div id="home-modem-list" class="list-group home-settings-list"></div>
              </div>
              <div class="col-md-8">
                <div class="row g-2">
                  <div class="col-md-6">
                    <label for="home-modem-name" class="form-label small">Name</label>
                    <input id="home-modem-name" type="text" class="form-control form-control-sm" readonly>
                  </div>
                  <div class="col-md-6">
                    <label for="home-modem-frequency" class="form-label small">Frequency (MHz)</label>
                    <input id="home-modem-frequency" type="number" step="0.001" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-bandwidth" class="form-label small">Bandwidth (kHz)</label>
                    <input id="home-modem-bandwidth" type="number" step="0.1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-sf" class="form-label small">Spreading factor</label>
                    <input id="home-modem-sf" type="number" min="6" max="12" step="1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-cr" class="form-label small">Coding rate</label>
                    <input id="home-modem-cr" type="number" min="5" max="8" step="1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-margin" class="form-label small">Implementation margin (dB)</label>
                    <input id="home-modem-margin" type="number" step="0.1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-power" class="form-label small">Power (dBm)</label>
                    <input id="home-modem-power" type="number" step="0.1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-modem-sensitivity" class="form-label small">Sensitivity (dBm)</label>
                    <input id="home-modem-sensitivity" type="number" step="0.1" class="form-control form-control-sm">
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div class="tab-pane fade" id="home-pane-environments" role="tabpanel">
            <div class="home-settings-split row g-3">
              <div class="col-md-4">
                <div class="d-flex flex-wrap gap-2 mb-2">
                  <button type="button" id="home-env-add" class="btn btn-outline-secondary btn-sm">Add</button>
                  <button type="button" id="home-env-duplicate" class="btn btn-outline-secondary btn-sm">Duplicate</button>
                  <button type="button" id="home-env-delete" class="btn btn-outline-danger btn-sm">Delete</button>
                </div>
                <div id="home-env-list" class="list-group home-settings-list"></div>
              </div>
              <div class="col-md-8">
                <div class="row g-2">
                  <div class="col-12">
                    <label for="home-env-name" class="form-label small">Name</label>
                    <input id="home-env-name" type="text" class="form-control form-control-sm" readonly>
                  </div>
                  <div class="col-12">
                    <label for="home-env-description" class="form-label small">Description</label>
                    <input id="home-env-description" type="text" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-6">
                    <label for="home-env-climate" class="form-label small">Climate</label>
                    <select id="home-env-climate" class="form-select form-select-sm">
{climate_opts}
                    </select>
                  </div>
                  <div class="col-md-6">
                    <label for="home-env-polarization" class="form-label small">Polarization</label>
                    <select id="home-env-polarization" class="form-select form-select-sm">
                      <option value="vertical">vertical</option>
                      <option value="horizontal">horizontal</option>
                    </select>
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-clutter" class="form-label small">Clutter height (m)</label>
                    <input id="home-env-clutter" type="number" step="0.1" min="0" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-fresnel" class="form-label small">Fresnel clearance</label>
                    <input id="home-env-fresnel" type="number" step="0.01" min="0" max="1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-pessimism" class="form-label small">Coverage pessimism (dB)</label>
                    <input id="home-env-pessimism" type="number" step="0.1" min="0" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-situation" class="form-label small">Situation %</label>
                    <input id="home-env-situation" type="number" step="0.1" min="1" max="100" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-time" class="form-label small">Time %</label>
                    <input id="home-env-time" type="number" step="0.1" min="1" max="100" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-4">
                    <label for="home-env-dielectric" class="form-label small">Ground dielectric</label>
                    <input id="home-env-dielectric" type="number" step="0.1" min="1" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-6">
                    <label for="home-env-conductivity" class="form-label small">Ground conductivity (S/m)</label>
                    <input id="home-env-conductivity" type="number" step="0.0001" min="0" class="form-control form-control-sm">
                  </div>
                  <div class="col-md-6">
                    <label for="home-env-bending" class="form-label small">Atmosphere bending (N)</label>
                    <input id="home-env-bending" type="number" step="0.1" min="0" class="form-control form-control-sm">
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Close</button>
        <button type="button" id="home-settings-save" class="btn btn-primary btn-sm">Save</button>
      </div>
    </div>
  </div>
</div>"""

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
  <div class="d-flex justify-content-between align-items-start gap-3 mb-4">
    <div>
      <h1 class="h2 mb-1">Peaky</h1>
      <p class="text-muted small mb-0">Projects in {html.escape(str(projects_dir))}</p>
    </div>
    {_HOME_SETTINGS_GEAR}
  </div>
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
</div>
{home_settings_modal_html(on_project_map=False)}
<script src="/static/home-settings.js"></script>"""
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
      <h1 class="h5 mb-0 me-auto">{html.escape(slug)}</h1>
      <div class="d-flex align-items-center gap-2">
        <label for="viewshed-opacity" class="form-label mb-0 small text-muted">Opacity</label>
        <input id="viewshed-opacity" type="range" class="form-range" min="0" max="100" value="75"
          style="width: 5.5rem;" title="Viewshed opacity">
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
          <span class="site-panel__label small text-muted text-uppercase d-block mb-1">PLSS</span>
          <div class="d-flex gap-1 align-items-start">
            <p class="site-panel__value small mb-0 font-monospace flex-grow-1 text-break" id="site-panel-plss"></p>
            <button type="button" id="site-panel-copy-plss" class="btn btn-sm btn-outline-secondary coord-action-btn flex-shrink-0" title="Copy PLSS">⎘</button>
          </div>
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
        <div class="site-panel__actions border-top pt-3 mt-2">
          <button type="button" id="site-panel-edit-open" class="btn btn-outline-primary btn-sm w-100">Edit</button>
        </div>
      </div>
      <div id="site-panel-edit" hidden>
        <div class="site-panel__header d-flex align-items-start justify-content-between gap-2 mb-3">
          <h2 class="site-panel__title h6 mb-0" id="site-panel-edit-title">Edit</h2>
          <button type="button" class="btn-close btn-close-sm" id="site-panel-edit-close" title="Close" aria-label="Close"></button>
        </div>
        <div class="site-panel__section mb-2">
          <label class="site-panel__label small text-muted text-uppercase" for="site-panel-edit-name">Name</label>
          <input id="site-panel-edit-name" type="text" class="form-control form-control-sm" required>
        </div>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase">Slug</span>
          <p class="site-panel__value small mb-0 font-monospace text-muted" id="site-panel-edit-slug"></p>
        </div>
        <div class="site-panel__section mb-2">
          <label class="site-panel__label small text-muted text-uppercase" for="site-panel-edit-type">Type</label>
          <select id="site-panel-edit-type" class="form-select form-select-sm"></select>
        </div>
        <div class="site-panel__section mb-2">
          <span class="site-panel__label small text-muted text-uppercase d-block mb-1">Coordinates</span>
          <div class="coord-input-row d-flex gap-1 align-items-center mb-1">
            <label class="small text-muted mb-0" for="site-panel-edit-lat">Lat</label>
            <input id="site-panel-edit-lat" type="text" class="form-control form-control-sm font-monospace coord-field" inputmode="decimal" autocomplete="off">
            <button type="button" id="site-panel-edit-copy-coords" class="btn btn-sm btn-outline-secondary coord-action-btn" title="Copy lat, lon">⎘</button>
          </div>
          <div class="coord-input-row d-flex gap-1 align-items-center">
            <label class="small text-muted mb-0" for="site-panel-edit-lon">Lon</label>
            <input id="site-panel-edit-lon" type="text" class="form-control form-control-sm font-monospace coord-field" inputmode="decimal" autocomplete="off">
          </div>
          <ul class="edit-coord-history list-unstyled mb-0 mt-2" id="site-panel-edit-coord-history" hidden></ul>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-edit-plss-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase d-block mb-1">PLSS</span>
          <div class="d-flex gap-1 align-items-start">
            <p class="site-panel__value small mb-0 font-monospace flex-grow-1 text-break" id="site-panel-edit-plss"></p>
            <button type="button" id="site-panel-edit-copy-plss" class="btn btn-sm btn-outline-secondary coord-action-btn flex-shrink-0" title="Copy PLSS">⎘</button>
          </div>
        </div>
        <div class="site-panel__section mb-2" id="site-panel-edit-links-section" hidden>
          <span class="site-panel__label small text-muted text-uppercase" id="site-panel-edit-links-label">Linked sites</span>
          <ul class="site-panel__links small mb-0 ps-3" id="site-panel-edit-links"></ul>
        </div>
        <div class="site-panel__viewshed form-check border-top pt-2 mt-2" id="site-panel-edit-viewshed-section">
          <input class="form-check-input" type="checkbox" id="site-panel-edit-viewshed" checked>
          <label class="form-check-label small" for="site-panel-edit-viewshed">Show viewshed preview</label>
          <span class="site-panel__viewshed-hint small text-muted ms-1" id="site-panel-edit-viewshed-hint"></span>
        </div>
        <div id="site-panel-edit-error" class="alert alert-danger py-2 small mb-2" role="alert" hidden></div>
        <div class="site-panel__create-actions d-flex gap-2 mt-3">
          <button type="button" id="site-panel-edit-save" class="btn btn-primary btn-sm">Save</button>
          <button type="button" id="site-panel-edit-cancel" class="btn btn-outline-secondary btn-sm">Cancel</button>
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
          <span class="site-panel__label small text-muted text-uppercase d-block mb-1">PLSS</span>
          <div class="d-flex gap-1 align-items-start">
            <p class="site-panel__value small mb-0 font-monospace flex-grow-1 text-break" id="site-panel-create-plss"></p>
            <button type="button" id="site-panel-create-copy-plss" class="btn btn-sm btn-outline-secondary coord-action-btn flex-shrink-0" title="Copy PLSS">⎘</button>
          </div>
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
{home_settings_modal_html(on_project_map=True)}
<script src="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.js" crossorigin=""></script>
<script>window.PEAKY_PROJECT = {peaky_config};</script>
<script src="/static/home-settings.js"></script>
<script src="/static/project-map.js"></script>"""
    return html_page(slug, body, wide=True, extra_head=_MAPLIBRE_HEAD)
