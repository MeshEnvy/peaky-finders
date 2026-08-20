//! Parse KML/KMZ Point placemarks for site import.

use std::io::Cursor;

use anyhow::{bail, Context, Result};
use quick_xml::events::Event;
use quick_xml::Reader;
use zip::ZipArchive;

#[derive(Debug, Clone, PartialEq)]
pub struct KmlPointSite {
    pub name: String,
    pub lat: f64,
    pub lon: f64,
}

pub fn parse_kml_point_placemarks(data: &[u8]) -> Result<(Vec<KmlPointSite>, usize)> {
    let mut reader = Reader::from_reader(data);
    reader.config_mut().trim_text(true);

    let mut sites = Vec::new();
    let mut skipped = 0;

    let mut in_placemark = false;
    let mut in_point = false;
    let mut in_name = false;
    let mut in_coords = false;
    let mut placemark_has_point = false;
    let mut name = String::new();
    let mut coords = String::new();

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let local = local_name(e.name().as_ref());
                match local.as_str() {
                    "Placemark" => {
                        in_placemark = true;
                        placemark_has_point = false;
                        name.clear();
                        coords.clear();
                    }
                    "Point" if in_placemark => {
                        in_point = true;
                        placemark_has_point = true;
                    }
                    "name" if in_placemark => in_name = true,
                    "coordinates" if in_point => in_coords = true,
                    _ => {}
                }
            }
            Ok(Event::Text(e)) if in_name => {
                name = e.unescape()?.trim().to_string();
            }
            Ok(Event::Text(e)) if in_coords => {
                coords = e.unescape()?.trim().to_string();
            }
            Ok(Event::End(e)) => {
                let local = local_name(e.name().as_ref());
                match local.as_str() {
                    "name" => in_name = false,
                    "coordinates" => in_coords = false,
                    "Point" => in_point = false,
                    "Placemark" => {
                        if placemark_has_point {
                            if let Some((lat, lon)) = parse_point_coordinates(&coords) {
                                let site_name = if name.trim().is_empty() {
                                    "Unnamed site".to_string()
                                } else {
                                    name.trim().to_string()
                                };
                                sites.push(KmlPointSite {
                                    name: site_name,
                                    lat,
                                    lon,
                                });
                            } else {
                                skipped += 1;
                            }
                        } else {
                            skipped += 1;
                        }
                        in_placemark = false;
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => bail!("invalid KML XML: {e}"),
            _ => {}
        }
        buf.clear();
    }

    Ok((sites, skipped))
}

fn local_name(tag: &[u8]) -> String {
    let s = std::str::from_utf8(tag).unwrap_or("");
    s.rsplit('}').next().unwrap_or(s).to_string()
}

fn parse_point_coordinates(coords_text: &str) -> Option<(f64, f64)> {
    let text = coords_text.trim();
    if text.is_empty() {
        return None;
    }
    let first = text.split_whitespace().next()?;
    let parts: Vec<&str> = first.split(',').map(str::trim).collect();
    if parts.len() < 2 {
        return None;
    }
    let lon: f64 = parts[0].parse().ok()?;
    let lat: f64 = parts[1].parse().ok()?;
    if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
        return None;
    }
    Some((lat, lon))
}

pub fn parse_kmz_point_placemarks(data: &[u8]) -> Result<(Vec<KmlPointSite>, usize)> {
    let cursor = Cursor::new(data);
    let mut archive = ZipArchive::new(cursor).map_err(|e| anyhow::anyhow!("invalid KMZ archive: {e}"))?;
    let mut kml_name = None;
    for i in 0..archive.len() {
        let file = archive.by_index(i).context("read KMZ entry")?;
        if file.name().to_ascii_lowercase().ends_with(".kml") {
            kml_name = Some(file.name().to_string());
            break;
        }
    }
    let kml_name = kml_name.context("no .kml member in KMZ archive")?;
    let kml_bytes = {
        let mut file = archive.by_name(&kml_name).context("open KML in KMZ")?;
        let mut buf = Vec::new();
        std::io::Read::read_to_end(&mut file, &mut buf).context("read KML from KMZ")?;
        buf
    };
    parse_kml_point_placemarks(&kml_bytes)
}

