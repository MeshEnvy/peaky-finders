// @ts-check

import { DRAFT_VIEWSHED_SLUG } from '../constants.js'

/** @param {object} store @param {string} slug @param {boolean} visible */
export function setAccessVisible(store, slug, visible) {
  if (!store.access) store.access = { bySlug: {}, visible: new Map() }
  if (!store.access.visible) store.access.visible = new Map()
  store.access.visible.set(slug, visible)
}

/** Access button pin (opt-in). Unset slugs are off. */
export function isAccessVisible(store, slug) {
  return store.access?.visible?.get(slug) === true
}

/** @param {object} store @param {string} slug */
export function isSelectedLinkEndpoint(store, slug) {
  const link = store.ui?.selectedLink
  return Boolean(link && (link.a === slug || link.b === slug))
}

/** Slug whose jeep/hike preview is focused, if any. */
export function activeAccessPreviewSlug(store) {
  return (
    store.peaks?.accessSlug ||
    store.fortify?.accessSlug ||
    store.linkSolver?.accessSlug ||
    null
  )
}

/** Catalog / Fortify / link-solver peak click preview (not a booked site slug). */
export function isPeakAccessPreview(store, slug) {
  return activeAccessPreviewSlug(store) === slug
}

/**
 * One focused access preview at a time across peaks, Fortify, and link solver.
 * @param {object} store
 * @param {'peaks'|'fortify'|'linkSolver'} domain
 * @param {string|null} slug
 */
export function setAccessPreviewFocus(store, domain, slug) {
  store.peaks.accessSlug = domain === 'peaks' ? slug : null
  store.fortify.accessSlug = domain === 'fortify' ? slug : null
  store.linkSolver.accessSlug = domain === 'linkSolver' ? slug : null
}

/** @param {object} store */
export function clearAccessPreviewFocus(store) {
  setAccessPreviewFocus(store, 'peaks', null)
}

/**
 * Pin / sheet copy while the OSM+GTLF graph is loading.
 * @param {{ status?: string, phase?: string, fraction?: number }|null|undefined} osm
 */
export function osmRoadsProgressLabel(osm) {
  if (!osm || osm.status !== 'parsing') return ''
  const phase = typeof osm.phase === 'string' ? osm.phase : ''
  const pct = Math.round((Number(osm.fraction) || 0) * 100)
  if (phase === 'BLM roads') {
    return pct > 0 ? `Merging BLM roads ${pct}%` : 'Merging BLM roads'
  }
  if (phase === 'OSM graph') return 'Loading OSM graph'
  if (phase === 'OSM nodes') return 'Reading OSM nodes'
  if (phase === 'OSM roads') return 'Reading OSM roads'
  if (phase === 'OSM index') return 'Indexing OSM roads'
  if (pct > 0) return `Loading roads ${pct}%`
  return 'Loading roads'
}

/**
 * Map jeep/hike lines for a site slug.
 * Hidden sites never paint (pin, viewshed, and access stay aligned).
 * Peak preview slugs are exempt — catalog peaks are not site rows.
 * @param {object} store
 * @param {string} slug
 * @param {boolean} [hidden]
 */
export function siteAccessShouldShow(store, slug, hidden = false) {
  if (!slug) return false
  if (isPeakAccessPreview(store, slug)) return true
  if (store.ui?.createMode && slug === DRAFT_VIEWSHED_SLUG) return true
  if (hidden) return false
  if (store.ui?.selectedSlug === slug) return true
  if (isSelectedLinkEndpoint(store, slug)) return true
  return isAccessVisible(store, slug)
}
