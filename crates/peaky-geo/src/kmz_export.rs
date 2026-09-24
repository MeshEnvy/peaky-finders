//! KMZ export: one folder per site (pin, viewshed overlay, access lines).

use std::io::{Cursor, Write};

use anyhow::{Context, Result};
use quick_xml::events::{BytesDecl, BytesEnd, BytesStart, BytesText, Event};
use quick_xml::Writer;
use zip::write::SimpleFileOptions;
use zip::ZipWriter;

/// 50% viewshed opacity in Google Earth (KML color aabbggrr).
const VIEWSHED_GROUND_OVERLAY_COLOR: &str = "80ffffff";

#[derive(Debug, Clone, PartialEq)]
pub struct KmzLatLonBox {
    pub north: f64,
    pub south: f64,
    pub east: f64,
    pub west: f64,
    pub rotation: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KmzViewshedOverlay {
    /// Path inside the KMZ archive, e.g. `files/my-site-viewshed.png`.
    pub href: String,
    pub bbox: KmzLatLonBox,
    pub png_bytes: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KmzAccessLine {
    pub name: String,
    pub coordinates: Vec<(f64, f64, f64)>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct KmzSiteFolder {
    pub folder_name: String,
    pub slug: String,
    pub tags: Vec<String>,
    pub pin_lat: f64,
    pub pin_lon: f64,
    pub pin_alt: f64,
    pub viewshed: Option<KmzViewshedOverlay>,
    pub access_lines: Vec<KmzAccessLine>,
}

pub fn build_kmz_bytes(document_name: &str, sites: &[KmzSiteFolder]) -> Result<Vec<u8>> {
    let kml = write_kml_document_bytes(document_name, sites)?;
    let mut archive = Cursor::new(Vec::new());
    {
        let mut zip = ZipWriter::new(&mut archive);
        let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);

        zip.start_file("doc.kml", options)
            .context("zip doc.kml")?;
        zip.write_all(&kml).context("write doc.kml")?;

        for site in sites {
            if let Some(vs) = &site.viewshed {
                let path = vs.href.strip_prefix("files/").unwrap_or(&vs.href);
                let entry = format!("files/{path}");
                zip.start_file(&entry, options)
                    .with_context(|| format!("zip {entry}"))?;
                zip.write_all(&vs.png_bytes)
                    .with_context(|| format!("write {entry}"))?;
            }
        }

        zip.finish().context("finish kmz zip")?;
    }
    Ok(archive.into_inner())
}

fn write_kml_document_bytes(document_name: &str, sites: &[KmzSiteFolder]) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    write_kml_document(document_name, sites, &mut buf)?;
    Ok(buf)
}

fn write_kml_document(
    document_name: &str,
    sites: &[KmzSiteFolder],
    mut out: impl Write,
) -> Result<()> {
    let mut writer = Writer::new_with_indent(&mut out, b' ', 2);
    writer.write_event(Event::Decl(BytesDecl::new("1.0", Some("UTF-8"), None)))?;

    let mut kml = BytesStart::new("kml");
    kml.push_attribute(("xmlns", "http://www.opengis.net/kml/2.2"));
    writer.write_event(Event::Start(kml))?;
    writer.write_event(Event::Start(BytesStart::new("Document")))?;
    write_text_element(&mut writer, "name", document_name)?;

    for site in sites {
        write_site_folder(&mut writer, site)?;
    }

    writer.write_event(Event::End(BytesEnd::new("Document")))?;
    writer.write_event(Event::End(BytesEnd::new("kml")))?;
    Ok(())
}

fn write_site_folder(writer: &mut Writer<impl Write>, site: &KmzSiteFolder) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new("Folder")))?;
    write_text_element(writer, "name", &site.folder_name)?;

    write_pin_placemark(writer, site)?;

    if let Some(vs) = &site.viewshed {
        write_viewshed_overlay(writer, vs)?;
    }

    for line in &site.access_lines {
        write_access_placemark(writer, line)?;
    }

    writer.write_event(Event::End(BytesEnd::new("Folder")))?;
    Ok(())
}

fn write_pin_placemark(writer: &mut Writer<impl Write>, site: &KmzSiteFolder) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new("Placemark")))?;
    write_text_element(writer, "name", &site.folder_name)?;

    writer.write_event(Event::Start(BytesStart::new("ExtendedData")))?;
    write_extended_data_value(writer, "role", "pin")?;
    write_extended_data_value(writer, "slug", &site.slug)?;
    let tags = site.tags.join(", ");
    write_extended_data_value(writer, "tags", &tags)?;
    writer.write_event(Event::End(BytesEnd::new("ExtendedData")))?;

    writer.write_event(Event::Start(BytesStart::new("Point")))?;
    let coords = format!("{},{},{}", site.pin_lon, site.pin_lat, site.pin_alt);
    write_text_element(writer, "coordinates", &coords)?;
    writer.write_event(Event::End(BytesEnd::new("Point")))?;

    writer.write_event(Event::End(BytesEnd::new("Placemark")))?;
    Ok(())
}

