// @ts-check

import { compareHuman, haversineMeters } from '../geo.js'
import { coordVisibleInMapViewport } from '../map/viewport.js'

export const IMPORT_DEDUPE_METERS = 100

/** @param {unknown} site */
export function normalizeSiteFromApi(site) {
  if (!site || typeof site !== 'object') return null
  const row = /** @type {Record<string, unknown>} */ (site)
  if (!row.slug) return null
  const lat = Number(row.lat)
  const lon = Number(row.lon)
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  return {
    ...row,
    slug: String(row.slug),
    name: String(row.name || row.slug),
    lat,
    lon,
    tags: Array.isArray(row.tags) ? row.tags.map(String).filter(Boolean) : [],
  }
}

/** @param {object|null|undefined} site */
export function siteTags(site) {
  return Array.isArray(site?.tags) ? site.tags.filter(Boolean) : []
}

/**
 * @param {object} site
 * @param {{ ui?: { tagFilters?: Set<string>, tagFilterMode?: string } }} store
 */
export function sitePassesTagFilter(site, store) {
  const activeTagFilters = store?.ui?.tagFilters
  if (!activeTagFilters || activeTagFilters.size === 0) return true
  const tags = siteTags(site)
  const tagFilterMode = store.ui?.tagFilterMode === 'or' ? 'or' : 'and'
  if (tagFilterMode === 'or') {
    for (const tag of activeTagFilters) {
      if (tags.includes(tag)) return true
    }
    return false
  }
  for (const tag of activeTagFilters) {
    if (!tags.includes(tag)) return false
  }
  return true
}

/**
 * Upsert a site into the store list. Returns normalized row or null.
 * Also clears hidden and may add tag-filter bypass for newly unmatched sites.
 * @param {object} store
 * @param {unknown} site
 */
export function registerSite(store, site) {
  const row = normalizeSiteFromApi(site)
  if (!row || !store?.sites) return null
  const list = store.sites.list
  const ix = list.findIndex((s) => s.slug === row.slug)
  if (ix >= 0) list[ix] = row
  else list.push(row)
  store.sites.hidden?.delete?.(row.slug)
  if (!sitePassesTagFilter(row, store)) {
    store.sites.tagFilterBypass?.add?.(row.slug)
  }
  return row
}

/**
 * Remove a site from the store list and related site Sets.
 * @param {object} store
 * @param {string} slug
 */
export function unregisterSite(store, slug) {
  if (!store?.sites || !slug) return
  const idx = store.sites.list.findIndex((s) => s.slug === slug)
  if (idx >= 0) store.sites.list.splice(idx, 1)
  store.sites.hidden?.delete?.(slug)
  store.sites.tagFilterBypass?.delete?.(slug)
  if (store.ui?.selectedSlug === slug) store.ui.selectedSlug = null
}

/** @param {{ sites?: { list?: object[] } }} store */
export function allProjectTags(store) {
  const found = new Set()
  for (const site of store?.sites?.list || []) {
    for (const tag of siteTags(site)) found.add(tag)
  }
  return [...found].sort((a, b) => a.localeCompare(b))
}

/**
 * Sites in the current map view when In view is on. Otherwise the full list.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function viewportSites(store, { map, mapReady } = {}) {
  const list = store?.sites?.list || []
  if (!store.ui?.filterByViewport || !map || !mapReady) return list
  return list.filter((site) =>
    coordVisibleInMapViewport(map, site.lon, site.lat, { mapReady })
  )
}

/**
 * Tag chips for the sidebar. In view limits chips to sites on screen.
 * Selected filters stay visible so they can be cleared after a pan.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function sidebarTags(store, opts = {}) {
  const found = new Set()
  for (const site of viewportSites(store, opts)) {
    for (const tag of siteTags(site)) found.add(tag)
  }
  for (const tag of store.ui?.tagFilters || []) found.add(tag)
  return [...found].sort((a, b) => a.localeCompare(b))
}

/** @param {{ sites?: { list?: object[] }, ui?: { tagFilters?: Set<string>, tagFilterMode?: string } }} store */
export function tagFilteredSites(store) {
  const list = store?.sites?.list || []
  if (!store?.ui?.tagFilters?.size) return list
  return list.filter((site) => sitePassesTagFilter(site, store))
}

/**
 * Sidebar-visible sites: In view first, then tag filter, hidden/bypass.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function sidebarSites(store, opts = {}) {
  const list = viewportSites(store, opts).filter((site) => {
    if (store.sites.hidden.has(site.slug) && !store.sites.tagFilterBypass.has(site.slug)) {
      return false
    }
    return sitePassesTagFilter(site, store)
  })
  return [...list].sort((a, b) => compareHuman(a.name, b.name))
}

/**
 * Mark KML preview points that sit within IMPORT_DEDUPE_METERS of an existing site.
 * @param {object[]} points
 * @param {object[]} sites
 */
export function annotateImportPoints(points, sites) {
  const list = Array.isArray(sites) ? sites : []
  return (Array.isArray(points) ? points : []).map((point) => {
    let nearest = null
    let nearestDist = Infinity
    for (const site of list) {
      const dist = haversineMeters(point.lat, point.lon, site.lat, site.lon)
      if (dist < nearestDist) {
        nearestDist = dist
        nearest = site
      }
    }
    const duplicate = nearest !== null && nearestDist <= IMPORT_DEDUPE_METERS
    return {
      name: point.name,
      lat: point.lat,
      lon: point.lon,
      ignored: duplicate,
      duplicate,
      duplicateDistM: duplicate ? Math.round(nearestDist) : null,
      duplicateSlug: duplicate ? nearest.slug : null,
      duplicateName: duplicate ? nearest.name : null,
    }
  })
}

/**
 * @param {object} pending tag -> 'all' | 'none'
 * @param {Map<string, number>|Record<string, number>} initialCounts
 * @param {string[]} extraTags
 */
export function bulkTagListTags(pending, initialCounts, extraTags = []) {
  const found = new Set([
    ...Object.keys(initialCounts instanceof Map ? Object.fromEntries(initialCounts) : initialCounts || {}),
    ...Object.keys(pending || {}),
    ...extraTags,
  ])
  return [...found].sort((a, b) => a.localeCompare(b))
}

/**
 * @param {string} tag
 * @param {number} total
 * @param {Record<string, string>} pending
 * @param {Record<string, number>} initialCounts
 */
export function bulkTagVisualState(tag, total, pending, initialCounts) {
  if (!total) return 'none'
  const next = pending?.[tag]
  if (next === 'all') return 'full'
  if (next === 'none') return 'none'
  const count = initialCounts?.[tag] || 0
  if (count === 0) return 'none'
  if (count >= total) return 'full'
  return 'partial'
}

/**
 * @param {string[]} slugs
 * @param {Record<string, string>} pending
 * @param {Record<string, number>} initialCounts
 */
export function computeBulkTagOps(slugs, pending, initialCounts) {
  const total = slugs.length
  const addTags = []
  const removeTags = []
  for (const [tag, next] of Object.entries(pending || {})) {
    const count = initialCounts?.[tag] || 0
    if (next === 'all' && count < total) addTags.push(tag)
    if (next === 'none' && count > 0) removeTags.push(tag)
  }
  return { addTags, removeTags }
}

/** @param {object[]} listedSites */
export function bulkTagInitialCounts(listedSites) {
  /** @type {Record<string, number>} */
  const counts = {}
  for (const site of listedSites || []) {
    for (const tag of siteTags(site)) {
      counts[tag] = (counts[tag] || 0) + 1
    }
  }
  return counts
}
