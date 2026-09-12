// @ts-check

import {
  PEAKS_ACCESS_LINE,
  PEAKS_ACCESS_SOURCE,
  PEAKS_CURSOR_CIRCLE,
  PEAKS_CURSOR_SOURCE,
  PEAKS_ICON_ID,
  PEAKS_ICON_URL,
  PEAKS_JEEP_LINE,
  PEAKS_JEEP_SOURCE,
  PEAKS_PAVED_CIRCLE,
  PEAKS_PAVED_SOURCE,
  PEAKS_RING_CIRCLE,
  PEAKS_ROAD_CIRCLE,
  PEAKS_ROAD_SOURCE,
  PEAKS_SOURCE,
  PEAKS_SYMBOL,
  DRAFT_LINKS_LAYER,
  LINKS_LAYER,
  SITES_CIRCLE,
} from '../constants.js'

/** Insert under RF links and site pins when those layers already exist. */
function beforeEstablished(map) {
  for (const id of [LINKS_LAYER, DRAFT_LINKS_LAYER, SITES_CIRCLE]) {
    if (map.getLayer(id)) return id
  }
  return undefined
}

/** @param {maplibregl.Map} map @param {object} spec @param {string|undefined} beforeId */
function addLayerBefore(map, spec, beforeId) {
  if (beforeId) map.addLayer(spec, beforeId)
  else map.addLayer(spec)
}

const DIFFICULTY_RANK = { easy: 0, medium: 1, difficult: 2, extreme: 3 }

/** @param {string|null|undefined} label */
function difficultyRank(label) {
  return DIFFICULTY_RANK[String(label || '').toLowerCase()] ?? -1
}

/** Worse of hike vs jeep (grade vs road class). */
export function accessDifficulty(peak) {
  if (peak?.access_difficulty) return String(peak.access_difficulty).toLowerCase()
  const hike = peak?.hike_difficulty || peak?.hike?.difficulty
  const jeep = peak?.jeep_difficulty || peak?.jeep?.difficulty
  if (difficultyRank(hike) >= difficultyRank(jeep)) return hike ? String(hike).toLowerCase() : ''
  return jeep ? String(jeep).toLowerCase() : ''
}

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
          access_difficulty: accessDifficulty(peak),
        },
      })),
  }
}

/** @param {object} peak */
function hikePathCoordinates(peak) {
  const profile = peak.hike?.profile
  if (!Array.isArray(profile) || profile.length < 2) return null
  return profile
    .filter((p) => Number.isFinite(p.lon) && Number.isFinite(p.lat))
    .map((p) => [p.lon, p.lat])
}

/** @param {object} peak */
function jeepPathCoordinates(peak) {
  const profile = peak.jeep?.profile
  if (!Array.isArray(profile) || profile.length < 2) return null
  return profile
    .filter((p) => Number.isFinite(p.lon) && Number.isFinite(p.lat))
    .map((p) => [p.lon, p.lat])
}

