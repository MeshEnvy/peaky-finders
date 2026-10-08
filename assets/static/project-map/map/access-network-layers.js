// @ts-check

import {
  ACCESS_NETWORK_HIKE_CASE,
  ACCESS_NETWORK_HIKE_LINE,
  ACCESS_NETWORK_HIKE_SOURCE,
  ACCESS_NETWORK_JEEP_CASE,
  ACCESS_NETWORK_JEEP_LINE,
  ACCESS_NETWORK_JEEP_SOURCE,
  ACCESS_NETWORK_PAVED_CASE,
  ACCESS_NETWORK_PAVED_LINE,
  ACCESS_NETWORK_PAVED_SOURCE,
  PEAKS_JEEP_LINE,
} from '../constants.js'

const EMPTY = { type: 'FeatureCollection', features: [] }
const CASE_COLOR = '#0f172a'
const CASE_WIDTH = 4.5
const LINE_WIDTH = 2.25

/** Hiking lowest, then jeep, then paved. Each stroke sits on a dark casing. */
const NETWORK_LAYERS = [
  {
    source: ACCESS_NETWORK_HIKE_SOURCE,
    line: ACCESS_NETWORK_HIKE_LINE,
    casing: ACCESS_NETWORK_HIKE_CASE,
    color: '#e7c9a8',
    kind: 'hiking',
  },
  {
    source: ACCESS_NETWORK_JEEP_SOURCE,
    line: ACCESS_NETWORK_JEEP_LINE,
    casing: ACCESS_NETWORK_JEEP_CASE,
    color: '#fb923c',
    kind: 'jeep',
  },
  {
    source: ACCESS_NETWORK_PAVED_SOURCE,
    line: ACCESS_NETWORK_PAVED_LINE,
    casing: ACCESS_NETWORK_PAVED_CASE,
    color: '#7dd3fc',
    kind: 'paved',
  },
]

/** @param {maplibregl.Map} map */
function networkBeforeId(map) {
  if (map.getLayer(PEAKS_JEEP_LINE)) return PEAKS_JEEP_LINE
  return undefined
}

/** @param {maplibregl.Map} map @param {object} spec @param {string|undefined} beforeId */
function addLayerBefore(map, spec, beforeId) {
  if (beforeId) map.addLayer(spec, beforeId)
  else map.addLayer(spec)
}

/**
 * @param {maplibregl.Map} map
 * @param {string} id
 * @param {string} source
 * @param {string} color
 * @param {number} width
 * @param {number} opacity
 * @param {string|undefined} beforeId
 */
function addLine(map, id, source, color, width, opacity, beforeId) {
  if (map.getLayer(id)) return
  addLayerBefore(
    map,
    {
      id,
      type: 'line',
      source,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': color,
        'line-width': width,
        'line-opacity': opacity,
      },
    },
    beforeId,
  )
}

/** @param {maplibregl.Map} map */
export function ensureAccessNetworkLayers(map) {
  const beforeId = networkBeforeId(map)
  for (const layer of NETWORK_LAYERS) {
    if (!map.getSource(layer.source)) {
      map.addSource(layer.source, { type: 'geojson', data: EMPTY })
    }
  }
  for (const layer of NETWORK_LAYERS) {
    addLine(map, layer.casing, layer.source, CASE_COLOR, CASE_WIDTH, 0.9, beforeId)
    addLine(map, layer.line, layer.source, layer.color, LINE_WIDTH, 1, beforeId)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {{ paved?: boolean, jeep?: boolean, hiking?: boolean }} vis
 */
export function syncAccessNetworkVisibility(map, vis) {
  for (const layer of NETWORK_LAYERS) {
    const on = layer.kind === 'paved' ? vis.paved : layer.kind === 'jeep' ? vis.jeep : vis.hiking
    for (const layerId of [layer.casing, layer.line]) {
      if (!map.getLayer(layerId)) continue
      map.setLayoutProperty(layerId, 'visibility', on ? 'visible' : 'none')
    }
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {'paved'|'jeep'|'hiking'} kind
 * @param {object} geojson
 */
export function setAccessNetworkLayerData(map, kind, geojson) {
  const layer = NETWORK_LAYERS.find((row) => row.kind === kind)
  if (!layer) return
  const source = map.getSource(layer.source)
  if (source) source.setData(geojson || EMPTY)
}
