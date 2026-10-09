// @ts-check

import { viewshedLayerId, viewshedSourceId } from '../constants.js'

/** Map overlay texture edge. Full splats are often 2530²; dozens of those stall the first pan frame. */
const VIEWSHED_TEX_MAX = 512

/** @type {Map<string, Promise<string>>} */
const displayUrlBySource = new Map()

/**
 * @param {string} url
 * @returns {Promise<string>}
 */
function viewshedDisplayUrl(url) {
  const cached = displayUrlBySource.get(url)
  if (cached) return cached
  const pending = new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      const width = img.naturalWidth || img.width
      const height = img.naturalHeight || img.height
      const edge = Math.max(width, height)
      if (!edge || edge <= VIEWSHED_TEX_MAX) {
        resolve(url)
        return
      }
      const scale = VIEWSHED_TEX_MAX / edge
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(width * scale))
      canvas.height = Math.max(1, Math.round(height * scale))
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        resolve(url)
        return
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      canvas.toBlob((blob) => {
        resolve(blob ? URL.createObjectURL(blob) : url)
      }, 'image/png')
    }
    img.onerror = () => resolve(url)
    img.src = url
  })
  displayUrlBySource.set(url, pending)
  return pending
}

/**
 * MapLibre viewshed overlay adapter. Imperative layer ops only — state lives in store.viewshed.
 * @param {maplibregl.Map} map
 * @param {string} slug
 * @param {{ url: string, coordinates: number[][] }} overlay
 * @param {number} opacity
 * @param {string} [beforeId] optional layer id to insert before
 * @param {() => boolean} [stillCurrent]
 */
export async function addViewshedRasterLayer(map, slug, overlay, opacity, beforeId, stillCurrent) {
  const sourceId = viewshedSourceId(slug)
  const layerId = viewshedLayerId(slug)
  if (map.getLayer(layerId)) map.removeLayer(layerId)
  if (map.getSource(sourceId)) map.removeSource(sourceId)
  const displayUrl = await viewshedDisplayUrl(overlay.url)
  if (stillCurrent && !stillCurrent()) return
  if (!map.getStyle()) return
  map.addSource(sourceId, {
    type: 'image',
    url: displayUrl,
    coordinates: overlay.coordinates,
  })
  const layer = {
    id: layerId,
    type: 'raster',
    source: sourceId,
    paint: { 'raster-opacity': opacity, 'raster-fade-duration': 0 },
  }
  if (beforeId && map.getLayer(beforeId)) map.addLayer(layer, beforeId)
  else map.addLayer(layer)
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
