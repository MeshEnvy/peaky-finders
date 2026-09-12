// @ts-check

/**
 * Parse `/?site=…` or `/?peak=…` plus optional camera query params.
 * @param {string} [search]
 */
export function parseDeepLink(search = typeof window !== 'undefined' ? window.location.search : '') {
  const raw = search.startsWith('?') ? search.slice(1) : search
  if (!raw) return {}
  const params = new URLSearchParams(raw)
  /** @type {{ site?: string, peak?: string, center?: [number, number], zoom?: number, bearing?: number, pitch?: number }} */
  const out = {}
  const site = params.get('site')
  const peak = params.get('peak')
  if (site) out.site = site
  else if (peak) out.peak = peak
  const lat = Number(params.get('lat'))
  const lon = Number(params.get('lon'))
  const z = Number(params.get('z'))
  const bearing = Number(params.get('bearing'))
  const pitch = Number(params.get('pitch'))
  if (Number.isFinite(lat) && Number.isFinite(lon)) out.center = [lon, lat]
  if (Number.isFinite(z)) out.zoom = z
  if (Number.isFinite(bearing) && Math.abs(bearing) > 0.05) out.bearing = bearing
  if (Number.isFinite(pitch) && Math.abs(pitch) > 0.05) out.pitch = pitch
  return out
}

/** @param {{ center?: [number, number], zoom?: number }} link */
export function deepLinkHasCamera(link) {
  return Array.isArray(link.center) && link.center.length === 2 && Number.isFinite(link.zoom)
}

/**
 * URL camera overrides localStorage snapshot for initial map creation.
 * @param {Record<string, unknown>|null} saved
 * @param {ReturnType<typeof parseDeepLink>} link
 */
export function mergeDeepLinkIntoSavedMapState(saved, link) {
  if (!deepLinkHasCamera(link)) return saved
  const base = saved && typeof saved === 'object' ? { ...saved } : { basemap: 'street' }
  base.center = link.center
  base.zoom = link.zoom
  base.bearing = link.bearing ?? 0
  base.pitch = link.pitch ?? 0
  return base
}

/**
 * @param {{ site?: string, peak?: string, center?: [number, number], zoom?: number, bearing?: number, pitch?: number }} state
 */
export function serializeDeepLink(state) {
  const params = new URLSearchParams()
  if (state.site) params.set('site', state.site)
  else if (state.peak) params.set('peak', state.peak)
  if (state.center) {
    params.set('lat', state.center[1].toFixed(6))
    params.set('lon', state.center[0].toFixed(6))
  }
  if (state.zoom != null && Number.isFinite(state.zoom)) params.set('z', state.zoom.toFixed(2))
  if (state.bearing != null && Math.abs(state.bearing) > 0.05) {
    params.set('bearing', state.bearing.toFixed(1))
  }
  if (state.pitch != null && Math.abs(state.pitch) > 0.05) {
    params.set('pitch', state.pitch.toFixed(1))
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

/**
 * @param {maplibregl.Map} map
 * @param {{ site?: string, peak?: string }} selection
 */
export function captureDeepLinkFromMap(map, selection) {
  const c = map.getCenter()
  return {
    site: selection.site || undefined,
    peak: selection.peak || undefined,
    center: [c.lng, c.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  }
}

/**
 * @param {ReturnType<typeof captureDeepLinkFromMap>} state
 * @param {{ replace?: boolean }} [opts]
 */
export function writeDeepLink(state, { replace = false } = {}) {
  if (typeof window === 'undefined') return
  const path = window.location.pathname
  const hash = window.location.hash || ''
  const url = `${path}${serializeDeepLink(state)}${hash}`
  if (replace) window.history.replaceState({ deepLink: state }, '', url)
  else window.history.pushState({ deepLink: state }, '', url)
}

/** @returns {ReturnType<typeof parseDeepLink>} */
export function readDeepLinkFromLocation() {
  if (typeof window === 'undefined') return {}
  const fromState = window.history.state?.deepLink
  if (fromState && typeof fromState === 'object') return fromState
  return parseDeepLink()
}
