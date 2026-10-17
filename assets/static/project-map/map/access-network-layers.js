// @ts-check

import {
  ACCESS_NETWORK_HIKE_CASE,
  ACCESS_NETWORK_HIKE_LINE,
  ACCESS_NETWORK_HIKE_SOURCE,
  ACCESS_NETWORK_HIT_LINE,
  ACCESS_NETWORK_HIT_SOURCE,
  ACCESS_NETWORK_JEEP_CASE,
  ACCESS_NETWORK_JEEP_LINE,
  ACCESS_NETWORK_JEEP_SOURCE,
  ACCESS_NETWORK_OTHER_CASE,
  ACCESS_NETWORK_OTHER_LINE,
  ACCESS_NETWORK_OTHER_SOURCE,
  ACCESS_NETWORK_PAVED_CASE,
  ACCESS_NETWORK_PAVED_LINE,
  ACCESS_NETWORK_PAVED_SOURCE,
  ACCESS_NETWORK_PRIVATE_CASE,
  ACCESS_NETWORK_PRIVATE_LINE,
  ACCESS_NETWORK_PRIVATE_SOURCE,
  PEAKS_JEEP_LINE,
} from '../constants.js'

const EMPTY = { type: 'FeatureCollection', features: [] }
const CASE_COLOR = '#0f172a'
const CASE_WIDTH = 4.5
const LINE_WIDTH = 2.25

/** Private and other sit under the routed layers. Each stroke sits on a dark casing. */
const NETWORK_LAYERS = [
  {
    source: ACCESS_NETWORK_PRIVATE_SOURCE,
    line: ACCESS_NETWORK_PRIVATE_LINE,
    casing: ACCESS_NETWORK_PRIVATE_CASE,
    color: '#ef4444',
    kind: 'private',
    dash: [2, 1.4],
  },
  {
    source: ACCESS_NETWORK_OTHER_SOURCE,
    line: ACCESS_NETWORK_OTHER_LINE,
    casing: ACCESS_NETWORK_OTHER_CASE,
    color: '#e879f9',
    kind: 'other',
  },
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

export const ACCESS_NETWORK_LINE_IDS = NETWORK_LAYERS.map((layer) => layer.line)

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
function addLine(map, id, source, color, width, opacity, beforeId, dash) {
  if (map.getLayer(id)) return
  const paint = {
    'line-color': color,
    'line-width': width,
    'line-opacity': opacity,
  }
  if (dash) paint['line-dasharray'] = dash
  addLayerBefore(
    map,
    {
      id,
      type: 'line',
      source,
      layout: { 'line-cap': dash ? 'butt' : 'round', 'line-join': 'round' },
      paint,
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
    addLine(map, layer.line, layer.source, layer.color, LINE_WIDTH, 1, beforeId, layer.dash)
  }
  if (!map.getSource(ACCESS_NETWORK_HIT_SOURCE)) {
    map.addSource(ACCESS_NETWORK_HIT_SOURCE, { type: 'geojson', data: EMPTY })
  }
  addLine(map, ACCESS_NETWORK_HIT_LINE, ACCESS_NETWORK_HIT_SOURCE, '#fef08a', 5, 1, beforeId)
}

/**
 * @param {maplibregl.Map} map
 * @param {{ paved?: boolean, jeep?: boolean, hiking?: boolean, other?: boolean, private?: boolean }} vis
 */
export function syncAccessNetworkVisibility(map, vis) {
  for (const layer of NETWORK_LAYERS) {
    const on = vis[layer.kind] === true
    for (const layerId of [layer.casing, layer.line]) {
      if (!map.getLayer(layerId)) continue
      map.setLayoutProperty(layerId, 'visibility', on ? 'visible' : 'none')
    }
  }
}

/** @param {maplibregl.Map} map @param {object|null} feature */
export function setAccessNetworkHit(map, feature) {
  const source = map.getSource(ACCESS_NETWORK_HIT_SOURCE)
  if (!source) return
  source.setData(
    feature
      ? { type: 'FeatureCollection', features: [feature] }
      : EMPTY,
  )
}

/**
 * @param {maplibregl.Map} map
 * @param {'paved'|'jeep'|'hiking'|'other'|'private'} kind
 * @param {object} geojson
 */
export function setAccessNetworkLayerData(map, kind, geojson) {
  const layer = NETWORK_LAYERS.find((row) => row.kind === kind)
  if (!layer) return
  const source = map.getSource(layer.source)
  if (source) source.setData(geojson || EMPTY)
}

