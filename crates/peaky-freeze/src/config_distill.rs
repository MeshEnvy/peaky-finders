//! Rewrite `land` in a merged preset mapping for frozen export.

use anyhow::{Context, Result};
use peaky_preset::{LandLayerRole, LandLayerStyleValue};
use serde_yaml::{Mapping, Value};

use crate::{
    FreezeLayerSummary, FREEZE_GEOJSON_NAME, FREEZE_GEOJSON_LAYER_NAME, FREEZE_LAYER_KEY_FIELD,
    FREEZE_SOURCE_ID,
};

fn yaml_key(s: &str) -> Value {
    Value::from(s)
}

fn role_to_yaml(role: Option<LandLayerRole>) -> Option<Value> {
    role.map(|r| match r {
        LandLayerRole::Aoi => Value::from("aoi"),
        LandLayerRole::Include => Value::from("include"),
        LandLayerRole::Exclude => Value::from("exclude"),
    })
}

fn style_to_yaml(style: &LandLayerStyleValue) -> Value {
    match style {
        LandLayerStyleValue::Single(s) => {
            let mut m = Mapping::new();
            m.insert(yaml_key("color"), Value::from(s.color.clone()));
            m.insert(yaml_key("opacity"), Value::from(s.opacity));
            Value::Mapping(m)
        }
        LandLayerStyleValue::Map(map) => {
            let mut outer = Mapping::new();
            for (k, s) in map {
                let mut m = Mapping::new();
                m.insert(yaml_key("color"), Value::from(s.color.clone()));
                m.insert(yaml_key("opacity"), Value::from(s.opacity));
                outer.insert(Value::from(k.clone()), Value::Mapping(m));
            }
            Value::Mapping(outer)
        }
    }
}

fn frozen_layer_entry(summary: &FreezeLayerSummary, preset: &peaky_preset::Preset) -> Result<Value> {
    let source = preset.land.sources.get(&summary.source_id).with_context(|| {
        format!("land source {} missing from preset", summary.source_id)
    })?;
    let layer = source
        .layers
        .iter()
        .find(|l| l.layer_key() == summary.layer_key)
        .with_context(|| {
            format!(
                "layer {} / {} missing from preset",
                summary.source_id, summary.layer_key
            )
        })?;

    let mut entry = Mapping::new();
    entry.insert(yaml_key("name"), Value::from(FREEZE_GEOJSON_LAYER_NAME));
    if let Some(id) = &layer.id {
        entry.insert(yaml_key("id"), Value::from(id.clone()));
    }
    if let Some(role) = role_to_yaml(layer.role) {
        entry.insert(yaml_key("role"), role);
    }

    let mut include_filter = Mapping::new();
    include_filter.insert(yaml_key("field"), Value::from(FREEZE_LAYER_KEY_FIELD));
    include_filter.insert(
        yaml_key("values"),
        Value::Sequence(vec![Value::from(summary.layer_key.clone())]),
    );
    entry.insert(
        yaml_key("include"),
        Value::Sequence(vec![Value::Mapping(include_filter)]),
    );
    entry.insert(yaml_key("exclude"), Value::Sequence(vec![]));

    if let Some(style) = &layer.style {
        entry.insert(yaml_key("style"), style_to_yaml(style));
    }

    Ok(Value::Mapping(entry))
}

pub fn distill_preset_mapping(
    doc: &mut Mapping,
    layer_summaries: &[FreezeLayerSummary],
) -> Result<()> {
    let preset: peaky_preset::Preset = {
        let value = Value::Mapping(doc.clone());
        serde_yaml::from_value(value).context("parse preset for land distill")?
    };

    let mut layers = Vec::new();
    for summary in layer_summaries {
        layers.push(frozen_layer_entry(summary, &preset)?);
    }

    let mut source = Mapping::new();
    source.insert(yaml_key("path"), Value::from(FREEZE_GEOJSON_NAME));
    source.insert(yaml_key("enabled"), Value::from(true));
    source.insert(yaml_key("layers"), Value::Sequence(layers));

    let mut sources = Mapping::new();
    sources.insert(yaml_key(FREEZE_SOURCE_ID), Value::Mapping(source));

    let mut land = Mapping::new();
    land.insert(yaml_key("sources"), Value::Mapping(sources));

    doc.remove(&yaml_key("land"));
    doc.insert(yaml_key("land"), Value::Mapping(land));

    if doc
        .get(&yaml_key("links"))
        .and_then(|v| v.as_sequence())
        .is_some_and(|s| s.is_empty())
    {
        doc.remove(&yaml_key("links"));
    }

    Ok(())
}
