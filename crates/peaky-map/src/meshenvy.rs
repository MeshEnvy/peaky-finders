//! meshenvy.org /map contract: Nevada hub anchors and legacy JSON field names.

use crate::hub_bridge::HubBridgeConfig;

/// GeoJSON `meshenvy_meta` key expected by meshenvy.org (historical name).
pub const META_HUB_BRIDGE_KEY: &str = "silver_triangle";

/// Reno, Wells, Las Vegas downtown anchors for the public statewide map.
pub fn nevada_hub_bridge_config() -> HubBridgeConfig {
    HubBridgeConfig {
        vertices: vec![
            [-119.8138, 39.5296],
            [-114.9644, 41.1116],
            [-115.1398, 36.1699],
        ],
        hub_ids: vec!["reno".into(), "wells".into(), "vegas".into()],
        link_pairs: vec![(0, 1), (1, 2), (2, 0)],
    }
}
