// @ts-check

import {
  ALTERNATES_CANDIDATES_LAYER,
  ALTERNATES_CANDIDATES_LABELS_LAYER,
  ALTERNATES_CANDIDATES_SOURCE,
  ALTERNATES_LINES_LAYER,
  ALTERNATES_LINES_LABELS_LAYER,
  ALTERNATES_LINES_SOURCE,
  DRAFT_MARKER_COLOR,
  MAP_LABEL_FONT,
  SITES_CIRCLE,
} from '../constants.js'
import { linkLabelsLayerSpec } from './links-layers.js'
import { rfLinesGeoJsonWithLabels } from './line-labels.js'

/** @param {maplibregl.Map} map */
export function removeAlternatesLayers(map) {
  for (const id of [
    ALTERNATES_LINES_LABELS_LAYER,
    ALTERNATES_LINES_LAYER,
    ALTERNATES_CANDIDATES_LABELS_LAYER,
    ALTERNATES_CANDIDATES_LAYER,
  ]) {
    if (map.getLayer(id)) map.removeLayer(id)
  }
  for (const src of [ALTERNATES_LINES_SOURCE, ALTERNATES_CANDIDATES_SOURCE]) {
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
export function applyAlternatesLayers(map, payload, opts = {}) {
  if (!payload) return
  removeAlternatesLayers(map)

  const selectedId = opts.selectedCandidateId || null
  const candidates = payload.candidates
  if (candidates?.features?.length) {
    map.addSource(ALTERNATES_CANDIDATES_SOURCE, { type: 'geojson', data: candidates })
    map.addLayer(
      {
        id: ALTERNATES_CANDIDATES_LAYER,
        type: 'circle',
        source: ALTERNATES_CANDIDATES_SOURCE,
        paint: {
          'circle-radius': [
            'case',
            ['==', ['get', 'candidate_id'], selectedId || ''],
            10,
            ['boolean', ['get', 'is_site'], false],
            9,
            8,
          ],
          'circle-color': [
            'case',
            ['==', ['get', 'candidate_id'], selectedId || ''],
            '#f59e0b',
            ['boolean', ['get', 'is_site'], false],
            DRAFT_MARKER_COLOR,
            '#38bdf8',
          ],
          'circle-stroke-width': 2,
          'circle-stroke-color': '#0f172a',
        },
      },
      SITES_CIRCLE,
    )
    map.addLayer(
      {
        id: ALTERNATES_CANDIDATES_LABELS_LAYER,
        type: 'symbol',
        source: ALTERNATES_CANDIDATES_SOURCE,
        layout: {
          'text-field': [
            'case',
            ['boolean', ['get', 'is_site'], false],
            ['coalesce', ['get', 'site_name'], ''],
            ['concat', ['to-string', ['round', ['get', 'elev_m']]], 'm'],
          ],
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
    map.addSource(ALTERNATES_LINES_SOURCE, { type: 'geojson', data: labeled })
    map.addLayer(
      {
        id: ALTERNATES_LINES_LAYER,
        type: 'line',
        source: ALTERNATES_LINES_SOURCE,
        paint: {
          'line-color': '#38bdf8',
          'line-width': 3,
          'line-opacity': 0.9,
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      },
      SITES_CIRCLE,
    )
    map.addLayer(
      linkLabelsLayerSpec(ALTERNATES_LINES_LABELS_LAYER, ALTERNATES_LINES_SOURCE, 'visible'),
      SITES_CIRCLE,
    )
  }

  opts.raiseSiteLayers?.()
}
