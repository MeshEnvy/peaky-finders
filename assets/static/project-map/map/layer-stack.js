// @ts-check

import { SITE_STACK_RAISE_IDS, SITES_CIRCLE } from '../constants.js'

/**
 * First map layer that sits above viewshed rasters (bottom of peaks / RF / sites stack).
 * @param {maplibregl.Map} map
 */
export function stackFloorBeforeId(map) {
  for (const id of SITE_STACK_RAISE_IDS) {
    if (map.getLayer(id)) return id
  }
  return map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined
}

/** @param {maplibregl.Map} map @param {string} beforeId */
export function raiseLandLayersBelow(map, beforeId) {
  if (!beforeId) return
  const layers = map.getStyle()?.layers
  if (!layers) return
  for (const layer of layers) {
    if (!layer.id.startsWith('land-')) continue
    try {
      map.moveLayer(layer.id, beforeId)
    } catch (_) {
      /* layer may be mid-remove */
    }
  }
}

/**
 * Viewshed rasters sit above land fills/lines and below the interactive stack.
 * @param {maplibregl.Map} map
 * @param {string} beforeId
 * @param {string[]} viewshedLayerIds
 */
export function raiseViewshedLayersBelowStack(map, beforeId, viewshedLayerIds) {
  if (!beforeId) return
  raiseLandLayersBelow(map, beforeId)
  for (const layerId of viewshedLayerIds) {
    if (!map.getLayer(layerId)) continue
    try {
      map.moveLayer(layerId, beforeId)
    } catch (_) {
      /* layer may be mid-remove */
    }
  }
}
