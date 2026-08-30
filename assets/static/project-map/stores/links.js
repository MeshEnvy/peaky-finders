// @ts-check

import { warmPrioritiesApiUrl } from '../api/urls.js'
import { apiJson } from '../api/client.js'

/**
 * Commit the site-links mesh payload into the reactive store.
 * MapLibre updates go through map/links-layers.js from the domain app.
 * @param {object} store
 * @param {object|null} payload
 */
export function setLinksPayload(store, payload) {
  store.links.payload = payload
}

/**
 * Bump warm queue priorities for viewport-visible / interactive sites.
 * Best-effort: callers typically `.catch(() => {})`.
 * @param {string} projectSlug
 * @param {string[]} slugs
 * @param {number} priority
 */
export async function bumpWarmPriorities(projectSlug, slugs, priority) {
  const list = Array.isArray(slugs) ? slugs.filter(Boolean) : []
  if (!list.length) return
  await apiJson(warmPrioritiesApiUrl(projectSlug), 'POST', { slugs: list, priority })
}
