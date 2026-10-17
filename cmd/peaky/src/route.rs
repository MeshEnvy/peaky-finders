//! ``peaky route <lat> <lng>`` — site-sheet routes to a pin, as KML.

use std::fs::File;
use std::io::{self, Write};
use std::path::Path;
use std::time::Instant;

use anyhow::{Context, Result};
use peaky_peaks::{
    compute_pin_routes, load_routing_near, update_roads, SourceFetch, OSM_ROUTE_PAD_DEG,
};
use peaky_preset::{load_preset, AccessRoute, RoadRole, SiteAccessOption};

pub fn run(project: &Path, lat: f64, lng: f64, output: Option<&Path>) -> Result<()> {
    let project_dir = peaky_preset::ensure_project_initialized(project)?;
    let config_path = project_dir.join("config.yaml");
    let preset = load_preset(&config_path)?;
    let meta = preset.resolved_access_meta();
    let opts = peaky_peaks::OsmRoutingOpts::from(&meta);

    eprintln!("route: refresh road graph");
    let t0 = Instant::now();
    update_roads(&config_path, SourceFetch::AssumeFresh)?;
    let refresh_secs = t0.elapsed().as_secs_f64();
    eprintln!("route: refresh done ({refresh_secs:.2}s)");

    eprintln!("route: load master graph around {lat:.5},{lng:.5}");
    let t1 = Instant::now();
    let routing = load_routing_near(&project_dir, lat, lng, OSM_ROUTE_PAD_DEG, false, &opts)?;
    let load_secs = t1.elapsed().as_secs_f64();
    let nodes = routing.osm().graph.node_count();
    eprintln!("route: load done ({load_secs:.2}s, {nodes} graph nodes)");

    let mirror = peaky_preset::resolved_skadi_mirror_dir_for_project(&project_dir);
    let session = splatter::Session::new(
        mirror,
        false,
        peaky_preset::resolved_dem_fetch_max_workers(&preset),
    );

    eprintln!("route: compute routes");
    let t2 = Instant::now();
    let options = compute_pin_routes(&project_dir, &session, &routing, lat, lng)?;
    let compute_secs = t2.elapsed().as_secs_f64();
    eprintln!(
        "route: compute done ({compute_secs:.2}s, {} route{})",
        options.len(),
        if options.len() == 1 { "" } else { "s" }
    );
    for (i, opt) in options.iter().enumerate() {
        eprintln!(
            "route: {} jeep {:.0} m  trail {:.0} m  hike {:.0} m{}",
            i + 1,
            opt.route.jeep_m.unwrap_or(0.0),
            opt.route.trail_m.unwrap_or(0.0),
            opt.route.hike_m.unwrap_or(0.0),
            if opt.recommended { "  recommended" } else { "" }
        );
    }

    let (west, south, east, north) = route_buffer_bbox(lat, lng, &options);
    let roads = roads_in_bbox(&routing, west, south, east, north);
    eprintln!(
        "route: context roads  paved {}  jeep {}  private {}  other {}",
        roads.iter().filter(|r| r.role == RoadRole::Paved).count(),
        roads.iter().filter(|r| r.role == RoadRole::Jeep).count(),
        roads.iter().filter(|r| r.role == RoadRole::Private).count(),
        roads
            .iter()
            .filter(|r| matches!(r.role, RoadRole::Other | RoadRole::Hiking))
            .count(),
    );

    let kml = pin_routes_kml(lat, lng, &options, &roads);
    if let Some(path) = output {
        let mut file = File::create(path).with_context(|| format!("create {}", path.display()))?;
        file.write_all(kml.as_bytes())?;
        eprintln!("route: wrote {}", path.display());
    } else {
        io::stdout().write_all(kml.as_bytes())?;
    }
    Ok(())
}

