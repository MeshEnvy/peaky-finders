//! HTTP API and page routes.

use std::collections::HashMap;
use std::time::Duration;

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{get, patch, post},
    Json, Router,
};
use peaky_geo::{parse_kml_point_placemarks, parse_kmz_point_placemarks};
use peaky_preset::{
    insert_preset_site, load_preset, load_preset_raw, patch_preset_site, patch_preset_sites_tags,
    place_slugs, preset_site_slugs, remove_preset_site, unique_place_slug, unique_site_slug,
    validate_coords, LandSidebar, SiteEntry,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::events::sse_keepalive_interval;
use crate::html::{project_error_html, project_html, site_api_row};
use crate::links::{
    compute_links_for_slugs, invalidate_project_site_links_cache, load_project_site_links,
    load_single_site_links, site_pair_link_detail,
};
use crate::fortify::parse_fortify_request;
use crate::site_prefetch::{load_site_placement_prefetch, SitePrefetchError};
use crate::state::AppState;
use crate::peaks::{
    list_peaks_payload, peak_hike_payload, peak_jeep_payload, place_access_payload,
};
use crate::land::{
    land_data_gdbs_payload, land_delete_source, land_import_preview_payload, land_import_source,
    land_patch_sidebar, land_patch_source, land_preview_fields_payload,
    land_preview_geojson_filtered_payload, land_preview_geojson_payload, land_preview_values_payload,
    list_land_payload, read_layer_geojson_bytes, read_overlay_geojson_bytes,
    read_overlay_part_geojson_bytes, LandImportBody,
    LandPatchSourceBody, LandPreviewGeoJsonBody,
};
use crate::simulation::project_simulation_payload;
use crate::viewshed::{
    coords_viewshed_overlay_if_ready, ensure_viewshed_png, read_coords_viewshed_png_if_ready,
};
use crate::alternates::parse_alternates_request;
use crate::link_solver::{
    accept_link_solver_route, link_solver_progress_key, parse_link_solver_request,
    routes_like_from_json, LinkSolverError,
};
use crate::viewshed_index::{build_viewshed_index, site_viewshed_overlay_if_ready, viewshed_cache_png_api_path};
use crate::viewshed_sim::{parse_lat_lon_params, parse_viewshed_sim_params, sim_status_from_err};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(project_page))
        .route("/api/p/{slug}/elev", get(crate::dem::place_elev))
        .route("/api/p/{slug}/sites", get(list_sites).post(add_site))
        .route("/api/p/{slug}/sites/prefetch", get(sites_prefetch))
        .route(
            "/api/p/{slug}/sites/{site_slug}",
            patch(patch_site).delete(delete_site),
        )
        .route("/api/p/{slug}/sites/tags/bulk", post(bulk_tags))
        .route(
            "/api/p/{slug}/sites/import/preview",
            post(import_preview),
        )
        .route("/api/p/{slug}/sites/import", post(import_sites))
        .route(
            "/api/p/{slug}/viewsheds/prefetch/warm",
            post(viewshed_prefetch_warm),
        )
        .route(
            "/api/p/{slug}/viewsheds/prefetch/splat.png",
            get(viewshed_prefetch_png),
        )
        .route("/api/p/{slug}/viewsheds/prefetch", get(viewshed_prefetch_meta))
        .route(
            "/api/p/{slug}/viewsheds/{site_slug}/splat.png",
            get(viewshed_png),
        )
        .route(
            "/api/p/{slug}/viewsheds/{site_slug}",
            get(viewshed_meta),
        )
        .route("/api/p/{slug}/viewsheds/index", get(viewshed_index))
        .route(
            "/api/p/{slug}/cache/viewsheds/{digest}/splat.png",
            get(viewshed_cache_png),
        )
        .route("/api/p/{slug}/links", get(links_mesh))
        .route("/api/p/{slug}/links/pair", get(link_pair_detail))
        .route("/api/p/{slug}/links/warm", post(links_warm))
        .route("/api/p/{slug}/sites/{site_slug}/links", get(site_links))
        .route("/api/p/{slug}/warm/priorities", post(warm_priorities))
        .route("/api/p/{slug}/warm/status", get(warm_status))
        .route("/api/p/{slug}/events", get(project_events))
        .route("/api/p/{slug}/peaks", get(peaks_list))
        .route("/api/p/{slug}/peaks/{peak_slug}/hike", get(peak_hike))
        .route("/api/p/{slug}/peaks/{peak_slug}/jeep", get(peak_jeep))
        .route("/api/p/{slug}/access/{place_slug}", get(place_access))
        .route("/api/p/{slug}/access/{place_slug}/warm", post(place_access_warm))
        .route("/api/p/{slug}/land", get(land_list))
        .route(
            "/api/p/{slug}/land/overlays/{kind}/geojson",
            get(land_overlay_geojson),
        )
        .route(
            "/api/p/{slug}/land/overlays/{kind}/sources/{source_id}/geojson",
            get(land_overlay_part_geojson),
        )
        .route("/api/p/{slug}/land/data-gdbs", get(land_data_gdbs))
        .route("/api/p/{slug}/land/import/preview", post(land_import_preview))
        .route(
            "/api/p/{slug}/land/import/preview/fields",
            get(land_import_preview_fields),
        )
        .route(
            "/api/p/{slug}/land/import/preview/values",
            get(land_import_preview_values),
        )
        .route("/api/p/{slug}/land/import", post(land_import))
        .route("/api/p/{slug}/land/preview/geojson", get(land_preview_geojson_get).post(land_preview_geojson_post))
        .route("/api/p/{slug}/land/sidebar", patch(land_sidebar_patch))
        .route(
            "/api/p/{slug}/land/sources/{source_id}",
            patch(land_source_patch).delete(land_source_delete),
        )
        .route(
            "/api/p/{slug}/land/sources/{source_id}/layers/{layer_key}/geojson",
            get(land_layer_geojson),
        )
        .route("/api/p/{slug}/simulation", get(project_simulation))
        .route("/api/p/{slug}/link-solver", get(link_solver_scan))
        .route("/api/p/{slug}/link-solver/scan-progress", get(link_solver_progress))
        .route("/api/p/{slug}/link-solver/like", get(link_solver_like))
        .route("/api/p/{slug}/link-solver/accept", post(link_solver_accept))
        .route("/api/p/{slug}/alternates", get(alternates_scan))
        .route("/api/p/{slug}/alternates/scan-progress", get(alternates_progress))
        .route("/api/p/{slug}/fortify", get(fortify_scan))
        .route("/api/p/{slug}/fortify/scan-progress", get(fortify_progress))
        .route("/api/home/modems", get(home_modems))
        .route("/api/home/environments", get(home_environments))
        .route("/api/home/simulation", get(home_simulation))
        .route("/api/dem/terrarium/{z}/{x}/{y}", get(dem_terrarium_tile))
        .route("/api/dem/hillshade/{z}/{x}/{y}", get(dem_hillshade_tile))
}

