//! List GDB / GeoJSON land layers via GDAL CLI (ogrinfo / ogr2ogr).

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use geojson::{FeatureCollection, GeoJson};
use peaky_preset::slugify_files_segment;
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Debug, Clone, serde::Serialize)]
pub struct LandPreviewLayer {
    pub name: String,
    pub geometry: String,
    pub count: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<[f64; 4]>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct LandPreviewField {
    pub name: String,
    #[serde(rename = "type")]
    pub field_type: String,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct LandFieldValueRow {
    pub value: String,
    pub count: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bbox: Option<[f64; 4]>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct LandFieldValues {
    pub values: Vec<LandFieldValueRow>,
    pub truncated: bool,
}

const FIELD_VALUES_MAX: usize = 100;
const FIELD_VALUES_AOI_MAX: usize = 10_000;

/// Resolve ``data/...`` relative to ``project_dir`` (``.gdb`` dir or ``.geojson`` file).
pub fn resolve_land_data_path(project_dir: &Path, rel_path: &str) -> Result<PathBuf> {
    let root = project_dir
        .canonicalize()
        .with_context(|| format!("resolve project dir {}", project_dir.display()))?;
    let normalized = rel_path.trim().replace('\\', "/");
    if normalized.is_empty() {
        bail!("path is required");
    }
    if normalized.starts_with('/') || normalized.split('/').any(|part| part == "..") {
        bail!("path must be relative to the project directory");
    }
    let lower = normalized.to_ascii_lowercase();
    if !(lower.ends_with(".gdb") || lower.ends_with(".geojson") || lower.ends_with(".json")) {
        bail!("path must end with .gdb, .geojson, or .json");
    }
    let resolved = root
        .join(&normalized)
        .canonicalize()
        .with_context(|| format!("land data not found: {normalized}"))?;
    let data_canon = root.join("data").canonicalize().unwrap_or_else(|_| root.join("data"));
    if !resolved.starts_with(&data_canon) {
        bail!("path must be under data/");
    }
    Ok(resolved)
}

pub fn list_land_data_gdbs(project_dir: &Path) -> Result<Vec<String>> {
    let data_dir = project_dir.join("data");
    if !data_dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut out = Vec::new();
    for entry in std::fs::read_dir(&data_dir).context("read data/")? {
        let entry = entry?;
        let path = entry.path();
        if path.is_dir() && path.extension().is_none_or(|e| e == "gdb") {
            let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
            if name.to_ascii_lowercase().ends_with(".gdb") {
                out.push(format!("data/{name}"));
            }
        }
    }
    out.sort();
    Ok(out)
}

pub fn list_preview_layers(abs_path: &Path) -> Result<Vec<LandPreviewLayer>> {
    let lower = abs_path.to_string_lossy().to_ascii_lowercase();
    if lower.ends_with(".geojson") || lower.ends_with(".json") {
        return list_geojson_file_layers(abs_path);
    }
    list_gdb_layers(abs_path)
}

fn list_geojson_file_layers(path: &Path) -> Result<Vec<LandPreviewLayer>> {
    let text = std::fs::read_to_string(path).context("read GeoJSON")?;
    let geo: GeoJson = text.parse().context("parse GeoJSON")?;
    let fc = match geo {
        GeoJson::FeatureCollection(fc) => fc,
        GeoJson::Feature(f) => FeatureCollection {
            features: vec![f],
            bbox: None,
            foreign_members: None,
        },
        _ => bail!("GeoJSON must be FeatureCollection or Feature"),
    };
    let name = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("layer")
        .to_string();
    let count = fc.features.len() as u64;
    let geometry = fc
        .features
        .first()
        .and_then(|f| f.geometry.as_ref())
        .map(|g| geometry_label(&g.value))
        .unwrap_or_else(|| "layer".to_string());
    let bbox = fc
        .bbox
        .clone()
        .and_then(|b| bbox_vec_to_array(b))
        .or_else(|| bbox_from_features(&fc));
    Ok(vec![LandPreviewLayer {
        name,
        geometry,
        count,
        bbox,
    }])
}

pub fn layer_fields(abs_path: &Path, layer: &str) -> Result<Vec<LandPreviewField>> {
    let lower = abs_path.to_string_lossy().to_ascii_lowercase();
    if lower.ends_with(".geojson") || lower.ends_with(".json") {
        return fields_from_geojson_file(abs_path);
    }
    fields_from_gdb(abs_path, layer)
}

pub fn layer_field_values(abs_path: &Path, layer: &str, field: &str) -> Result<LandFieldValues> {
    let lower = abs_path.to_string_lossy().to_ascii_lowercase();
    if lower.ends_with(".geojson") || lower.ends_with(".json") {
        return field_values_from_geojson(abs_path, field, FIELD_VALUES_MAX, true);
    }
    field_values_from_gdb(abs_path, layer, field)
}

/// Distinct field values from an in-memory GeoJSON FeatureCollection (e.g. after AOI clip).
pub fn field_values_from_geojson_value(value: &Value, field: &str) -> Result<LandFieldValues> {
    field_values_from_geojson_value_limited(value, field, FIELD_VALUES_AOI_MAX, false)
}

pub fn read_layer_geojson_value(abs_path: &Path, layer: &str) -> Result<Value> {
    let lower = abs_path.to_string_lossy().to_ascii_lowercase();
    if lower.ends_with(".geojson") || lower.ends_with(".json") {
        let text = std::fs::read_to_string(abs_path).context("read GeoJSON")?;
        return serde_json::from_str(&text).context("parse GeoJSON JSON");
    }
    let out = run_ogr2ogr_stdout(abs_path, layer)?;
    serde_json::from_slice(&out).context("parse ogr2ogr GeoJSON")
}

/// Read layer GeoJSON from ``.peaky/cache/land/_export/`` when available (avoids ogr2ogr).
pub fn read_layer_geojson_cached(
    project_dir: &Path,
    rel_path: &str,
    layer: &str,
) -> Result<Value> {
    let lower = rel_path.trim().to_ascii_lowercase();
    if lower.ends_with(".geojson") || lower.ends_with(".json") {
        let abs = resolve_land_data_path(project_dir, rel_path)?;
        let text = std::fs::read_to_string(&abs).context("read GeoJSON")?;
        return serde_json::from_str(&text).context("parse GeoJSON JSON");
    }
    let cache_path = project_dir
        .join(".peaky/cache/land/_export")
        .join(slugify_files_segment(rel_path))
        .join(format!("{}.geojson", slugify_files_segment(layer)));
    if cache_path.is_file() {
        let text = std::fs::read_to_string(&cache_path).context("read cached layer GeoJSON")?;
        return serde_json::from_str(&text).context("parse cached layer GeoJSON");
    }
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    read_layer_geojson_value(&abs, layer)
}

fn list_gdb_layers(gdb_path: &Path) -> Result<Vec<LandPreviewLayer>> {
    #[derive(Deserialize)]
    struct OgrInfoRoot {
        #[serde(default)]
        layers: Vec<OgrLayer>,
    }
    #[derive(Deserialize)]
    struct OgrLayer {
        name: String,
        #[serde(default)]
        geometryFields: Vec<OgrGeomField>,
        #[serde(default)]
        featureCount: u64,
    }
    #[derive(Deserialize)]
    struct OgrGeomField {
        #[serde(rename = "type")]
        geom_type: String,
        #[serde(default)]
        extent: Vec<f64>,
    }

    let json_bytes = run_ogrinfo_json(gdb_path, None)?;
    let root: OgrInfoRoot = serde_json::from_slice(&json_bytes).context("parse ogrinfo JSON")?;
    Ok(root
        .layers
        .into_iter()
        .map(|layer| {
            let geom = layer.geometryFields.first();
            let geometry = geom
                .map(|g| g.geom_type.clone())
                .unwrap_or_else(|| "layer".to_string());
            let bbox = geom.and_then(|g| extent_to_bbox(&g.extent));
            LandPreviewLayer {
                name: layer.name,
                geometry,
                count: layer.featureCount,
                bbox,
            }
        })
        .collect())
}

fn fields_from_gdb(gdb_path: &Path, layer: &str) -> Result<Vec<LandPreviewField>> {
    #[derive(Deserialize)]
    struct OgrInfoRoot {
        #[serde(default)]
        layers: Vec<OgrLayer>,
    }
    #[derive(Deserialize)]
    struct OgrLayer {
        name: String,
        #[serde(default)]
        fields: Vec<OgrField>,
    }
    #[derive(Deserialize)]
    struct OgrField {
        name: String,
        #[serde(rename = "type")]
        field_type: String,
    }

    let json_bytes = run_ogrinfo_json(gdb_path, Some(layer))?;
    let root: OgrInfoRoot = serde_json::from_slice(&json_bytes).context("parse ogrinfo JSON")?;
    let fields = root
        .layers
        .into_iter()
        .find(|l| l.name == layer)
        .map(|l| l.fields)
        .unwrap_or_default();
    Ok(fields
        .into_iter()
        .filter(|f| f.name != "SHAPE" && f.name != "SHAPE_Length" && f.name != "SHAPE_Area")
        .map(|f| LandPreviewField {
            name: f.name,
            field_type: f.field_type,
        })
        .collect())
}

fn fields_from_geojson_file(path: &Path) -> Result<Vec<LandPreviewField>> {
    let text = std::fs::read_to_string(path).context("read GeoJSON")?;
    let value: Value = serde_json::from_str(&text).context("parse GeoJSON")?;
    let features = value
        .get("features")
        .and_then(|v| v.as_array())
        .context("GeoJSON missing features")?;
    let mut names = std::collections::BTreeSet::new();
    for feat in features {
        if let Some(props) = feat.get("properties").and_then(|p| p.as_object()) {
            for key in props.keys() {
                names.insert(key.clone());
            }
        }
    }
    Ok(names
        .into_iter()
        .map(|name| LandPreviewField {
            name,
            field_type: "String".to_string(),
        })
        .collect())
}

fn field_values_from_gdb(gdb_path: &Path, layer: &str, field: &str) -> Result<LandFieldValues> {
    validate_sql_ident(field, "field")?;
    validate_sql_ident(layer, "layer")?;
    let sql = format!(
        "SELECT \"{field}\", COUNT(*) FROM \"{layer}\" WHERE \"{field}\" IS NOT NULL GROUP BY \"{field}\""
    );
    let out = Command::new("ogrinfo")
        .arg("-q")
        .arg("-al")
        .arg("-fields=YES")
        .arg("-dialect")
        .arg("SQLITE")
        .arg("-sql")
        .arg(sql)
        .arg(gdb_path)
        .output()
        .context("spawn ogrinfo")?;
    if !out.status.success() {
        bail!(
            "ogrinfo failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    parse_ogrinfo_grouped_field_values(&String::from_utf8_lossy(&out.stdout), field)
}

fn field_values_from_geojson(
    path: &Path,
    field: &str,
    max: usize,
    sort_by_count: bool,
) -> Result<LandFieldValues> {
    let text = std::fs::read_to_string(path).context("read GeoJSON")?;
    let value: Value = serde_json::from_str(&text).context("parse GeoJSON")?;
    field_values_from_geojson_value_limited(&value, field, max, sort_by_count)
}

fn field_values_from_geojson_value_limited(
    value: &Value,
    field: &str,
    max: usize,
    sort_by_count: bool,
) -> Result<LandFieldValues> {
    let features = value
        .get("features")
        .and_then(|v| v.as_array())
        .context("GeoJSON missing features")?;
    let mut counts = std::collections::HashMap::<String, (u64, Option<[f64; 4]>)>::new();
    for feat in features {
        let Some(props) = feat.get("properties") else {
            continue;
        };
        let Some(v) = props.get(field).and_then(value_as_string) else {
            continue;
        };
        let feat_bbox = feat
            .get("geometry")
            .and_then(|g| serde_json::from_value::<geojson::Geometry>(g.clone()).ok())
            .and_then(|geom| geometry_bbox(&geom.value));
        let entry = counts.entry(v).or_insert((0, None));
        entry.0 += 1;
        entry.1 = merge_bbox(entry.1, feat_bbox);
    }
    let rows: Vec<LandFieldValueRow> = counts
        .into_iter()
        .map(|(value, (count, bbox))| LandFieldValueRow {
            value,
            count,
            bbox,
        })
        .collect();
    Ok(finalize_field_value_rows(rows, max, sort_by_count))
}

fn merge_bbox(a: Option<[f64; 4]>, b: Option<[f64; 4]>) -> Option<[f64; 4]> {
    match (a, b) {
        (None, None) => None,
        (Some(b), None) | (None, Some(b)) => Some(b),
        (Some(a), Some(b)) => Some([
            a[0].min(b[0]),
            a[1].min(b[1]),
            a[2].max(b[2]),
            a[3].max(b[3]),
        ]),
    }
}

fn validate_sql_ident(name: &str, label: &str) -> Result<()> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        bail!("invalid {label} name");
    }
    Ok(())
}

fn finalize_field_value_rows(
    mut rows: Vec<LandFieldValueRow>,
    max: usize,
    sort_by_count: bool,
) -> LandFieldValues {
    if sort_by_count {
        rows.sort_by(|a, b| b.count.cmp(&a.count).then_with(|| a.value.cmp(&b.value)));
    } else {
        rows.sort_by(|a, b| a.value.cmp(&b.value));
    }
    let truncated = rows.len() > max;
    if truncated {
        rows.truncate(max);
    }
    LandFieldValues {
        values: rows,
        truncated,
    }
}

fn finalize_field_values(
    counts: std::collections::HashMap<String, u64>,
    max: usize,
    sort_by_count: bool,
) -> LandFieldValues {
    let rows: Vec<LandFieldValueRow> = counts
        .into_iter()
        .map(|(value, count)| LandFieldValueRow {
            value,
            count,
            bbox: None,
        })
        .collect();
    finalize_field_value_rows(rows, max, sort_by_count)
}

fn parse_ogrinfo_grouped_field_values(text: &str, field: &str) -> Result<LandFieldValues> {
    let field_needle = format!("{field} (");
    let count_needle = "COUNT(*) (Integer) = ";
    let mut counts = std::collections::HashMap::<String, u64>::new();
    let mut current_value: Option<String> = None;

    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix(&field_needle) {
            if let Some(val) = rest.split('=').nth(1) {
                let v = val.trim().trim_matches(')').trim();
                if !v.is_empty() {
                    current_value = Some(v.to_string());
                }
            }
        } else if let Some(count_str) = trimmed.strip_prefix(count_needle) {
            if let Ok(count) = count_str.trim().parse::<u64>() {
                if let Some(value) = current_value.take() {
                    counts.insert(value, count);
                }
            }
        }
    }

    Ok(finalize_field_values(counts, FIELD_VALUES_MAX, true))
}

fn run_ogrinfo_json(path: &Path, layer: Option<&str>) -> Result<Vec<u8>> {
    let mut cmd = Command::new("ogrinfo");
    cmd.arg("-so").arg("-json");
    if let Some(layer) = layer {
        cmd.arg(path).arg(layer);
    } else {
        cmd.arg(path);
    }
    let out = cmd.output().context("spawn ogrinfo")?;
    if !out.status.success() {
        bail!(
            "ogrinfo failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    Ok(out.stdout)
}

fn run_ogr2ogr_stdout(path: &Path, layer: &str) -> Result<Vec<u8>> {
    let out = Command::new("ogr2ogr")
        .arg("-f")
        .arg("GeoJSON")
        .arg("/vsistdout/")
        .arg(path)
        .arg(layer)
        .arg("-t_srs")
        .arg("EPSG:4326")
        .output()
        .context("spawn ogr2ogr")?;
    if !out.status.success() {
        bail!(
            "ogr2ogr failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
    Ok(out.stdout)
}

fn geometry_label(value: &geojson::Value) -> String {
    match value {
        geojson::Value::Point(_) => "Point".to_string(),
        geojson::Value::MultiPoint(_) => "MultiPoint".to_string(),
        geojson::Value::LineString(_) => "LineString".to_string(),
        geojson::Value::MultiLineString(_) => "MultiLineString".to_string(),
        geojson::Value::Polygon(_) => "Polygon".to_string(),
        geojson::Value::MultiPolygon(_) => "MultiPolygon".to_string(),
        geojson::Value::GeometryCollection(_) => "GeometryCollection".to_string(),
    }
}

fn bbox_from_features(fc: &FeatureCollection) -> Option<[f64; 4]> {
    let mut west = f64::INFINITY;
    let mut south = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut north = f64::NEG_INFINITY;
    for feat in &fc.features {
        if let Some(geom) = &feat.geometry {
            if let Some(b) = geometry_bbox(&geom.value) {
                west = west.min(b[0]);
                south = south.min(b[1]);
                east = east.max(b[2]);
                north = north.max(b[3]);
            }
        }
    }
    if west.is_finite() {
        Some([west, south, east, north])
    } else {
        None
    }
}

fn geometry_bbox(value: &geojson::Value) -> Option<[f64; 4]> {
    use geojson::Value as G;
    let points: Vec<[f64; 2]> = match value {
        G::Point(c) => vec![[c[0], c[1]]],
        G::MultiPoint(ps) => ps.iter().map(|c| [c[0], c[1]]).collect(),
        G::LineString(ls) => ls.iter().map(|c| [c[0], c[1]]).collect(),
        G::MultiLineString(m) => m.iter().flat_map(|ls| ls.iter().map(|c| [c[0], c[1]])).collect(),
        G::Polygon(rings) => rings
            .iter()
            .flat_map(|ring| ring.iter().map(|c| [c[0], c[1]]))
            .collect(),
        G::MultiPolygon(pols) => pols
            .iter()
            .flat_map(|poly| {
                poly.iter()
                    .flat_map(|ring| ring.iter().map(|c| [c[0], c[1]]))
            })
            .collect(),
        G::GeometryCollection(geoms) => {
            let mut merged: Option<[f64; 4]> = None;
            for g in geoms {
                if let Some(b) = geometry_bbox(&g.value) {
                    merged = Some(match merged {
                        None => b,
                        Some(a) => [
                            a[0].min(b[0]),
                            a[1].min(b[1]),
                            a[2].max(b[2]),
                            a[3].max(b[3]),
                        ],
                    });
                }
            }
            return merged;
        }
    };
    if points.is_empty() {
        return None;
    }
    let mut west = f64::INFINITY;
    let mut south = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut north = f64::NEG_INFINITY;
    for [x, y] in points {
        west = west.min(x);
        south = south.min(y);
        east = east.max(x);
        north = north.max(y);
    }
    Some([west, south, east, north])
}

fn extent_to_bbox(extent: &[f64]) -> Option<[f64; 4]> {
    if extent.len() == 4 {
        Some([extent[0], extent[1], extent[2], extent[3]])
    } else {
        None
    }
}

fn bbox_vec_to_array(bbox: Vec<f64>) -> Option<[f64; 4]> {
    if bbox.len() == 4 {
        Some([bbox[0], bbox[1], bbox[2], bbox[3]])
    } else {
        None
    }
}

fn value_as_string(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::Bool(b) => Some(b.to_string()),
        Value::Number(n) => Some(n.to_string()),
        Value::String(s) => {
            let t = s.trim();
            if t.is_empty() {
                None
            } else {
                Some(t.to_string())
            }
        }
        other => Some(other.to_string()),
    }
}

pub fn filter_geojson_preview(
    mut geojson: Value,
    include: &[peaky_preset::LandAttributeFilter],
    exclude: &[peaky_preset::LandAttributeFilter],
) -> Value {
    let Some(features) = geojson.get_mut("features").and_then(|v| v.as_array_mut()) else {
        return geojson;
    };
    features.retain(|feat| {
        let Some(props) = feat.get("properties").and_then(|p| p.as_object()) else {
            return include.is_empty();
        };
        crate::land_filter::matches_land_attribute_filters(props, include, exclude)
    });
    geojson
}

pub fn preview_layers_json(layers: &[LandPreviewLayer]) -> Value {
    json!({
        "layers": layers.iter().map(|l| json!({
            "name": l.name,
            "geometry": l.geometry,
            "count": l.count,
            "bbox": l.bbox,
        })).collect::<Vec<_>>()
    })
}

/// Export one GDB layer to GeoJSON on disk (EPSG:4326).
pub fn export_gdb_layer_to_geojson(gdb: &Path, layer: &str, dest: &Path) -> Result<()> {
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let status = Command::new("ogr2ogr")
        .arg("-f")
        .arg("GeoJSON")
        .arg(dest)
        .arg(gdb)
        .arg(layer)
        .arg("-t_srs")
        .arg("EPSG:4326")
        .status()
        .context("spawn ogr2ogr")?;
    if !status.success() {
        bail!("ogr2ogr failed with status {status}");
    }
    Ok(())
}
