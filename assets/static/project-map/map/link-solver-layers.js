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

/** @param {object[]} [routes] @param {string|null} [routeId] */
function peakSlugsOnRoute(routes, routeId) {
  if (!routeId || !Array.isArray(routes)) return null
  const route = routes.find((r) => r.route_id === routeId)
  if (!route?.peaks?.length) return null
  return new Set(
    route.peaks.map((p) => String(p.peak_slug || p.slug || '')).filter(Boolean),
  )
}

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
 * @param {{
 *   selectedRouteId?: string|null,
 *   selectedPeakSlug?: string|null,
 *   routes?: object[],
 *   raiseSiteLayers?: () => void,
 * }} [opts]
 */
export function applyLinkSolverLayers(map, payload, opts = {}) {
  if (!payload) return
  removeLinkSolverLayers(map)

  const selectedId = opts.selectedRouteId || null
  const selectedPeak = opts.selectedPeakSlug || null
  const routePeakSlugs = peakSlugsOnRoute(opts.routes, selectedId)
  const peaks = payload.peaks
  const peakFeatures = peaks?.features?.length
    ? peaks.features.filter((feature) => {
        if (!routePeakSlugs) return true
        const slug = String(feature?.properties?.peak_slug || feature?.properties?.slug || '')
        return routePeakSlugs.has(slug)
      })
    : []
  if (peakFeatures.length) {
    map.addSource(LINK_SOLVER_PEAKS_SOURCE, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: peakFeatures },
    })
    map.addLayer(
      {
        id: LINK_SOLVER_PEAKS_LAYER,
        type: 'circle',
        source: LINK_SOLVER_PEAKS_SOURCE,
        paint: {
          'circle-radius': [
            'case',
            [
              '==',
              ['coalesce', ['get', 'peak_slug'], ['get', 'slug']],
              selectedPeak || '',
            ],
            10,
            8,
          ],
          'circle-color': [
            'case',
            [
              '==',
              ['coalesce', ['get', 'peak_slug'], ['get', 'slug']],
              selectedPeak || '',
            ],
            '#f59e0b',
            '#38bdf8',
          ],
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
