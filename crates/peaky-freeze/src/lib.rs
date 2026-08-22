//! Export a compact official project base (`config.yaml` + `project.geojson`).

mod config_distill;
mod geo_compact;

use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use peaky_geo::{
    aoi_land_digest, read_layer_geojson_bytes, warm_land_pipeline_caches_for_preset,
    LandPipelineWarmCache,
};
use peaky_preset::{
    load_preset, load_preset_raw, parse_preset_dict, resolved_preset_slug, write_preset_document,
    LandLayerEntry, Preset,
};
use rayon::prelude::*;
use serde::Serialize;
use serde_json::{json, Value};
use serde_yaml::{Mapping, Value as YamlValue};
use tracing::info;

pub use config_distill::distill_preset_mapping;

pub const FREEZE_LAYER_KEY_FIELD: &str = "_peaky_layer_key";
pub const FREEZE_GEOJSON_NAME: &str = "project.geojson";
pub const FREEZE_CONFIG_NAME: &str = "config.yaml";
pub const FREEZE_MANIFEST_NAME: &str = "freeze.json";
pub const FREEZE_SOURCE_ID: &str = "base";
pub const FREEZE_GEOJSON_LAYER_NAME: &str = "project";

#[derive(Debug, Clone)]
pub struct FreezeOptions {
    pub include_sites: bool,
    pub verbose: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct FreezeLayerSummary {
    pub source_id: String,
    pub layer_key: String,
    pub features: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct FreezeSummary {
    pub output_dir: PathBuf,
    pub source_slug: String,
    pub include_sites: bool,
    pub layers: Vec<FreezeLayerSummary>,
    pub feature_count: u64,
    pub config_bytes: u64,
    pub geojson_bytes: u64,
}

struct LayerBakeJob {
    source_id: String,
    layer: LandLayerEntry,
    layer_key: String,
}

pub fn default_freeze_output_dir(preset_path: &Path) -> PathBuf {
    let project_dir = preset_path
        .parent()
        .unwrap_or_else(|| Path::new("."));
    let slug = resolved_preset_slug(preset_path);
    let d = time::OffsetDateTime::now_utc().date();
    let date = format!(
        "{:04}-{:02}-{:02}",
        d.year(),
        u8::from(d.month()),
        d.day()
    );
    project_dir.join(format!("{slug}-base-{date}"))
}

pub fn resolve_preset_path(project: &Path) -> PathBuf {
    if project.is_file() {
        return project.to_path_buf();
    }
    let config = project.join("config.yaml");
    if config.is_file() {
        return config;
    }
    project.join("config.yaml")
}

fn ensure_empty_output_dir(output_dir: &Path) -> Result<()> {
    if output_dir.exists() {
        let mut entries = fs::read_dir(output_dir)
            .with_context(|| format!("read output dir {}", output_dir.display()))?;
        if entries.next().is_some() {
            bail!(
                "output directory is not empty: {} (remove it or choose another path with -o)",
                output_dir.display()
            );
        }
    } else {
        fs::create_dir_all(output_dir)
            .with_context(|| format!("create output dir {}", output_dir.display()))?;
    }
    Ok(())
}

fn collect_layer_jobs(preset: &Preset) -> Vec<LayerBakeJob> {
    let mut jobs = Vec::new();
    for (source_id, source) in &preset.land.sources {
        if !source.is_enabled() {
            continue;
        }
        for layer in &source.layers {
            let layer_key = layer.layer_key();
            jobs.push(LayerBakeJob {
                source_id: source_id.clone(),
                layer: layer.clone(),
                layer_key,
            });
        }
    }
    jobs
}

fn bake_layers(
    preset_path: &Path,
    jobs: &[LayerBakeJob],
    verbose: bool,
) -> Result<(Vec<Value>, Vec<FreezeLayerSummary>)> {
    if jobs.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }

    let baked: Result<Vec<(Vec<Value>, FreezeLayerSummary)>> = jobs
        .par_iter()
        .map(|job| {
            let (bytes, _digest) = read_layer_geojson_bytes(
                preset_path,
                &job.source_id,
                &job.layer_key,
            )
            .with_context(|| {
                format!(
                    "read layer {} / {}",
                    job.source_id, job.layer_key
                )
            })?;
            let value: Value = serde_json::from_slice(&bytes).context("parse layer GeoJSON")?;
            let had_label_field = job
                .layer
                .label_field
                .as_deref()
                .is_some_and(|s| !s.is_empty());
            let features = geo_compact::extract_and_compact_features(
                &value,
                &job.layer_key,
                had_label_field,
            )?;
            let count = features.len() as u64;
            if verbose {
                eprintln!(
                    "[peaky freeze] {} / {}: {count} feature(s)",
                    job.source_id, job.layer_key
                );
            }
            Ok((
                features,
                FreezeLayerSummary {
                    source_id: job.source_id.clone(),
                    layer_key: job.layer_key.clone(),
                    features: count,
                },
            ))
        })
        .collect();

    let baked = baked?;
    let mut all_features = Vec::new();
    let mut summaries = Vec::with_capacity(baked.len());
    for (mut feats, summary) in baked {
        if summary.features > 0 {
            all_features.append(&mut feats);
            summaries.push(summary);
        } else if verbose {
            eprintln!(
                "[peaky freeze] skip empty layer {} / {}",
                summary.source_id, summary.layer_key
            );
        }
    }
    Ok((all_features, summaries))
}

fn write_geojson(output_dir: &Path, features: &[Value]) -> Result<u64> {
    let fc = json!({
        "type": "FeatureCollection",
        "features": features,
    });
    let text = serde_json::to_string(&fc).context("serialize project.geojson")?;
    let bytes = text.len() as u64;
    let path = output_dir.join(FREEZE_GEOJSON_NAME);
    fs::write(&path, text).with_context(|| format!("write {}", path.display()))?;
    Ok(bytes)
}

fn compact_sites_in_mapping(doc: &mut Mapping) {
    let Some(YamlValue::Mapping(sites)) = doc.get_mut(&YamlValue::from("sites")) else {
        return;
    };
    for (_slug, entry) in sites.iter_mut() {
        let Some(site_map) = entry.as_mapping_mut() else {
            continue;
        };
        site_map.remove(&YamlValue::from("description"));
        if site_map
            .get(&YamlValue::from("height_m"))
            .is_some_and(|v| v.is_null())
        {
            site_map.remove(&YamlValue::from("height_m"));
        }
        if site_map
            .get(&YamlValue::from("tags"))
            .and_then(|v| v.as_sequence())
            .is_some_and(|s| s.is_empty())
        {
            site_map.remove(&YamlValue::from("tags"));
        }
    }
}

fn write_manifest(
    output_dir: &Path,
    summary: &FreezeSummary,
    created_at: &str,
) -> Result<()> {
    let manifest = json!({
        "peaky_version": env!("CARGO_PKG_VERSION"),
        "created_at": created_at,
        "source_slug": summary.source_slug,
        "include_sites": summary.include_sites,
        "layers": summary.layers,
        "feature_count": summary.feature_count,
        "config_bytes": summary.config_bytes,
        "geojson_bytes": summary.geojson_bytes,
    });
    let path = output_dir.join(FREEZE_MANIFEST_NAME);
    fs::write(&path, serde_json::to_vec_pretty(&manifest)?)
        .with_context(|| format!("write {}", path.display()))?;
    Ok(())
}

/// Write `config.yaml`, `project.geojson`, and `freeze.json` into `output_dir`.
pub fn build_freeze_dir(
    preset_path: &Path,
    output_dir: &Path,
    opts: FreezeOptions,
) -> Result<FreezeSummary> {
    preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    if !preset_path.is_file() {
        bail!("preset not found: {}", preset_path.display());
    }

    ensure_empty_output_dir(output_dir)?;

    if opts.verbose {
        eprintln!("[peaky freeze] warming land pipeline caches…");
    }
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let pipeline_warm = LandPipelineWarmCache::open(project_dir, &aoi_digest)
        .context("open land pipeline warm cache")?;
    warm_land_pipeline_caches_for_preset(preset_path, &pipeline_warm)
        .context("warm land pipeline caches")?;

    let preset = load_preset(preset_path).context("load preset")?;
    let jobs = collect_layer_jobs(&preset);
    if opts.verbose {
        eprintln!("[peaky freeze] baking {} layer(s)…", jobs.len());
    }

    let (features, layer_summaries) = bake_layers(preset_path, &jobs, opts.verbose)?;
    let feature_count = features.len() as u64;
    let geojson_bytes = write_geojson(output_dir, &features)?;

    let mut raw = load_preset_raw(preset_path)?.as_mapping().cloned().context(
        "preset root must be a mapping",
    )?;
    if !opts.include_sites {
        raw.remove(&YamlValue::from("sites"));
    } else {
        compact_sites_in_mapping(&mut raw);
    }
    distill_preset_mapping(&mut raw, &layer_summaries)?;

    let config_path = output_dir.join(FREEZE_CONFIG_NAME);
    write_preset_document(&config_path, &raw)
        .context("write config.yaml")?;
    parse_preset_dict(raw).context("validate frozen config.yaml")?;

    let config_bytes = fs::metadata(&config_path)?.len();

    let source_slug = resolved_preset_slug(preset_path);
    let now = time::OffsetDateTime::now_utc();
    let created_at = format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second()
    );

    let summary = FreezeSummary {
        output_dir: output_dir.to_path_buf(),
        source_slug,
        include_sites: opts.include_sites,
        layers: layer_summaries,
        feature_count,
        config_bytes,
        geojson_bytes,
    };

    write_manifest(output_dir, &summary, &created_at)?;

    info!(
        output = %output_dir.display(),
        features = feature_count,
        layers = summary.layers.len(),
        "freeze complete"
    );

    Ok(summary)
}

#[cfg(test)]
mod tests;
