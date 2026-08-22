//! HTTP API and page routes.

use std::collections::HashMap;
use std::time::Duration;

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{delete, get, patch, post},
    Json, Router,
};
use peaky_geo::{parse_kml_point_placemarks, parse_kmz_point_placemarks};
use peaky_preset::{
    insert_preset_site, load_preset, load_preset_raw, patch_preset_site, patch_preset_sites_tags,
    preset_site_slugs, remove_preset_site, unique_site_slug, validate_coords, SiteEntry,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::events::sse_keepalive_interval;
use crate::html::{project_error_html, project_html, site_api_row};
use crate::links::{load_project_site_links, load_single_site_links};
use crate::site_prefetch::{load_site_placement_prefetch, SitePrefetchError};
use crate::state::AppState;
use crate::land::{list_land_payload, read_layer_geojson_bytes};
use crate::simulation::project_simulation_payload;
use crate::viewshed::{
    coords_viewshed_overlay_if_ready, ensure_viewshed_png, read_coords_viewshed_png_if_ready,
};
use crate::seek::parse_seek_request;
use crate::seek_plan::{
    clear_seek_plan, convert_seek_plan_locs_to_sites, load_seek_plan_payload, patch_seek_plan,
    SeekPlanError,
};
use crate::viewshed_index::{build_viewshed_index, site_viewshed_overlay_if_ready, viewshed_cache_png_api_path};
use crate::viewshed_sim::{parse_lat_lon_params, parse_viewshed_sim_params, sim_status_from_err};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/", get(project_page))
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
        .route("/api/p/{slug}/links/warm", post(links_warm))
        .route("/api/p/{slug}/sites/{site_slug}/links", get(site_links))
        .route("/api/p/{slug}/warm/priorities", post(warm_priorities))
        .route("/api/p/{slug}/warm/status", get(warm_status))
        .route("/api/p/{slug}/events", get(project_events))
        .route("/api/p/{slug}/land", get(land_list))
        .route(
            "/api/p/{slug}/land/sources/{source_id}/layers/{layer_key}/geojson",
            get(land_layer_geojson),
        )
        .route("/api/p/{slug}/simulation", get(project_simulation))
        .route("/api/p/{slug}/seek/candidates", get(seek_candidates))
        .route("/api/p/{slug}/seek/scan-progress", get(seek_progress))
        .route(
            "/api/p/{slug}/seek/plan",
            get(get_seek_plan).patch(patch_seek_plan_handler).delete(clear_seek_plan_handler),
        )
        .route(
            "/api/p/{slug}/seek/plan/convert-to-sites",
            post(convert_seek_plan),
        )
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
}

async fn add_site(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<AddSiteBody>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, String)> {
    validate_coords(body.lat, body.lon).map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
    let path = state.preset_path();
    let raw = load_preset_raw(&path).map_err(|e| (StatusCode::NOT_FOUND, e.to_string()))?;
    let map = raw.as_mapping().ok_or((StatusCode::INTERNAL_SERVER_ERROR, "invalid preset".to_string()))?;
    let existing = preset_site_slugs(map);
    let site_slug = unique_site_slug(&existing, &body.name);
    let entry = SiteEntry {
        name: body.name,
        loc: [body.lat, body.lon],
        tags: body.tags,
        height_m: body.height_m,
        description: None,
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

async fn land_list(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, StatusCode> {
    let path = state.preset_path();
    list_land_payload(&path)
        .map(Json)
        .map_err(|_| StatusCode::NOT_FOUND)
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

async fn seek_candidates(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, Json<Value>)> {
    if !state.preset_path().is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "slug": state.slug.clone(), "error": "not found" }))));
    }
    let request = parse_seek_request(&state.slug, state.preset_path(), &q).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    let gen = state.seek.enqueue(request, state.verbose).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.0 })),
        )
    })?;
    Ok((
        StatusCode::ACCEPTED,
        Json(json!({ "project": state.slug.clone(), "status": "pending", "gen": gen })),
    ))
}

async fn seek_progress(State(state): State<AppState>, Path(_slug): Path<String>) -> Json<Value> {
    Json(state.seek.poll(&state.slug))
}

async fn get_seek_plan(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "slug": state.slug.clone(), "error": "not found" }))));
    }
    let plan = load_seek_plan_payload(&path).map_err(|e| {
        (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": e.to_string() })),
        )
    })?;
    Ok(Json(json!({ "project": state.slug.clone(), "plan": plan })))
}

async fn patch_seek_plan_handler(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<Value>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "slug": state.slug.clone(), "error": "not found" }))));
    }
    let plan = patch_seek_plan(&path, &body).map_err(|e| match e {
        SeekPlanError(msg) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        ),
    })?;
    Ok(Json(json!({ "project": state.slug.clone(), "plan": plan })))
}

async fn clear_seek_plan_handler(State(state): State<AppState>,
    Path(_slug): Path<String>) -> Result<StatusCode, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "slug": state.slug.clone(), "error": "not found" }))));
    }
    clear_seek_plan(&path).map_err(|e| match e {
        SeekPlanError(msg) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        ),
    })?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
struct ConvertSeekPlanBody {
    name_prefix: String,
    #[serde(default)]
    tags: Vec<String>,
}

async fn convert_seek_plan(State(state): State<AppState>,
    
    Path(_slug): Path<String>,
    Json(body): Json<ConvertSeekPlanBody>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let path = state.preset_path();
    if !path.is_file() {
        return Err((StatusCode::NOT_FOUND, Json(json!({ "slug": state.slug.clone(), "error": "not found" }))));
    }
    let result = convert_seek_plan_locs_to_sites(&path, &body.name_prefix, &body.tags).map_err(|e| match e {
        SeekPlanError(msg) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(json!({ "slug": state.slug.clone(), "error": msg })),
        ),
    })?;
    let mut payload = result;
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("slug".to_string(), json!(state.slug));
        obj.insert("project".to_string(), json!(state.slug));
    }
    Ok(Json(payload))
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