async fn project_page(
    State(state): State<AppState>,
) -> Result<Html<String>, Html<String>> {
    let path = state.preset_path();
    match load_preset(&path) {
        Ok(preset) => {
            state.warm.ensure_aoi_dem_prefetch(&state.slug, path.clone());
            Ok(Html(project_html(&state.slug, &preset, &path)))
        }
        Err(e) => Err(Html(project_error_html(&state.slug, &e.to_string()))),
    }
}

async fn list_sites(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, StatusCode> {
    let preset = load_preset(&state.preset_path()).map_err(|_| StatusCode::NOT_FOUND)?;
    let sites: Vec<Value> = preset
        .sites
        .iter()
        .map(|(s, e)| site_api_row(s, e))
        .collect();
    Ok(Json(json!({ "sites": sites })))
}

#[derive(Deserialize)]
struct AddSiteBody {
    name: String,
    lat: f64,
    lon: f64,
    #[serde(default)]
    tags: Vec<String>,
    height_m: Option<f64>,
    /// When promoting a peak, keep this slug so `access/<slug>.yaml` carries over.
    #[serde(default)]
    preferred_slug: Option<String>,
}

async fn add_site(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<AddSiteBody>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, String)> {
    validate_coords(body.lat, body.lon).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
    let path = state.preset_path();
    let mut existing = place_slugs(&path).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    // Allow reusing a peak slug when promoting (peak row may remain; access is shared by slug).
    if let Some(pref) = body.preferred_slug.as_deref() {
        existing.remove(pref);
    }
    let site_slug = if let Some(pref) = body.preferred_slug.as_deref() {
        unique_place_slug(&existing, pref, &body.name)
    } else {
        unique_site_slug(&existing, &body.name)
    };
    let entry = SiteEntry {
        name: body.name,
        loc: [body.lat, body.lon],
        tags: body.tags,
        height_m: body.height_m,
        description: None,
        node: None,
    };
    insert_preset_site(&path, &site_slug, &entry)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let preset = load_preset(&path).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let site = preset
        .sites
        .get(&site_slug)
        .ok_or((StatusCode::INTERNAL_SERVER_ERROR, "site missing after insert".to_string()))?;
    Ok((
        StatusCode::CREATED,
        Json(json!({ "site": site_api_row(&site_slug, site) })),
    ))
}

async fn sites_prefetch(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let (lat, lon) = parse_lat_lon_params(&params).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
    let exclude_site = params
        .get("exclude_site")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty());
    let path = state.preset_path();
    if !path.is_file() {
        return Err((StatusCode::NOT_FOUND, "project not found".to_string()));
    }
    let payload = load_site_placement_prefetch(
        state.session.clone(),
        &path,
        lat,
        lon,
        exclude_site,
    )
    .await
    .map_err(|e| match e {
        SitePrefetchError(msg) => (StatusCode::INTERNAL_SERVER_ERROR, msg),
    })?;
    let mut obj = payload
        .as_object()
        .cloned()
        .unwrap_or_default();
    obj.insert("project".into(), json!(state.slug));
    Ok(Json(Value::Object(obj)))
}

