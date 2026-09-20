// @ts-check

import {
  GTLF_ROADS_INVENTORY_LINE,
  GTLF_ROADS_INVENTORY_SOURCE,
  GTLF_TRAILS_INVENTORY_LINE,
  GTLF_TRAILS_INVENTORY_SOURCE,
  PEAKS_JEEP_LINE,
} from '../constants.js'

const EMPTY = { type: 'FeatureCollection', features: [] }

/** @param {maplibregl.Map} map */
function inventoryBeforeId(map) {
  if (map.getLayer(PEAKS_JEEP_LINE)) return PEAKS_JEEP_LINE
  return undefined
}

/** @param {maplibregl.Map} map @param {object} spec @param {string|undefined} beforeId */
function addLayerBefore(map, spec, beforeId) {
  if (beforeId) map.addLayer(spec, beforeId)
  else map.addLayer(spec)
}

/** @param {maplibregl.Map} map */
export function ensureGtlfInventoryLayers(map) {
  const beforeId = inventoryBeforeId(map)
  if (!map.getSource(GTLF_ROADS_INVENTORY_SOURCE)) {
    map.addSource(GTLF_ROADS_INVENTORY_SOURCE, { type: 'geojson', data: EMPTY })
  }
  if (!map.getSource(GTLF_TRAILS_INVENTORY_SOURCE)) {
    map.addSource(GTLF_TRAILS_INVENTORY_SOURCE, { type: 'geojson', data: EMPTY })
  }
  if (!map.getLayer(GTLF_ROADS_INVENTORY_LINE)) {
    addLayerBefore(
      map,
      {
        id: GTLF_ROADS_INVENTORY_LINE,
        type: 'line',
        source: GTLF_ROADS_INVENTORY_SOURCE,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#fb923c',
          'line-width': 1.5,
          'line-opacity': 0.45,
        },
      },
      beforeId,
    )
  }
  if (!map.getLayer(GTLF_TRAILS_INVENTORY_LINE)) {
    addLayerBefore(
      map,
      {
        id: GTLF_TRAILS_INVENTORY_LINE,
        type: 'line',
        source: GTLF_TRAILS_INVENTORY_SOURCE,
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#d4a574',
          'line-width': 1.25,
          'line-opacity': 0.4,
        },
      },
      beforeId,
    )
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {{ roads?: boolean, trails?: boolean }} vis
 */
export function syncGtlfInventoryVisibility(map, vis) {
  if (map.getLayer(GTLF_ROADS_INVENTORY_LINE)) {
    map.setLayoutProperty(
      GTLF_ROADS_INVENTORY_LINE,
      'visibility',
      vis.roads ? 'visible' : 'none',
    )
  }
  if (map.getLayer(GTLF_TRAILS_INVENTORY_LINE)) {
    map.setLayoutProperty(
      GTLF_TRAILS_INVENTORY_LINE,
      'visibility',
      vis.trails ? 'visible' : 'none',
    )
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {'roads'|'trails'} kind
 * @param {object} geojson
 */
export function setGtlfInventoryData(map, kind, geojson) {
  const sourceId =
    kind === 'trails' ? GTLF_TRAILS_INVENTORY_SOURCE : GTLF_ROADS_INVENTORY_SOURCE
  const source = map.getSource(sourceId)
  if (source) source.setData(geojson || EMPTY)
}
