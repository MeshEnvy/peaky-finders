//! Static assets and favicon routes.

use axum::{
    body::Body,
    http::{header, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};

use crate::embed::StaticAssets;
use crate::state::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/static/{*path}", get(serve_static))
        .route("/favicon.ico", get(|| async { serve_embed("favicon.ico").await }))
        .route("/favicon.svg", get(|| async { serve_embed("favicon.svg").await }))
        .route("/favicon-96x96.png", get(|| async { serve_embed("favicon-96x96.png").await }))
        .route("/apple-touch-icon.png", get(|| async { serve_embed("apple-touch-icon.png").await }))
        .route("/site.webmanifest", get(|| async { serve_embed("site.webmanifest").await }))
}

async fn serve_static(uri: Uri) -> impl IntoResponse {
    let path = uri.path().trim_start_matches("/static/");
    serve_embed(path).await
}

async fn serve_embed(path: &str) -> Response {
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
