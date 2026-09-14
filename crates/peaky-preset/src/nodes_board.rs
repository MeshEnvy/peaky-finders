//! Optional ``nodes.yaml`` board index for per-board viewshed radius.

use std::collections::HashMap;
use std::path::Path;

use serde_yaml::Value;

use crate::board_sim::normalize_board_id;

/// Maps fleet unit keys (``me0041``) to normalized board ids (``heltec-t096``).
#[derive(Debug, Clone, Default)]
pub struct NodesBoardIndex {
    node_to_board: HashMap<String, String>,
}

impl NodesBoardIndex {
    pub fn board_for_node(&self, node_key: &str) -> Option<&str> {
        let key = node_key.trim().to_ascii_lowercase();
        self.node_to_board.get(&key).map(String::as_str)
    }

    pub fn is_empty(&self) -> bool {
        self.node_to_board.is_empty()
    }
}

/// Load ``<project>/nodes.yaml`` when present; missing file yields an empty index.
pub fn load_nodes_board_index(project_dir: &Path) -> NodesBoardIndex {
    let path = project_dir.join("nodes.yaml");
    load_nodes_board_index_from_path(&path).unwrap_or_default()
}

pub fn load_nodes_board_index_from_path(path: &Path) -> Result<NodesBoardIndex, String> {
    if !path.is_file() {
        return Ok(NodesBoardIndex::default());
    }
    let raw = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let doc: Value = serde_yaml::from_str(&raw).map_err(|e| e.to_string())?;
    let nodes = doc
        .get("nodes")
        .and_then(|v| v.as_mapping())
        .ok_or_else(|| "nodes.yaml: nodes must be a mapping".to_string())?;

    let mut node_to_board = HashMap::new();
    for (node_key, node_val) in nodes {
        let Some(node_key) = node_key.as_str() else {
            continue;
        };
        let Some(node_map) = node_val.as_mapping() else {
            continue;
        };
        let Some(board_raw) = node_map
            .get(Value::from("board"))
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
        else {
            continue;
        };
        node_to_board.insert(
            node_key.trim().to_ascii_lowercase(),
            normalize_board_id(board_raw),
        );
    }
    Ok(NodesBoardIndex { node_to_board })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn loads_board_per_node() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nodes.yaml");
        let mut f = std::fs::File::create(&path).unwrap();
        writeln!(
            f,
            r#"nodes:
  me0041:
    board: heltec-t096
  me0001:
    board: rak4631
"#
        )
        .unwrap();
        let idx = load_nodes_board_index_from_path(&path).unwrap();
        assert_eq!(idx.board_for_node("me0041"), Some("heltec-t096"));
        assert_eq!(idx.board_for_node("ME0041"), Some("heltec-t096"));
        assert_eq!(idx.board_for_node("me0001"), Some("rak4631"));
        assert_eq!(idx.board_for_node("me9999"), None);
    }

    #[test]
    fn missing_file_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        let idx = load_nodes_board_index(dir.path());
        assert!(idx.is_empty());
    }
}
