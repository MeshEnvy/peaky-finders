// @ts-check

import {
  FORTIFY_CANDIDATES_LAYER,
  FORTIFY_CANDIDATES_LABELS_LAYER,
  FORTIFY_CANDIDATES_SOURCE,
  FORTIFY_LINES_LAYER,
  FORTIFY_LINES_LABELS_LAYER,
  FORTIFY_LINES_SOURCE,
  MAP_LABEL_FONT,
  SITES_CIRCLE,
} from '../constants.js'
import { linkLabelsLayerSpec } from './links-layers.js'
import { rfLinesGeoJsonWithLabels } from './line-labels.js'

/** @param {maplibregl.Map} map */
export function removeFortifyLayers(map) {
  for (const id of [
    FORTIFY_LINES_LABELS_LAYER,
    FORTIFY_LINES_LAYER,
    FORTIFY_CANDIDATES_LABELS_LAYER,
    FORTIFY_CANDIDATES_LAYER,
  ]) {
    if (map.getLayer(id)) map.removeLayer(id)
  }
  for (const src of [FORTIFY_LINES_SOURCE, FORTIFY_CANDIDATES_SOURCE]) {
    if (map.getSource(src)) map.removeSource(src)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {{
 *   candidates?: import('geojson').FeatureCollection,
 *   lines?: import('geojson').FeatureCollection,
 * }} payload
 * @param {{ selectedCandidateId?: string|null, raiseSiteLayers?: () => void }} [opts]
 */
export function applyFortifyLayers(map, payload, opts = {}) {
  if (!payload) return
  removeFortifyLayers(map)

  const selectedId = opts.selectedCandidateId || null
  const candidates = payload.candidates
  if (candidates?.features?.length) {
    map.addSource(FORTIFY_CANDIDATES_SOURCE, { type: 'geojson', data: candidates })
    map.addLayer(
      {
        id: FORTIFY_CANDIDATES_LAYER,
        type: 'circle',
        source: FORTIFY_CANDIDATES_SOURCE,
        paint: {
          'circle-radius': [
            'case',
            ['==', ['get', 'candidate_id'], selectedId || ''],
            10,
            8,
          ],
          'circle-color': [
            'case',
            ['==', ['get', 'candidate_id'], selectedId || ''],
            '#f59e0b',
            '#a78bfa',
          ],
          'circle-stroke-width': 2,
          'circle-stroke-color': '#0f172a',
        },
      },
      SITES_CIRCLE,
    )
    map.addLayer(
      {
        id: FORTIFY_CANDIDATES_LABELS_LAYER,
        type: 'symbol',
        source: FORTIFY_CANDIDATES_SOURCE,
        layout: {
          'text-field': ['concat', ['to-string', ['round', ['get', 'elev_m']]], 'm'],
          'text-size': 12,
          'text-offset': [0, -1.4],
          'text-anchor': 'bottom',
          'text-font': MAP_LABEL_FONT,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
        },
        paint: {
          'text-color': '#e8eaed',
          'text-halo-color': '#1a1a1a',
          'text-halo-width': 2,
        },
      },
      SITES_CIRCLE,
    )
  }

  const lineFeatures = (payload.lines?.features || []).filter((feature) => {
    if (!selectedId) return false
    const props = feature.properties || {}
    return props.candidate_id === selectedId
  })
  if (lineFeatures.length) {
    const labeled = rfLinesGeoJsonWithLabels({
      type: 'FeatureCollection',
      features: lineFeatures,
    })
    map.addSource(FORTIFY_LINES_SOURCE, { type: 'geojson', data: labeled })
    map.addLayer(
      {
        id: FORTIFY_LINES_LAYER,
        type: 'line',
        source: FORTIFY_LINES_SOURCE,
        paint: {
          'line-color': '#a78bfa',
          'line-width': 3,
          'line-opacity': 0.9,
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      },
      SITES_CIRCLE,
    )
    map.addLayer(
      linkLabelsLayerSpec(FORTIFY_LINES_LABELS_LAYER, FORTIFY_LINES_SOURCE, 'visible'),
      SITES_CIRCLE,
    )
  }

  opts.raiseSiteLayers?.()
}
