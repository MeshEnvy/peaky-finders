//! Project discovery under `peaky_projects_dir`.

use std::fs;
use std::path::{Path, PathBuf};

use crate::paths::peaky_projects_dir;

/// Return sorted project slugs (dirs with `config.yaml`) under `projects_dir`.
pub fn discover_projects(projects_dir: Option<&Path>) -> Vec<String> {
    let root: PathBuf = projects_dir
        .map(|p| p.to_path_buf())
        .unwrap_or_else(peaky_projects_dir);

    let entries = match fs::read_dir(&root) {
        Ok(entries) => entries,
        Err(_) => return Vec::new(),
    };

    let mut slugs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() && path.join("config.yaml").is_file() {
            if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                slugs.push(name.to_string());
            }
        }
    }
    slugs.sort();
    slugs
}
