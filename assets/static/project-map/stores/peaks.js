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
 * Peaks in the current map view when In view is on. Otherwise the full catalog.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function viewportPeaks(store, { map, mapReady } = {}) {
  const list = store?.peaks?.list || []
  if (!store.ui?.filterByViewport || !map || !mapReady) return list
  return list.filter((peak) => {
    const lat = Number(peak.lat)
    const lon = Number(peak.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false
    return coordVisibleInMapViewport(map, lon, lat, { mapReady })
  })
}

/**
 * Sidebar peak rows: viewport scope when In view is on.
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
