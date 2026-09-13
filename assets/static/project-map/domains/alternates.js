// @ts-check

import * as apiUrls from '../api/urls.js'
import { ALTERNATE_VIEWSHED_SLUG, ALTERNATES_CANDIDATES_LAYER, viewshedLayerId } from '../constants.js'
import { applyAlternatesLayers, removeAlternatesLayers } from '../map/alternates-layers.js'
import { setPendingCoords } from '../stores/viewshed.js'

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
 *   clearLinkSolver?: () => void,
 *   sitesDomain?: { createSite: (body: Record<string, unknown>) => Promise<{ site?: object }> },
 *   applySavedSiteToMap?: (site: object, lat: number, lon: number) => void,
 *   loadSingleSiteLinks?: (slug: string) => Promise<void>,
 *   raiseSiteLayers?: () => void,
 *   linkedPeersForSite?: (slug: string) => string[],
 *   getAlternatesAnchorSlugs?: (slug: string) => string[],
 *   getViewshed?: () => object,
 *   applyViewshedVisibilityForSite?: (slug: string) => void,
 *   clearFortify?: () => void,
 * }} ctx
 */
export function createAlternatesDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    clearLinkSolver,
    clearFortify,
    sitesDomain,
    applySavedSiteToMap,
    loadSingleSiteLinks,
    raiseSiteLayers,
    getAlternatesAnchorSlugs,
    getViewshed,
    applyViewshedVisibilityForSite,
  } = ctx

  let fetchEpoch = 0
  /** @type {AbortController|null} */
  let fetchAbort = null
  let alternateViewshedGen = 0
  /** @type {Set<string>} */
  const hiddenPreviewSiteSlugs = new Set()

  function vs() {
    return getViewshed?.()
  }

  function cancelAlternateViewshedLoad() {
    alternateViewshedGen += 1
    store.viewshed.pendingEpoch.delete(ALTERNATE_VIEWSHED_SLUG)
    store.viewshed.loading.delete(ALTERNATE_VIEWSHED_SLUG)
    vs()?.clearViewshedLoadingState?.(ALTERNATE_VIEWSHED_SLUG)
  }

  function dismissAlternateViewshedOverlay() {
    cancelAlternateViewshedLoad()
    vs()?.removeViewshedLayer?.(ALTERNATE_VIEWSHED_SLUG)
    store.viewshed.visible.delete(ALTERNATE_VIEWSHED_SLUG)
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

  function clearAlternateViewshedPreview() {
    dismissAlternateViewshedOverlay()
    restoreHiddenPreviewSiteViewsheds()
    vs()?.updatePinOverlays?.()
  }

  async function loadAlternateCoordViewshed(lat, lon) {
    const vsDomain = vs()
    if (!vsDomain) return
    const gen = ++alternateViewshedGen
    store.viewshed.visible.set(ALTERNATE_VIEWSHED_SLUG, true)
    if (await vsDomain.tryLoadCoordViewshedFromCache?.(ALTERNATE_VIEWSHED_SLUG, lat, lon)) {
      vsDomain.raiseViewshedLayers?.()
      return
    }
    vsDomain.removeViewshedLayer?.(ALTERNATE_VIEWSHED_SLUG)
    store.viewshed.loading.add(ALTERNATE_VIEWSHED_SLUG)
    const epoch = vsDomain.getViewshedLoadEpoch?.()
    store.viewshed.pendingEpoch.set(ALTERNATE_VIEWSHED_SLUG, epoch)
    setPendingCoords(store, ALTERNATE_VIEWSHED_SLUG, lat, lon)
    vsDomain.updatePinOverlays?.()
    try {
      const resp = await fetch(vsDomain.viewshedPrefetchWarmUrl(lat, lon), { method: 'POST' })
      if (alternateViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(ALTERNATE_VIEWSHED_SLUG) !== epoch) return
      if (!resp.ok) {
        cancelAlternateViewshedLoad()
        vsDomain.updatePinOverlays?.()
        return
      }
      const ready = await resp.json()
      if (alternateViewshedGen !== gen) return
      if (store.viewshed.pendingEpoch.get(ALTERNATE_VIEWSHED_SLUG) !== epoch) return
      if (ready && ready.status === 'ready') {
        vsDomain.handleViewshedReady({ ...ready, slug: ALTERNATE_VIEWSHED_SLUG }, epoch)
        vsDomain.raiseViewshedLayers?.()
      }
    } catch (_) {
      if (alternateViewshedGen === gen) {
        cancelAlternateViewshedLoad()
        vsDomain.updatePinOverlays?.()
      }
    }
  }

  function showViewshedForAlternate(feature) {
    dismissAlternateViewshedOverlay()
    restoreHiddenPreviewSiteViewsheds()

    const props = feature?.properties || {}
    const subjectSlug = store.alternates.siteSlug
    if (subjectSlug) hideSiteViewshedLayer(subjectSlug)
    if (props.is_site && props.site_slug) {
      hideSiteViewshedLayer(String(props.site_slug))
    }

    const coords = feature?.geometry?.coordinates
    if (!coords) return
    const lon = Number(coords[0])
    const lat = Number(coords[1])
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    void loadAlternateCoordViewshed(lat, lon)
  }

  function alternatesActive() {
    return !!store.alternates?.active && !!store.alternates.siteSlug
  }

  function setStatus(text) {
    store.alternates.statusText = text || ''
  }

  function setScanning(on) {
    store.alternates.scanning = !!on
  }

  function bumpFetchEpoch() {
    fetchEpoch += 1
    if (fetchAbort) {
      fetchAbort.abort()
      fetchAbort = null
    }
    return fetchEpoch
  }

  function clearAlternatesLayers() {
    const map = getMap()
    if (map && getMapReady()) removeAlternatesLayers(map)
  }

  function applyPayload(payload) {
    store.alternates.payload = payload
    const map = getMap()
    if (!map || !getMapReady()) return
    applyAlternatesLayers(map, payload, {
      selectedCandidateId: store.alternates.selectedCandidateId,
      raiseSiteLayers,
    })
  }

  function clearAlternates() {
    bumpFetchEpoch()
    store.alternates.active = false
    store.alternates.siteSlug = null
    store.alternates.scanning = false
    store.alternates.selectedCandidateId = null
    store.alternates.payload = null
    setStatus('')
    clearAlternateViewshedPreview()
    clearAlternatesLayers()
  }

  async function pollUntilDone(siteSlug, anchorSlugs, expectedGen, signal, epoch) {
    const started = Date.now()
    for (;;) {
      if (signal?.aborted || epoch !== fetchEpoch) return { cancelled: true }
      if (Date.now() - started > PROGRESS_TIMEOUT_MS) {
        return { error: 'Alternates scan timed out' }
      }
      let resp
      try {
        resp = await fetch(
          apiUrls.alternatesScanProgressUrl(projectSlug, siteSlug, anchorSlugs),
          { signal },
        )
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
        return { error: body.error || 'Alternates scan failed' }
      }
      if (body?.status === 'idle' && Date.now() - started > PROGRESS_IDLE_GRACE_MS) {
        return { error: 'Alternates scan lost sync — try again' }
      }
      await sleepMs(PROGRESS_POLL_MS)
    }
  }

  async function findAlternatesForSite(siteSlug) {
    if (!siteSlug) return
    const anchorSlugs = [...(getAlternatesAnchorSlugs?.(siteSlug) || [])].sort()
    if (!anchorSlugs.length) {
      setStatus('Needs at least one RF link to a map-visible neighbor.')
      return
    }

    clearLinkSolver?.()
    clearFortify?.()
    bumpFetchEpoch()
    const epoch = fetchEpoch
    fetchAbort = new AbortController()
    const { signal } = fetchAbort

    store.alternates.active = true
    store.alternates.siteSlug = siteSlug
    store.alternates.selectedCandidateId = null
    store.alternates.payload = null
    clearAlternateViewshedPreview()
    clearAlternatesLayers()
    setScanning(true)
    setStatus('Starting alternates scan…')

    try {
      const resp = await fetch(apiUrls.alternatesScanUrl(projectSlug, siteSlug, anchorSlugs), {
        signal,
      })
      const kickoff = await resp.json().catch(() => ({}))
      if (epoch !== fetchEpoch) return
      if (!resp.ok) {
        setScanning(false)
        setStatus(kickoff.error || `Alternates failed (${resp.status})`)
        return
      }
      if (resp.status !== 202 || kickoff.gen == null) {
        setStatus('Unexpected alternates response')
        return
      }
      const outcome = await pollUntilDone(siteSlug, anchorSlugs, kickoff.gen, signal, epoch)
      if (epoch !== fetchEpoch) return
      if (outcome.cancelled) return
      if (outcome.error) {
        setScanning(false)
        setStatus(outcome.error)
        return
      }
      applyPayload(outcome.payload)
      const n = outcome.payload?.meta?.n_candidates ?? 0
      const nSites = outcome.payload?.meta?.n_site_candidates ?? 0
      const anchorNames = (outcome.payload?.anchors || [])
        .map((a) => a.name || a.slug)
        .filter(Boolean)
        .join(', ')
      setStatus(
        `${n} alternate(s)${nSites ? `, ${nSites} existing site(s)` : ''}` +
          (anchorNames ? ` — still links to ${anchorNames}` : ''),
      )
    } catch (err) {
      if (err?.name === 'AbortError') return
      if (epoch !== fetchEpoch) return
      setStatus(String(err))
    } finally {
      if (epoch === fetchEpoch) setScanning(false)
    }
  }

  function selectAlternateCandidate(feature) {
    const props = feature?.properties || {}
    const candidateId = props.candidate_id
    if (!candidateId) return
    store.alternates.selectedCandidateId = String(candidateId)
    if (store.alternates.payload) {
      applyPayload(store.alternates.payload)
    }
    showViewshedForAlternate(feature)
    if (props.is_site && props.site_name) {
      setStatus(`Selected ${props.site_name} (existing site)`)
      return
    }
    const elev = Number(props.elev_m)
    const margin = Number(props.margin_db)
    const bits = []
    if (Number.isFinite(elev)) bits.push(`${Math.round(elev)} m`)
    if (Number.isFinite(margin)) bits.push(`${margin.toFixed(1)} dB margin`)
    setStatus(bits.length ? `Selected alternate — ${bits.join(', ')}` : 'Selected alternate')
  }

  function selectedAlternateFeature() {
    const id = store.alternates.selectedCandidateId
    const features = store.alternates.payload?.candidates?.features
    if (!id || !Array.isArray(features)) return null
    return features.find((f) => f?.properties?.candidate_id === id) || null
  }

  async function addSelectedAlternateAsSite() {
    const feature = selectedAlternateFeature()
    const props = feature?.properties || {}
    const coords = feature?.geometry?.coordinates
    if (!coords || props.is_site) {
      throw new Error('Pick an alternate dot first.')
    }
    const subjectSlug = store.alternates.siteSlug
    const subject = store.sites.list.find((s) => s.slug === subjectSlug)
    if (!subject) throw new Error('Subject site not found.')
    const lon = Number(coords[0])
    const lat = Number(coords[1])
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      throw new Error('Invalid alternate coordinates.')
    }
    const body = {
      name: `${subject.name} alt`,
      lat,
      lon,
    }
    if (props.peak_slug || props.slug) {
      body.preferred_slug = String(props.peak_slug || props.slug)
    }
    if (subject.height_m != null && Number.isFinite(Number(subject.height_m))) {
      body.height_m = Number(subject.height_m)
    }
    if (subject.tags?.length) body.tags = [...subject.tags]
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    const { site } = await sitesDomain.createSite(body)
    if (!site?.slug) throw new Error('Unexpected server response.')
    applySavedSiteToMap?.(site, lat, lon)
    await loadSingleSiteLinks?.(site.slug)
    clearAlternates()
    return site
  }

  function installMapHandlers(map) {
    map.on('click', ALTERNATES_CANDIDATES_LAYER, (ev) => {
      if (!alternatesActive()) return
      const feature = ev.features?.[0]
      if (feature) selectAlternateCandidate(feature)
    })
  }

  return {
    alternatesActive,
    findAlternatesForSite,
    clearAlternates,
    selectAlternateCandidate,
    addSelectedAlternateAsSite,
    installMapHandlers,
  }
}
