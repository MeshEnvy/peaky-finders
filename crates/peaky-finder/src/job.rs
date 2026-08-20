//! CLI job struct for `peaky find path`.

use std::path::{Path, PathBuf};

use anyhow::{bail, Result};
use peaky_preset::{normalize_site_tags, resolve_preset_path};

#[derive(Debug, Clone)]
pub struct FindPathJob {
    pub project: String,
    pub preset_path: PathBuf,
    pub route_path: PathBuf,
    pub name_prefix: String,
    pub new_tags: Vec<String>,
    pub allow_tags: Vec<String>,
    pub dry_run: bool,
    pub quiet: bool,
    pub simplify_m: f64,
}

impl FindPathJob {
    pub fn from_cli(
        project: &str,
        route: &Path,
        name_prefix: &str,
        new_tags: &[String],
        allow_tags: &[String],
        dry_run: bool,
        quiet: bool,
        simplify_m: f64,
    ) -> Result<Self> {
        let project = project.trim();
        if project.is_empty() {
            bail!("--project is required");
        }
        let name_prefix = name_prefix.trim();
        if name_prefix.is_empty() {
            bail!("--name-prefix is required");
        }
        if !route.is_file() {
            bail!("route file not found: {}", route.display());
        }

        let new_tags = normalize_site_tags(Some(&serde_yaml::Value::Sequence(
            new_tags
                .iter()
                .map(|t| serde_yaml::Value::from(t.clone()))
                .collect(),
        )))
        .map_err(|e| anyhow::anyhow!("invalid --tag: {e}"))?;

        let allow_tags = if allow_tags.is_empty() {
            vec!["installed".to_string()]
        } else {
            normalize_site_tags(Some(&serde_yaml::Value::Sequence(
                allow_tags
                    .iter()
                    .map(|t| serde_yaml::Value::from(t.clone()))
                    .collect(),
            )))
            .map_err(|e| anyhow::anyhow!("invalid --allow-tag: {e}"))?
        };

        Ok(Self {
            project: project.to_string(),
            preset_path: resolve_preset_path(project),
            route_path: route.to_path_buf(),
            name_prefix: name_prefix.to_string(),
            new_tags,
            allow_tags,
            dry_run,
            quiet,
            simplify_m: simplify_m.max(0.0),
        })
    }
}
