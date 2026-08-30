// @ts-check

import { viewshedLayerId, viewshedSourceId } from '../constants.js'

/**
 * MapLibre viewshed overlay adapter. Imperative layer ops only — state lives in store.viewshed.
 * @param {maplibregl.Map} map
 * @param {string} slug
 * @param {{ url: string, coordinates: number[][] }} overlay
 * @param {number} opacity
 * @param {string} [beforeId] optional layer id to insert before
 */
export function addViewshedRasterLayer(map, slug, overlay, opacity, beforeId) {
  const sourceId = viewshedSourceId(slug)
  const layerId = viewshedLayerId(slug)
  if (map.getLayer(layerId)) map.removeLayer(layerId)
  if (map.getSource(sourceId)) map.removeSource(sourceId)
  map.addSource(sourceId, {
    type: 'image',
    url: overlay.url,
    coordinates: overlay.coordinates,
  })
  map.addLayer(
    {
      id: layerId,
      type: 'raster',
      source: sourceId,
      paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0 },
    },
    beforeId,
  )
}

/** @param {maplibregl.Map} map @param {string} slug */
export function removeViewshedLayer(map, slug) {
  const sourceId = viewshedSourceId(slug)
  const layerId = viewshedLayerId(slug)
  if (map.getLayer(layerId)) map.removeLayer(layerId)
  if (map.getSource(sourceId)) map.removeSource(sourceId)
}

/** @param {maplibregl.Map} map @param {string} slug @param {boolean} visible */
export function setViewshedLayerVisibility(map, slug, visible) {
  const layerId = viewshedLayerId(slug)
  if (map.getLayer(layerId)) {
    map.setLayoutProperty(layerId, 'visibility', visible ? 'visible' : 'none')
  }
}
