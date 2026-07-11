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

_WEBAWESOME_VERSION = "3.8.0"

_WEBAWESOME_HEAD = f"""\
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{_WEBAWESOME_VERSION}/dist/styles/webawesome.css" \
crossorigin="anonymous">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{_WEBAWESOME_VERSION}/dist/styles/native.css" \
crossorigin="anonymous">
  <link rel="stylesheet" href="/static/app.css">"""

_WEBAWESOME_FOOT = f"""\
  <script type="module" src="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{_WEBAWESOME_VERSION}/dist-cdn/webawesome.loader.js" \
crossorigin="anonymous"></script>"""

_HOME_SETTINGS_GEAR = """\
<wa-button id="home-settings-open" appearance="outlined" size="s" data-dialog="open home-settings-modal" \
title="Settings" aria-label="Settings">
  <wa-icon name="gear" label="Settings"></wa-icon>
</wa-button>"""

_PEAKY_LOGO_LINK = """\
<a class="project-toolbar__logo" href="/" aria-label="Peaky Finders">
  <img src="/favicon-96x96.png" width="28" height="28" alt="">
</a>"""

_CLIMATE_OPTIONS = (
    "equatorial",
    "continental_subtropical",
    "maritime_subtropical",
    "desert",
    "continental_temperate",
    "maritime_temperate_land",
    "maritime_temperate_sea",
)


def _sim_reset_button(override_key: str) -> str:
    key = html.escape(override_key)
    return f"""\
<wa-button class="home-sim-reset" appearance="plain" size="s" data-override-key="{key}" hidden \
type="button" title="Reset to default" aria-label="Reset to default">
  <wa-icon name="arrow-rotate-left" label="Reset to default"></wa-icon>
</wa-button>"""


def _sim_inline_field(override_key: str, label: str, control_html: str, *, title: str = "") -> str:
    key = html.escape(override_key)
    title_attr = f' title="{html.escape(title)}"' if title else ""
    return f"""\
                  <div class="home-sim-field home-sim-field--inline" data-override-key="{key}">
                    <label class="home-sim-inline-label"{title_attr}>{html.escape(label)}</label>
                    {control_html}
                    {_sim_reset_button(override_key)}
                  </div>"""


def _sim_chain_group(title: str, fields_html: str) -> str:
    return f"""\
              <div class="home-sim-col">
                <div class="home-sim-chain">
                  <div class="home-sim-chain__title">{html.escape(title)}</div>
                  <div class="home-sim-chain__fields">
{fields_html}
                  </div>
                </div>
              </div>"""


def _sim_reset_row(override_key: str, label: str, control_html: str, *, compact: bool = False) -> str:
    key = html.escape(override_key)
    col = "home-sim-col home-sim-col--compact" if compact else "home-sim-col"
    return f"""\
              <div class="home-sim-field {col}" data-override-key="{key}">
                <div class="pf-field-head">
                  <span class="pf-label">{html.escape(label)}</span>
                  {_sim_reset_button(override_key)}
                </div>
                {control_html}
              </div>"""


