// @ts-check

import { PITCH_TERRAIN_ON } from '../constants.js'
import { lngLatBoundsFromPoints } from '../geo.js'

/** @param {maplibregl.Map} map @param {boolean} mapReady */
export function isMapTiltedView(map, mapReady) {
  return Boolean(map && mapReady && map.getPitch() >= PITCH_TERRAIN_ON)
}

/** @param {maplibregl.Map} map */
export function mapOverheadEquivalentBounds(map) {
  const center = map.getCenter()
  const zoom = map.getZoom()
  const canvas = map.getCanvas()
  const w = Math.max(1, canvas.clientWidth)
  const h = Math.max(1, canvas.clientHeight)
  const latRad = (center.lat * Math.PI) / 180
  const worldSize = 512 * 2 ** zoom
  const metersPerPixel = (40_075_016.686 * Math.cos(latRad)) / worldSize
  const halfWidthM = (w / 2) * metersPerPixel
  const halfHeightM = (h / 2) * metersPerPixel
  const latDelta = halfHeightM / 111_320
  const lonDelta = halfWidthM / (111_320 * Math.max(1e-6, Math.cos(latRad)))
  return {
    getWest: () => center.lng - lonDelta,
    getEast: () => center.lng + lonDelta,
    getSouth: () => center.lat - latDelta,
    getNorth: () => center.lat + latDelta,
  }
}

/** @param {maplibregl.Map} map @param {number} lon @param {number} lat @param {{ mapReady?: boolean }} [opts] */
export function coordVisibleInMapViewport(map, lon, lat, { mapReady = true } = {}) {
  if (!map || lon == null || lat == null) return true
  if (isMapTiltedView(map, mapReady)) {
    const b = mapOverheadEquivalentBounds(map)
    return (
      lon >= b.getWest() &&
      lon <= b.getEast() &&
      lat >= b.getSouth() &&
      lat <= b.getNorth()
    )
  }
  const projected = map.project([lon, lat])
  if (!Number.isFinite(projected.x) || !Number.isFinite(projected.y)) return false
  const canvas = map.getCanvas()
  const w = canvas.clientWidth
  const h = canvas.clientHeight
  return projected.x >= 0 && projected.x <= w && projected.y >= 0 && projected.y <= h
}

/** @param {maplibregl.Map} map @param {boolean} mapReady */
export function mapDataViewportBounds(map, mapReady) {
  if (isMapTiltedView(map, mapReady)) return mapOverheadEquivalentBounds(map)
  return map.getBounds()
}