#[derive(Deserialize)]
struct PatchSiteBody {
    name: Option<String>,
    lat: Option<f64>,
    lon: Option<f64>,
    tags: Option<Vec<String>>,
    height_m: Option<f64>,
}

async fn patch_site(State(state): State<AppState>,
    
    Path((_slug, site_slug)): Path<(String, String)>,
    Json(body): Json<PatchSiteBody>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;
    if !preset.sites.contains_key(&site_slug) {
        return Err((StatusCode::NOT_FOUND, "site not found".to_string()));
    }
    if let (Some(lat), Some(lon)) = (body.lat, body.lon) {
        validate_coords(lat, lon).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
    }
    patch_preset_site(
        &path,
        &site_slug,
        body.name.as_deref(),
        match (body.lat, body.lon) {
            (Some(lat), Some(lon)) => Some([lat, lon]),
            _ => None,
        },
        body.tags.as_deref(),
        body.height_m.map(Some),
    )
    .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let preset = load_preset(&path).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let site = preset
        .sites
        .get(&site_slug)
        .ok_or((StatusCode::NOT_FOUND, "site not found".to_string()))?;
    Ok(Json(json!({ "site": site_api_row(&site_slug, site) })))
}

async fn delete_site(State(state): State<AppState>,
    
    Path((_slug, site_slug)): Path<(String, String)>,
) -> Result<StatusCode, (StatusCode, String)> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;
    if !preset.sites.contains_key(&site_slug) {
        return Err((StatusCode::NOT_FOUND, "site not found".to_string()));
    }
    if preset.sites.len() <= 1 {
        return Err((StatusCode::BAD_REQUEST, "cannot delete last site".to_string()));
    }
    remove_preset_site(&path, &site_slug).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
struct BulkTagsBody {
    slugs: Vec<String>,
    #[serde(default)]
    add_tags: Vec<String>,
    #[serde(default)]
    remove_tags: Vec<String>,
}

async fn bulk_tags(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<BulkTagsBody>,
) -> Result<Json<Value>, (StatusCode, String)> {
    if body.slugs.is_empty() {
        return Err((StatusCode::BAD_REQUEST, "slugs must be a non-empty list".to_string()));
    }
    if body.add_tags.is_empty() && body.remove_tags.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            "add_tags or remove_tags required".to_string(),
        ));
    }
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;
    let targets: Vec<String> = body
        .slugs
        .iter()
        .filter(|s| preset.sites.contains_key(*s))
        .cloned()
        .collect();
    patch_preset_sites_tags(&path, &targets, &body.add_tags, &body.remove_tags)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let updated = load_preset(&path).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let site_rows: Vec<_> = targets
        .iter()
        .filter_map(|s| updated.sites.get(s).map(|site| site_api_row(s, site)))
        .collect();
    Ok(Json(json!({
        "slug": state.slug.clone(),
        "sites": site_rows,
        "updated": site_rows.len(),
    })))
}

async fn import_preview(
    Path(_slug): Path<String>,
    body: axum::body::Bytes,
) -> Result<Json<Value>, (StatusCode, String)> {
    let points = if body.starts_with(b"PK") {
        parse_kmz_point_placemarks(&body).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?.0
    } else {
        parse_kml_point_placemarks(&body).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?.0
    };
    Ok(Json(json!({
        "points": points.iter().map(|p| json!({
            "name": p.name,
            "lat": p.lat,
            "lon": p.lon,
        })).collect::<Vec<_>>(),
        "skipped": [],
    })))
}

#[derive(Deserialize)]
struct ImportSitesBody {
    points: Vec<ImportPoint>,
    tags: Vec<String>,
}

#[derive(Deserialize)]
struct ImportPoint {
    name: String,
    lat: f64,
    lon: f64,
}

async fn import_sites(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<ImportSitesBody>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let path = state.preset_path();
    let mut slugs = Vec::new();
    for pt in body.points {
        let raw = load_preset_raw(&path).map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;
        let map = raw.as_mapping().ok_or((StatusCode::INTERNAL_SERVER_ERROR, "invalid preset".to_string()))?;
        let existing = preset_site_slugs(map);
        let site_slug = unique_site_slug(&existing, &pt.name);
        insert_preset_site(
            &path,
            &site_slug,
            &SiteEntry {
                name: pt.name,
                loc: [pt.lat, pt.lon],
                tags: body.tags.clone(),
                height_m: None,
                description: None,
                node: None,
            },
        )
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
        slugs.push(site_slug);
    }
    Ok(Json(json!({ "slugs": slugs })))
}