def home_settings_modal_html(*, on_project_map: bool = False) -> str:
    project_hint = ""
    if on_project_map:
        project_hint = (
            '<p class="wa-caption pf-muted" id="home-settings-project-hint">'
            "Simulation values show this project's effective settings. "
            "Highlighted fields override global defaults; use the reset control to clear a project override."
            "</p>"
        )
    else:
        project_hint = (
            '<p class="wa-caption pf-muted" id="home-settings-project-hint">'
            "Simulation tab edits global defaults shared by all projects. "
            "Open a project map to override per project."
            "</p>"
        )
    climate_opts = "\n".join(
        f'              <option value="{html.escape(c)}">{html.escape(c.replace("_", " "))}</option>'
        for c in _CLIMATE_OPTIONS
    )
    return f"""\
<wa-dialog id="home-settings-modal" label="Settings" style="--width: 56rem" with-footer>
  <wa-callout id="home-settings-error" variant="danger" hidden></wa-callout>
  {project_hint}
  <wa-tab-group id="home-settings-tabs" active="simulation">
    <wa-tab slot="nav" panel="simulation">Simulation</wa-tab>
    <wa-tab slot="nav" panel="modems">Modems</wa-tab>
    <wa-tab slot="nav" panel="environments">Environments</wa-tab>
    <wa-tab-panel name="simulation">
      <div class="home-sim-grid">
        {_sim_reset_row("modem", "Modem preset", '<select id="home-sim-modem"></select>')}
        {_sim_reset_row("environment", "Environment preset", '<select id="home-sim-environment"></select>')}
        <div class="home-sim-field home-sim-col" data-override-key="radius_km">
          <div class="pf-field-head">
            <span class="pf-label">Radius (km)</span>
            <div class="pf-field-head__meta">
              <span id="home-sim-radius-km-value" class="pf-mono pf-muted home-sim-field__value-label">50</span>
              {_sim_reset_button("radius_km")}
            </div>
          </div>
          <input id="home-sim-radius-km" type="range" class="pf-range" min="1" max="100" value="50">
        </div>
        <div class="home-sim-field home-sim-col" data-override-key="raster_dimension">
          <div class="pf-field-head">
            <span class="pf-label">Resolution (px)</span>
            <div class="pf-field-head__meta">
              <span id="home-sim-raster-dimension-value" class="pf-mono pf-muted home-sim-field__value-label">500</span>
              {_sim_reset_button("raster_dimension")}
            </div>
          </div>
          <input id="home-sim-raster-dimension" type="range" class="pf-range" min="128" max="4096" value="500">
        </div>
        {_sim_chain_group(
            "Transmitter",
            _sim_inline_field(
                "transmitter.height_m",
                "Height",
                '<input id="home-sim-tx-height" type="number" step="0.1">',
                title="Height (m)",
            )
            + _sim_inline_field(
                "transmitter.gain_dbi",
                "Gain",
                '<input id="home-sim-tx-gain" type="number" step="0.1">',
                title="Gain (dBi)",
            )
            + _sim_inline_field(
                "transmitter.loss_db",
                "Loss",
                '<input id="home-sim-tx-loss" type="number" step="0.1">',
                title="Loss (dB)",
            ),
        )}
        {_sim_chain_group(
            "Receiver",
            _sim_inline_field(
                "receiver.height_m",
                "Height",
                '<input id="home-sim-rx-height" type="number" step="0.1">',
                title="Height (m)",
            )
            + _sim_inline_field(
                "receiver.gain_dbi",
                "Gain",
                '<input id="home-sim-rx-gain" type="number" step="0.1">',
                title="Gain (dBi)",
            )
            + _sim_inline_field(
                "receiver.loss_db",
                "Loss",
                '<input id="home-sim-rx-loss" type="number" step="0.1">',
                title="Loss (dB)",
            ),
        )}
        {_sim_reset_row("max_workers.splatter", "Splatter workers", '<input id="home-sim-splatter-workers" type="number" min="1" step="1" class="home-sim-field--narrow">')}
      </div>
    </wa-tab-panel>
    <wa-tab-panel name="modems">
      <div class="home-settings-split">
        <div class="home-settings-split__list">
          <div class="pf-button-row">
            <wa-button id="home-modem-add" appearance="outlined" size="s" type="button">Add</wa-button>
            <wa-button id="home-modem-duplicate" appearance="outlined" size="s" type="button">Duplicate</wa-button>
            <wa-button id="home-modem-delete" appearance="outlined" size="s" variant="danger" type="button">Delete</wa-button>
          </div>
          <div id="home-modem-list" class="home-settings-list"></div>
        </div>
        <div class="home-settings-split__form pf-form-grid">
          <div class="pf-form-field">
            <label for="home-modem-name" class="pf-label">Name</label>
            <input id="home-modem-name" type="text" readonly>
          </div>
          <div class="pf-form-field">
            <label for="home-modem-frequency" class="pf-label">Frequency (MHz)</label>
            <input id="home-modem-frequency" type="number" step="0.001">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-bandwidth" class="pf-label">Bandwidth (kHz)</label>
            <input id="home-modem-bandwidth" type="number" step="0.1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-sf" class="pf-label">Spreading factor</label>
            <input id="home-modem-sf" type="number" min="6" max="12" step="1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-cr" class="pf-label">Coding rate</label>
            <input id="home-modem-cr" type="number" min="5" max="8" step="1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-margin" class="pf-label">Implementation margin (dB)</label>
            <input id="home-modem-margin" type="number" step="0.1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-power" class="pf-label">Power (dBm)</label>
            <input id="home-modem-power" type="number" step="0.1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-modem-sensitivity" class="pf-label">Sensitivity (dBm)</label>
            <input id="home-modem-sensitivity" type="number" step="0.1">
          </div>
        </div>
      </div>
    </wa-tab-panel>
    <wa-tab-panel name="environments">
      <div class="home-settings-split">
        <div class="home-settings-split__list">
          <div class="pf-button-row">
            <wa-button id="home-env-add" appearance="outlined" size="s" type="button">Add</wa-button>
            <wa-button id="home-env-duplicate" appearance="outlined" size="s" type="button">Duplicate</wa-button>
            <wa-button id="home-env-delete" appearance="outlined" size="s" variant="danger" type="button">Delete</wa-button>
          </div>
          <div id="home-env-list" class="home-settings-list"></div>
        </div>
        <div class="home-settings-split__form pf-form-grid">
          <div class="pf-form-field pf-form-field--full">
            <label for="home-env-name" class="pf-label">Name</label>
            <input id="home-env-name" type="text" readonly>
          </div>
          <div class="pf-form-field pf-form-field--full">
            <label for="home-env-description" class="pf-label">Description</label>
            <input id="home-env-description" type="text">
          </div>
          <div class="pf-form-field">
            <label for="home-env-climate" class="pf-label">Climate</label>
            <select id="home-env-climate">
{climate_opts}
            </select>
          </div>
          <div class="pf-form-field">
            <label for="home-env-polarization" class="pf-label">Polarization</label>
            <select id="home-env-polarization">
              <option value="vertical">vertical</option>
              <option value="horizontal">horizontal</option>
            </select>
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-clutter" class="pf-label">Clutter height (m)</label>
            <input id="home-env-clutter" type="number" step="0.1" min="0">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-fresnel" class="pf-label">Fresnel clearance</label>
            <input id="home-env-fresnel" type="number" step="0.01" min="0" max="1">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-pessimism" class="pf-label">Coverage pessimism (dB)</label>
            <input id="home-env-pessimism" type="number" step="0.1" min="0">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-situation" class="pf-label">Situation %</label>
            <input id="home-env-situation" type="number" step="0.1" min="1" max="100">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-time" class="pf-label">Time %</label>
            <input id="home-env-time" type="number" step="0.1" min="1" max="100">
          </div>
          <div class="pf-form-field pf-form-field--third">
            <label for="home-env-dielectric" class="pf-label">Ground dielectric</label>
            <input id="home-env-dielectric" type="number" step="0.1" min="1">
          </div>
          <div class="pf-form-field">
            <label for="home-env-conductivity" class="pf-label">Ground conductivity (S/m)</label>
            <input id="home-env-conductivity" type="number" step="0.0001" min="0">
          </div>
          <div class="pf-form-field">
            <label for="home-env-bending" class="pf-label">Atmosphere bending (N)</label>
            <input id="home-env-bending" type="number" step="0.1" min="0">
          </div>
        </div>
      </div>
    </wa-tab-panel>
  </wa-tab-group>
  <wa-button slot="footer" data-dialog="close" appearance="outlined" size="s" type="button">Close</wa-button>
  <wa-button slot="footer" id="home-settings-save" variant="brand" size="s" type="button">Save</wa-button>
</wa-dialog>"""

