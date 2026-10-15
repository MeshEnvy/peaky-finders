/** REST path builders for `/api/p/:projectSlug/...`. */

export function linksApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/links`
}

export function siteLinksApiUrl(projectSlug, slug) {
  return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}/links`
}

export function linkPairApiUrl(projectSlug, slugA, slugB) {
  const params = new URLSearchParams({ a: slugA, b: slugB })
  return `/api/p/${projectSlug}/links/pair?${params}`
}

export function linkPairPostUrl(projectSlug) {
  return `/api/p/${projectSlug}/links/pair`
}

export function linksWarmApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/links/warm`
}

export function warmPrioritiesApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/warm/priorities`
}

export function placeElevApiUrl(projectSlug, lat, lon) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
  })
  return `/api/p/${projectSlug}/elev?${params}`
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

export function sitesExportKmzApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/sites/export/kmz`
}

export function siteApiUrl(projectSlug, slug) {
  return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}`
}

export function sitesPrefetchUrl(projectSlug, lat, lon, excludeSite, previewId) {
  const params = new URLSearchParams({
    lat: String(lat),
    lon: String(lon),
  })
  if (excludeSite) params.set('exclude_site', excludeSite)
  if (previewId) params.set('preview', String(previewId))
  return `/api/p/${projectSlug}/sites/prefetch?${params}`
}

export function peaksApiUrl(projectSlug) {
  return `/api/p/${projectSlug}/peaks`
}

/** @param {string} projectSlug @param {string} placeSlug @param {{ warm?: boolean, lat?: number, lon?: number, preview?: number }} [opts] */
export function placeAccessApiUrl(projectSlug, placeSlug, opts = {}) {
  const params = new URLSearchParams()
  if (opts.warm) params.set('warm', '1')
  if (Number.isFinite(opts.lat)) params.set('lat', String(opts.lat))
  if (Number.isFinite(opts.lon)) params.set('lon', String(opts.lon))
  if (opts.preview) params.set('preview', String(opts.preview))
  const qs = params.toString()
  const base = `/api/p/${projectSlug}/access/${encodeURIComponent(placeSlug)}`
  return qs ? `${base}?${qs}` : base
}

/** @param {string} projectSlug @param {string} placeSlug @param {{ lat?: number, lon?: number, preview?: number }} [opts] */
export function placeAccessWarmApiUrl(projectSlug, placeSlug, opts = {}) {
  const params = new URLSearchParams()
  if (Number.isFinite(opts.lat)) params.set('lat', String(opts.lat))
  if (Number.isFinite(opts.lon)) params.set('lon', String(opts.lon))
  if (opts.preview) params.set('preview', String(opts.preview))
  const qs = params.toString()
  const base = `/api/p/${projectSlug}/access/${encodeURIComponent(placeSlug)}/warm`
  return qs ? `${base}?${qs}` : base
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

/** @param {string} projectSlug @param {'paved'|'jeep'|'hiking'} layer @param {{ west: number, south: number, east: number, north: number }} bbox @param {number} zoom map zoom (integer) */
export function roadsNetworkLayerGeoJsonUrl(projectSlug, layer, bbox, zoom) {
  const q = new URLSearchParams({
    west: String(bbox.west),
    south: String(bbox.south),
    east: String(bbox.east),
    north: String(bbox.north),
    z: String(Math.round(zoom)),
  })
  return `/api/p/${projectSlug}/roads/network/${layer}/geojson?${q}`
}

/** Full OSM + GTLF inventory (map "All" overlay). */
export function roadsNetworkAllGeoJsonUrl(projectSlug, bbox) {
  const q = new URLSearchParams({
    west: String(bbox.west),
    south: String(bbox.south),
    east: String(bbox.east),
    north: String(bbox.north),
  })
  return `/api/p/${projectSlug}/roads/network/all/geojson?${q}`
}

/** Combined paved + jeep from one drive-graph pass. */
export function roadsNetworkDriveGeoJsonUrl(projectSlug, bbox, zoom) {
  const q = new URLSearchParams({
    west: String(bbox.west),
    south: String(bbox.south),
    east: String(bbox.east),
    north: String(bbox.north),
    z: String(Math.round(zoom)),
  })
  return `/api/p/${projectSlug}/roads/network/drive/geojson?${q}`
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

export function linkSolverScanUrl(projectSlug, slugA, slugB, minRoutes, excludePeaks = []) {
  const params = new URLSearchParams({ a: slugA, b: slugB })
  if (minRoutes != null) params.set('min_routes', String(minRoutes))
  if (excludePeaks?.length) params.set('exclude_peaks', excludePeaks.join(','))
  return `/api/p/${projectSlug}/link-solver?${params}`
}

export function linkSolverScanProgressUrl(projectSlug, slugA, slugB) {
  const params = new URLSearchParams({ a: slugA, b: slugB })
  return `/api/p/${projectSlug}/link-solver/scan-progress?${params}`
}

export function linkSolverLikeUrl(projectSlug, slugA, slugB, routeId) {
  const params = new URLSearchParams({ a: slugA, b: slugB, route_id: routeId })
  return `/api/p/${projectSlug}/link-solver/like?${params}`
}

export function linkSolverAcceptUrl(projectSlug) {
  return `/api/p/${projectSlug}/link-solver/accept`
}

export function alternatesScanUrl(projectSlug, siteSlug, anchorSlugs = []) {
  const params = new URLSearchParams({ site: siteSlug })
  if (anchorSlugs?.length) params.set('anchors', anchorSlugs.join(','))
  return `/api/p/${projectSlug}/alternates?${params}`
}

export function alternatesScanProgressUrl(projectSlug, siteSlug, anchorSlugs = []) {
  const params = new URLSearchParams({ site: siteSlug })
  if (anchorSlugs?.length) params.set('anchors', anchorSlugs.join(','))
  return `/api/p/${projectSlug}/alternates/scan-progress?${params}`
}

export function fortifyScanUrl(projectSlug, slugA, slugB, excludePeaks = []) {
  const params = new URLSearchParams({ a: slugA, b: slugB })
  if (excludePeaks?.length) params.set('exclude_peaks', excludePeaks.join(','))
  return `/api/p/${projectSlug}/fortify?${params}`
}

export function fortifyScanProgressUrl(projectSlug, slugA, slugB) {
  const params = new URLSearchParams({ a: slugA, b: slugB })
  return `/api/p/${projectSlug}/fortify/scan-progress?${params}`
}

export function roadsBookApiUrl(projectSlug) {
  return `/api/p/${encodeURIComponent(projectSlug)}/roads`
}
