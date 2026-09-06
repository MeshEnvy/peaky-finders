// @ts-check

import * as apiUrls from '../api/urls.js'
import { ALTERNATES_CANDIDATES_LAYER } from '../constants.js'
import { applyAlternatesLayers, removeAlternatesLayers } from '../map/alternates-layers.js'

const PROGRESS_POLL_MS = 250

function sleepMs(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

/**
 * @param {{
 *   store: object,
 *   projectSlug: string,
 *   getMap: () => import('maplibregl').Map | null,
 *   getMapReady: () => boolean,
 *   resetSeekRun?: () => void,
 *   sitesDomain?: { createSite: (body: Record<string, unknown>) => Promise<{ site?: object }> },
 *   applySavedSiteToMap?: (site: object, lat: number, lon: number) => void,
 *   loadSingleSiteLinks?: (slug: string) => Promise<void>,
 *   raiseSiteLayers?: () => void,
 *   linkedPeersForSite?: (slug: string) => string[],
 *   getAlternatesAnchorSlugs?: (slug: string) => string[],
 * }} ctx
 */
export function createAlternatesDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    resetSeekRun,
    sitesDomain,
    applySavedSiteToMap,
    loadSingleSiteLinks,
    raiseSiteLayers,
    getAlternatesAnchorSlugs,
  } = ctx

  let fetchEpoch = 0
  /** @type {AbortController|null} */
  let fetchAbort = null

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
    clearAlternatesLayers()
  }

  async function pollUntilDone(siteSlug, anchorSlugs, expectedGen, signal, epoch) {
    for (;;) {
      if (signal?.aborted || epoch !== fetchEpoch) return { cancelled: true }
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
      await sleepMs(PROGRESS_POLL_MS)
    }
  }

  async function findAlternatesForSite(siteSlug) {
    if (!siteSlug) return
    const anchorSlugs = getAlternatesAnchorSlugs?.(siteSlug) || []
    if (!anchorSlugs.length) {
      setStatus('Needs at least one visible RF link.')
      return
    }

    resetSeekRun?.()
    bumpFetchEpoch()
    const epoch = fetchEpoch
    fetchAbort = new AbortController()
    const { signal } = fetchAbort

    store.alternates.active = true
    store.alternates.siteSlug = siteSlug
    store.alternates.selectedCandidateId = null
    store.alternates.payload = null
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
