//! Shared HTTP query parsing helpers.

use std::collections::{HashMap, HashSet};

/// Comma-separated peak slugs to omit from RF catalog scans (map-hidden peaks).
pub fn parse_exclude_peaks(params: &HashMap<String, String>) -> HashSet<String> {
    params
        .get("exclude_peaks")
        .map(|raw| {
            raw.split(',')
                .map(|s| s.trim())
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_exclude_peaks_splits_and_trims() {
        let mut params = HashMap::new();
        params.insert("exclude_peaks".into(), " alpha, beta , ,gamma".into());
        let slugs = parse_exclude_peaks(&params);
        assert_eq!(slugs.len(), 3);
        assert!(slugs.contains("alpha"));
        assert!(slugs.contains("beta"));
        assert!(slugs.contains("gamma"));
    }

    #[test]
    fn parse_exclude_peaks_empty_when_missing() {
        assert!(parse_exclude_peaks(&HashMap::new()).is_empty());
    }
}
