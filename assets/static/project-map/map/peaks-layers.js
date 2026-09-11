// @ts-check

import {
  PEAKS_ACCESS_LINE,
  PEAKS_ACCESS_SOURCE,
  PEAKS_ICON_ID,
  PEAKS_ICON_URL,
  PEAKS_ROAD_CIRCLE,
  PEAKS_ROAD_SOURCE,
  PEAKS_SOURCE,
  PEAKS_SYMBOL,
  SITES_CIRCLE,
} from '../constants.js'

/** @param {object[]} peaks */
export function peaksGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter((peak) => Number.isFinite(peak.lat) && Number.isFinite(peak.lon))
      .map((peak) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [peak.lon, peak.lat] },
        properties: {
          slug: peak.slug || '',
          name: peak.name || '',
          source: peak.source || '',
          elev_m: peak.elev_m ?? null,
        },
      })),
  }
}

/** @param {object} peak */
function hikePathCoordinates(peak) {
  const profile = peak.hike?.profile
  if (Array.isArray(profile) && profile.length >= 2) {
    return profile
      .filter((p) => Number.isFinite(p.lon) && Number.isFinite(p.lat))
      .map((p) => [p.lon, p.lat])
  }
  if (
    Number.isFinite(peak.road_lat) &&
    Number.isFinite(peak.road_lon) &&
    Number.isFinite(peak.lat) &&
    Number.isFinite(peak.lon)
  ) {
    return [
      [peak.road_lon, peak.road_lat],
      [peak.lon, peak.lat],
    ]
  }
  return null
}

/** @param {object[]} peaks */
export function peaksRoadGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter((peak) => Number.isFinite(peak.road_lat) && Number.isFinite(peak.road_lon))
      .map((peak) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [peak.road_lon, peak.road_lat] },
        properties: {
          slug: peak.slug || '',
          road_m: peak.road_m ?? null,
        },
      })),
  }
}

/** @param {object[]} peaks */
export function peaksAccessGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter((peak) => hikePathCoordinates(peak))
      .map((peak) => ({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: hikePathCoordinates(peak),
        },
        properties: {
          slug: peak.slug || '',
          road_m: peak.road_m ?? null,
          hike_m: peak.hike_m ?? null,
          difficulty: peak.hike?.difficulty ?? null,
        },
      })),
  }
}

/** @param {maplibregl.Map} map */
async function ensurePeaksIcon(map) {
  if (map.hasImage(PEAKS_ICON_ID)) return
  const { data } = await map.loadImage(PEAKS_ICON_URL)
  map.addImage(PEAKS_ICON_ID, data, { pixelRatio: 2 })
}

/** @param {maplibregl.Map} map */
export async function ensurePeaksLayers(map) {
  await ensurePeaksIcon(map)
  if (!map.getSource(PEAKS_ACCESS_SOURCE)) {
    map.addSource(PEAKS_ACCESS_SOURCE, { type: 'geojson', data: peaksAccessGeoJson([]) })
  }
  if (!map.getSource(PEAKS_ROAD_SOURCE)) {
    map.addSource(PEAKS_ROAD_SOURCE, { type: 'geojson', data: peaksRoadGeoJson([]) })
  }
  if (!map.getSource(PEAKS_SOURCE)) {
    map.addSource(PEAKS_SOURCE, { type: 'geojson', data: peaksGeoJson([]) })
  }
  if (!map.getLayer(PEAKS_ACCESS_LINE)) {
    map.addLayer(
      {
        id: PEAKS_ACCESS_LINE,
        type: 'line',
        source: PEAKS_ACCESS_SOURCE,
        paint: {
          'line-color': '#22c55e',
          'line-width': 3,
          'line-opacity': 0.95,
        },
        layout: {
          'line-cap': 'round',
          'line-join': 'round',
        },
      },
      SITES_CIRCLE,
    )
  }
  if (!map.getLayer(PEAKS_SYMBOL)) {
    map.addLayer(
      {
        id: PEAKS_SYMBOL,
        type: 'symbol',
        source: PEAKS_SOURCE,
        layout: {
          'icon-image': PEAKS_ICON_ID,
          'icon-size': ['interpolate', ['linear'], ['zoom'], 8, 0.18, 12, 0.28, 16, 0.38],
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
      },
      SITES_CIRCLE,
    )
  }
  if (!map.getLayer(PEAKS_ROAD_CIRCLE)) {
    map.addLayer(
      {
        id: PEAKS_ROAD_CIRCLE,
        type: 'circle',
        source: PEAKS_ROAD_SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 4, 14, 7, 17, 10],
          'circle-color': '#fbbf24',
          'circle-stroke-color': '#0f172a',
          'circle-stroke-width': 1.5,
          'circle-opacity': 0.95,
        },
      },
      PEAKS_SYMBOL,
    )
  }
}

/** @param {maplibregl.Map} map @param {object[]} peaks */
export function setPeaksLayerData(map, peaks) {
  if (map.getSource(PEAKS_SOURCE)) {
    map.getSource(PEAKS_SOURCE).setData(peaksGeoJson(peaks))
  }
  if (map.getSource(PEAKS_ACCESS_SOURCE)) {
    map.getSource(PEAKS_ACCESS_SOURCE).setData(peaksAccessGeoJson(peaks))
  }
  if (map.getSource(PEAKS_ROAD_SOURCE)) {
    map.getSource(PEAKS_ROAD_SOURCE).setData(peaksRoadGeoJson(peaks))
  }
}
