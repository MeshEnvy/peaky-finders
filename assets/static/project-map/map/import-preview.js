// @ts-check

import { MAP_LABEL_FONT } from '../constants.js'
import { basemapStyle } from './basemap.js'

export const IMPORT_PREVIEW_SOURCE = 'import-preview-points'
export const IMPORT_PREVIEW_LAYER = 'import-preview-points-layer'
export const IMPORT_PREVIEW_LABEL_LAYER = 'import-preview-points-labels'

export const IMPORT_PREVIEW_CIRCLE_PAINT = {
  'circle-radius': 6,
  'circle-color': ['case', ['get', 'ignored'], '#94a3b8', '#4a6cf7'],
  'circle-stroke-width': 1.5,
  'circle-stroke-color': '#ffffff',
  'circle-opacity': ['case', ['get', 'ignored'], 0.55, 1],
}

export const IMPORT_PREVIEW_LABEL_PAINT = {
  'text-color': ['case', ['get', 'ignored'], '#94a3b8', '#e8eaed'],
  'text-halo-color': '#1a1a1a',
  'text-halo-width': 2,
  'text-opacity': ['case', ['get', 'ignored'], 0.7, 1],
}

/** @param {object[]} points */
export function importPreviewGeoJson(points) {
  return {
    type: 'FeatureCollection',
    features: (points || []).map((point, index) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
      properties: {
        name: point.name || `Point ${index + 1}`,
        ignored: !!point.ignored,
        index,
      },
    })),
  }
}

/**
 * @param {HTMLElement} container
 * @param {string} basemapKey
 */
export function createImportPreviewMap(container, basemapKey) {
  const map = new maplibregl.Map({
    container,
    style: basemapStyle(basemapKey),
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false,
    interactive: true,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  return map
}

/** @param {maplibregl.Map|null} map */
export function destroyImportPreviewMap(map) {
  if (map) map.remove()
}

/** @param {maplibregl.Map} map @param {object[]} points */
export function fitImportPreviewBounds(map, points) {
  if (!map || !points.length) return
  if (points.length === 1) {
    const point = points[0]
    map.jumpTo({ center: [point.lon, point.lat], zoom: 11 })
    return
  }
  const bounds = new maplibregl.LngLatBounds()
  for (const point of points) bounds.extend([point.lon, point.lat])
  map.fitBounds(bounds, { padding: 36, maxZoom: 12, duration: 0 })
}

/**
 * @param {maplibregl.Map} map
 * @param {object[]} points
 */
export function applyImportPreviewData(map, points) {
  const geojson = importPreviewGeoJson(points)
  const source = map.getSource(IMPORT_PREVIEW_SOURCE)
  if (source) {
    source.setData(geojson)
  } else {
    map.addSource(IMPORT_PREVIEW_SOURCE, { type: 'geojson', data: geojson })
  }
  if (!map.getLayer(IMPORT_PREVIEW_LAYER)) {
    map.addLayer({
      id: IMPORT_PREVIEW_LAYER,
      type: 'circle',
      source: IMPORT_PREVIEW_SOURCE,
      paint: IMPORT_PREVIEW_CIRCLE_PAINT,
    })
  }
  if (!map.getLayer(IMPORT_PREVIEW_LABEL_LAYER)) {
    map.addLayer({
      id: IMPORT_PREVIEW_LABEL_LAYER,
      type: 'symbol',
      source: IMPORT_PREVIEW_SOURCE,
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 11,
        'text-offset': [0, -1.4],
        'text-anchor': 'bottom',
        'text-font': MAP_LABEL_FONT,
        'text-allow-overlap': true,
      },
      paint: IMPORT_PREVIEW_LABEL_PAINT,
    })
  }
  map.resize()
  fitImportPreviewBounds(map, points)
}