fn write_extended_data_value(writer: &mut Writer<impl Write>, name: &str, value: &str) -> Result<()> {
    let mut data = BytesStart::new("Data");
    data.push_attribute(("name", name));
    writer.write_event(Event::Start(data))?;
    write_text_element(writer, "value", value)?;
    writer.write_event(Event::End(BytesEnd::new("Data")))?;
    Ok(())
}

fn write_viewshed_overlay(writer: &mut Writer<impl Write>, vs: &KmzViewshedOverlay) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new("GroundOverlay")))?;
    write_text_element(writer, "name", "viewshed")?;

    writer.write_event(Event::Start(BytesStart::new("Icon")))?;
    write_text_element(writer, "href", &vs.href)?;
    writer.write_event(Event::End(BytesEnd::new("Icon")))?;

    write_text_element(writer, "color", VIEWSHED_GROUND_OVERLAY_COLOR)?;

    writer.write_event(Event::Start(BytesStart::new("LatLonBox")))?;
    write_text_element(writer, "north", &format!("{}", vs.bbox.north))?;
    write_text_element(writer, "south", &format!("{}", vs.bbox.south))?;
    write_text_element(writer, "east", &format!("{}", vs.bbox.east))?;
    write_text_element(writer, "west", &format!("{}", vs.bbox.west))?;
    if let Some(rot) = vs.bbox.rotation {
        write_text_element(writer, "rotation", &format!("{rot}"))?;
    }
    writer.write_event(Event::End(BytesEnd::new("LatLonBox")))?;

    writer.write_event(Event::End(BytesEnd::new("GroundOverlay")))?;
    Ok(())
}

fn write_access_placemark(writer: &mut Writer<impl Write>, line: &KmzAccessLine) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new("Placemark")))?;
    write_text_element(writer, "name", &line.name)?;

    writer.write_event(Event::Start(BytesStart::new("LineString")))?;
    let coord_text = line
        .coordinates
        .iter()
        .map(|(lon, lat, alt)| format!("{lon},{lat},{alt}"))
        .collect::<Vec<_>>()
        .join(" ");
    write_text_element(writer, "coordinates", &coord_text)?;
    writer.write_event(Event::End(BytesEnd::new("LineString")))?;

    writer.write_event(Event::End(BytesEnd::new("Placemark")))?;
    Ok(())
}

fn write_text_element(writer: &mut Writer<impl Write>, tag: &str, text: &str) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new(tag)))?;
    writer.write_event(Event::Text(BytesText::new(text)))?;
    writer.write_event(Event::End(BytesEnd::new(tag)))?;
    Ok(())
}

/// Safe archive file stem from a book slug.
pub fn viewshed_archive_href(slug: &str) -> String {
    let safe: String = slug
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("files/{safe}-viewshed.png")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use zip::ZipArchive;

    fn minimal_png() -> Vec<u8> {
        // 1×1 RGBA PNG
        vec![
            0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48,
            0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00,
            0x00, 0x1f, 0x15, 0xc4, 0x89, 0x00, 0x00, 0x00, 0x0a, 0x49, 0x44, 0x41, 0x54, 0x78,
            0x9c, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0d, 0x0a, 0x2d, 0xb4, 0x00,
            0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
        ]
    }

    #[test]
    fn kmz_contains_doc_kml_image_and_tag_extended_data() {
        let href = viewshed_archive_href("north-peak");
        let sites = vec![KmzSiteFolder {
            folder_name: "North Peak".to_string(),
            slug: "north-peak".to_string(),
            tags: vec!["reno-vegas".to_string(), "installed".to_string()],
            pin_lat: 39.5,
            pin_lon: -119.5,
            pin_alt: 2100.0,
            viewshed: Some(KmzViewshedOverlay {
                href: href.clone(),
                bbox: KmzLatLonBox {
                    north: 39.6,
                    south: 39.4,
                    east: -119.4,
                    west: -119.6,
                    rotation: None,
                },
                png_bytes: minimal_png(),
            }),
            access_lines: vec![KmzAccessLine {
                name: "hike".to_string(),
                coordinates: vec![(-119.51, 39.49, 2000.0), (-119.5, 39.5, 2100.0)],
            }],
        }];

        let kmz = build_kmz_bytes("test-export", &sites).unwrap();
        let cursor = Cursor::new(kmz);
        let mut zip = ZipArchive::new(cursor).unwrap();

        assert!(zip.by_name("doc.kml").is_ok());
        assert!(zip.by_name("files/north-peak-viewshed.png").is_ok());

        let mut doc = String::new();
        zip.by_name("doc.kml")
            .unwrap()
            .read_to_string(&mut doc)
            .unwrap();
        assert!(doc.contains("<Data name=\"tags\">"));
        assert!(doc.contains("<value>reno-vegas, installed</value>"));
        assert!(doc.contains("<name>North Peak</name>"));
        assert!(doc.contains("<value>pin</value>"));
        assert!(doc.contains("<name>viewshed</name>"));
        assert!(doc.contains("<color>80ffffff</color>"));
        assert!(doc.contains("<name>hike</name>"));
    }
}