/** @param {object[]} peaks */
export function peaksRoadGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter(
        (peak) =>
          hikePathCoordinates(peak) &&
          Number.isFinite(peak.road_lat) &&
          Number.isFinite(peak.road_lon),
      )
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
export function peaksPavedGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter(
        (peak) =>
          jeepPathCoordinates(peak) &&
          Number.isFinite(peak.paved_lat) &&
          Number.isFinite(peak.paved_lon),
      )
      .map((peak) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [peak.paved_lon, peak.paved_lat] },
        properties: {
          slug: peak.slug || '',
          jeep_m: peak.jeep_m ?? null,
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

/** @param {object[]} peaks */
export function peaksJeepGeoJson(peaks) {
  return {
    type: 'FeatureCollection',
    features: peaks
      .filter((peak) => jeepPathCoordinates(peak))
      .map((peak) => ({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: jeepPathCoordinates(peak),
        },
        properties: {
          slug: peak.slug || '',
          jeep_m: peak.jeep_m ?? null,
          difficulty: peak.jeep?.difficulty ?? null,
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
  if (!map.getSource(PEAKS_JEEP_SOURCE)) {
    map.addSource(PEAKS_JEEP_SOURCE, { type: 'geojson', data: peaksJeepGeoJson([]) })
  }
  if (!map.getSource(PEAKS_ACCESS_SOURCE)) {
    map.addSource(PEAKS_ACCESS_SOURCE, { type: 'geojson', data: peaksAccessGeoJson([]) })
  }
  if (!map.getSource(PEAKS_PAVED_SOURCE)) {
    map.addSource(PEAKS_PAVED_SOURCE, { type: 'geojson', data: peaksPavedGeoJson([]) })
  }
  if (!map.getSource(PEAKS_ROAD_SOURCE)) {
    map.addSource(PEAKS_ROAD_SOURCE, { type: 'geojson', data: peaksRoadGeoJson([]) })
  }
  if (!map.getSource(PEAKS_SOURCE)) {
    map.addSource(PEAKS_SOURCE, { type: 'geojson', data: peaksGeoJson([]) })
  }
  if (!map.getSource(PEAKS_CURSOR_SOURCE)) {
    map.addSource(PEAKS_CURSOR_SOURCE, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    })
  }
  const beforeId = beforeEstablished(map)
  if (!map.getLayer(PEAKS_JEEP_LINE)) {
    addLayerBefore(
      map,
      {
        id: PEAKS_JEEP_LINE,
        type: 'line',
        source: PEAKS_JEEP_SOURCE,
        paint: {
          'line-color': '#ea580c',
          'line-width': 4,
          'line-opacity': 0.95,
        },
        layout: {
          'line-cap': 'round',
          'line-join': 'round',
        },
      },
      beforeId,
    )
  }
  if (!map.getLayer(PEAKS_ACCESS_LINE)) {
    addLayerBefore(
      map,
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
      beforeId,
    )
  }
  if (!map.getLayer(PEAKS_RING_CIRCLE)) {
    const beforeRing = map.getLayer(PEAKS_SYMBOL) ? PEAKS_SYMBOL : beforeId
    addLayerBefore(
      map,
      {
        id: PEAKS_RING_CIRCLE,
        type: 'circle',
        source: PEAKS_SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 8, 12, 11, 16, 14],
          'circle-color': 'rgba(15, 23, 42, 0.35)',
          'circle-stroke-width': 2.5,
          'circle-stroke-color': [
            'match',
            ['downcase', ['coalesce', ['get', 'access_difficulty'], '']],
            'easy',
            '#22c55e',
            'medium',
            '#3b82f6',
            'difficult',
            '#f97316',
            'extreme',
            '#ef4444',
            '#64748b',
          ],
          'circle-opacity': 0.95,
        },
      },
      beforeRing,
    )
  }
  if (!map.getLayer(PEAKS_SYMBOL)) {
    addLayerBefore(
      map,
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
      beforeId,
    )
  }
  if (!map.getLayer(PEAKS_PAVED_CIRCLE)) {
    map.addLayer(
      {
        id: PEAKS_PAVED_CIRCLE,
        type: 'circle',
        source: PEAKS_PAVED_SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 4, 14, 7, 17, 10],
          'circle-color': '#ea580c',
          'circle-stroke-color': '#0f172a',
          'circle-stroke-width': 1.5,
          'circle-opacity': 0.95,
        },
      },
      PEAKS_SYMBOL,
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
  if (!map.getLayer(PEAKS_CURSOR_CIRCLE)) {
    addLayerBefore(
      map,
      {
        id: PEAKS_CURSOR_CIRCLE,
        type: 'circle',
        source: PEAKS_CURSOR_SOURCE,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 6, 14, 9, 17, 12],
          'circle-color': '#f8fafc',
          'circle-stroke-color': '#ef4444',
          'circle-stroke-width': 2.5,
          'circle-opacity': 0.98,
        },
      },
      beforeId,
    )
  }
}

/** @param {maplibregl.Map} map @param {number} lat @param {number} lon */
export function setPeaksCursorPoint(map, lat, lon) {
  const source = map.getSource(PEAKS_CURSOR_SOURCE)
  if (!source) return
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    source.setData({ type: 'FeatureCollection', features: [] })
    return
  }
  source.setData({
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon, lat] },
        properties: {},
      },
    ],
  })
}

/** @param {maplibregl.Map} map */
export function clearPeaksCursorPoint(map) {
  setPeaksCursorPoint(map, NaN, NaN)
}

/** @param {maplibregl.Map} map @param {object[]} peaks */
export function setPeaksLayerData(map, peaks) {
  if (map.getSource(PEAKS_SOURCE)) {
    map.getSource(PEAKS_SOURCE).setData(peaksGeoJson(peaks))
  }
  if (map.getSource(PEAKS_JEEP_SOURCE)) {
    map.getSource(PEAKS_JEEP_SOURCE).setData(peaksJeepGeoJson(peaks))
  }
  if (map.getSource(PEAKS_ACCESS_SOURCE)) {
    map.getSource(PEAKS_ACCESS_SOURCE).setData(peaksAccessGeoJson(peaks))
  }
  if (map.getSource(PEAKS_PAVED_SOURCE)) {
    map.getSource(PEAKS_PAVED_SOURCE).setData(peaksPavedGeoJson(peaks))
  }
  if (map.getSource(PEAKS_ROAD_SOURCE)) {
    map.getSource(PEAKS_ROAD_SOURCE).setData(peaksRoadGeoJson(peaks))
  }
}
