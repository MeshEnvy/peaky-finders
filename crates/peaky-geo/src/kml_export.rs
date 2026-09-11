//! Write KML Point placemarks for onX / Gaia import.

use std::io::Write;

use anyhow::Result;
use quick_xml::events::{BytesDecl, BytesEnd, BytesStart, BytesText, Event};
use quick_xml::Writer;

#[derive(Debug, Clone, PartialEq)]
pub struct KmlPointPlacemark {
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub altitude: f64,
    pub description: Option<String>,
}

pub fn write_kml_point_document(
    document_name: &str,
    placemarks: &[KmlPointPlacemark],
    mut out: impl Write,
) -> Result<()> {
    let mut writer = Writer::new_with_indent(&mut out, b' ', 2);
    writer.write_event(Event::Decl(BytesDecl::new("1.0", Some("UTF-8"), None)))?;

    let mut kml = BytesStart::new("kml");
    kml.push_attribute(("xmlns", "http://www.opengis.net/kml/2.2"));
    writer.write_event(Event::Start(kml))?;
    writer.write_event(Event::Start(BytesStart::new("Document")))?;

    write_text_element(&mut writer, "name", document_name)?;

    for placemark in placemarks {
        write_placemark(&mut writer, placemark)?;
    }

    writer.write_event(Event::End(BytesEnd::new("Document")))?;
    writer.write_event(Event::End(BytesEnd::new("kml")))?;
    Ok(())
}

fn write_text_element(writer: &mut Writer<impl Write>, tag: &str, text: &str) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new(tag)))?;
    writer.write_event(Event::Text(BytesText::new(text)))?;
    writer.write_event(Event::End(BytesEnd::new(tag)))?;
    Ok(())
}

fn write_placemark(writer: &mut Writer<impl Write>, placemark: &KmlPointPlacemark) -> Result<()> {
    writer.write_event(Event::Start(BytesStart::new("Placemark")))?;
    write_text_element(writer, "name", &placemark.name)?;

    if let Some(description) = &placemark.description {
        if !description.is_empty() {
            write_text_element(writer, "description", description)?;
        }
    }

    writer.write_event(Event::Start(BytesStart::new("Point")))?;
    let coords = format!(
        "{},{},{}",
        placemark.lon, placemark.lat, placemark.altitude
    );
    write_text_element(writer, "coordinates", &coords)?;
    writer.write_event(Event::End(BytesEnd::new("Point")))?;

    writer.write_event(Event::End(BytesEnd::new("Placemark")))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kml_import::parse_kml_point_placemarks;

    #[test]
    fn round_trip_point_placemarks() {
        let placemarks = vec![
            KmlPointPlacemark {
                name: "North Peak (north-peak)".to_string(),
                lat: 39.5,
                lon: -119.5,
                altitude: 2100.0,
                description: Some("slug: north-peak\ntags: reno-vegas".to_string()),
            },
            KmlPointPlacemark {
                name: "South Vista (south-vista)".to_string(),
                lat: 37.2,
                lon: -117.1,
                altitude: 0.0,
                description: None,
            },
        ];

        let mut buf = Vec::new();
        write_kml_point_document("reno-vegas", &placemarks, &mut buf).unwrap();

        let (parsed, skipped) = parse_kml_point_placemarks(&buf).unwrap();
        assert_eq!(skipped, 0);
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].name, "North Peak (north-peak)");
        assert!((parsed[0].lat - 39.5).abs() < 1e-6);
        assert!((parsed[0].lon - (-119.5)).abs() < 1e-6);
        assert_eq!(parsed[1].name, "South Vista (south-vista)");
    }

    #[test]
    fn xml_escapes_special_characters_in_name() {
        let placemarks = vec![KmlPointPlacemark {
            name: "A & B <test> (slug)".to_string(),
            lat: 39.0,
            lon: -119.0,
            altitude: 0.0,
            description: None,
        }];

        let mut buf = Vec::new();
        write_kml_point_document("test", &placemarks, &mut buf).unwrap();

        let text = String::from_utf8(buf).unwrap();
        assert!(text.contains("A &amp; B &lt;test&gt; (slug)"));

        let (parsed, _) = parse_kml_point_placemarks(text.as_bytes()).unwrap();
        assert_eq!(parsed[0].name, "A & B <test> (slug)");
    }
}