_MAPLIBRE_HEAD = """\
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.24.0/dist/maplibre-gl.css" crossorigin="">"""


def html_page(title: str, body: str, *, wide: bool = False, extra_head: str = "") -> bytes:
    body_class = ' class="wide"' if wide else ""
    doc = f"""<!DOCTYPE html>
<html lang="en" class="wa-dark wa-theme-default wa-brand-blue">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  {_FAVICON_HEAD}
  {_WEBAWESOME_HEAD}
  {extra_head}
</head>
<body{body_class}>
{body}
{_WEBAWESOME_FOOT}
</body>
</html>"""
    return doc.encode("utf-8")


def landing_html(projects_dir: Path, projects: list[str], *, error: str | None = None) -> bytes:
    err = ""
    if error:
        err = f'<wa-callout variant="danger">{html.escape(error)}</wa-callout>'

    if projects:
        items = "\n".join(
            f'    <a class="project-list__item" href="/p/{html.escape(slug)}/">'
            f"{html.escape(slug)}</a>"
            for slug in projects
        )
        project_block = f'<div class="project-list">\n{items}\n  </div>'
    else:
        project_block = '<p class="wa-caption pf-muted pf-italic">No projects yet — create one below.</p>'

    body = f"""<div class="landing-container pf-page">
  {err}
  <div class="landing-header">
    <div>
      <h1 class="wa-heading">Peaky</h1>
    </div>
    {_HOME_SETTINGS_GEAR}
  </div>
  {project_block}
  <wa-card>
    <form method="post" action="/projects" class="pf-stack pf-gap-m">
      <div class="pf-form-field">
        <label for="slug" class="pf-label">New project</label>
        <input id="slug" name="slug" type="text" placeholder="my-region" required autofocus>
      </div>
      <wa-button type="submit" variant="brand">Create empty template</wa-button>
    </form>
  </wa-card>
</div>
{home_settings_modal_html(on_project_map=False)}
<script src="/static/home-settings.js"></script>"""
    return html_page("Peaky", body)