fn pin_routes_kml(
    lat: f64,
    lng: f64,
    options: &[SiteAccessOption],
    roads: &[ContextRoad],
) -> String {
    let mut out = String::new();
    out.push_str(r#"<?xml version="1.0" encoding="UTF-8"?>"#);
    out.push('\n');
    out.push_str(r#"<kml xmlns="http://www.opengis.net/kml/2.2"><Document>"#);
    out.push_str(&style("road-paved", "ffff0000", 2));
    out.push_str(&style("road-jeep", "ff0c58ea", 2));
    out.push_str(&style("road-other", "ffb469ff", 2));
    out.push_str(&style("road-private", "ff0000ff", 4));
    out.push_str(&style("jeep", "ff0c58ea", 5));
    out.push_str(&style("trail", "ff74a5d4", 5));
    out.push_str(&style("hike", "ff5ec522", 5));
    out.push_str("<Folder><name>roads</name>");
    for road in roads {
        let style_id = match road.role {
            RoadRole::Paved => "road-paved",
            RoadRole::Jeep => "road-jeep",
            RoadRole::Private => "road-private",
            RoadRole::Hiking | RoadRole::Other => "road-other",
        };
        push_road(&mut out, style_id, road);
    }
    out.push_str("</Folder>");
    out.push_str(&point("pin", lat, lng));
    for (i, opt) in options.iter().enumerate() {
        let name = route_name(i, opt);
        out.push_str("<Folder><name>");
        out.push_str(&xml_escape(&name));
        out.push_str("</name>");
        push_line(&mut out, "jeep", "jeep", &jeep_coords(&opt.route));
        push_line(
            &mut out,
            "trail",
            "trail",
            &hike_coords(opt.route.trail.as_ref()),
        );
        push_line(
            &mut out,
            "hike",
            "hike",
            &hike_coords(opt.route.hike.as_ref()),
        );
        out.push_str("</Folder>");
    }
    out.push_str("</Document></kml>\n");
    out
}

fn route_name(index: usize, opt: &SiteAccessOption) -> String {
    let jeep = opt.route.jeep_m.unwrap_or(0.0);
    let hike = opt.route.hike_m.unwrap_or(0.0);
    let mark = if opt.recommended { " recommended" } else { "" };
    format!(
        "route {}{mark}  jeep {jeep:.0} m  hike {hike:.0} m",
        index + 1
    )
}

fn jeep_coords(route: &AccessRoute) -> Vec<(f64, f64)> {
    let Some(jeep) = &route.jeep else {
        return Vec::new();
    };
    if jeep.jeep_line.len() >= 2 {
        return jeep.jeep_line.iter().map(|p| (p.lat, p.lon)).collect();
    }
    jeep.profile.iter().map(|p| (p.lat, p.lon)).collect()
}

fn hike_coords(profile: Option<&peaky_preset::PeakHikeProfile>) -> Vec<(f64, f64)> {
    profile
        .map(|h| h.profile.iter().map(|p| (p.lat, p.lon)).collect())
        .unwrap_or_default()
}

struct ContextRoad {
    role: RoadRole,
    source: String,
    id: String,
    kind: String,
    surface: String,
    grade: String,
    access: String,
    reason: Option<String>,
    coords: Vec<(f64, f64)>,
}

fn push_road(out: &mut String, style_id: &str, road: &ContextRoad) {
    if road.coords.len() < 2 {
        return;
    }
    out.push_str("<Placemark><name>");
    out.push_str(&xml_escape(&road.id));
    out.push_str("</name><styleUrl>#");
    out.push_str(style_id);
    out.push_str("</styleUrl>");
    if road.role == RoadRole::Private {
        out.push_str("<Style><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>");
    }
    out.push_str("<description><![CDATA[");
    out.push_str(&road_description(road));
    out.push_str("]]></description><ExtendedData>");
    data(out, "source", &road.source);
    data(out, "id", &road.id);
    data(out, "role", road.role.as_str());
    data(out, "kind", &road.kind);
    data(out, "surface", &road.surface);
    data(out, "grade", &road.grade);
    data(out, "access", &road.access);
    if let Some(reason) = &road.reason {
        data(out, "reason", reason);
    }
    out.push_str("</ExtendedData>");
    if road.role == RoadRole::Private {
        out.push_str("<MultiGeometry>");
        for dash in dash_line(&road.coords) {
            write_coordinates(out, &dash, false);
        }
        out.push_str("</MultiGeometry>");
    } else {
        write_coordinates(out, &road.coords, true);
    }
    out.push_str("</Placemark>");
}

fn road_description(road: &ContextRoad) -> String {
    let mut rows = vec![
        ("source", road.source.clone()),
        ("id", road.id.clone()),
        ("role", road.role.as_str().to_string()),
        ("kind", road.kind.clone()),
        ("surface", road.surface.clone()),
        ("grade", road.grade.clone()),
        ("access", road.access.clone()),
    ];
    if let Some(reason) = &road.reason {
        rows.push(("reason", reason.clone()));
    }
    let mut html = String::from("<table>");
    for (label, value) in rows {
        html.push_str("<tr><td><b>");
        html.push_str(&xml_escape(label));
        html.push_str("</b></td><td>");
        html.push_str(&xml_escape(&value));
        html.push_str("</td></tr>");
    }
    html.push_str("</table>");
    html
}

fn data(out: &mut String, name: &str, value: &str) {
    out.push_str("<Data name=\"");
    out.push_str(&xml_escape(name));
    out.push_str("\"><value>");
    out.push_str(&xml_escape(value));
    out.push_str("</value></Data>");
}

fn push_line(out: &mut String, name: &str, style_id: &str, coords: &[(f64, f64)]) {
    if coords.len() < 2 {
        return;
    }
    out.push_str("<Placemark><name>");
    out.push_str(name);
    out.push_str("</name><styleUrl>#");
    out.push_str(style_id);
    out.push_str("</styleUrl>");
    write_coordinates(out, coords, true);
    out.push_str("</Placemark>");
}

fn dash_line(coords: &[(f64, f64)]) -> Vec<Vec<(f64, f64)>> {
    const DASH_M: f64 = 80.0;
    const GAP_M: f64 = 40.0;
    if coords.len() < 2 {
        return Vec::new();
    }
    let mut dashes = Vec::new();
    let mut dash: Vec<(f64, f64)> = vec![coords[0]];
    let mut filled = 0.0;
    let mut drawing = true;
    for window in coords.windows(2) {
        let (lat1, lon1) = window[0];
        let (lat2, lon2) = window[1];
        let span = haversine_m(lat1, lon1, lat2, lon2);
        if span < 0.5 {
            continue;
        }
        let mut walked = 0.0;
        while walked < span - 0.01 {
            let budget = if drawing { DASH_M - filled } else { GAP_M - filled };
            let step = budget.min(span - walked);
            walked += step;
            filled += step;
            let t = walked / span;
            let point = (
                lat1 + t * (lat2 - lat1),
                lon1 + t * (lon2 - lon1),
            );
            if drawing {
                dash.push(point);
                if filled >= DASH_M - 0.01 {
                    if dash.len() >= 2 {
                        dashes.push(std::mem::take(&mut dash));
                    }
                    dash = vec![point];
                    drawing = false;
                    filled = 0.0;
                }
            } else if filled >= GAP_M - 0.01 {
                dash = vec![point];
                drawing = true;
                filled = 0.0;
            }
        }
    }
    if drawing && dash.len() >= 2 {
        dashes.push(dash);
    }
    if dashes.is_empty() {
        dashes.push(coords.to_vec());
    }
    dashes
}

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6_371_000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    r * 2.0 * a.sqrt().asin()
}

fn write_coordinates(out: &mut String, coords: &[(f64, f64)], tessellate: bool) {
    out.push_str("<LineString>");
    if tessellate {
        out.push_str("<tessellate>1</tessellate>");
    } else {
        out.push_str("<altitudeMode>clampToGround</altitudeMode>");
    }
    out.push_str("<coordinates>");
    for (i, (lat, lon)) in coords.iter().enumerate() {
        if i > 0 {
            out.push(' ');
        }
        out.push_str(&format!("{lon:.6},{lat:.6},0"));
    }
    out.push_str("</coordinates></LineString>");
}

fn point(name: &str, lat: f64, lng: f64) -> String {
    format!(
        "<Placemark><name>{name}</name><Point><coordinates>{lng:.6},{lat:.6},0</coordinates></Point></Placemark>"
    )
}

fn style(id: &str, color: &str, width: u8) -> String {
    format!(
        "<Style id=\"{id}\"><LineStyle><color>{color}</color><width>{width}</width></LineStyle></Style>"
    )
}

/// Bounding box of every route vertex and the pin, expanded by 1 km.
fn route_buffer_bbox(lat: f64, lng: f64, options: &[SiteAccessOption]) -> (f64, f64, f64, f64) {
    let mut west = lng;
    let mut east = lng;
    let mut south = lat;
    let mut north = lat;
    let mut absorb = |coords: &[(f64, f64)]| {
        for &(la, lo) in coords {
            west = west.min(lo);
            east = east.max(lo);
            south = south.min(la);
            north = north.max(la);
        }
    };
    for opt in options {
        absorb(&jeep_coords(&opt.route));
        absorb(&hike_coords(opt.route.trail.as_ref()));
        absorb(&hike_coords(opt.route.hike.as_ref()));
    }
    let mid = (south + north) / 2.0;
    let dlat = 1_000.0 / 111_320.0;
    let dlon = 1_000.0 / (111_320.0 * mid.to_radians().cos().abs().max(0.2));
    (west - dlon, south - dlat, east + dlon, north + dlat)
}

fn roads_in_bbox(
    routing: &peaky_peaks::ProjectRouting,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Vec<ContextRoad> {
    routing
        .osm()
        .segment_groups
        .iter()
        .filter(|group| segment_hits(group.coords.as_slice(), west, south, east, north))
        .filter(|group| {
            matches!(
                group.role,
                RoadRole::Paved
                    | RoadRole::Jeep
                    | RoadRole::Other
                    | RoadRole::Hiking
                    | RoadRole::Private
            )
        })
        .map(|group| {
            let grade = match &group.tags.tracktype {
                Some(value) => value
                    .strip_prefix("grade")
                    .filter(|rest| !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()))
                    .unwrap_or(value)
                    .to_string(),
                None => "unknown".to_string(),
            };
            ContextRoad {
                role: group.role,
                source: group.source_name(),
                id: group.segment_id(),
                kind: group.tags.highway.clone(),
                surface: group
                    .tags
                    .surface
                    .clone()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| "unknown".to_string()),
                grade,
                access: group.access.clone(),
                reason: group.reason.clone(),
                coords: group.coords.clone(),
            }
        })
        .collect()
}

