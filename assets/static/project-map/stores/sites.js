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
 * Toggle `tag` in a tag list. Returns a new array.
 * @param {string[]|null|undefined} tags
 * @param {string} tag
 */
export function toggleTag(tags, tag) {
  const value = String(tag || '').trim()
  const list = [...(tags || [])]
  if (!value) return list
  const ix = list.indexOf(value)
  if (ix >= 0) list.splice(ix, 1)
  else list.push(value)
  return list
}

/**
 * Project tags plus any selected values not yet on a site (new chips).
 * @param {string[]} projectTags
 * @param {string[]} selected
 */
export function tagChipChoices(projectTags, selected = []) {
  return [...new Set([...(projectTags || []), ...(selected || [])])].sort((a, b) =>
    a.localeCompare(b),
  )
}

/**
 * Rebuild sites.hidden from manualHidden plus active tag filters.
 * @param {object} store
 */
export function syncTagFilterVisibility(store) {
  if (!store?.sites) return
  const manual = store.sites.manualHidden
  const hidden = new Set()
  const hasTags = !!store.ui?.tagFilters?.size
  for (const site of store.sites.list || []) {
    const slug = site.slug
    if (manual.has(slug)) {
      hidden.add(slug)
      continue
    }
    if (hasTags && !sitePassesTagFilter(site, store)) {
      hidden.add(slug)
    }
  }
  store.sites.hidden.clear()
  for (const slug of hidden) store.sites.hidden.add(slug)
  store.sites.revision = (store.sites.revision || 0) + 1
}

/** @param {object} store @param {string} slug */
export function isSiteMapVisible(store, slug) {
  return !store.sites.hidden.has(slug)
}

/** @param {object} store @param {string} slug */
export function toggleSiteManualHidden(store, slug) {
  const value = String(slug || '').trim()
  if (!value || !store?.sites) return
  if (store.sites.manualHidden.has(value)) store.sites.manualHidden.delete(value)
  else store.sites.manualHidden.add(value)
  syncTagFilterVisibility(store)
}

/**
 * Upsert sites into the store list.
 * @param {object} store
 * @param {unknown[]} sites
 */
export function registerSites(store, sites) {
  const rows = []
  for (const site of sites || []) {
    const row = normalizeSiteFromApi(site)
    if (!row) continue
    rows.push(row)
  }
  if (!rows.length || !store?.sites) return rows
  const list = store.sites.list
  for (const row of rows) {
    const ix = list.findIndex((site) => site.slug === row.slug)
    if (ix >= 0) list[ix] = row
    else list.push(row)
  }
  syncTagFilterVisibility(store)
  return rows
}

/**
 * Upsert a site into the store list. Returns normalized row or null.
 * @param {object} store
 * @param {unknown} site
 */
export function registerSite(store, site) {
  return registerSites(store, [site])[0] || null
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
  store.sites.manualHidden?.delete?.(slug)
  if (store.ui?.selectedSlug === slug) store.ui.selectedSlug = null
  store.sites.revision = (store.sites.revision || 0) + 1
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
  void store.sites?.revision
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
 * Sidebar site rows: viewport scope when In view is on; always lists all scoped sites.
 * @param {object} store
 * @param {{ map?: object, mapReady?: boolean }} [opts]
 */
export function sidebarSites(store, opts = {}) {
  void store.sites?.revision
  return [...viewportSites(store, opts)].sort((a, b) => compareHuman(a.name, b.name))
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