#[derive(Debug, Clone, PartialEq)]
pub struct KmlRouteWaypoint {
    pub lat: f64,
    pub lon: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KmlLineRoute {
    pub name: String,
    pub waypoints: Vec<KmlRouteWaypoint>,
}

/// Alias for onX route exports (`KmlLineRoute`).
pub type KmlRoute = KmlLineRoute;

/// Parse onX-style KML LineString placemarks (ordered route vertices).
pub fn parse_kml_linestring_routes(data: &[u8]) -> Result<(Vec<KmlLineRoute>, usize)> {
    let mut reader = Reader::from_reader(data);
    reader.config_mut().trim_text(true);

    let mut routes = Vec::new();
    let mut skipped = 0;

    let mut in_placemark = false;
    let mut in_linestring = false;
    let mut in_name = false;
    let mut in_coords = false;
    let mut placemark_has_line = false;
    let mut name = String::new();
    let mut coords = String::new();

    let mut buf = Vec::new();
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(e)) => {
                let local = local_name(e.name().as_ref());
                match local.as_str() {
                    "Placemark" => {
                        in_placemark = true;
                        placemark_has_line = false;
                        name.clear();
                        coords.clear();
                    }
                    "LineString" if in_placemark => {
                        in_linestring = true;
                        placemark_has_line = true;
                    }
                    "name" if in_placemark => in_name = true,
                    "coordinates" if in_linestring => in_coords = true,
                    _ => {}
                }
            }
            Ok(Event::Text(e)) if in_name => {
                name = e.unescape()?.trim().to_string();
            }
            Ok(Event::Text(e)) if in_coords => {
                coords = e.unescape()?.trim().to_string();
            }
            Ok(Event::End(e)) => {
                let local = local_name(e.name().as_ref());
                match local.as_str() {
                    "name" => in_name = false,
                    "coordinates" => in_coords = false,
                    "LineString" => in_linestring = false,
                    "Placemark" => {
                        if placemark_has_line {
                            let waypoints = parse_linestring_coordinates(&coords);
                            if waypoints.is_empty() {
                                skipped += 1;
                            } else {
                                let route_name = if name.trim().is_empty() {
                                    "Unnamed route".to_string()
                                } else {
                                    name.trim().to_string()
                                };
                                routes.push(KmlLineRoute {
                                    name: route_name,
                                    waypoints,
                                });
                            }
                        } else {
                            skipped += 1;
                        }
                        in_placemark = false;
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(e) => bail!("invalid KML XML: {e}"),
            _ => {}
        }
        buf.clear();
    }

    Ok((routes, skipped))
}

fn parse_linestring_coordinates(coords_text: &str) -> Vec<KmlRouteWaypoint> {
    let mut out = Vec::new();
    for token in coords_text.split_whitespace() {
        let token = token.trim();
        if token.is_empty() {
            continue;
        }
        let parts: Vec<&str> = token.split(',').map(str::trim).collect();
        if parts.len() < 2 {
            continue;
        }
        let Ok(lon) = parts[0].parse::<f64>() else {
            continue;
        };
        let Ok(lat) = parts[1].parse::<f64>() else {
            continue;
        };
        if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
            continue;
        }
        out.push(KmlRouteWaypoint { lat, lon });
    }
    out
}

pub fn parse_kmz_linestring_routes(data: &[u8]) -> Result<(Vec<KmlLineRoute>, usize)> {
    let cursor = Cursor::new(data);
    let mut archive = ZipArchive::new(cursor).map_err(|e| anyhow::anyhow!("invalid KMZ archive: {e}"))?;
    let mut kml_name = None;
    for i in 0..archive.len() {
        let file = archive.by_index(i).context("read KMZ entry")?;
        if file.name().to_ascii_lowercase().ends_with(".kml") {
            kml_name = Some(file.name().to_string());
            break;
        }
    }
    let kml_name = kml_name.context("no .kml member in KMZ archive")?;
    let kml_bytes = {
        let mut file = archive.by_name(&kml_name).context("open KML in KMZ")?;
        let mut buf = Vec::new();
        std::io::Read::read_to_end(&mut file, &mut buf).context("read KML from KMZ")?;
        buf
    };
    parse_kml_linestring_routes(&kml_bytes)
}

pub fn serialize_kml_point(site: &KmlPointSite) -> serde_json::Map<String, serde_json::Value> {
    let mut map = serde_json::Map::new();
    map.insert(
        "name".to_string(),
        serde_json::Value::String(site.name.clone()),
    );
    map.insert(
        "lat".to_string(),
        serde_json::json!(site.lat),
    );
    map.insert(
        "lon".to_string(),
        serde_json::json!(site.lon),
    );
    map
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn linestring_lon_lat_to_internal() {
        let kml = br#"<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Test Route</name>
      <LineString><coordinates>-119.5,39.5,0 -118.0,40.0,0</coordinates></LineString>
    </Placemark>
  </Document>
</kml>"#;
        let (routes, skipped) = parse_kml_linestring_routes(kml).unwrap();
        assert_eq!(skipped, 0);
        assert_eq!(routes.len(), 1);
        assert_eq!(routes[0].name, "Test Route");
        assert!((routes[0].waypoints[0].lat - 39.5).abs() < 1e-6);
        assert!((routes[0].waypoints[0].lon - (-119.5)).abs() < 1e-6);
    }

    #[test]
    fn linestring_skips_point_placemarks() {
        let kml = br#"<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>P</name><Point><coordinates>-119.0,39.0,0</coordinates></Point></Placemark>
    <Placemark><name>R</name><LineString><coordinates>-119.0,39.0,0 -118.0,40.0,0</coordinates></LineString></Placemark>
  </Document>
</kml>"#;
        let (routes, skipped) = parse_kml_linestring_routes(kml).unwrap();
        assert_eq!(routes.len(), 1);
        assert_eq!(skipped, 1);
    }
}
