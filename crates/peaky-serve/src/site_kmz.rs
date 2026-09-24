//! Resolve viewshed + access and build site KMZ archives.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_geo::{
    build_kmz_bytes, viewshed_archive_href, KmzAccessLine, KmzLatLonBox, KmzSiteFolder,
    KmzViewshedOverlay,
};
use peaky_peaks::{
    load_routing_for_points, warm_place_access_with_routing, OsmRoutingOpts, OSM_ROUTE_PAD_DEG,
    ProjectRouting,
};
use peaky_preset::{
    load_preset, resolved_dem_fetch_max_workers, resolved_skadi_mirror_dir_for_project,
    AccessMeta, BoardViewshedResolver, PeakHikeProfile, PeakJeepProfile, SiteEntry,
};
use splatter::Session;

use crate::rf::load_board_viewshed;
use crate::viewshed::ensure_viewshed_for_site_blocking;
use crate::viewshed_index::load_bounds_from_manifest;

pub fn export_sites_kmz_blocking(
    session: Arc<Session>,
    preset_path: &Path,
    board_viewshed: &BoardViewshedResolver,
    routing: &ProjectRouting,
    sites: &[(String, SiteEntry)],
    document_name: &str,
    verbose: bool,
) -> Result<Vec<u8>> {
    let preset = load_preset(preset_path).context("load preset")?;
    let meta = preset.resolved_access_meta();

    let mut sites: Vec<(String, SiteEntry)> = sites.to_vec();
    sites.sort_by(|(slug_a, site_a), (slug_b, site_b)| {
        site_a
            .name
            .cmp(&site_b.name)
            .then_with(|| slug_a.cmp(slug_b))
    });

    let mut folders = Vec::with_capacity(sites.len());
    for (slug, site) in &sites {
        folders.push(resolve_site_folder(
            session.clone(),
            preset_path,
            &preset,
            board_viewshed,
            routing,
            &meta,
            slug.as_str(),
            site,
            verbose,
        )?);
    }

    build_kmz_bytes(document_name, &folders).context("build kmz")
}

pub fn export_sites_kmz_for_cli(
    project_dir: &Path,
    sites: &[(String, SiteEntry)],
    document_name: &str,
    verbose: bool,
) -> Result<Vec<u8>> {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).context("load preset")?;
    let board_viewshed = load_board_viewshed(&preset_path);
    let mirror = resolved_skadi_mirror_dir_for_project(project_dir);
    std::fs::create_dir_all(&mirror).context("create skadi mirror")?;
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, verbose, dem_workers));

    let points: Vec<(f64, f64)> = sites.iter().map(|(_, s)| (s.lat(), s.lon())).collect();
    let meta = preset.resolved_access_meta();
    let opts = OsmRoutingOpts::from(&meta);
    let routing = load_routing_for_points(project_dir, &points, OSM_ROUTE_PAD_DEG, false, &opts)
        .context("load OSM routing for export")?;

    export_sites_kmz_blocking(
        session,
        &preset_path,
        &board_viewshed,
        &routing,
        sites,
        document_name,
        verbose,
    )
}

fn resolve_site_folder(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &peaky_preset::Preset,
    board_viewshed: &BoardViewshedResolver,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    slug: &str,
    site: &SiteEntry,
    verbose: bool,
) -> Result<KmzSiteFolder> {
    let splat_png = ensure_viewshed_for_site_blocking(
        session.clone(),
        preset_path,
        preset,
        slug,
        site,
        Some(board_viewshed),
        verbose,
    )
    .with_context(|| format!("viewshed {slug}"))?;

    let viewshed = read_viewshed_overlay(slug, &splat_png)?;

    let access_lines = warm_place_access_with_routing(
        preset_path,
        &session,
        routing,
        meta,
        site.lat(),
        site.lon(),
    )
    .with_context(|| format!("access {slug}"))?
    .map(|access| access_lines_from_route(&access.recommended))
    .unwrap_or_default();

    Ok(KmzSiteFolder {
        folder_name: site.name.clone(),
        slug: slug.to_string(),
        tags: site.tags.clone(),
        pin_lat: site.lat(),
        pin_lon: site.lon(),
        pin_alt: site.height_m.unwrap_or(0.0),
        viewshed,
        access_lines,
    })
}

fn read_viewshed_overlay(slug: &str, splat_png: &Path) -> Result<Option<KmzViewshedOverlay>> {
    if !splat_png.is_file() {
        return Ok(None);
    }
    let workdir = splat_png
        .parent()
        .with_context(|| format!("viewshed png has no parent: {}", splat_png.display()))?;
    let manifest_path = workdir.join("manifest.json");
    let png_bytes =
        std::fs::read(splat_png).with_context(|| format!("read {}", splat_png.display()))?;
    let bounds = load_bounds_from_manifest(&manifest_path)
        .with_context(|| format!("manifest {}", manifest_path.display()))?;
    let north = *bounds.get("north").context("bbox north")?;
    let south = *bounds.get("south").context("bbox south")?;
    let east = *bounds.get("east").context("bbox east")?;
    let west = *bounds.get("west").context("bbox west")?;
    let rotation = bounds.get("rotation").copied();
    Ok(Some(KmzViewshedOverlay {
        href: viewshed_archive_href(slug),
        bbox: KmzLatLonBox {
            north,
            south,
            east,
            west,
            rotation,
        },
        png_bytes,
    }))
}

fn access_lines_from_route(route: &peaky_preset::AccessRoute) -> Vec<KmzAccessLine> {
    let mut out = Vec::new();
    if let Some(hike) = route.hike.as_ref() {
        if let Some(coords) = line_from_hike(hike) {
            out.push(KmzAccessLine {
                name: "hike".to_string(),
                coordinates: coords,
            });
        }
    }
    if let Some(jeep) = route.jeep.as_ref() {
        if let Some(coords) = line_from_jeep(jeep) {
            out.push(KmzAccessLine {
                name: "jeep".to_string(),
                coordinates: coords,
            });
        }
    }
    out
}

fn line_from_hike(hike: &PeakHikeProfile) -> Option<Vec<(f64, f64, f64)>> {
    if hike.profile.len() < 2 {
        return None;
    }
    Some(
        hike.profile
            .iter()
            .map(|p| (p.lon, p.lat, p.elev_m))
            .collect(),
    )
}

fn line_from_jeep(jeep: &PeakJeepProfile) -> Option<Vec<(f64, f64, f64)>> {
    if jeep.profile.len() < 2 {
        return None;
    }
    Some(
        jeep.profile
            .iter()
            .map(|p| (p.lon, p.lat, p.elev_m))
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::TempDir;

    #[test]
    fn read_viewshed_overlay_uses_splat_png_path_not_workdir() {
        let dir = TempDir::new().unwrap();
        let manifest = dir.path().join("manifest.json");
        std::fs::write(
            &manifest,
            r#"{"bbox":{"north":40.0,"south":39.0,"east":-115.0,"west":-116.0,"rotation":0.0}}"#,
        )
        .unwrap();
        let splat = dir.path().join("splat.png");
        let mut f = std::fs::File::create(&splat).unwrap();
        f.write_all(&[0x89, 0x50, 0x4e, 0x47]).unwrap();

        let overlay = read_viewshed_overlay("tower-mountain", &splat)
            .unwrap()
            .expect("overlay");
        assert_eq!(overlay.href, "files/tower-mountain-viewshed.png");
        assert!((overlay.bbox.north - 40.0).abs() < 1e-6);
    }
}
