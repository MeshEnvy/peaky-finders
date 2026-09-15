//! Bootstrap a new project directory with MeshCore-oriented defaults.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

use crate::io::save_preset;
use crate::model::{
    CoverageProvider, Preset, SimulationConfig, SimulationMaxWorkers,
};
use crate::paths::resolve_project_dir;

fn yaml_mapping(pairs: &[(&str, Value)]) -> Mapping {
    let mut m = Mapping::new();
    for (k, v) in pairs {
        m.insert(Value::from(*k), v.clone());
    }
    m
}

/// Typical MeshCore US LoRa defaults for a fresh project.
pub fn default_meshcore_preset() -> Preset {
    let mut modem_presets = HashMap::new();
    modem_presets.insert(
        "meshcore-us".to_string(),
        yaml_mapping(&[
            ("frequency_mhz", Value::from(910.525)),
            ("bandwidth_khz", Value::from(62.5)),
            ("spreading_factor", Value::from(7)),
            ("coding_rate", Value::from(5)),
            ("implementation_margin_db", Value::from(3.0)),
            ("power_dbm", Value::from(22.0)),
            ("sensitivity_dbm", Value::from(-121.0)),
        ]),
    );

    let mut environment_presets = HashMap::new();
    environment_presets.insert(
        "meshcore-open".to_string(),
        yaml_mapping(&[
            ("climate", Value::from("continental_temperate")),
            ("polarization", Value::from("vertical")),
            ("clutter_height_m", Value::from(0.0)),
            ("fresnel_clearance_fraction", Value::from(0.6)),
            ("coverage_pessimism_db", Value::from(0.0)),
            ("situation_pct", Value::from(95.0)),
            ("time_pct", Value::from(95.0)),
            ("ground_dielectric_v_m", Value::from(15.0)),
            ("ground_conductivity_s_m", Value::from(0.005)),
            ("atmosphere_bending_n", Value::from(301.0)),
        ]),
    );

    let mut transmitter = HashMap::new();
    transmitter.insert("height_m".to_string(), Value::from(2.0));
    transmitter.insert("gain_dbi".to_string(), Value::from(3.0));
    transmitter.insert("loss_db".to_string(), Value::from(2.0));

    let mut receiver = HashMap::new();
    receiver.insert("height_m".to_string(), Value::from(2.0));
    receiver.insert("gain_dbi".to_string(), Value::from(3.0));
    receiver.insert("loss_db".to_string(), Value::from(2.0));

    Preset {
        modem_presets,
        environment_presets,
        simulation: SimulationConfig {
            provider: CoverageProvider::Splatter,
            radius_km: Value::from(50),
            viewshed_quality: 3,
            max_workers: SimulationMaxWorkers::default(),
            modem: Some(Value::String("meshcore-us".into())),
            environment: Some(Value::String("meshcore-open".into())),
            transmitter,
            receiver,
        },
        ..Preset::default()
    }
}

/// True when the project directory has no ``config.yaml`` yet.
pub fn project_needs_init(project_dir: &Path) -> bool {
    !project_dir.join("config.yaml").is_file()
}

/// Create ``config.yaml`` with MeshCore defaults when the project is empty.
///
/// Returns the resolved project directory. Does nothing when ``config.yaml`` already exists.
pub fn ensure_project_initialized(project: &Path) -> Result<PathBuf> {
    let project_dir = resolve_project_dir(&project.to_string_lossy());
    let config_path = project_dir.join("config.yaml");
    if config_path.is_file() {
        return Ok(project_dir);
    }
    std::fs::create_dir_all(&project_dir)
        .with_context(|| format!("create project directory {}", project_dir.display()))?;
    let preset = default_meshcore_preset();
    save_preset(&config_path, &preset).context("write default config.yaml")?;
    Ok(project_dir)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::load_preset;

    #[test]
    fn default_meshcore_preset_resolves_modem_and_environment() {
        let preset = default_meshcore_preset();
        assert!(preset.modem_presets.contains_key("meshcore-us"));
        assert!(preset.environment_presets.contains_key("meshcore-open"));
        assert_eq!(
            preset.simulation.modem.as_ref().and_then(|v| v.as_str()),
            Some("meshcore-us")
        );
        assert!(preset.sites.is_empty());
    }

    #[test]
    fn ensure_project_initialized_writes_config_for_empty_dir() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("fresh");
        let resolved = ensure_project_initialized(&project).unwrap();
        assert_eq!(resolved, project);
        let config_path = project.join("config.yaml");
        assert!(config_path.is_file());
        let preset = load_preset(&config_path).unwrap();
        assert!(preset.modem_presets.contains_key("meshcore-us"));
        assert!(preset.sites.is_empty());
    }

    #[test]
    fn ensure_project_initialized_is_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join("again");
        ensure_project_initialized(&project).unwrap();
        let first = std::fs::read_to_string(project.join("config.yaml")).unwrap();
        ensure_project_initialized(&project).unwrap();
        let second = std::fs::read_to_string(project.join("config.yaml")).unwrap();
        assert_eq!(first, second);
    }
}
