//! HTML page builders for peaky serve.

use std::path::Path;

use peaky_preset::{site_row_from_entry, Preset, SiteEntry};

const WEBAWESOME_VERSION: &str = "3.8.0";

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

fn webawesome_head(extra: &str) -> String {
    format!(
        r#"<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{WEBAWESOME_VERSION}/dist/styles/webawesome.css" crossorigin="anonymous">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{WEBAWESOME_VERSION}/dist/styles/native.css" crossorigin="anonymous">
<link rel="stylesheet" href="/static/app.css">
{extra}"#
    )
}

fn webawesome_foot() -> String {
    format!(
        r#"<script type="module" src="https://cdn.jsdelivr.net/npm/@awesome.me/webawesome@{WEBAWESOME_VERSION}/dist-cdn/webawesome.loader.js" crossorigin="anonymous"></script>"#
    )
}

fn favicon_head() -> &'static str {
    r##"<link rel="icon" type="image/png" href="/favicon-96x96.png" sizes="96x96" />
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="shortcut icon" href="/favicon.ico" />
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
<link rel="manifest" href="/site.webmanifest" />
<meta name="theme-color" content="#111820" />"##
}

pub fn html_page(title: &str, body: &str, wide: bool, extra_head: &str) -> String {
    let cls = if wide { " pf-page--wide" } else { "" };
    format!(
        r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{favicon}
{head}
{extra}
</head>
<body class="pf-body{cls}">
{body}
{foot}
</body>
</html>"#,
        title = html_escape(title),
        favicon = favicon_head(),
        head = webawesome_head(""),
        extra = extra_head,
        body = body,
        foot = webawesome_foot(),
        cls = cls,
    )
}

pub fn site_api_row(slug: &str, site: &SiteEntry) -> serde_json::Value {
    serde_json::Value::Object(site_row_from_entry(slug, site))
}

pub fn project_html(slug: &str, preset: &Preset, _preset_path: &Path) -> String {
    let sites: Vec<serde_json::Value> = preset
        .sites
        .iter()
        .map(|(s, ent)| site_api_row(s, ent))
        .collect();
    let land_sources: Vec<serde_json::Value> = preset
        .land
        .sources
        .iter()
        .filter(|(_, src)| src.is_enabled())
        .map(|(id, src)| {
            serde_json::json!({
                "id": id,
                "path": src.path,
                "label": src.label,
                "layers": src.layers,
            })
        })
        .collect();
    let seek_plan = preset
        .seek
        .plan
        .as_ref()
        .map(|p| serde_json::to_value(p).unwrap_or_default());
    let peaky_config = serde_json::json!({
        "slug": slug,
        "sites": sites,
        "simulation": preset.simulation,
        "seek": {
            "peak_bin_size_m": preset.seek.peak_bin_size_m,
            "max_candidates": preset.seek.max_candidates,
            "plan": seek_plan,
        },
        "land": {
            "sources": land_sources,
            "dataGdbPaths": [],
            "aoiDigest": "none",
            "sidebar": preset.land.sidebar,
        },
    });
    let config_js = serde_json::to_string(&peaky_config).unwrap_or_else(|_| "{}".to_string());
    let mut page = include_str!("../../../assets/templates/project_body.html").to_string();
    page = page.replace("__PEAKY_CONFIG__", &config_js);
    page = page.replace("__PEAKY_SLUG__", &html_escape(slug));
    page
}

pub fn project_error_html(slug: &str, message: &str) -> String {
    let body = format!(
        r#"<div class="landing-container pf-page">
  <h1 class="wa-heading">{slug}</h1>
  <wa-callout variant="danger">Could not load project: {msg}</wa-callout>
</div>"#,
        slug = html_escape(slug),
        msg = html_escape(message),
    );
    html_page(slug, &body, true, "")
}
