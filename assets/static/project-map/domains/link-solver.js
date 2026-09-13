// @ts-check

import * as apiUrls from '../api/urls.js'
import { LINK_SOLVER_PEAKS_LAYER, LINK_SOLVER_VIEWSHED_PREFIX } from '../constants.js'
import { applyLinkSolverLayers, removeLinkSolverLayers } from '../map/link-solver-layers.js'
import { setPendingCoords } from '../stores/viewshed.js'

const PROGRESS_POLL_MS = 250
const PROGRESS_IDLE_GRACE_MS = 3000
const PROGRESS_TIMEOUT_MS = 180_000

function sleepMs(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

/**
 * @param {{
 *   store: object,
 *   projectSlug: string,
 *   getMap: () => import('maplibregl').Map | null,
 *   getMapReady: () => boolean,
 *   clearAlternates?: () => void,
 *   clearFortify?: () => void,
 *   registerSiteFromApi?: (site: object) => void,
 *   mergeSeededLinks?: (payload: object) => void,
 *   loadSingleSiteLinks?: (slug: string) => Promise<void>,
 *   loadSiteLinks?: () => Promise<void>,
 *   raiseSiteLayers?: () => void,
 *   getSiteBySlug?: (slug: string) => object | undefined,
 *   findSiteLinkFeature?: (a: string, b: string) => object | null,
 *   selectSite?: (slug: string) => void,
 *   getViewshed?: () => object,
 *   mapToolLinkSolver?: HTMLElement | null,
 *   linkSolverPanel?: HTMLElement | null,
 *   updatePinOverlays?: () => void,
 * }} ctx
 */
export function createLinkSolverDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    clearAlternates,
    clearFortify,
    registerSiteFromApi,
    mergeSeededLinks,
    loadSingleSiteLinks,
    loadSiteLinks,
    raiseSiteLayers,
    getSiteBySlug,
    findSiteLinkFeature,
    selectSite,
    getViewshed,
    mapToolLinkSolver,
    linkSolverPanel,
    updatePinOverlays,
  } = ctx

  let fetchEpoch = 0
  /** @type {AbortController|null} */
  let fetchAbort = null
  let hopViewshedGen = 0
  /** @type {Set<string>} */
  const hopViewshedSlugs = new Set()
  /** @type {Map<string, { lat: number, lon: number }>} */
  const hopViewshedCoords = new Map()

  function vs() {
    return getViewshed?.()
  }

  function setStatus(text) {
    store.linkSolver.statusText = text || ''
  }

  function setScanning(on) {
    store.linkSolver.scanning = !!on
  }

  function bumpFetchEpoch() {
    fetchEpoch += 1
    if (fetchAbort) {
      fetchAbort.abort()
      fetchAbort = null
    }
    return fetchEpoch
  }

  function clearLinkSolverLayers() {
    const map = getMap()
    if (map && getMapReady()) removeLinkSolverLayers(map)
  }

  function hopViewshedSlug(index) {
    return `${LINK_SOLVER_VIEWSHED_PREFIX}${index}`
  }

  function clearHopViewsheds() {
    hopViewshedGen += 1
    const vsDomain = vs()
    for (const slug of hopViewshedSlugs) {
      store.viewshed.pendingEpoch.delete(slug)
      store.viewshed.loading.delete(slug)
      store.viewshed.visible.delete(slug)
      vsDomain?.clearViewshedLoadingState?.(slug)
      vsDomain?.removeViewshedLayer?.(slug)
    }
    hopViewshedSlugs.clear()
    hopViewshedCoords.clear()
    updatePinOverlays?.()
  }

  async function loadHopViewshed(slug, lat, lon, gen) {
    const vsDomain = vs()
    if (!vsDomain) return
    store.viewshed.visible.set(slug, true)
    if (await vsDomain.tryLoadCoordViewshedFromCache?.(slug, lat, lon)) {
      vsDomain.raiseViewshedLayers?.()
      return
    }
    vsDomain.removeViewshedLayer?.(slug)
    store.viewshed.loading.add(slug)
    const epoch = vsDomain.getViewshedLoadEpoch?.()
    store.viewshed.pendingEpoch.set(slug, epoch)
    setPendingCoords(store, slug, lat, lon)
    updatePinOverlays?.()
    try {
      const resp = await fetch(vsDomain.viewshedPrefetchWarmUrl(lat, lon), { method: 'POST' })
      if (hopViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(slug) !== epoch) return
      if (!resp.ok) {
        store.viewshed.loading.delete(slug)
        store.viewshed.pendingEpoch.delete(slug)
        updatePinOverlays?.()
        return
      }
      const ready = await resp.json()
      if (hopViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(slug) !== epoch) return
      if (ready && ready.status === 'ready') {
        vsDomain.handleViewshedReady({ ...ready, slug }, epoch)
        vsDomain.raiseViewshedLayers?.()
      }
    } catch (_) {
      if (hopViewshedGen === gen) {
        store.viewshed.loading.delete(slug)
        store.viewshed.pendingEpoch.delete(slug)
        updatePinOverlays?.()
      }
    }
  }

  function syncLinkSolverChrome() {
    const open = !!store.linkSolver.panelOpen
    if (mapToolLinkSolver) {
      mapToolLinkSolver.classList.toggle('map-toolbar-tool--active', open)
      mapToolLinkSolver.setAttribute('aria-pressed', open ? 'true' : 'false')
    }
    if (linkSolverPanel) linkSolverPanel.hidden = !open
  }

  function applyPayload(payload) {
    store.linkSolver.payload = payload
    const map = getMap()
    if (!map || !getMapReady()) return
    applyLinkSolverLayers(map, payload, {
      selectedRouteId: store.linkSolver.selectedRouteId,
      raiseSiteLayers,
    })
  }

  function routeById(routeId) {
    const routes = displayRoutes()
    return routes.find((r) => r.route_id === routeId) || null
  }

  function displayRoutes() {
    const base = store.linkSolver.payload?.routes
    const rows = Array.isArray(base) ? [...base] : []
    const seen = new Set(rows.map((r) => r.route_id))
    for (const row of store.linkSolver.likeRoutes || []) {
      if (!row?.route_id || seen.has(row.route_id)) continue
      seen.add(row.route_id)
      rows.push(row)
    }
    return rows
  }

  function peakAccessBySlug() {
    /** @type {Map<string, string|null>} */
    const map = new Map()
    const features = store.linkSolver.payload?.peaks?.features
    if (!Array.isArray(features)) return map
    for (const feature of features) {
      const props = feature.properties || {}
      const slug = props.peak_slug || props.slug
      if (slug) map.set(String(slug), props.access_difficulty ?? null)
    }
    return map
  }

  function fitRouteBounds(route) {
    const map = getMap()
    if (!map || !getMapReady() || !route) return
    const a = store.linkSolver.a
    const b = store.linkSolver.b
    const siteA = a ? getSiteBySlug?.(a) : null
    const siteB = b ? getSiteBySlug?.(b) : null
    const lons = []
    const lats = []
    if (siteA) {
      lons.push(Number(siteA.lon))
      lats.push(Number(siteA.lat))
    }
    if (siteB) {
      lons.push(Number(siteB.lon))
      lats.push(Number(siteB.lat))
    }
    for (const peak of route.peaks || []) {
      if (Number.isFinite(peak.lon)) lons.push(Number(peak.lon))
      if (Number.isFinite(peak.lat)) lats.push(Number(peak.lat))
    }
    if (lons.length < 2 || lats.length < 2) return
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 80, duration: 600, maxZoom: 13 },
    )
  }

  async function loadRouteHopViewsheds(route) {
    clearHopViewsheds()
    const peaks = route?.peaks
    if (!Array.isArray(peaks) || !peaks.length) return
    const gen = ++hopViewshedGen
    for (let i = 0; i < peaks.length; i += 1) {
      const peak = peaks[i]
      const lat = Number(peak.lat)
      const lon = Number(peak.lon)
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue
      const slug = hopViewshedSlug(i)
      hopViewshedSlugs.add(slug)
      hopViewshedCoords.set(slug, { lat, lon })
      await loadHopViewshed(slug, lat, lon, gen)
      if (hopViewshedGen !== gen) return
    }
    vs()?.raiseViewshedLayers?.()
    updatePinOverlays?.()
  }

  function selectRoute(routeId) {
    if (!routeId) return
    store.linkSolver.selectedRouteId = routeId
    applyPayload(store.linkSolver.payload)
    const route = routeById(routeId)
    if (!route) return
    fitRouteBounds(route)
    void loadRouteHopViewsheds(route)
    const hops = route.hops ?? '?'
    const bottleneck = route.bottleneck_db
    const bits = [`${hops} hop${hops === 1 ? '' : 's'}`]
    if (Number.isFinite(bottleneck)) bits.push(`${Number(bottleneck).toFixed(1)} dB bottleneck`)
    setStatus(`Selected route — ${bits.join(', ')}`)
  }

  function checkAlreadyLinked(a, b) {
    if (!a || !b) {
      store.linkSolver.alreadyLinked = false
      return false
    }
    const linked = Boolean(findSiteLinkFeature?.(a, b))
    store.linkSolver.alreadyLinked = linked
    return linked
  }

  function clearLinkSolver({ closePanel = false } = {}) {
    bumpFetchEpoch()
    clearHopViewsheds()
    store.linkSolver.scanning = false
    store.linkSolver.statusText = ''
    store.linkSolver.payload = null
    store.linkSolver.selectedRouteId = null
    store.linkSolver.likeOf = null
    store.linkSolver.likeRoutes = []
    store.linkSolver.accepting = false
    if (closePanel) store.linkSolver.panelOpen = false
    clearLinkSolverLayers()
    syncLinkSolverChrome()
  }

  function toggleLinkSolverPanel(force) {
    const next = typeof force === 'boolean' ? force : !store.linkSolver.panelOpen
    store.linkSolver.panelOpen = next
    syncLinkSolverChrome()
    if (!next) clearLinkSolver()
  }

  async function pollUntilDone(a, b, expectedGen, signal, epoch) {
    const started = Date.now()
    for (;;) {
      if (signal?.aborted || epoch !== fetchEpoch) return { cancelled: true }
      if (Date.now() - started > PROGRESS_TIMEOUT_MS) {
        return { error: 'Link solver scan timed out' }
      }
      let resp
      try {
        resp = await fetch(apiUrls.linkSolverScanProgressUrl(projectSlug, a, b), { signal })
      } catch (err) {
        if (err?.name === 'AbortError') return { cancelled: true }
        await sleepMs(PROGRESS_POLL_MS)
        continue
      }
      if (!resp.ok) {
        await sleepMs(PROGRESS_POLL_MS)
        continue
      }
      const body = await resp.json().catch(() => ({}))
      if (body?.gen != null && body.gen !== expectedGen) return { cancelled: true }
      if (body?.progress?.detail) setStatus(body.progress.detail)
      if (body?.status === 'done' && body?.result) {
        return { payload: body.result }
      }
      if (body?.status === 'cancelled') return { cancelled: true }
      if (body?.status === 'error') {
        return { error: body.error || 'Link solver scan failed' }
      }
      if (body?.status === 'idle' && Date.now() - started > PROGRESS_IDLE_GRACE_MS) {
        return { error: 'Link solver scan lost sync — try again' }
      }
      await sleepMs(PROGRESS_POLL_MS)
    }
  }

  async function solve(a, b) {
    if (!a || !b || a === b) return
    if (checkAlreadyLinked(a, b)) {
      setStatus('These sites are already linked.')
      return
    }
    clearAlternates?.()
    clearFortify?.()
    const epoch = bumpFetchEpoch()
    store.linkSolver.a = a
    store.linkSolver.b = b
    store.linkSolver.selectedRouteId = null
    store.linkSolver.likeOf = null
    store.linkSolver.likeRoutes = []
    store.linkSolver.payload = null
    clearHopViewsheds()
    setScanning(true)
    setStatus('Starting link solver scan…')
    clearLinkSolverLayers()

    fetchAbort = new AbortController()
    const signal = fetchAbort.signal
    try {
      const resp = await fetch(
        apiUrls.linkSolverScanUrl(projectSlug, a, b, store.linkSolver.minRoutes),
        { signal },
      )
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}))
        const msg = errBody.error || `HTTP ${resp.status}`
        if (/already linked/i.test(msg)) store.linkSolver.alreadyLinked = true
        throw new Error(msg)
      }
      const accepted = await resp.json()
      const gen = accepted.gen
      const outcome = await pollUntilDone(a, b, gen, signal, epoch)
      if (fetchEpoch !== epoch || signal.aborted) return
      if (outcome.cancelled) return
      if (outcome.error) {
        setStatus(outcome.error)
        return
      }
      applyPayload(outcome.payload)
      const n = outcome.payload?.meta?.n_routes ?? outcome.payload?.routes?.length ?? 0
      setStatus(n ? `Found ${n} route(s)` : 'No RF routes between these sites')
      if (n && outcome.payload.routes?.[0]?.route_id) {
        selectRoute(outcome.payload.routes[0].route_id)
      }
    } catch (err) {
      if (signal.aborted || fetchEpoch !== epoch) return
      setStatus(String(err?.message || err))
    } finally {
      if (fetchEpoch === epoch) setScanning(false)
    }
  }

  async function loadMore() {
    const a = store.linkSolver.a
    const b = store.linkSolver.b
    if (!a || !b || store.linkSolver.scanning) return
    store.linkSolver.minRoutes += 5
    await solve(a, b)
  }

  async function moreLikeThis(routeId) {
    const a = store.linkSolver.a
    const b = store.linkSolver.b
    if (!a || !b || !routeId) return
    store.linkSolver.likeOf = routeId
    setStatus('Loading similar routes…')
    try {
      const resp = await fetch(apiUrls.linkSolverLikeUrl(projectSlug, a, b, routeId))
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}))
        throw new Error(errBody.error || `HTTP ${resp.status}`)
      }
      const body = await resp.json()
      store.linkSolver.likeRoutes = Array.isArray(body.routes) ? body.routes : []
      setStatus(
        store.linkSolver.likeRoutes.length
          ? `Added ${store.linkSolver.likeRoutes.length} similar route(s)`
          : 'No similar routes found',
      )
    } catch (err) {
      setStatus(String(err?.message || err))
    }
  }

  async function acceptSelectedRoute() {
    const routeId = store.linkSolver.selectedRouteId
    const a = store.linkSolver.a
    const b = store.linkSolver.b
    const route = routeById(routeId)
    if (!routeId || !a || !b || !route) {
      throw new Error('Select a route first.')
    }
    const hops = (route.peaks || []).map((peak) => ({
      peak_slug: peak.peak_slug || peak.slug,
      lat: Number(peak.lat),
      lon: Number(peak.lon),
      name: peak.name || undefined,
    }))
    if (!hops.length) throw new Error('Route has no relay peaks.')
    store.linkSolver.accepting = true
    try {
      const resp = await fetch(apiUrls.linkSolverAcceptUrl(projectSlug), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ a, b, route_id: routeId, hops }),
      })
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}))
        throw new Error(errBody.error || `HTTP ${resp.status}`)
      }
      const payload = await resp.json()
      for (const site of payload.created || []) {
        if (site?.slug) registerSiteFromApi?.(site)
      }
      mergeSeededLinks?.(payload)
      await loadSiteLinks?.()
      const firstCreated = payload.created?.[0]?.slug
      clearLinkSolver({ closePanel: true })
      if (firstCreated) selectSite?.(firstCreated)
      else if (a) selectSite?.(a)
      return payload
    } finally {
      store.linkSolver.accepting = false
    }
  }

  function installMapHandlers(map) {
    map.on('click', LINK_SOLVER_PEAKS_LAYER, (ev) => {
      if (!store.linkSolver.panelOpen || !store.linkSolver.payload) return
      const routeId = store.linkSolver.selectedRouteId
      if (!routeId) return
      selectRoute(routeId)
    })
  }

  function install() {
    mapToolLinkSolver?.addEventListener('click', () => toggleLinkSolverPanel())
    syncLinkSolverChrome()
  }

  return {
    toggleLinkSolverPanel,
    clearLinkSolver,
    solve,
    selectRoute,
    loadMore,
    moreLikeThis,
    acceptSelectedRoute,
    displayRoutes,
    peakAccessBySlug,
    checkAlreadyLinked,
    getLinkSolverHopViewshedSlugs: () => [...hopViewshedSlugs],
    getLinkSolverHopViewshedCoords: () => hopViewshedCoords,
    syncLinkSolverHopViewsheds: () => {
      const route = routeById(store.linkSolver.selectedRouteId)
      if (route) void loadRouteHopViewsheds(route)
    },
    install,
    installMapHandlers,
  }
}