async fn viewshed_prefetch_meta(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, StatusCode> {
    let (lat, lon) = parse_lat_lon_params(&params).map_err(sim_status_from_err)?;
    let sim = parse_viewshed_sim_params(&params).map_err(sim_status_from_err)?;
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| StatusCode::NOT_FOUND)?;
    let overlay = coords_viewshed_overlay_if_ready(&state.slug, &path, &preset, lat, lon, Some(&sim))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .ok_or(StatusCode::NOT_FOUND)?;
    Ok(Json(overlay))
}

async fn viewshed_prefetch_png(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Response, StatusCode> {
    let (lat, lon) = parse_lat_lon_params(&params).map_err(sim_status_from_err)?;
    let sim = parse_viewshed_sim_params(&params).map_err(sim_status_from_err)?;
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| StatusCode::NOT_FOUND)?;
    let png = read_coords_viewshed_png_if_ready(&path, &preset, lat, lon, Some(&sim))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .ok_or(StatusCode::NOT_FOUND)?;
    serve_file_png(&png, true)
}

async fn viewshed_prefetch_warm(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    let (lat, lon) = parse_lat_lon_params(&params).map_err(|msg| {
        (
            sim_status_from_err(msg.clone()),
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        )
    })?;
    let sim = parse_viewshed_sim_params(&params).map_err(|msg| {
        (
            sim_status_from_err(msg.clone()),
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        )
    })?;
    let path = state.preset_path();
    match state.warm.warm_coords_viewshed(&state.slug, path, lat, lon, sim) {
        Ok(payload) => {
            let status = if payload.get("status").and_then(|v| v.as_str()) == Some("ready") {
                StatusCode::OK
            } else {
                StatusCode::ACCEPTED
            };
            Ok((status, Json(payload)))
        }
        Err(msg) => Err((
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        )),
    }
}

async fn viewshed_png(
    State(state): State<AppState>,
    Path((_slug, site_slug)): Path<(String, String)>,
) -> Result<Response, StatusCode> {
    let path = state.preset_path();
    let project_slug = state.slug.clone();
    let png = ensure_viewshed_png(state.session.clone(), &path, &site_slug, state.verbose)
        .await
        .map_err(|e| {
            tracing::error!("viewshed {project_slug}/{site_slug}: {e:#}");
            StatusCode::INTERNAL_SERVER_ERROR
        })?;
    serve_file_png(&png, false)
}

async fn viewshed_cache_png(State(state): State<AppState>,
    
    Path((_slug, digest)): Path<(String, String)>,
) -> Result<Response, StatusCode> {
    let png = peaky_preset::resolved_viewshed_root(state.preset_path()).join(&digest).join("splat.png");
    if !png.is_file() {
        return Err(StatusCode::NOT_FOUND);
    }
    serve_file_png(&png, true)
}

fn serve_file_png(path: &std::path::Path, immutable: bool) -> Result<Response, StatusCode> {
    let bytes = std::fs::read(path).map_err(|_| StatusCode::NOT_FOUND)?;
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "image/png");
    if immutable {
        builder = builder.header(header::CACHE_CONTROL, "public, max-age=31536000, immutable");
    }
    builder
        .body(Body::from(bytes))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn viewshed_meta(State(state): State<AppState>,
    
    Path((_slug, site_slug)): Path<(String, String)>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| StatusCode::NOT_FOUND)?;
    let site = preset.sites.get(&site_slug).ok_or(StatusCode::NOT_FOUND)?;
    let sim = parse_viewshed_sim_params(&params).map_err(sim_status_from_err)?;
    if let Ok(Some(overlay)) =
        site_viewshed_overlay_if_ready(&state.slug, &path, &site_slug, site, &preset, Some(&sim))
    {
        return Ok(Json(overlay));
    }
    let target_raster = crate::viewshed_sim::effective_target_raster_for_preset(&preset, Some(&sim));
    let digest = crate::viewshed::viewshed_digest_for_raster(&preset, site, target_raster)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(json!({
        "slug": site_slug,
        "digest": digest,
        "url": viewshed_cache_png_api_path(&state.slug, &digest),
        "ready": false,
        "raster_target": target_raster,
    })))
}

async fn viewshed_index(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| StatusCode::NOT_FOUND)?;
    build_viewshed_index(&state.slug, &path, &preset)
        .map(Json)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn links_mesh(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| StatusCode::NOT_FOUND)?;
    let payload = load_project_site_links(&path, &preset).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    if payload.get("status").and_then(|v| v.as_str()) != Some("ready") {
        state.warm.start_links_warm(&state.slug, path.clone());
    }
    let mut out = payload.as_object().cloned().unwrap_or_default();
    out.insert("project".into(), json!(state.slug));
    Ok(Json(Value::Object(out)))
}

