// @ts-check

/** @param {import('geojson').FeatureCollection|null|undefined} geojson */
export function rfLinesGeoJsonWithLabels(geojson) {
  if (!geojson || !geojson.features) return geojson
  return {
    type: geojson.type || 'FeatureCollection',
    features: geojson.features.map((feature) => {
      const props = feature.properties || {}
      const dist =
        props.distance_km != null && !Number.isNaN(Number(props.distance_km))
          ? `${Number(props.distance_km).toFixed(1)} km`
          : null
      const bearing =
        props.bearing_deg != null ? `${Math.round(Number(props.bearing_deg))}°` : ''
      const label = dist && bearing ? `${dist} · ${bearing}` : dist || bearing || ''
      return { ...feature, properties: { ...props, label } }
    }),
  }
}
