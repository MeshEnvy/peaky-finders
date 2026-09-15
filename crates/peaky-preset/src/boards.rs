//! Project ``boards.yaml`` catalog for per-board simulation params.

use std::collections::HashMap;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_yaml::Value;

use crate::nodes_board::{load_nodes_board_index, NodesBoardIndex};
use crate::viewshed_quality::preset_radius_km;
use crate::{Preset, PresetResult, PresetValidationError, SiteEntry};

fn err(msg: impl Into<String>) -> PresetValidationError {
    PresetValidationError::Message(msg.into())
}

fn normalize_board_key(raw: &str) -> String {
    raw.trim().to_ascii_lowercase()
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct BoardConfig {
    pub radius_km: Option<f64>,
}

#[derive(Debug, Clone, Default)]
pub struct BoardsCatalog {
    boards: HashMap<String, BoardConfig>,
}

impl BoardsCatalog {
    pub fn config_for_board(&self, board: &str) -> Option<&BoardConfig> {
        self.boards.get(&normalize_board_key(board))
    }

    pub fn is_empty(&self) -> bool {
        self.boards.is_empty()
    }
}

/// Joins ``nodes.yaml`` fleet units to ``boards.yaml`` params for viewshed radius.
#[derive(Debug, Clone, Default)]
pub struct BoardViewshedResolver {
    pub node_boards: NodesBoardIndex,
    pub board_configs: BoardsCatalog,
}

impl BoardViewshedResolver {
    pub fn load(project_dir: &Path) -> Result<Self, String> {
        let board_configs = load_boards_catalog(project_dir)?;
        Ok(Self {
            node_boards: load_nodes_board_index(project_dir),
            board_configs,
        })
    }
}

pub fn load_boards_catalog(project_dir: &Path) -> Result<BoardsCatalog, String> {
    let path = project_dir.join("boards.yaml");
    if !path.is_file() {
        return Ok(BoardsCatalog::default());
    }
    let catalog = load_boards_catalog_from_path(&path)?;
    validate_boards_catalog(&catalog).map_err(|e| e.to_string())?;
    Ok(catalog)
}

pub fn load_boards_catalog_from_path(path: &Path) -> Result<BoardsCatalog, String> {
    if !path.is_file() {
        return Ok(BoardsCatalog::default());
    }
    let raw = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let doc: Value = serde_yaml::from_str(&raw).map_err(|e| e.to_string())?;
    let boards_val = doc
        .get("boards")
        .ok_or_else(|| "boards.yaml: boards must be a mapping".to_string())?;
    let boards_map = boards_val
        .as_mapping()
        .ok_or_else(|| "boards.yaml: boards must be a mapping".to_string())?;

    let mut boards = HashMap::new();
    for (board_key, board_val) in boards_map {
        let Some(board_key) = board_key.as_str() else {
            continue;
        };
        let cfg: BoardConfig = if board_val.is_null() {
            BoardConfig::default()
        } else {
            serde_yaml::from_value(board_val.clone()).map_err(|e| {
                format!("boards.yaml boards.{board_key}: {e}")
            })?
        };
        boards.insert(normalize_board_key(board_key), cfg);
    }
    Ok(BoardsCatalog { boards })
}

pub fn board_viewshed_radius_km(catalog: &BoardsCatalog, board: &str) -> Option<f64> {
    catalog
        .config_for_board(board)
        .and_then(|cfg| cfg.radius_km)
}

/// Viewshed radius for a staked site: board override when ``site.node`` maps in ``nodes.yaml``.
pub fn site_viewshed_radius_km(
    preset: &Preset,
    site: &SiteEntry,
    resolver: Option<&BoardViewshedResolver>,
) -> f64 {
    let default = preset_radius_km(preset);
    let Some(resolver) = resolver else {
        return default;
    };
    let Some(node_key) = site
        .node
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_ascii_lowercase())
    else {
        return default;
    };
    let Some(board) = resolver.node_boards.board_for_node(&node_key) else {
        return default;
    };
    board_viewshed_radius_km(&resolver.board_configs, board).unwrap_or(default)
}

pub fn validate_board_radius_km(radius_km: f64, board: &str) -> PresetResult<()> {
    if !(1.0..=100.0).contains(&radius_km) {
        return Err(err(format!(
            "boards.{board}.radius_km must be between 1 and 100"
        )));
    }
    Ok(())
}