async fn links_warm(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
) -> Json<Value> {
    let path = state.preset_path();
    Json(state.warm.start_links_warm(&state.slug, path))
}

async fn site_links(
    State(state): State<AppState>,
    Path((_slug, site_slug)): Path<(String, String)>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    let preset = load_preset(&path).map_err(|_| {
        (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "project not found" })),
        )
    })?;
    let preset_path_buf = path.clone();
    let site_slug_owned = site_slug.clone();
    let session = state.session.clone();
    let result = tokio::task::spawn_blocking(move || {
        load_single_site_links(session, &preset_path_buf, &preset, &site_slug_owned)
    })
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": "link task failed" })),
        )
    })?
    .map_err(|e| {
        let status = if e.to_string().contains("unknown site") {
            StatusCode::NOT_FOUND
        } else {
            StatusCode::INTERNAL_SERVER_ERROR
        };
        (status, Json(json!({ "error": e.to_string() })))
    })?;
    let mut out = result.as_object().cloned().unwrap_or_default();
    out.insert("project".into(), json!(state.slug));
    Ok(Json(Value::Object(out)))
}

async fn warm_priorities(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(body): Json<WarmPrioritiesBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "project not found" })),
        ));
    }
    let result = state
        .warm
        .bump_priorities(&state.slug, path, &body.slugs, body.priority)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })?;
    let mut out = result.as_object().cloned().unwrap_or_default();
    out.insert("project".into(), json!(state.slug));
    Ok(Json(Value::Object(out)))
}

async fn warm_status(State(state): State<AppState>, Path(_slug): Path<String>) -> Json<Value> {
    Json(state.warm.warm_status(&state.slug, state.preset_path()))
}

#[derive(Deserialize)]
struct WarmPrioritiesBody {
    slugs: Vec<String>,
    priority: i32,
}

async fn project_events(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
) -> impl IntoResponse {
    let mut rx = state.events.subscribe(&state.slug);
    let stream = async_stream::stream! {
        yield Ok::<_, std::convert::Infallible>(
            axum::response::sse::Event::default().comment("connected"),
        );
        yield Ok(axum::response::sse::Event::default().event("hello").data(
            serde_json::json!({ "project": state.slug.clone() }).to_string(),
        ));
        let mut interval = tokio::time::interval(sse_keepalive_interval());
        loop {
            tokio::select! {
                msg = rx.recv() => {
                    match msg {
                        Ok(ev) => {
                            yield Ok(axum::response::sse::Event::default()
                                .event(&ev.name)
                                .data(ev.data.to_string()));
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                        Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                    }
                }
                _ = interval.tick() => {
                    yield Ok(axum::response::sse::Event::default().comment("keepalive"));
                }
            }
        }
    };
    axum::response::Sse::new(stream).keep_alive(
        axum::response::sse::KeepAlive::new().interval(Duration::from_secs(15)),
    )
}

async fn peaks_list(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    tokio::task::spawn_blocking(move || list_peaks_payload(&path))
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn peak_hike(
    State(state): State<AppState>,
    Path((_slug, peak_slug)): Path<(String, String)>,
) -> Result<Json<Value>, StatusCode> {
    peak_hike_payload(&state.preset_path(), &state.session, &peak_slug)
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn peak_jeep(
    State(state): State<AppState>,
    Path((_slug, peak_slug)): Path<(String, String)>,
) -> Result<Json<Value>, StatusCode> {
    peak_jeep_payload(&state.preset_path(), &state.session, &peak_slug)
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn place_access(
    State(state): State<AppState>,
    Path((_slug, place_slug)): Path<(String, String)>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, StatusCode> {
    let warm = params.get("warm").map(|v| v == "1" || v == "true").unwrap_or(false);
    let lat = params.get("lat").and_then(|s| s.parse().ok());
    let lon = params.get("lon").and_then(|s| s.parse().ok());
    place_access_payload(
        &state.preset_path(),
        &state.session,
        &place_slug,
        warm,
        lat,
        lon,
    )
    .map(Json)
    .map_err(|_| StatusCode::NOT_FOUND)
}

async fn place_access_warm(
    State(state): State<AppState>,
    Path((_slug, place_slug)): Path<(String, String)>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let lat = params
        .get("lat")
        .and_then(|s| s.parse().ok())
        .ok_or((StatusCode::BAD_REQUEST, "lat required".into()))?;
    let lon = params
        .get("lon")
        .and_then(|s| s.parse().ok())
        .ok_or((StatusCode::BAD_REQUEST, "lon required".into()))?;
    place_access_payload(
        &state.preset_path(),
        &state.session,
        &place_slug,
        true,
        Some(lat),
        Some(lon),
    )
    .map(Json)
    .map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))
}

async fn land_list(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    list_land_payload(&path)
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn land_data_gdbs(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_data_gdbs_payload(&state.preset_path()).map(Json).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": e.to_string() })),
        )
    })
}

#[derive(Deserialize)]
struct LandPathBody {
    path: String,
}

async fn land_import_preview(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(body): Json<LandPathBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_import_preview_payload(&state.preset_path(), &body.path)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_import(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(body): Json<LandImportBody>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    land_import_source(&state.preset_path(), body)
        .map(|(source_id, source)| {
            (
                StatusCode::CREATED,
                Json(json!({ "source": source, "sourceId": source_id })),
            )
        })
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_source_patch(
    State(state): State<AppState>,
    Path((_slug, source_id)): Path<(String, String)>,
    Json(body): Json<LandPatchSourceBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_patch_source(&state.preset_path(), &source_id, body)
        .map(|source| Json(json!({ "source": source })))
        .map_err(|e| {
            let msg = e.to_string();
            let status = if msg.contains("unknown land source") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            };
            (status, Json(json!({ "error": msg })))
        })
}

async fn land_source_delete(
    State(state): State<AppState>,
    Path((_slug, source_id)): Path<(String, String)>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_delete_source(&state.preset_path(), &source_id)
        .map(|_| Json(json!({ "deleted": source_id })))
        .map_err(|e| {
            let msg = e.to_string();
            let status = if msg.contains("unknown land source") {
                StatusCode::NOT_FOUND
            } else {
                StatusCode::UNPROCESSABLE_ENTITY
            };
            (status, Json(json!({ "error": msg })))
        })
}

async fn land_sidebar_patch(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(sidebar): Json<LandSidebar>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_patch_sidebar(&state.preset_path(), sidebar)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_preview_geojson_get(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = params.get("path").map(String::as_str).unwrap_or("").trim();
    let layer = params.get("layer").map(String::as_str).unwrap_or("").trim();
    if path.is_empty() || layer.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": "path and layer query parameters are required" })),
        ));
    }
    land_preview_geojson_payload(&state.preset_path(), path, layer)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_preview_geojson_post(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(body): Json<LandPreviewGeoJsonBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    land_preview_geojson_filtered_payload(&state.preset_path(), body)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_import_preview_fields(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = params.get("path").map(String::as_str).unwrap_or("").trim();
    let layer = params.get("layer").map(String::as_str).unwrap_or("").trim();
    if path.is_empty() || layer.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": "path and layer query parameters are required" })),
        ));
    }
    land_preview_fields_payload(&state.preset_path(), path, layer)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_import_preview_values(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = params.get("path").map(String::as_str).unwrap_or("").trim();
    let layer = params.get("layer").map(String::as_str).unwrap_or("").trim();
    let field = params.get("field").map(String::as_str).unwrap_or("").trim();
    if path.is_empty() || layer.is_empty() || field.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": "path, layer, and field query parameters are required" })),
        ));
    }
    land_preview_values_payload(&state.preset_path(), path, layer, field)
        .map(Json)
        .map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": e.to_string() })),
            )
        })
}

