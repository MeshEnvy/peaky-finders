// @ts-check

import {
  LINK_SOLVER_LINES_LABELS_LAYER,
  LINK_SOLVER_LINES_LAYER,
  LINK_SOLVER_LINES_SOURCE,
  LINK_SOLVER_PEAKS_LAYER,
  LINK_SOLVER_PEAKS_SOURCE,
  MAP_LABEL_FONT,
  SITES_CIRCLE,
} from '../constants.js'
import { linkLabelsLayerSpec } from './links-layers.js'
import { rfLinesGeoJsonWithLabels } from './line-labels.js'

/** @param {maplibregl.Map} map */
export function removeLinkSolverLayers(map) {
  for (const id of [LINK_SOLVER_LINES_LABELS_LAYER, LINK_SOLVER_LINES_LAYER, LINK_SOLVER_PEAKS_LAYER]) {
    if (map.getLayer(id)) map.removeLayer(id)
  }
  for (const src of [LINK_SOLVER_LINES_SOURCE, LINK_SOLVER_PEAKS_SOURCE]) {
    if (map.getSource(src)) map.removeSource(src)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {{
 *   peaks?: import('geojson').FeatureCollection,
 *   lines?: import('geojson').FeatureCollection,
 * }} payload
 * @param {{ selectedRouteId?: string|null, raiseSiteLayers?: () => void }} [opts]
 */
export function applyLinkSolverLayers(map, payload, opts = {}) {
  if (!payload) return
  removeLinkSolverLayers(map)

  const selectedId = opts.selectedRouteId || null
  const peaks = payload.peaks
  if (peaks?.features?.length) {
    map.addSource(LINK_SOLVER_PEAKS_SOURCE, { type: 'geojson', data: peaks })
    map.addLayer(
      {
        id: LINK_SOLVER_PEAKS_LAYER,
        type: 'circle',
        source: LINK_SOLVER_PEAKS_SOURCE,
        paint: {
          'circle-radius': 8,
          'circle-color': '#38bdf8',
          'circle-stroke-width': 2,
          'circle-stroke-color': '#0f172a',
        },
      },
      SITES_CIRCLE,
    )
  }

  const lineFeatures = (payload.lines?.features || []).filter((feature) => {
    if (!selectedId) return false
    const props = feature.properties || {}
    return props.route_id === selectedId
  })
  if (lineFeatures.length) {
    const labeled = rfLinesGeoJsonWithLabels({
      type: 'FeatureCollection',
      features: lineFeatures,
    })
    map.addSource(LINK_SOLVER_LINES_SOURCE, { type: 'geojson', data: labeled })
    map.addLayer(
      {
        id: LINK_SOLVER_LINES_LAYER,
        type: 'line',
        source: LINK_SOLVER_LINES_SOURCE,
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
      linkLabelsLayerSpec(LINK_SOLVER_LINES_LABELS_LAYER, LINK_SOLVER_LINES_SOURCE, 'visible'),
      SITES_CIRCLE,
    )
  }

  opts.raiseSiteLayers?.()
}
