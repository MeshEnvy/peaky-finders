// @ts-check

import * as apiUrls from '../api/urls.js'
import { FORTIFY_CANDIDATES_LAYER, FORTIFY_VIEWSHED_SLUG, viewshedLayerId } from '../constants.js'
import { applyFortifyLayers, removeFortifyLayers } from '../map/fortify-layers.js'

const PROGRESS_POLL_MS = 250
const PROGRESS_IDLE_GRACE_MS = 3000
const PROGRESS_TIMEOUT_MS = 120_000

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
 *   sitesDomain?: { createSite: (body: Record<string, unknown>) => Promise<{ site?: object }> },
 *   applySavedSiteToMap?: (site: object, lat: number, lon: number) => void,
 *   loadSingleSiteLinks?: (slug: string) => Promise<void>,
 *   raiseSiteLayers?: () => void,
 *   getSiteBySlug?: (slug: string) => object | undefined,
 *   getViewshed?: () => object,
 *   applyViewshedVisibilityForSite?: (slug: string) => void,
 *   siteAccessDomain?: {
 *     ingestAccess: (slug: string, access: object, site?: object) => void,
 *     refreshLayers: () => void,
 *   },
 * }} ctx
 */
export function createFortifyDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    clearAlternates,
    sitesDomain,
    applySavedSiteToMap,
    loadSingleSiteLinks,
    raiseSiteLayers,
    getSiteBySlug,
    getViewshed,
    applyViewshedVisibilityForSite,
    siteAccessDomain,
  } = ctx

  let fetchEpoch = 0
  /** @type {AbortController|null} */
  let fetchAbort = null
  let fortifyViewshedGen = 0
  /** @type {Set<string>} */
  const hiddenPreviewSiteSlugs = new Set()

  function vs() {
    return getViewshed?.()
  }

  function fortifyActive() {
    return !!store.fortify?.active && !!store.fortify.linkA && !!store.fortify.linkB
  }

  function setStatus(text) {
    store.fortify.statusText = text || ''
  }

  function setScanning(on) {
    store.fortify.scanning = !!on
  }

  function bumpFetchEpoch() {
    fetchEpoch += 1
    if (fetchAbort) {
      fetchAbort.abort()
      fetchAbort = null
    }
    return fetchEpoch
  }

  function clearFortifyLayers() {
    const map = getMap()
    if (map && getMapReady()) removeFortifyLayers(map)
  }

  function cancelFortifyViewshedLoad() {
    fortifyViewshedGen += 1
    store.viewshed.pendingEpoch.delete(FORTIFY_VIEWSHED_SLUG)
    store.viewshed.loading.delete(FORTIFY_VIEWSHED_SLUG)
    vs()?.clearViewshedLoadingState?.(FORTIFY_VIEWSHED_SLUG)
  }

  function dismissFortifyViewshedOverlay() {
    cancelFortifyViewshedLoad()
    vs()?.removeViewshedLayer?.(FORTIFY_VIEWSHED_SLUG)
    store.viewshed.visible.delete(FORTIFY_VIEWSHED_SLUG)
  }

  function hideSiteViewshedLayer(slug) {
    const map = getMap()
    if (!slug || !map) return
    const layerId = viewshedLayerId(slug)
    if (!map.getLayer(layerId)) return
    map.setLayoutProperty(layerId, 'visibility', 'none')
    hiddenPreviewSiteSlugs.add(slug)
  }

  function restoreHiddenPreviewSiteViewsheds() {
    for (const slug of hiddenPreviewSiteSlugs) {
      applyViewshedVisibilityForSite?.(slug)
    }
    hiddenPreviewSiteSlugs.clear()
  }

  function clearFortifyPreview() {
    dismissFortifyViewshedOverlay()
    restoreHiddenPreviewSiteViewsheds()
    store.fortify.accessSlug = null
    siteAccessDomain?.refreshLayers?.()
    vs()?.updatePinOverlays?.()
  }

  async function loadFortifyCoordViewshed(lat, lon) {
    const vsDomain = vs()
    if (!vsDomain) return
    const gen = ++fortifyViewshedGen
    store.viewshed.visible.set(FORTIFY_VIEWSHED_SLUG, true)
    if (await vsDomain.tryLoadCoordViewshedFromCache?.(FORTIFY_VIEWSHED_SLUG, lat, lon)) {
      vsDomain.raiseViewshedLayers?.()
      return
    }
    vsDomain.removeViewshedLayer?.(FORTIFY_VIEWSHED_SLUG)
    store.viewshed.loading.add(FORTIFY_VIEWSHED_SLUG)
    const epoch = vsDomain.getViewshedLoadEpoch?.()
    store.viewshed.pendingEpoch.set(FORTIFY_VIEWSHED_SLUG, epoch)
    vsDomain.updatePinOverlays?.()
    try {
      const resp = await fetch(vsDomain.viewshedPrefetchWarmUrl(lat, lon), { method: 'POST' })
      if (fortifyViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(FORTIFY_VIEWSHED_SLUG) !== epoch) return
      if (!resp.ok) {
        cancelFortifyViewshedLoad()
        vsDomain.updatePinOverlays?.()
        return
      }
      const ready = await resp.json()
      if (fortifyViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(FORTIFY_VIEWSHED_SLUG) !== epoch) return
      if (ready && ready.status === 'ready') {
        vsDomain.handleViewshedReady({ ...ready, slug: FORTIFY_VIEWSHED_SLUG }, epoch)
        vsDomain.raiseViewshedLayers?.()
      }
    } catch (_) {
      if (fortifyViewshedGen === gen) {
        cancelFortifyViewshedLoad()
        vsDomain.updatePinOverlays?.()
      }
    }
  }

  async function loadFortifyAccess(peakSlug, lat, lon, name) {
    if (!peakSlug || !siteAccessDomain) return
    store.fortify.accessSlug = peakSlug
    const cached = store.access?.bySlug?.[peakSlug]
    if (cached?.hike || cached?.jeep) {
      siteAccessDomain.refreshLayers()
      return
    }
    try {
      const resp = await fetch(
        apiUrls.placeAccessApiUrl(projectSlug, peakSlug, { warm: true, lat, lon }),
      )
      if (!resp.ok) return
      const access = await resp.json()
      if (store.fortify.accessSlug !== peakSlug) return
      siteAccessDomain.ingestAccess(peakSlug, access, { slug: peakSlug, name, lat, lon })
      siteAccessDomain.refreshLayers()
    } catch (err) {
      console.warn('fortify access load failed', peakSlug, err)
    }
  }

  function showPreviewForCandidate(feature) {
    clearFortifyPreview()

    const props = feature?.properties || {}
    const linkA = store.fortify.linkA
    const linkB = store.fortify.linkB
    if (linkA) hideSiteViewshedLayer(linkA)
    if (linkB) hideSiteViewshedLayer(linkB)

    const coords = feature?.geometry?.coordinates
    if (!coords) return
    const lon = Number(coords[0])
    const lat = Number(coords[1])
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return

    void loadFortifyCoordViewshed(lat, lon)
    const peakSlug = String(props.peak_slug || props.slug || '')
    if (peakSlug) {
      void loadFortifyAccess(peakSlug, lat, lon, props.name || peakSlug)
    }
  }

  function flyToFortifyCandidate(feature) {
    const map = getMap()
    if (!map || !getMapReady()) return
    const coords = feature?.geometry?.coordinates
    if (!coords) return
    const lon = Number(coords[0])
    const lat = Number(coords[1])
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return

    const linkA = store.fortify.linkA
    const linkB = store.fortify.linkB
    const siteA = linkA ? getSiteBySlug?.(linkA) : null
    const siteB = linkB ? getSiteBySlug?.(linkB) : null
    if (siteA && siteB) {
      const lons = [lon, Number(siteA.lon), Number(siteB.lon)].filter(Number.isFinite)
      const lats = [lat, Number(siteA.lat), Number(siteB.lat)].filter(Number.isFinite)
      if (lons.length >= 2 && lats.length >= 2) {
        map.fitBounds(
          [
            [Math.min(...lons), Math.min(...lats)],
            [Math.max(...lons), Math.max(...lats)],
          ],
          { padding: 80, duration: 600, maxZoom: 13 },
        )
        return
      }
    }
    map.flyTo({
      center: [lon, lat],
      zoom: Math.max(map.getZoom(), 12),
      duration: 600,
    })
  }

  function applyPayload(payload) {
    store.fortify.payload = payload
    const map = getMap()
    if (!map || !getMapReady()) return
    applyFortifyLayers(map, payload, {
      selectedCandidateId: store.fortify.selectedCandidateId,
      raiseSiteLayers,
    })
  }

  function clearFortify() {
    bumpFetchEpoch()
    clearFortifyPreview()
    store.fortify.active = false
    store.fortify.linkA = null
    store.fortify.linkB = null
    store.fortify.scanning = false
    store.fortify.selectedCandidateId = null
    store.fortify.accessSlug = null
    store.fortify.payload = null
    setStatus('')
    clearFortifyLayers()
  }

  async function pollUntilDone(linkA, linkB, expectedGen, signal, epoch) {
    const started = Date.now()
    for (;;) {
      if (signal?.aborted || epoch !== fetchEpoch) return { cancelled: true }
      if (Date.now() - started > PROGRESS_TIMEOUT_MS) {
        return { error: 'Fortify scan timed out' }
      }
      let resp
      try {
        resp = await fetch(apiUrls.fortifyScanProgressUrl(projectSlug, linkA, linkB), { signal })
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
        return { error: body.error || 'Fortify scan failed' }
      }
      if (body?.status === 'idle' && Date.now() - started > PROGRESS_IDLE_GRACE_MS) {
        return { error: 'Fortify scan lost sync — try again' }
      }
      await sleepMs(PROGRESS_POLL_MS)
    }
  }

  async function fortifyLink(linkA, linkB) {
    if (!linkA || !linkB || linkA === linkB) return
    clearAlternates?.()
    const epoch = bumpFetchEpoch()
    store.fortify.active = true
    store.fortify.linkA = linkA
    store.fortify.linkB = linkB
    store.fortify.selectedCandidateId = null
    store.fortify.accessSlug = null
    store.fortify.payload = null
    clearFortifyPreview()
    setScanning(true)
    setStatus('Starting Fortify scan…')
    clearFortifyLayers()

    fetchAbort = new AbortController()
    const signal = fetchAbort.signal
    try {
      const resp = await fetch(apiUrls.fortifyScanUrl(projectSlug, linkA, linkB), { signal })
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}))
        throw new Error(errBody.error || `HTTP ${resp.status}`)
      }
      const accepted = await resp.json()
      const gen = accepted.gen
      const outcome = await pollUntilDone(linkA, linkB, gen, signal, epoch)
      if (fetchEpoch !== epoch || signal.aborted) return
      if (outcome.cancelled) return
      if (outcome.error) {
        setStatus(outcome.error)
        return
      }
      applyPayload(outcome.payload)
      const n = outcome.payload?.meta?.n_candidates ?? outcome.payload?.candidates?.features?.length ?? 0
      setStatus(n ? `Found ${n} fortify candidate(s)` : 'No fortify candidates in lens')
    } catch (err) {
      if (signal.aborted || fetchEpoch !== epoch) return
      setStatus(String(err?.message || err))
    } finally {
      if (fetchEpoch === epoch) setScanning(false)
    }
  }

  function selectFortifyCandidate(feature) {
    const props = feature?.properties || {}
    const id = props.candidate_id
    if (!id) return
    store.fortify.selectedCandidateId = id
    applyPayload(store.fortify.payload)
    showPreviewForCandidate(feature)
    flyToFortifyCandidate(feature)
    const elev = Number(props.elev_m)
    const margin = Number(props.margin_db)
    const bits = []
    if (Number.isFinite(elev)) bits.push(`${Math.round(elev)} m`)
    if (Number.isFinite(margin)) bits.push(`${margin.toFixed(1)} dB weaker leg`)
    setStatus(bits.length ? `Selected relay — ${bits.join(', ')}` : 'Selected relay candidate')
  }

  function selectFortifyCandidateById(candidateId) {
    const features = store.fortify.payload?.candidates?.features
    if (!candidateId || !Array.isArray(features)) return
    const feature = features.find((f) => f?.properties?.candidate_id === candidateId)
    if (feature) selectFortifyCandidate(feature)
  }

  function selectedFortifyFeature() {
    const id = store.fortify.selectedCandidateId
    const features = store.fortify.payload?.candidates?.features
    if (!id || !Array.isArray(features)) return null
    return features.find((f) => f?.properties?.candidate_id === id) || null
  }

  async function addSelectedFortifyAsSite() {
    const feature = selectedFortifyFeature()
    const props = feature?.properties || {}
    const coords = feature?.geometry?.coordinates
    if (!coords) throw new Error('Pick a fortify dot first.')
    const linkA = store.fortify.linkA
    const linkB = store.fortify.linkB
    const siteA = getSiteBySlug?.(linkA)
    const siteB = getSiteBySlug?.(linkB)
    if (!siteA || !siteB) throw new Error('Link endpoints not found.')
    const lon = Number(coords[0])
    const lat = Number(coords[1])
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      throw new Error('Invalid candidate coordinates.')
    }
    const body = {
      name: `${siteA.name}–${siteB.name} relay`,
      lat,
      lon,
    }
    if (props.peak_slug || props.slug) {
      body.preferred_slug = String(props.peak_slug || props.slug)
    }
    const tags = new Set([...(siteA.tags || []), ...(siteB.tags || [])])
    if (tags.size) body.tags = [...tags]
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    const { site } = await sitesDomain.createSite(body)
    if (!site?.slug) throw new Error('Unexpected server response.')
    applySavedSiteToMap?.(site, lat, lon)
    await loadSingleSiteLinks?.(site.slug)
    clearFortify()
    return site
  }

  function installMapHandlers(map) {
    map.on('click', FORTIFY_CANDIDATES_LAYER, (ev) => {
      if (!fortifyActive()) return
      const feature = ev.features?.[0]
      if (feature) selectFortifyCandidate(feature)
    })
  }

  return {
    fortifyActive,
    fortifyLink,
    clearFortify,
    selectFortifyCandidate,
    selectFortifyCandidateById,
    addSelectedFortifyAsSite,
    installMapHandlers,
  }
}