async fn land_overlay_part_geojson(
    State(state): State<AppState>,
    Path((_slug, kind, source_id)): Path<(String, String, String)>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    let kind_owned = kind.clone();
    let source_owned = source_id.clone();
    let result = tokio::task::spawn_blocking(move || {
        read_overlay_part_geojson_bytes(&path, &kind_owned, &source_owned)
    })
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": "overlay task failed" })),
        )
    })?;
    let (bytes, digest) = result.map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": e.to_string() })),
        )
    })?;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/geo+json")
        .header("X-Peaky-Digest", digest)
        .body(Body::from(bytes))
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": "overlay response failed" })),
            )
        })
}

async fn land_overlay_geojson(
    State(state): State<AppState>,
    Path((_slug, kind)): Path<(String, String)>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    let kind_owned = kind.clone();
    let result = tokio::task::spawn_blocking(move || {
        read_overlay_geojson_bytes(&path, &kind_owned)
    })
    .await
    .map_err(|_| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": "overlay task failed" })),
        )
    })?;
    let (bytes, digest) = result.map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": e.to_string() })),
        )
    })?;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/geo+json")
        .header("X-Peaky-Digest", digest)
        .body(Body::from(bytes))
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": "response build failed" })),
            )
        })
}

async fn land_layer_geojson(State(state): State<AppState>,
    
    Path((_slug, source_id, layer_key)): Path<(String, String, String)>,
) -> Result<Response, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    let (bytes, digest) = read_layer_geojson_bytes(&path, &source_id, &layer_key).map_err(|e| {
        let status = if e.to_string().contains("unknown") {
            StatusCode::NOT_FOUND
        } else {
            StatusCode::UNPROCESSABLE_ENTITY
        };
        (status, Json(json!({ "error": e.to_string() })))
    })?;
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/geo+json")
        .header("X-Peaky-Digest", digest)
        .body(Body::from(bytes))
        .map_err(|_| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": "response build failed" })),
            )
        })
}