def project_error_html(slug: str, project_dir: Path, message: str) -> bytes:
    body = f"""<div class="landing-container pf-page">
  <wa-button href="/" appearance="plain" size="s">&larr; All projects</wa-button>
  <h1 class="wa-heading">{html.escape(slug)}</h1>
  <wa-callout variant="danger">
    Could not load project sites: {html.escape(message)}
  </wa-callout>
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
    body = f"""<header class="project-toolbar">
  <div class="project-toolbar__inner">
    <div class="project-toolbar__row">
      {_PEAKY_LOGO_LINK}
      <h1 class="project-toolbar__title">{html.escape(slug)}</h1>
    </div>
  </div>
</header>
<div class="map-shell">
  <aside id="entity-panel" class="entity-panel" hidden aria-label="Sites and goals">
    <div class="entity-panel__header">
      <div class="entity-tabs" role="tablist">
        <button type="button" class="entity-tab active" id="entity-tab-sites" data-entity-tab="sites" role="tab" aria-selected="true">Sites</button>
        <button type="button" class="entity-tab" id="entity-tab-goals" data-entity-tab="goals" role="tab" aria-selected="false">Goals</button>
      </div>
    </div>
    <div class="entity-panel__body">
      <div id="entity-panel-sites-pane" class="entity-panel__pane" role="tabpanel">
        <div id="entity-panel-tag-filters" class="entity-panel__tag-filters" role="toolbar" aria-label="Site tag filters"></div>
        <div id="entity-panel-sites-list" class="entity-panel__list"></div>
        <div class="entity-panel__footer">
          <wa-button id="entity-panel-add-site" appearance="outlined" variant="brand" size="s" class="pf-stretch" type="button" title="Add site">+ Site</wa-button>
        </div>
      </div>
      <div id="entity-panel-goals-pane" class="entity-panel__pane" hidden role="tabpanel">
        <div id="entity-panel-goals-list" class="entity-panel__list"></div>
        <div class="entity-panel__footer">
          <wa-button id="entity-panel-add-goal" appearance="outlined" variant="brand" size="s" class="pf-stretch" type="button" title="Add goal">+ Goal</wa-button>
        </div>
      </div>
    </div>
  </aside>
  <button
    type="button"
    id="entity-panel-toggle"
    class="entity-panel-toggle"
    title="Sites and goals"
    aria-label="Show sites and goals"
    aria-expanded="false"
    aria-controls="entity-panel"
  >
    <wa-icon name="chevron-right" label=""></wa-icon>
  </button>
  <div id="map"></div>
  <div id="pin-load-overlays" class="pin-load-overlays" aria-hidden="true"></div>
  <aside id="site-panel" class="site-panel" hidden>
    <div class="site-panel__body">
      <div id="site-panel-view">
        <div class="site-panel__header">
          <h2 class="site-panel__title" id="site-panel-name"></h2>
          <wa-button id="site-panel-close" appearance="plain" size="s" type="button" title="Close" aria-label="Close">
            <wa-icon name="xmark" label="Close"></wa-icon>
          </wa-button>
        </div>
        <span class="site-panel__badge" id="site-panel-type"></span>
        <div class="site-panel__section" id="site-panel-tags-section">
          <span class="site-panel__label">Tags</span>
          <div class="site-tags" id="site-panel-tags"></div>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Coordinates</span>
          <p class="site-panel__value" id="site-panel-coords"></p>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Elevation</span>
          <p class="site-panel__value" id="site-panel-elevation"></p>
        </div>
        <div class="site-panel__section" id="site-panel-plss-section" hidden>
          <span class="site-panel__label">PLSS</span>
          <div class="pf-copy-row">
            <p class="site-panel__value pf-mono pf-break" id="site-panel-plss"></p>
            <wa-button id="site-panel-copy-plss" appearance="outlined" size="s" class="coord-action-btn" type="button" title="Copy PLSS">
              <wa-icon name="copy" label="Copy PLSS"></wa-icon>
            </wa-button>
          </div>
        </div>
        <div class="site-panel__section" id="site-panel-desc-section" hidden>
          <span class="site-panel__label">Description</span>
          <p class="site-panel__value" id="site-panel-desc"></p>
        </div>
        <div class="site-panel__section" id="site-panel-rationale-section" hidden>
          <span class="site-panel__label">Rationale</span>
          <p class="site-panel__value" id="site-panel-rationale"></p>
        </div>
        <div class="site-panel__section" id="site-panel-links-section" hidden>
          <span class="site-panel__label">Linked sites</span>
          <ul class="site-panel__links" id="site-panel-links"></ul>
        </div>
        <div class="site-panel__section" id="site-panel-goal-links-section" hidden>
          <span class="site-panel__label">Linked repeaters</span>
          <ul class="site-panel__links" id="site-panel-goal-links"></ul>
        </div>
        <label class="site-panel__viewshed pf-check" id="site-panel-viewshed-section">
          <input type="checkbox" id="site-panel-viewshed" checked>
          <span>Show viewshed</span>
          <span class="site-panel__viewshed-hint pf-muted" id="site-panel-viewshed-hint"></span>
        </label>
        <div class="site-panel__actions">
          <wa-button id="site-panel-edit-open" appearance="outlined" variant="brand" size="s" class="pf-stretch" type="button">Edit</wa-button>
        </div>
      </div>
      <div id="site-panel-edit" hidden>
        <div class="site-panel__header">
          <h2 class="site-panel__title" id="site-panel-edit-title">Edit</h2>
          <wa-button id="site-panel-edit-close" appearance="plain" size="s" type="button" title="Close" aria-label="Close">
            <wa-icon name="xmark" label="Close"></wa-icon>
          </wa-button>
        </div>
        <div class="site-panel__section">
          <label class="site-panel__label" for="site-panel-edit-name">Name</label>
          <input id="site-panel-edit-name" type="text" required>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Slug</span>
          <p class="site-panel__value pf-mono pf-muted" id="site-panel-edit-slug"></p>
        </div>
        <div class="site-panel__section">
          <label class="site-panel__label" for="site-panel-edit-type">Type</label>
          <select id="site-panel-edit-type"></select>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Coordinates</span>
          <div class="coord-input-row">
            <label class="pf-label" for="site-panel-edit-lat">Lat</label>
            <input id="site-panel-edit-lat" type="text" class="pf-mono coord-field" inputmode="decimal" autocomplete="off">
            <wa-button id="site-panel-edit-copy-coords" appearance="outlined" size="s" class="coord-action-btn" type="button" title="Copy lat, lon">
              <wa-icon name="copy" label="Copy coordinates"></wa-icon>
            </wa-button>
          </div>
          <div class="coord-input-row">
            <label class="pf-label" for="site-panel-edit-lon">Lon</label>
            <input id="site-panel-edit-lon" type="text" class="pf-mono coord-field" inputmode="decimal" autocomplete="off">
          </div>
          <ul class="edit-coord-history" id="site-panel-edit-coord-history" hidden></ul>
        </div>
        <div class="site-panel__section" id="site-panel-edit-plss-section" hidden>
          <span class="site-panel__label">PLSS</span>
          <div class="pf-copy-row">
            <p class="site-panel__value pf-mono pf-break" id="site-panel-edit-plss"></p>
            <wa-button id="site-panel-edit-copy-plss" appearance="outlined" size="s" class="coord-action-btn" type="button" title="Copy PLSS">
              <wa-icon name="copy" label="Copy PLSS"></wa-icon>
            </wa-button>
          </div>
        </div>
        <div class="site-panel__section" id="site-panel-edit-links-section" hidden>
          <span class="site-panel__label" id="site-panel-edit-links-label">Linked sites</span>
          <ul class="site-panel__links" id="site-panel-edit-links"></ul>
        </div>
        <label class="site-panel__viewshed pf-check" id="site-panel-edit-viewshed-section">
          <input type="checkbox" id="site-panel-edit-viewshed" checked>
          <span>Show viewshed preview</span>
          <span class="site-panel__viewshed-hint pf-muted" id="site-panel-edit-viewshed-hint"></span>
        </label>
        <wa-callout id="site-panel-edit-error" variant="danger" hidden></wa-callout>
        <div class="site-panel__create-actions">
          <wa-button id="site-panel-edit-save" variant="brand" size="s" type="button">Save</wa-button>
          <wa-button id="site-panel-edit-cancel" appearance="outlined" size="s" type="button">Cancel</wa-button>
        </div>
      </div>
      <div id="site-panel-create" hidden>
        <div class="site-panel__header">
          <h2 class="site-panel__title" id="site-panel-create-title">New site</h2>
          <wa-button id="site-panel-create-close" appearance="plain" size="s" type="button" title="Close" aria-label="Close">
            <wa-icon name="xmark" label="Close"></wa-icon>
          </wa-button>
        </div>
        <span class="site-panel__badge site-panel__badge--planned" id="site-panel-create-badge">planned</span>
        <div class="site-panel__section">
          <label class="site-panel__label" for="site-panel-create-name">Name</label>
          <input id="site-panel-create-name" type="text" required>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Slug</span>
          <p class="site-panel__value pf-mono pf-muted" id="site-panel-slug-preview"></p>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Coordinates</span>
          <p class="site-panel__value" id="site-panel-create-coords"></p>
        </div>
        <div class="site-panel__section" id="site-panel-create-plss-section" hidden>
          <span class="site-panel__label">PLSS</span>
          <div class="pf-copy-row">
            <p class="site-panel__value pf-mono pf-break" id="site-panel-create-plss"></p>
            <wa-button id="site-panel-create-copy-plss" appearance="outlined" size="s" class="coord-action-btn" type="button" title="Copy PLSS">
              <wa-icon name="copy" label="Copy PLSS"></wa-icon>
            </wa-button>
          </div>
        </div>
        <div class="site-panel__section" id="site-panel-create-links-section" hidden>
          <span class="site-panel__label" id="site-panel-create-links-label">Linked sites</span>
          <ul class="site-panel__links" id="site-panel-create-links"></ul>
        </div>
        <label class="site-panel__viewshed pf-check" id="site-panel-create-viewshed-section">
          <input type="checkbox" id="site-panel-create-viewshed" checked>
          <span>Show viewshed</span>
          <span class="site-panel__viewshed-hint pf-muted" id="site-panel-create-viewshed-hint"></span>
        </label>
        <wa-callout id="site-panel-create-error" variant="danger" hidden></wa-callout>
        <div class="site-panel__create-actions">
          <wa-button id="site-panel-create-save" variant="brand" size="s" type="button">Save</wa-button>
          <wa-button id="site-panel-create-cancel" appearance="outlined" size="s" type="button">Cancel</wa-button>
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
