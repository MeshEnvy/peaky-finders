//! Filesystem paths for presets, Skadi cache, and viewshed workspaces.

use std::env;
use std::path::{Path, PathBuf};

/// Workspace root (`peaky-finders-v5`).
pub fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("workspace root")
        .to_path_buf()
}

/// Runtime home directory (`PEAKY_HOME`, else `../../ops/peaky_home` when present, else workspace).
pub fn peaky_home() -> PathBuf {
    if let Ok(raw) = env::var("PEAKY_HOME") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return expand_user(trimmed);
        }
    }

    let workspace = workspace_root();
    let ops_home = workspace.join("../../ops/peaky_home");
    if ops_home.is_dir() {
        return canonicalize_lossy(&ops_home);
    }

    let local = workspace.join("peaky_home");
    if local.is_dir() {
        return canonicalize_lossy(&local);
    }

    workspace
}

/// Project presets root (`PEAKY_PROJECTS` or `<peaky_home>/projects`).
pub fn peaky_projects_dir() -> PathBuf {
    if let Ok(raw) = env::var("PEAKY_PROJECTS") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return expand_user(trimmed);
        }
    }
    peaky_home().join("projects")
}

/// Skadi HGT mirror for a project: ``<project>/.peaky/cache/skadi`` (``.hgt.gz`` tiles).
///
/// When ``SPLAT_CACHE`` is set, it overrides the project path (shared mirror or migration).
pub fn resolved_skadi_mirror_dir_for_project(project_dir: impl AsRef<Path>) -> PathBuf {
    if let Some(global) = splat_cache_from_env() {
        return global;
    }
    canonicalize_lossy(
        &project_dir
            .as_ref()
            .join(".peaky/cache/skadi"),
    )
}

/// Legacy global Skadi mirror when no project context (``SPLAT_CACHE`` or ``<peaky_home>/splat_cache``).
pub fn resolved_skadi_mirror_dir() -> PathBuf {
    if let Some(global) = splat_cache_from_env() {
        return global;
    }
    canonicalize_lossy(&peaky_home().join("splat_cache"))
}

fn splat_cache_from_env() -> Option<PathBuf> {
    env::var("SPLAT_CACHE")
        .ok()
        .map(|raw| raw.trim().to_string())
        .filter(|s| !s.is_empty())
        .map(|s| expand_user(&s))
}

/// Resolve a project argument to that project's directory.
///
/// Accepts a directory with `config.yaml`, a path to `config.yaml`, or a slug
/// under `$PEAKY_HOME/projects/<slug>/`.
pub fn resolve_project_dir(project: &str) -> PathBuf {
    let trimmed = project.trim();
    let path = expand_user(trimmed);
    if path.is_file() {
        return path
            .parent()
            .map(canonicalize_lossy)
            .unwrap_or_else(|| path.clone());
    }
    if path.join("config.yaml").is_file() {
        return canonicalize_lossy(&path);
    }
    if looks_like_filesystem_path(&path) {
        return canonicalize_lossy(&path);
    }
    canonicalize_lossy(&peaky_projects_dir().join(trimmed))
}

fn looks_like_filesystem_path(path: &Path) -> bool {
    path.is_absolute()
        || path.starts_with(".")
        || path.components().count() > 1
}

/// Resolve a project argument to `config.yaml`.
pub fn resolve_preset_path(project: &str) -> PathBuf {
    resolve_project_dir(project).join("config.yaml")
}

/// Per-preset serve runtime cache: `<preset-dir>/.peaky/cache`.
pub fn resolved_preset_cache_dir(preset_path: impl AsRef<Path>) -> PathBuf {
    let p = preset_path.as_ref();
    canonicalize_lossy(
        &p.parent()
            .unwrap_or_else(|| Path::new("."))
            .join(".peaky/cache"),
    )
}

/// Per-preset viewshed workspace root: `<preset-dir>/.peaky/cache/viewsheds`.
pub fn resolved_viewshed_root(preset_path: impl AsRef<Path>) -> PathBuf {
    resolved_preset_cache_dir(preset_path).join("viewsheds")
}

/// Stable preset id for document titles.
pub fn resolved_preset_slug(preset_path: impl AsRef<Path>) -> String {
    let path = preset_path.as_ref();
    if path.file_stem().and_then(|s| s.to_str()) == Some("config") {
        return path
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|s| s.to_str())
            .unwrap_or("preset")
            .to_string();
    }
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("preset")
        .to_string()
}

fn expand_user(raw: &str) -> PathBuf {
    if raw.starts_with('~') {
        if let Ok(home) = env::var("HOME") {
            return PathBuf::from(home).join(raw.trim_start_matches("~/").trim_start_matches('~'));
        }
    }
    PathBuf::from(raw)
}

fn canonicalize_lossy(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}