async fn project_simulation(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, StatusCode> {
    project_simulation_payload(&state.preset_path())
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn link_solver_scan(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    if !state.preset_path().is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "slug": state.slug.clone(), "error": "not found" })),
        ));
    }
    let request = parse_link_solver_request(state.preset_path(), &q).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    let gen = state.link_solver.enqueue(request).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({
            "project": state.slug.clone(),
            "status": "pending",
            "gen": gen,
        })),
    ))
}

async fn link_solver_progress(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Json<Value> {
    let slug_a = q.get("a").map(String::as_str).unwrap_or("").trim();
    let slug_b = q.get("b").map(String::as_str).unwrap_or("").trim();
    let key = if slug_a.is_empty() || slug_b.is_empty() {
        String::new()
    } else {
        link_solver_progress_key(slug_a, slug_b)
    };
    if key.is_empty() {
        return Json(json!({
            "project": state.slug,
            "status": "idle",
            "progress": Value::Null,
        }));
    }
    Json(state.link_solver.poll(&key, &state.slug))
}

async fn link_solver_like(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let slug_a = q.get("a").map(String::as_str).unwrap_or("").trim();
    let slug_b = q.get("b").map(String::as_str).unwrap_or("").trim();
    let route_id = q.get("route_id").map(String::as_str).unwrap_or("").trim();
    if slug_a.is_empty() || slug_b.is_empty() || route_id.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "error": "a, b, and route_id are required" })),
        ));
    }
    let key = link_solver_progress_key(slug_a, slug_b);
    let poll = state.link_solver.poll(&key, &state.slug);
    let routes = poll
        .get("result")
        .and_then(|r| r.get("routes"))
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let like = routes_like_from_json(&routes, route_id);
    Ok(Json(json!({
        "project": state.slug,
        "routes": like,
    })))
}

#[derive(Deserialize)]
struct LinkSolverAcceptBody {
    a: String,
    b: String,
    route_id: String,
    hops: Vec<LinkSolverAcceptHop>,
}

#[derive(Deserialize)]
struct LinkSolverAcceptHop {
    peak_slug: String,
    lat: f64,
    lon: f64,
    name: Option<String>,
}

async fn link_solver_accept(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Json(body): Json<LinkSolverAcceptBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "slug": state.slug.clone(), "error": "not found" })),
        ));
    }
    let body_value = json!({
        "a": body.a,
        "b": body.b,
        "route_id": body.route_id,
        "hops": body.hops.iter().map(|h| json!({
            "peak_slug": h.peak_slug,
            "lat": h.lat,
            "lon": h.lon,
            "name": h.name,
        })).collect::<Vec<_>>(),
    });
    let mut result = accept_link_solver_route(&path, state.session.as_ref(), body_value).map_err(
        |e| match e {
            LinkSolverError(msg) => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "slug": state.slug.clone(), "error": msg })),
            ),
        },
    )?;
    invalidate_project_site_links_cache(&path);
    let created_slugs: Vec<String> = result
        .get("created")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter_map(|v| v.get("slug").and_then(|s| s.as_str()).map(str::to_string))
                .collect()
        })
        .or_else(|| {
            result.get("created_slugs").and_then(|v| v.as_array()).map(|rows| {
                rows.iter()
                    .filter_map(|v| v.as_str().map(str::to_string))
                    .collect()
            })
        })
        .unwrap_or_default();
    if !created_slugs.is_empty() {
        let session = state.session.clone();
        let path_for_links = path.clone();
        let created_for_links = created_slugs.clone();
        if let Ok(Ok(links)) = tokio::task::spawn_blocking(move || {
            let preset = load_preset(&path_for_links)?;
            compute_links_for_slugs(session, &preset, &created_for_links)
        })
        .await
        {
            if let Some(obj) = result.as_object_mut() {
                obj.insert(
                    "links".to_string(),
                    links.get("links").cloned().unwrap_or(json!([])),
                );
                obj.insert(
                    "geojson".to_string(),
                    links.get("geojson").cloned().unwrap_or_else(|| {
                        json!({ "type": "FeatureCollection", "features": [] })
                    }),
                );
            }
        }
    }
    state.warm.start_links_warm(&state.slug, path);
    if let Some(obj) = result.as_object_mut() {
        obj.insert("slug".to_string(), json!(state.slug));
        obj.insert("project".to_string(), json!(state.slug));
    }
    Ok(Json(result))
}

async fn alternates_scan(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    if !state.preset_path().is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "slug": state.slug.clone(), "error": "not found" })),
        ));
    }
    let request =
        parse_alternates_request(state.preset_path(), &q).map_err(|e| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
            )
        })?;
    let gen = state.alternates.enqueue(request).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({
            "project": state.slug.clone(),
            "status": "pending",
            "gen": gen,
        })),
    ))
}