fn segment_hits(coords: &[(f64, f64)], west: f64, south: f64, east: f64, north: f64) -> bool {
    if coords.len() < 2 {
        return false;
    }
    let mut seg_west = f64::INFINITY;
    let mut seg_east = f64::NEG_INFINITY;
    let mut seg_south = f64::INFINITY;
    let mut seg_north = f64::NEG_INFINITY;
    for &(lat, lon) in coords {
        seg_west = seg_west.min(lon);
        seg_east = seg_east.max(lon);
        seg_south = seg_south.min(lat);
        seg_north = seg_north.max(lat);
    }
    seg_east >= west && seg_west <= east && seg_north >= south && seg_south <= north
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kml_has_a_pin_and_one_folder_per_route() {
        let kml = pin_routes_kml(39.5, -119.8, &[], &[]);
        assert!(kml.contains("<Point>"));
        assert!(kml.contains("-119.800000,39.500000,0"));
        assert!(!kml.contains("<Folder>"));
    }

    #[test]
    fn road_info_box_lists_source_fields() {
        let road = ContextRoad {
            role: RoadRole::Jeep,
            source: "osm".into(),
            id: "osm:392541569".into(),
            kind: "track".into(),
            surface: "unpaved".into(),
            grade: "2".into(),
            access: "public".into(),
            reason: None,
            coords: vec![(39.5, -119.8), (39.51, -119.8)],
        };
        let kml = pin_routes_kml(39.5, -119.8, &[], &[road]);
        assert!(kml.contains("<Data name=\"source\"><value>osm</value></Data>"));
        assert!(kml.contains("<Data name=\"id\"><value>osm:392541569</value></Data>"));
        assert!(kml.contains("<Data name=\"kind\"><value>track</value></Data>"));
        assert!(kml.contains("<Data name=\"surface\"><value>unpaved</value></Data>"));
        assert!(kml.contains("<Data name=\"grade\"><value>2</value></Data>"));
        assert!(kml.contains("<Data name=\"role\"><value>jeep</value></Data>"));
    }
}
