// @ts-check

import { apiFetch, apiJson } from '../api/client.js'
import * as apiUrls from '../api/urls.js'
import { normalizeSiteFromApi, registerSite, registerSites, unregisterSite } from '../stores/sites.js'

/**
 * Site CRUD against the project API; mutates store via register/unregister.
 * @param {{ store: object, projectSlug: string }} ctx
 */
export function createSitesDomain({ store, projectSlug }) {
  /** @param {Record<string, unknown>} body */
  async function createSite(body) {
    const payload = /** @type {Record<string, unknown>} */ (
      await apiJson(apiUrls.sitesApiUrl(projectSlug), 'POST', body)
    )
    const site = normalizeSiteFromApi(payload.site)
    if (site) registerSite(store, site)
    return { site, payload, ok: true }
  }

  /**
   * @param {string} slug
   * @param {Record<string, unknown>} body
   */
  async function updateSite(slug, body) {
    const payload = /** @type {Record<string, unknown>} */ (
      await apiJson(apiUrls.siteApiUrl(projectSlug, slug), 'PATCH', body)
    )
    const site = normalizeSiteFromApi(payload.site)
    if (site) registerSite(store, site)
    return { site, payload, promoted: !!payload.promoted, ok: true }
  }

  /** @param {string} slug */
  async function deleteSite(slug) {
    await apiFetch(apiUrls.siteDeleteUrl(projectSlug, slug), { method: 'DELETE' })
    unregisterSite(store, slug)
    return { ok: true }
  }

  /** @param {FormData} formData */
  async function previewImport(formData) {
    return apiFetch(apiUrls.sitesImportPreviewApiUrl(projectSlug), {
      method: 'POST',
      body: formData,
    })
  }

  /** @param {Record<string, unknown>} body */
  async function previewImportJson(body) {
    return apiJson(apiUrls.sitesImportPreviewApiUrl(projectSlug), 'POST', body)
  }

  /** @param {Record<string, unknown>} body */
  async function importSites(body) {
    const payload = /** @type {Record<string, unknown>} */ (
      await apiJson(apiUrls.sitesImportApiUrl(projectSlug), 'POST', body)
    )
    const imported = Array.isArray(payload.sites) ? payload.sites : []
    const sites = registerSites(store, imported)
    return { sites, payload, ok: true }
  }

  /** @param {Record<string, unknown>} body */
  async function bulkTagSites(body) {
    const payload = /** @type {Record<string, unknown>} */ (
      await apiJson(apiUrls.sitesTagsBulkApiUrl(projectSlug), 'POST', body)
    )
    const updated = Array.isArray(payload.sites) ? payload.sites : []
    const sites = registerSites(store, updated)
    return { sites, payload, ok: true }
  }

  return {
    createSite,
    updateSite,
    deleteSite,
    previewImport,
    previewImportJson,
    importSites,
    bulkTagSites,
  }
}
