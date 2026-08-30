/** REST path builders for `/api/p/:projectSlug/...`. */

export function linksApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/links`
}

export function siteLinksApiUrl(projectSlug, slug) {
  return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}/links`
}

export function linksWarmApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/links/warm`
}

export function warmPrioritiesApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/warm/priorities`
}

export function sitesApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/sites`
}

export function sitesImportPreviewApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/sites/import/preview`
}

export function sitesImportApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/sites/import`
}

export function sitesTagsBulkApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/sites/tags/bulk`
}

export function siteApiUrl(projectSlug, slug) {
  return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}`
}

export const siteDeleteUrl = siteApiUrl

export function sitesPrefetchUrl(projectSlug, lat, lon, excludeSite) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
  })
  if (excludeSite) params.set('exclude_site', excludeSite)
  return `/api/p/${projectSlug}/sites/prefetch?${params}`
}

export function landApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/land`
}

export function landSidebarApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/land/sidebar`
}

export function landDataGdbsUrl(projectSlug) {
  return `/api/p/${projectSlug}/land/data-gdbs`
}

export function landImportPreviewApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/land/import/preview`
}

export function landImportApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/land/import`
}

export function landSourceApiUrl(projectSlug, sourceId) {
  return `/api/p/${projectSlug}/land/sources/${encodeURIComponent(sourceId)}`
}

export function landLayerGeoJsonUrl(projectSlug, sourceId, layer) {
  return `/api/p/${projectSlug}/land/sources/${encodeURIComponent(sourceId)}/layers/${encodeURIComponent(layer)}/geojson`
}

export function landOverlayGeoJsonUrl(projectSlug, kind) {
  return `/api/p/${projectSlug}/land/overlays/${encodeURIComponent(kind)}/geojson`
}

export function landOverlayPartGeoJsonUrl(projectSlug, kind, sourceId) {
  return `/api/p/${projectSlug}/land/overlays/${encodeURIComponent(kind)}/sources/${encodeURIComponent(sourceId)}/geojson`
}

export function landPreviewGeoJsonUrl(projectSlug, path, layer) {
  const params = new URLSearchParams({ path, layer })
  return `/api/p/${projectSlug}/land/preview/geojson?${params}`
}

export function landPreviewCacheKey(path, layer) {
  return `preview|${path}|${layer}`
}

export function landLayerCacheKey(sourceId, layer, digest) {
  const d = digest ? String(digest) : ''
  return d ? `layer|${sourceId}|${layer}|${d}` : `layer|${sourceId}|${layer}`
}

export function landFieldsApiUrl(projectSlug, path, layer) {
  const params = new URLSearchParams({ path, layer })
  return `/api/p/${projectSlug}/land/import/preview/fields?${params}`
}

export function landValuesApiUrl(projectSlug, path, layer, field) {
  const params = new URLSearchParams({ path, layer, field })
  return `/api/p/${projectSlug}/land/import/preview/values?${params}`
}

export function landPreviewGeoJsonPostUrl(projectSlug) {
  return `/api/p/${projectSlug}/land/preview/geojson`
}

export function projectEventsUrl(projectSlug) {
  return `/api/p/${projectSlug}/events`
}

export function viewshedWarmUrl(projectSlug, siteSlug, params) {
  return `/api/p/${projectSlug}/viewsheds/${siteSlug}/warm?${params}`
}

export function viewshedPrefetchWarmUrl(projectSlug, params) {
  return `/api/p/${projectSlug}/viewsheds/prefetch/warm?${params}`
}

export function viewshedMetaUrl(projectSlug, siteSlug, params, lat, lon) {
  if (lat != null && lon != null) {
    const q = new URLSearchParams(params)
    q.set('lat', String(lat))
    q.set('lon', String(lon))
    return `/api/p/${projectSlug}/viewsheds/prefetch?${q}`
  }
  return `/api/p/${projectSlug}/viewsheds/${siteSlug}?${params}`
}

export function viewshedPrefetchMetaUrl(projectSlug, params) {
  return `/api/p/${projectSlug}/viewsheds/prefetch?${params}`
}

export function viewshedIndexUrl(projectSlug, params) {
  return `/api/p/${projectSlug}/viewsheds/index?${params}`
}

export function seekPlanUrl(projectSlug) {
  return `/api/p/${projectSlug}/seek/plan`
}

export function seekScanProgressUrl(projectSlug) {
  return `/api/p/${projectSlug}/seek/scan-progress`
}

export function seekPlanConvertToSitesUrl(projectSlug) {
  return `/api/p/${projectSlug}/seek/plan/convert-to-sites`
}

export function seekCandidatesUrl(projectSlug, params) {
  return `/api/p/${projectSlug}/seek/candidates?${params}`
}