pub fn validate_boards_catalog(catalog: &BoardsCatalog) -> PresetResult<()> {
    for (board, cfg) in &catalog.boards {
        if let Some(radius_km) = cfg.radius_km {
            validate_board_radius_km(radius_km, board)?;
        }
    }
    Ok(())
}

pub fn reject_legacy_simulation_boards(raw: &serde_yaml::Mapping) -> PresetResult<()> {
    if let Some(sim) = raw.get(&Value::from("simulation")) {
        if let Value::Mapping(sim_map) = sim {
            if sim_map.contains_key(Value::from("boards")) {
                return Err(err(
                    "simulation.boards is removed; move board params to boards.yaml",
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::SimulationConfig;
    use serde_yaml::Value;

    fn preset_default_radius() -> Preset {
        Preset {
            simulation: SimulationConfig {
                radius_km: Value::from(72.0),
                ..SimulationConfig::default()
            },
            ..Preset::default()
        }
    }

    fn boards_yaml(dir: &tempfile::TempDir, yaml: &str) {
        std::fs::write(dir.path().join("boards.yaml"), yaml).unwrap();
    }

    fn nodes_yaml(dir: &tempfile::TempDir, yaml: &str) {
        std::fs::write(dir.path().join("nodes.yaml"), yaml).unwrap();
    }

    fn resolver_for(dir: &tempfile::TempDir) -> BoardViewshedResolver {
        BoardViewshedResolver::load(dir.path()).unwrap()
    }

    #[test]
    fn t096_site_gets_board_radius() {
        let dir = tempfile::tempdir().unwrap();
        boards_yaml(
            &dir,
            "boards:\n  heltec-t096:\n    radius_km: 50\n",
        );
        nodes_yaml(
            &dir,
            "nodes:\n  me0041:\n    board: heltec-t096\n",
        );
        let preset = preset_default_radius();
        let site = SiteEntry {
            name: "Test".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0041".into()),
        };
        let resolver = resolver_for(&dir);
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&resolver)),
            50.0
        );
    }

    #[test]
    fn rak_site_keeps_project_radius() {
        let dir = tempfile::tempdir().unwrap();
        boards_yaml(
            &dir,
            "boards:\n  heltec-t096:\n    radius_km: 50\n",
        );
        nodes_yaml(
            &dir,
            "nodes:\n  me0041:\n    board: heltec-t096\n  me0001:\n    board: rak4631\n",
        );
        let preset = preset_default_radius();
        let site = SiteEntry {
            name: "RAK".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0001".into()),
        };
        let resolver = resolver_for(&dir);
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&resolver)),
            72.0
        );
    }

    #[test]
    fn unbound_site_keeps_project_radius() {
        let dir = tempfile::tempdir().unwrap();
        boards_yaml(
            &dir,
            "boards:\n  heltec-t096:\n    radius_km: 50\n",
        );
        nodes_yaml(
            &dir,
            "nodes:\n  me0041:\n    board: heltec-t096\n",
        );
        let preset = preset_default_radius();
        let site = SiteEntry {
            name: "Peak".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: None,
        };
        let resolver = resolver_for(&dir);
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&resolver)),
            72.0
        );
    }

    #[test]
    fn no_boards_yaml_keeps_project_radius() {
        let dir = tempfile::tempdir().unwrap();
        nodes_yaml(
            &dir,
            "nodes:\n  me0041:\n    board: heltec-t096\n",
        );
        let preset = preset_default_radius();
        let site = SiteEntry {
            name: "Test".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0041".into()),
        };
        let resolver = resolver_for(&dir);
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&resolver)),
            72.0
        );
    }

    #[test]
    fn rejects_simulation_boards_in_config() {
        let mut raw = serde_yaml::Mapping::new();
        let mut sim = serde_yaml::Mapping::new();
        sim.insert(Value::from("boards"), Value::Mapping(serde_yaml::Mapping::new()));
        raw.insert(Value::from("simulation"), Value::Mapping(sim));
        let err = reject_legacy_simulation_boards(&raw).unwrap_err();
        assert!(err.to_string().contains("boards.yaml"));
    }
}
