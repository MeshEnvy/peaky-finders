// @ts-check

import { PITCH_TERRAIN_ON } from '../constants.js'

/** @param {maplibregl.Map} map @param {boolean} mapReady */
export function isMapTiltedView(map, mapReady) {
  return Boolean(map && mapReady && map.getPitch() >= PITCH_TERRAIN_ON)
}

/**
 * True when the lon/lat projects onto the canvas. Uses MapLibre's camera
 * (pitch, bearing, terrain) so In view matches pins on a tilted map.
 * `getBounds()` / nadir boxes miss anything toward the horizon.
 *
 * @param {maplibregl.Map} map
 * @param {number} lon
 * @param {number} lat
 * @param {{ mapReady?: boolean }} [_opts]
 */
export function coordVisibleInMapViewport(map, lon, lat, _opts = {}) {
  if (!map || lon == null || lat == null) return true
  const projected = map.project([lon, lat])
  if (!Number.isFinite(projected.x) || !Number.isFinite(projected.y)) return false
  const canvas = map.getCanvas()
  const w = canvas.clientWidth
  const h = canvas.clientHeight
  return projected.x >= 0 && projected.x <= w && projected.y >= 0 && projected.y <= h
}
