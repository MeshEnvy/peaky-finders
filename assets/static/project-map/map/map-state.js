// @ts-check

import { mapStateKey } from '../constants.js'
import { BASEMAPS } from './basemap.js'

/** @param {Record<string, unknown>|null|undefined} saved */
export function isValidSavedState(saved) {
  if (!saved || typeof saved !== 'object') return false
  if (!Array.isArray(saved.center) || saved.center.length !== 2) return false
  if (typeof saved.basemap !== 'string' || !BASEMAPS[saved.basemap]) return false
  if (typeof saved.zoom !== 'number') return false
  return true
}

/** @param {string} projectSlug */
export function loadMapState(projectSlug) {
  try {
    const raw = localStorage.getItem(mapStateKey(projectSlug))
    if (!raw) return null
    const saved = JSON.parse(raw)
    return isValidSavedState(saved) ? saved : null
  } catch {
    return null
  }
}

/**
 * @param {string} key localStorage key (e.g. mapStateKey(slug))
 * @param {Record<string, unknown>} state
 */
export function persistMapState(key, state) {
  try {
    localStorage.setItem(key, JSON.stringify(state))
  } catch {
    /* private mode or quota */
  }
}

/**
 * Camera fields shared by every persisted map snapshot.
 * @param {maplibregl.Map} map
 */
export function captureMapCamera(map) {
  const c = map.getCenter()
  return {
    center: [c.lng, c.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  }
}

/**
 * Build a v1 map-state payload (camera + caller extras).
 * @param {maplibregl.Map} map
 * @param {Record<string, unknown>} extras
 */
export function captureMapState(map, extras = {}) {
  return {
    v: 1,
    ...captureMapCamera(map),
    ...extras,
  }
}
