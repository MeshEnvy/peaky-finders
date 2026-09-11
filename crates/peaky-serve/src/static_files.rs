//! Static assets and favicon routes.
//!
//! Debug builds serve from `assets/static/` on disk when present (override with
//! `PEAKY_STATIC_DIR`). Release builds always use compile-time `rust-embed`.

use std::path::{Path, PathBuf};

use axum::{
    body::Body,
    http::{header, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};
use tokio::fs;

use crate::embed::StaticAssets;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/static/{*path}", get(serve_static))
        .route("/favicon.ico", get(|| async { serve_asset("favicon.ico").await }))
        .route("/favicon.svg", get(|| async { serve_asset("favicon.svg").await }))
        .route("/favicon-96x96.png", get(|| async { serve_asset("favicon-96x96.png").await }))
        .route(
            "/apple-touch-icon.png",
            get(|| async { serve_asset("apple-touch-icon.png").await }),
        )
        .route(
            "/site.webmanifest",
            get(|| async { serve_asset("site.webmanifest").await }),
        )
}

async fn serve_static(uri: Uri) -> impl IntoResponse {
    let path = uri.path().trim_start_matches("/static/");
    serve_asset(path).await
}

/// Resolve disk static root when `PEAKY_STATIC_DIR` is set, or in debug builds
/// from the workspace `assets/static/` tree (no cargo rebuild on JS/CSS edits).
fn static_dir_on_disk() -> Option<PathBuf> {
    if let Ok(raw) = std::env::var("PEAKY_STATIC_DIR") {
        let dir = PathBuf::from(raw);
        if dir.is_dir() {
            return dir.canonicalize().ok();
        }
    }
    if !cfg!(debug_assertions) {
        return None;
    }
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let dir = manifest.join("../../assets/static");
    dir.canonicalize().ok().filter(|p| p.is_dir())
}

async fn read_disk_asset(path: &str) -> Option<Vec<u8>> {
    let root = static_dir_on_disk()?;
    let file = root.join(path);
    if !file.is_file() {
        return None;
    }
    fs::read(&file).await.ok()
}

async fn serve_asset(path: &str) -> Response {
    if let Some(bytes) = read_disk_asset(path).await {
        let mime = mime_guess(path);
        return Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, mime)
            .body(Body::from(bytes))
            .unwrap();
    }

    match StaticAssets::get(path) {
        Some(content) => {
            let mime = mime_guess(path);
            Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, mime)
                .body(Body::from(content.data.into_owned()))
                .unwrap()
        }
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

fn mime_guess(path: &str) -> &'static str {
    if path.ends_with(".js") {
        "application/javascript"
    } else if path.ends_with(".css") {
        "text/css"
    } else if path.ends_with(".png") {
        "image/png"
    } else if path.ends_with(".svg") {
        "image/svg+xml"
    } else if path.ends_with(".json") || path.ends_with(".webmanifest") {
        "application/json"
    } else {
        "application/octet-stream"
    }
}
