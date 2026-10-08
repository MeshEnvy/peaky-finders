//! ``peaky export`` subcommand — tag-filtered KMZ for onX / Google Earth.

use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use peaky_preset::{load_preset, resolve_preset_path, resolve_project_dir, SiteEntry};
use peaky_serve::site_kmz::export_sites_kmz_for_cli;

pub fn run(
    project: PathBuf,
    tags: &[String],
    exclude_tags: &[String],
    output: Option<PathBuf>,
    verbose: bool,
) -> Result<()> {
    if tags.is_empty() {
        anyhow::bail!("at least one --tag is required");
    }

    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }

    let preset = load_preset(&preset_path).context("load preset")?;
    let include: HashSet<&str> = tags.iter().map(String::as_str).collect();
    let exclude: HashSet<&str> = exclude_tags.iter().map(String::as_str).collect();

    let mut matched = filter_sites_by_tag(&preset.sites, &include, &exclude);
    if matched.is_empty() {
        anyhow::bail!("no sites matched tag(s): {}", tags.join(", "));
    }

    sort_sites_alphabetical(&mut matched);

    let sites: Vec<(String, SiteEntry)> = matched
        .into_iter()
        .map(|(slug, site)| (slug.to_string(), site.clone()))
        .collect();

    let document_name = tags.join("+");
    let output_path = output.unwrap_or_else(|| PathBuf::from(format!("{}.kmz", tags[0])));

    let project_dir = resolve_project_dir(&project.to_string_lossy());
    let kmz = export_sites_kmz_for_cli(&project_dir, &sites, &document_name, verbose)
        .context("build KMZ")?;

    fs::write(&output_path, &kmz)
        .with_context(|| format!("write {}", output_path.display()))?;

    if verbose {
        eprintln!(
            "[peaky export] {} site(s) → {}",
            sites.len(),
            output_path.display()
        );
    } else {
        println!("{}", output_path.display());
    }

    Ok(())
}

fn site_matches_tags(
    site: &SiteEntry,
    include: &HashSet<&str>,
    exclude: &HashSet<&str>,
) -> bool {
    let site_tags: HashSet<&str> = site.tags.iter().map(String::as_str).collect();
    if include.intersection(&site_tags).next().is_none() {
        return false;
    }
    exclude.intersection(&site_tags).next().is_none()
}

fn filter_sites_by_tag<'a>(
    sites: &'a std::collections::HashMap<String, SiteEntry>,
    include: &HashSet<&str>,
    exclude: &HashSet<&str>,
) -> Vec<(&'a str, &'a SiteEntry)> {
    sites
        .iter()
        .filter(|(_, site)| site_matches_tags(site, include, exclude))
        .map(|(slug, site)| (slug.as_str(), site))
        .collect()
}

fn sort_sites_alphabetical(sites: &mut [(&str, &SiteEntry)]) {
    sites.sort_by(|(slug_a, site_a), (slug_b, site_b)| {
        site_a
            .name
            .cmp(&site_b.name)
            .then_with(|| slug_a.cmp(slug_b))
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn site_with_tags(tags: &[&str]) -> SiteEntry {
        SiteEntry {
            name: "Test".to_string(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: tags.iter().map(|t| t.to_string()).collect(),
            node: None,
            access_choice: None,
        }
    }

    #[test]
    fn filter_or_include_and_exclude() {
        let mut sites = HashMap::new();
        sites.insert("a".to_string(), site_with_tags(&["reno-vegas", "installed"]));
        sites.insert("b".to_string(), site_with_tags(&["reno-vegas", "optional"]));
        sites.insert("c".to_string(), site_with_tags(&["other"]));

        let include: HashSet<&str> = ["reno-vegas"].into_iter().collect();
        let exclude: HashSet<&str> = ["optional"].into_iter().collect();

        let matched = filter_sites_by_tag(&sites, &include, &exclude);
        assert_eq!(matched.len(), 1);
        assert_eq!(matched[0].0, "a");
    }

    #[test]
    fn filter_empty_when_no_include_match() {
        let mut sites = HashMap::new();
        sites.insert("a".to_string(), site_with_tags(&["other"]));

        let include: HashSet<&str> = ["reno-vegas"].into_iter().collect();
        let exclude = HashSet::new();

        assert!(filter_sites_by_tag(&sites, &include, &exclude).is_empty());
    }
}
