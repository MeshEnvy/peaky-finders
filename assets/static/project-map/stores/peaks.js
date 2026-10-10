// @ts-check

import { coordVisibleInMapViewport } from '../map/viewport.js'

/** @param {object} store @param {string} slug */
export function isPeakMapVisible(store, slug) {
  return !store.peaks.hidden.has(slug)
}

/** @param {object} store */
export function visiblePeaksForMap(store) {
  return store.peaks.list.filter((peak) => isPeakMapVisible(store, peak.slug))
}

/** @param {object} store */
export function sortedPeaksList(store) {
  return [...store.peaks.list].sort((a, b) =>
    String(a.name || a.slug).localeCompare(String(b.name || b.slug)),
  )
}

/**
 * Peaks whose pins project onto the current map canvas.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function viewportPeaks(store, { map, mapReady } = {}) {
  const list = store?.peaks?.list || []
  if (!map || !mapReady) return []
  return list.filter((peak) => {
    const lat = Number(peak.lat)
    const lon = Number(peak.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false
    return coordVisibleInMapViewport(map, lon, lat, { mapReady })
  })
}

/**
 * Sidebar peak rows: peaks on the map canvas.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function sidebarPeaks(store, opts = {}) {
  void store.peaks?.panelRevision
  return [...viewportPeaks(store, opts)].sort((a, b) =>
    String(a.name || a.slug).localeCompare(String(b.name || b.slug)),
  )
}

/** @param {object} store */
export function hiddenPeakSlugs(store) {
  return [...store.peaks.hidden]
}