async fn link_pair_detail(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    if !state.preset_path().is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "slug": state.slug.clone(), "error": "not found" })),
        ));
    }
    let slug_a = q.get("a").map(String::as_str).unwrap_or("").trim();
    let slug_b = q.get("b").map(String::as_str).unwrap_or("").trim();
    if slug_a.is_empty() || slug_b.is_empty() {
        return Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": "a and b query parameters are required" })),
        ));
    }
    let preset = load_preset(&state.preset_path()).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.to_string() })),
        )
    })?;
    let detail = site_pair_link_detail(&state.session, &preset, slug_a, slug_b).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    Ok(Json(detail))
}

async fn fortify_scan(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    if !state.preset_path().is_file() {
        return Err((
            StatusCode::NOT_FOUND,
            Json(json!({ "slug": state.slug.clone(), "error": "not found" })),
        ));
    }
    let request = parse_fortify_request(state.preset_path(), &q).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    let gen = state.fortify.enqueue(request).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({
            "project": state.slug.clone(),
            "status": "pending",
            "gen": gen,
        })),
    ))
}

async fn fortify_progress(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Json<Value> {
    let slug_a = q.get("a").map(String::as_str).unwrap_or("").trim();
    let slug_b = q.get("b").map(String::as_str).unwrap_or("").trim();
    let key = if slug_a.is_empty() || slug_b.is_empty() {
        String::new()
    } else {
        crate::fortify::fortify_progress_key(slug_a, slug_b)
    };
    if key.is_empty() {
        return Json(json!({
            "project": state.slug,
            "status": "idle",
            "progress": Value::Null,
        }));
    }
    Json(state.fortify.poll(&key, &state.slug))
}

async fn alternates_progress(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Json<Value> {
    let site = q.get("site").map(String::as_str).unwrap_or("");
    let anchor_slugs = q
        .get("anchors")
        .map(|raw| {
            raw.split(',')
                .map(|s| s.trim())
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect::<Vec<_>>()
        })
        .filter(|slugs| !slugs.is_empty());
    let key = if site.is_empty() {
        String::new()
    } else {
        crate::alternates::alternates_progress_key(site, anchor_slugs.as_deref())
    };
    if key.is_empty() {
        return Json(json!({
            "project": state.slug,
            "status": "idle",
            "progress": Value::Null,
        }));
    }
    Json(state.alternates.poll(&key, &state.slug))
}

async fn home_modems(State(state): State<AppState>) -> Json<Value> {
    let catalog = peaky_preset::load_modem_catalog(&state.preset_path()).unwrap_or_default();
    let presets: HashMap<String, Value> = catalog
        .iter()
        .map(|(k, v)| {
            (
                k.clone(),
                serde_json::to_value(serde_yaml::Value::Mapping(v.clone())).unwrap_or(json!({})),
            )
        })
        .collect();
    Json(json!({ "presets": presets }))
}

async fn home_environments(State(state): State<AppState>) -> Json<Value> {
    let catalog = peaky_preset::load_environment_catalog(&state.preset_path()).unwrap_or_default();
    let presets: HashMap<String, Value> = catalog
        .iter()
        .map(|(k, v)| {
            (
                k.clone(),
                serde_json::to_value(serde_yaml::Value::Mapping(v.clone())).unwrap_or(json!({})),
            )
        })
        .collect();
    Json(json!({ "presets": presets }))
}

async fn home_simulation(State(state): State<AppState>) -> Result<Json<Value>, StatusCode> {
    project_simulation_payload(&state.preset_path())
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
}

async fn dem_terrarium_tile(
    State(state): State<AppState>,
    Path((z, x, y)): Path<(u32, u32, u32)>,
) -> Result<Response, StatusCode> {
    let _permit = state
        .dem_tile_render
        .acquire()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let session = state.session.clone();
    let png = tokio::task::spawn_blocking(move || session.skadi_terrarium_tile_png(z, x, y))
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .map_err(dem_tile_status)?;
    Ok(png_response(png))
}

async fn dem_hillshade_tile(
    State(state): State<AppState>,
    Path((z, x, y)): Path<(u32, u32, u32)>,
) -> Result<Response, StatusCode> {
    let _permit = state
        .dem_tile_render
        .acquire()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let session = state.session.clone();
    let png = tokio::task::spawn_blocking(move || session.skadi_hillshade_tile_png(z, x, y))
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .map_err(dem_tile_status)?;
    Ok(png_response(png))
}

fn dem_tile_status(err: anyhow::Error) -> StatusCode {
    if err.to_string().contains("waiting on") {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::NOT_FOUND
    }
}

fn png_response(bytes: Vec<u8>) -> Response {
    Response::builder()
        .header(header::CONTENT_TYPE, "image/png")
        .header(header::CACHE_CONTROL, "public, max-age=86400")
        .body(Body::from(bytes))
        .unwrap()
}
