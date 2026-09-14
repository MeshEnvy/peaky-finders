//! Per-board simulation overrides (viewshed radius today).

use std::collections::HashMap;

use crate::model::BoardSimConfig;
use crate::nodes_board::NodesBoardIndex;
use crate::{Preset, PresetResult, PresetValidationError, SiteEntry};
use crate::viewshed_quality::preset_radius_km;

fn err(msg: impl Into<String>) -> PresetValidationError {
    PresetValidationError::Message(msg.into())
}

/// Normalize board ids to match ``nodes.yaml`` and envybot (``t096`` → ``heltec-t096``).
pub fn normalize_board_id(raw: &str) -> String {
    let s = raw.trim().to_ascii_lowercase();
    if s == "t096" {
        "heltec-t096".to_string()
    } else {
        s
    }
}

fn board_lookup_keys(board: &str) -> Vec<String> {
    let norm = normalize_board_id(board);
    let mut keys = vec![norm.clone()];
    if norm == "heltec-t096" {
        keys.push("t096".into());
    }
    let trimmed = board.trim().to_ascii_lowercase();
    if trimmed != norm && !keys.iter().any(|k| k == &trimmed) {
        keys.push(trimmed);
    }
    keys
}

fn board_sim_config<'a>(preset: &'a Preset, board: &str) -> Option<&'a BoardSimConfig> {
    for key in board_lookup_keys(board) {
        if let Some(cfg) = preset.simulation.boards.get(&key) {
            return Some(cfg);
        }
    }
    None
}

pub fn board_viewshed_radius_km(preset: &Preset, board: &str) -> Option<f64> {
    board_sim_config(preset, board).and_then(|cfg| cfg.radius_km)
}

/// Viewshed radius for a staked site: board override when ``site.node`` maps in ``nodes.yaml``.
pub fn site_viewshed_radius_km(
    preset: &Preset,
    site: &SiteEntry,
    board_index: Option<&NodesBoardIndex>,
) -> f64 {
    let default = preset_radius_km(preset);
    let Some(node_key) = site
        .node
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_ascii_lowercase())
    else {
        return default;
    };
    let Some(board_index) = board_index else {
        return default;
    };
    let Some(board) = board_index.board_for_node(&node_key) else {
        return default;
    };
    board_viewshed_radius_km(preset, board).unwrap_or(default)
}

pub fn validate_board_radius_km(radius_km: f64, board: &str) -> PresetResult<()> {
    if !(1.0..=100.0).contains(&radius_km) {
        return Err(err(format!(
            "simulation.boards.{board}.radius_km must be between 1 and 100"
        )));
    }
    Ok(())
}

pub fn validate_board_sim_configs(boards: &HashMap<String, BoardSimConfig>) -> PresetResult<()> {
    for (board, cfg) in boards {
        if let Some(radius_km) = cfg.radius_km {
            validate_board_radius_km(radius_km, board)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{BoardSimConfig, SimulationConfig};
    use serde_yaml::Value;

    fn preset_with_boards(boards: HashMap<String, BoardSimConfig>) -> Preset {
        Preset {
            simulation: SimulationConfig {
                radius_km: Value::from(72.0),
                boards,
                ..SimulationConfig::default()
            },
            ..Preset::default()
        }
    }

    fn t096_index() -> NodesBoardIndex {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nodes.yaml");
        std::fs::write(
            &path,
            "nodes:\n  me0041:\n    board: heltec-t096\n",
        )
        .unwrap();
        crate::nodes_board::load_nodes_board_index_from_path(&path).unwrap()
    }

    #[test]
    fn t096_site_gets_board_radius() {
        let mut boards = HashMap::new();
        boards.insert(
            "heltec-t096".into(),
            BoardSimConfig {
                radius_km: Some(50.0),
            },
        );
        let preset = preset_with_boards(boards);
        let site = SiteEntry {
            name: "Test".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0041".into()),
        };
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&t096_index())),
            50.0
        );
    }

    #[test]
    fn t096_alias_board_key() {
        let mut boards = HashMap::new();
        boards.insert(
            "t096".into(),
            BoardSimConfig {
                radius_km: Some(50.0),
            },
        );
        let preset = preset_with_boards(boards);
        let site = SiteEntry {
            name: "Test".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0041".into()),
        };
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&t096_index())),
            50.0
        );
    }

    #[test]
    fn rak_site_keeps_project_radius() {
        let mut boards = HashMap::new();
        boards.insert(
            "heltec-t096".into(),
            BoardSimConfig {
                radius_km: Some(50.0),
            },
        );
        let preset = preset_with_boards(boards);
        let idx = {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("nodes.yaml");
            std::fs::write(
                &path,
                "nodes:\n  me0041:\n    board: heltec-t096\n  me0001:\n    board: rak4631\n",
            )
            .unwrap();
            crate::nodes_board::load_nodes_board_index_from_path(&path).unwrap()
        };
        let site = SiteEntry {
            name: "RAK".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0001".into()),
        };
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&idx)),
            72.0
        );
    }

    #[test]
    fn unbound_site_keeps_project_radius() {
        let mut boards = HashMap::new();
        boards.insert(
            "heltec-t096".into(),
            BoardSimConfig {
                radius_km: Some(50.0),
            },
        );
        let preset = preset_with_boards(boards);
        let site = SiteEntry {
            name: "Peak".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: None,
        };
        assert_eq!(
            site_viewshed_radius_km(&preset, &site, Some(&t096_index())),
            72.0
        );
    }

    #[test]
    fn no_nodes_yaml_keeps_project_radius() {
        let mut boards = HashMap::new();
        boards.insert(
            "heltec-t096".into(),
            BoardSimConfig {
                radius_km: Some(50.0),
            },
        );
        let preset = preset_with_boards(boards);
        let site = SiteEntry {
            name: "Test".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0041".into()),
        };
        assert_eq!(site_viewshed_radius_km(&preset, &site, None), 72.0);
    }
}
