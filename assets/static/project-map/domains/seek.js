// @ts-check

import * as apiUrls from '../api/urls.js'
import {
  SEEK_ANCILLARY_LINKS_DEBOUNCE_MS,
  SEEK_GOAL_SAME_AS_START_M,
  SEEK_HOP_VIEWSHED_PREFIX,
  SEEK_PEAK_BINS_ACROSS_VIEWPORT,
  SEEK_PLAN_SAVE_MS,
  SEEK_PROGRESS_POLL_MS,
  seekStateKey,
  seekRedoKey,
  viewshedLayerId,
} from '../constants.js'
import {
  haversineMeters,
  seekPeakBinSizeMForBounds,
  buildSeekWedgeFeature,
  normalizeTagInput,
} from '../geo.js'
import { seekScanBoundsForRequest as seekScanBoundsForRequestAt } from '../map/viewport.js'
import {
  applySeekAncillaryLinksLayer,
  removeSeekAncillaryLinksLayer,
} from '../map/links-layers.js'
import {
  applySeekLayers as applySeekLayersOnMap,
  removeSeekLayers,
  seekCandidatesGeoJsonForDisplay,
  syncSeekGoalLine,
  updateSeekPathLayer,
} from '../map/seek-layers.js'
import {
  setSeekState,
  setSeekScanning as setSeekScanningStore,
  setSeekPanelOpen,
  setSeekGoalPlacementMode as setSeekGoalPlacementModeStore,
  setSeekPendingGoal,
  setSeekStartSlug,
  setSeekRunning,
  setSeekStatusText,
  beginSeekFetchEpoch,
  abortSeekFetch,
  invalidateSeekFetchEpoch,
  updateSeekProgress,
  clearSeekProgress,
} from '../stores/seek.js'

/**
 * @param {object} ctx
 */
export function createSeekDomain(ctx) {
  const {
    store,
    projectSlug,
    config,
    getMap,
    getMapReady,
    getSites,
    siteBySlug,
    simDefaults,
    scheduleSaveMapState,
    raiseSiteLayers,
    isSiteMapHidden,
    sitePassesTagFilter,
    tagFilterBypassSlugs,
    refreshFilteredLinks,
    applySiteLayerFilters,
    warmDraftViewshedForSeek,
    loadSingleSiteLinks,
    mapShell,
    mapToolSeek,
    seekPanel,
    syncMapCursor,
    updatePinOverlays,
    selectSite,
    fetchOutboundLinksParallel,
    registerSiteFromApi,
    mergeConvertedSeekLinks,
    bypassSiteTagFilter,
  } = ctx

  const SEEK_STATE_KEY = seekStateKey(projectSlug)
  const SEEK_REDO_KEY = seekRedoKey(projectSlug)

  let seekFetchTimer = null
  let seekGoalMarker = null
  let seekSiteCandidateSlugs = new Set()
  let seekActiveFetchKey = null
  let seekAncillaryLinksGen = 0
  let seekAncillaryLinksRawFeatures = []
  let seekAncillaryLinksTimer = null
  let seekAncillaryLinksAbort = null
  const seekHopCoordViewshedSlugs = new Set()
  const seekHopCoordViewshedCoords = new Map()
  const seekHopViewshedGen = new Map()
  let seekMarkers = []
  let seekGoalInRange = false
  let seekProgressPollTimer = null
  let seekProgressTickTimer = null
  let lastSeekProgress = null
  let seekViewshedRetryCount = 0
  let seekScanStartedAt = 0
  let seekPlanSaveTimer = null
  let seekPlanSaveSeq = 0
  let seekSelectsHydrating = false

  function seekState() {
    return store.seek.state
  }

  function setSeekStateLocal(state) {
    store.seek.state = state
    setSeekState(store, state)
  }

  function seekSessionActive() {
    return Boolean(store.seek.state?.running && !store.seek.state?.complete)
  }

  function seekGoalFromState() {
    const s = store.seek.state
    if (s?.goalLat != null && s?.goalLon != null) {
      return { lat: s.goalLat, lon: s.goalLon }
    }
    if (store.seek.pendingGoalLat != null && store.seek.pendingGoalLon != null) {
      return { lat: store.seek.pendingGoalLat, lon: store.seek.pendingGoalLon }
    }
    return null
  }

  function seekGoalCoords() {
    return seekGoalFromState()
  }

  function seekCurrentFrom() {
    const s = store.seek.state
    if (s?.currentFrom) return s.currentFrom
    const startSlug = s?.startSlug || store.seek.startSlug
    const startSite = startSlug ? siteBySlug.get(startSlug) : null
    if (!startSite) return null
    return { lat: startSite.lat, lon: startSite.lon }
  }

  function seekHopRadiusM() {
    const km = Number(simDefaults?.radius_km) || 50
    return km * 1000
  }

  function setSeekStatus(text) {
    setSeekStatusText(store, text || '')
  }

  function updateSeekProgressUi(prog) {
    lastSeekProgress = prog || null
    updateSeekProgress(store, prog, seekScanStartedAt)
  }

  function startSeekProgressTick() {
    stopSeekProgressTick()
    seekProgressTickTimer = window.setInterval(() => {
      if (!store.seek.scanning) return
      updateSeekProgressUi(lastSeekProgress || { phase: 'starting' })
    }, 1000)
  }

  function stopSeekProgressTick() {
    if (seekProgressTickTimer) window.clearInterval(seekProgressTickTimer)
    seekProgressTickTimer = null
  }

  function stopSeekProgressPoll() {
    if (seekProgressPollTimer) window.clearInterval(seekProgressPollTimer)
    seekProgressPollTimer = null
  }

  function stopSeekProgressUi() {
    stopSeekProgressPoll()
    stopSeekProgressTick()
    lastSeekProgress = null
    clearSeekProgress(store)
  }

  function sleepMs(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms))
  }

  function abortSeekInFlight() {
    abortSeekFetch(store)
  }

  function invalidateSeekFetch() {
    invalidateSeekFetchEpoch(store)
    if (seekFetchTimer) window.clearTimeout(seekFetchTimer)
    seekFetchTimer = null
  }

  function beginSeekFetch() {
    return beginSeekFetchEpoch(store)
  }

  function setSeekScanning(active) {
    setSeekScanningStore(store, active)
    if (active) {
      setSeekStatus('')
      seekScanStartedAt = Date.now()
      updateSeekProgressUi({ phase: 'starting' })
      startSeekProgressTick()
    } else {
      stopSeekProgressUi()
      seekScanStartedAt = 0
    }
    if (updatePinOverlays) updatePinOverlays()
    syncSeekPanelUi()
  }

  function cancelSeekScanUi() {
    invalidateSeekFetch()
    setSeekScanning(false)
  }

  async function pollSeekUntilDone(expectedGen, signal, epoch) {
    for (;;) {
      if (signal?.aborted) return { cancelled: true }
      if (epoch !== store.seek.fetchEpoch) return { cancelled: true }
      let resp
      try {
        resp = await fetch(apiUrls.seekScanProgressUrl(projectSlug), { signal })
      } catch (err) {
        if (err?.name === 'AbortError') return { cancelled: true }
        await sleepMs(SEEK_PROGRESS_POLL_MS)
        continue
      }
      if (!resp.ok) {
        await sleepMs(SEEK_PROGRESS_POLL_MS)
        continue
      }
      const body = await resp.json().catch(() => ({}))
      if (body?.gen != null && body.gen !== expectedGen) return { cancelled: true }
      if (body?.progress) updateSeekProgressUi(body.progress)
      if (body?.status === 'done' && body?.result) {
        return { payload: { project: body.project, ...body.result } }
      }
      if (body?.status === 'cancelled') return { cancelled: true }
      if (body?.status === 'error') {
        return {
          error: body.error || 'Seek failed',
          errorStatus: body.error_status || 422,
          notReady: body.error_status === 503,
        }
      }
      await sleepMs(SEEK_PROGRESS_POLL_MS)
    }
  }

  function seekViewportBbox() {
    const map = getMap()
    const b = seekScanBoundsForRequestAt(map)
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      .map((v) => v.toFixed(6))
      .join(',')
  }

  function seekPeakBinSizeM() {
    const map = getMap()
    return seekPeakBinSizeMForBounds(seekScanBoundsForRequestAt(map))
  }

  function seekExcludeParam() {
    const s = store.seek.state
    if (!s || !Array.isArray(s.hops)) return ''
    return s.hops.map((hop) => `${hop.lat},${hop.lon}`).join(';')
  }

  function seekExcludeSlugsParam() {
    const slugs = new Set()
    const s = store.seek.state
    if (s?.startSlug) slugs.add(s.startSlug)
    for (const hop of s?.hops || []) {
      if (hop.site_slug) slugs.add(hop.site_slug)
    }
    return [...slugs].join(';')
  }

  function seekPathSiteSlugs() {
    const slugs = new Set()
    const s = store.seek.state
    if (s?.startSlug) slugs.add(s.startSlug)
    for (const hop of s?.hops || []) {
      if (hop.site_slug) slugs.add(hop.site_slug)
    }
    return slugs
  }

  function seekCoordsNearGoal(lat, lon) {
    const goal = seekGoalCoords()
    if (!goal) return false
    return haversineMeters(lat, lon, goal.lat, goal.lon) <= SEEK_GOAL_SAME_AS_START_M
  }

  function isSiteInSeekPlan(slug) {
    return Boolean(store.seek.state?.running && seekPathSiteSlugs().has(slug))
  }

  function seekSiteSlugNear(lat, lon, maxM = SEEK_GOAL_SAME_AS_START_M) {
    let best = null
    let bestDist = maxM
    for (const site of getSites() || []) {
      const dist = haversineMeters(lat, lon, site.lat, site.lon)
      if (dist <= bestDist) {
        bestDist = dist
        best = site.slug
      }
    }
    return best
  }

  function seekHopCoordViewshedSlug(lat, lon) {
    return `${SEEK_HOP_VIEWSHED_PREFIX}${Number(lat).toFixed(5)}_${Number(lon).toFixed(5)}`
  }

  function cancelSeekHopViewshedLoad(slug) {
    const vs = ctx.getViewshed?.()
    seekHopViewshedGen.set(slug, (seekHopViewshedGen.get(slug) || 0) + 1)
    store.viewshed.pendingEpoch.delete(slug)
    store.viewshed.loading.delete(slug)
    vs?.clearViewshedLoadingState?.(slug)
  }

  function clearSeekHopViewshed(slug) {
    const vs = ctx.getViewshed?.()
    cancelSeekHopViewshedLoad(slug)
    vs?.removeViewshedLayer?.(slug)
    seekHopCoordViewshedSlugs.delete(slug)
    seekHopCoordViewshedCoords.delete(slug)
    store.viewshed.visible.delete(slug)
  }

  function clearAllSeekHopViewsheds() {
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      clearSeekHopViewshed(slug)
    }
    seekHopCoordViewshedSlugs.clear()
    seekHopCoordViewshedCoords.clear()
  }

  async function loadSeekHopCoordViewshed(slug, lat, lon) {
    if (!String(slug).startsWith(SEEK_HOP_VIEWSHED_PREFIX)) return
    const vs = ctx.getViewshed?.()
    if (!vs) return
    const gen = (seekHopViewshedGen.get(slug) || 0) + 1
    seekHopViewshedGen.set(slug, gen)
    store.viewshed.visible.set(slug, true)
    seekHopCoordViewshedCoords.set(slug, { lat, lon })
    if (await vs?.tryLoadCoordViewshedFromCache?.(slug, lat, lon)) return
    vs?.removeViewshedLayer?.(slug)
    store.viewshed.loading.add(slug)
    const epoch = vs?.getViewshedLoadEpoch?.()
    store.viewshed.pendingEpoch.set(slug, epoch)
    vs?.updatePinOverlays?.()
    try {
      const resp = await fetch(vs.viewshedPrefetchWarmUrl(lat, lon), { method: 'POST' })
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return
      if (store.viewshed.pendingEpoch.get(slug) !== epoch) return
      if (!resp.ok) {
        cancelSeekHopViewshedLoad(slug)
        vs.updatePinOverlays()
        return
      }
      const ready = await resp.json()
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return
      if (store.viewshed.pendingEpoch.get(slug) !== epoch) return
      if (ready && ready.status === 'ready') {
        vs.handleViewshedReady({ ...ready, slug }, epoch)
      }
    } catch (_) {
      if ((seekHopViewshedGen.get(slug) || 0) === gen) {
        cancelSeekHopViewshedLoad(slug)
        vs?.updatePinOverlays?.()
      }
    }
  }

  function syncSeekHopViewsheds() {
    const vs = ctx.getViewshed?.()
    const map = getMap()
    if (!map || !getMapReady() || !store.seek.state?.running || !Array.isArray(store.seek.state?.hops)) {
      clearAllSeekHopViewsheds()
      cancelSeekAncillaryLinksFetch()
      seekAncillaryLinksGen += 1
      if (map) removeSeekAncillaryLinksLayer(map)
      return
    }
    const wantedCoordSlugs = new Set()
    for (const hop of store.seek.state.hops) {
      const siteSlug = hop.site_slug || seekSiteSlugNear(hop.lat, hop.lon)
      if (siteSlug) {
        const coordSlug = seekHopCoordViewshedSlug(hop.lat, hop.lon)
        if (seekHopCoordViewshedSlugs.has(coordSlug)) clearSeekHopViewshed(coordSlug)
        store.viewshed.visible.set(siteSlug, true)
        vs?.ensureViewshedLoadedForSlug?.(siteSlug)
        if (map.getLayer(viewshedLayerId(siteSlug))) {
          vs?.applyViewshedVisibilityForSite?.(siteSlug)
        }
        continue
      }
      const slug = seekHopCoordViewshedSlug(hop.lat, hop.lon)
      wantedCoordSlugs.add(slug)
      seekHopCoordViewshedSlugs.add(slug)
      store.viewshed.visible.set(slug, true)
      if (!map.getLayer(viewshedLayerId(slug)) && !store.viewshed.loading.has(slug)) {
        void loadSeekHopCoordViewshed(slug, hop.lat, hop.lon)
      } else if (map.getLayer(viewshedLayerId(slug))) {
        map.setLayoutProperty(viewshedLayerId(slug), 'visibility', 'visible')
      }
    }
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      if (!wantedCoordSlugs.has(slug)) clearSeekHopViewshed(slug)
    }
    vs?.raiseViewshedLayers?.()
    scheduleSeekAncillaryLinks()
  }

  function seekHopEndpointKey(hop) {
    if (hop.site_slug) return `site:${hop.site_slug}`
    return `coord:${Number(hop.lat).toFixed(5)}_${Number(hop.lon).toFixed(5)}`
  }

  function seekChainNeighborKeys(hopIndex) {
    const hops = store.seek.state?.hops
    const keys = new Set()
    if (!hops || hopIndex < 0 || hopIndex >= hops.length) return keys
    if (hopIndex > 0) keys.add(seekHopEndpointKey(hops[hopIndex - 1]))
    if (hopIndex < hops.length - 1) keys.add(seekHopEndpointKey(hops[hopIndex + 1]))
    return keys
  }

  function canonicalSitePairKey(slugA, slugB) {
    return slugA <= slugB ? `${slugA}|${slugB}` : `${slugB}|${slugA}`
  }

  function seekAncillaryLinkFeatureVisible(feature) {
    const props = feature?.properties || {}
    if (props.a && props.b) {
      return !isSiteMapHidden(String(props.a)) && !isSiteMapHidden(String(props.b))
    }
    const slug = props.slug
    if (slug) return !isSiteMapHidden(String(slug))
    return true
  }

  function addSeekAncillaryLinksLayer(geojson) {
    const map = getMap()
    if (!map || !getMapReady()) {
      if (map) removeSeekAncillaryLinksLayer(map)
      return
    }
    applySeekAncillaryLinksLayer(map, geojson, seekAncillaryLinkFeatureVisible, raiseSiteLayers)
  }

  function refreshSeekAncillaryLinksDisplay() {
    if (!seekAncillaryLinksRawFeatures.length) {
      const map = getMap()
      if (map) removeSeekAncillaryLinksLayer(map)
      return
    }
    addSeekAncillaryLinksLayer({
      type: 'FeatureCollection',
      features: seekAncillaryLinksRawFeatures,
    })
  }

  function cancelSeekAncillaryLinksFetch() {
    if (seekAncillaryLinksAbort) {
      seekAncillaryLinksAbort.abort()
      seekAncillaryLinksAbort = null
    }
  }

  async function collectSeekAncillaryLinkFeatures(signal, gen) {
    const hops = store.seek.state?.hops
    if (!hops?.length) return []
    const seen = new Set()
    const features = []
    const addFeature = (feature, dedupeKey) => {
      if (!feature || seen.has(dedupeKey)) return
      seen.add(dedupeKey)
      features.push(feature)
    }
    for (let hopIndex = 0; hopIndex < hops.length; hopIndex += 1) {
      if (signal?.aborted || gen !== seekAncillaryLinksGen) return null
      if (seekSessionActive() && hopIndex === hops.length - 1) continue
      const hop = hops[hopIndex]
      const neighbors = seekChainNeighborKeys(hopIndex)
      if (hop.site_slug) {
        await (ctx.ensureSiteLinksForSlug || loadSingleSiteLinks)?.(hop.site_slug)
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null
        const peers = ctx.linkedPeersForSite?.(hop.site_slug) || []
        for (const peerSlug of peers) {
          if (neighbors.has(`site:${peerSlug}`)) continue
          if (isSiteMapHidden(peerSlug)) continue
          const linkFeature = ctx.findSiteLinkFeature?.(hop.site_slug, peerSlug)
          if (!linkFeature) continue
          if (!seekAncillaryLinkFeatureVisible(linkFeature)) continue
          const props = linkFeature.properties || {}
          addFeature(linkFeature, canonicalSitePairKey(String(props.a), String(props.b)))
        }
        continue
      }
      try {
        const resp = await fetch(apiUrls.sitesPrefetchUrl(projectSlug, hop.lat, hop.lon), {
          signal,
        })
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null
        if (!resp.ok) continue
        const payload = await resp.json()
        const geojson = payload?.links_geojson
        if (!geojson?.features?.length) continue
        const fromKey = seekHopEndpointKey(hop)
        for (const feature of geojson.features) {
          const slug = feature.properties?.slug
          if (!slug) continue
          if (neighbors.has(`site:${slug}`)) continue
          if (!seekAncillaryLinkFeatureVisible(feature)) continue
          addFeature(feature, `${fromKey}|site:${slug}`)
        }
      } catch (err) {
        if (err?.name === 'AbortError') return null
      }
    }
    return features
  }

  async function flushSeekAncillaryLinks() {
    if (seekAncillaryLinksTimer) {
      window.clearTimeout(seekAncillaryLinksTimer)
      seekAncillaryLinksTimer = null
    }
    cancelSeekAncillaryLinksFetch()
    const hops = store.seek.state?.hops
    if (!getMapReady() || !store.seek.state?.running || !Array.isArray(hops) || !hops.length) {
      seekAncillaryLinksRawFeatures = []
      const map = getMap()
      if (map) removeSeekAncillaryLinksLayer(map)
      return
    }
    const gen = ++seekAncillaryLinksGen
    const ac = new AbortController()
    seekAncillaryLinksAbort = ac
    const features = await collectSeekAncillaryLinkFeatures(ac.signal, gen)
    if (gen !== seekAncillaryLinksGen) return
    seekAncillaryLinksAbort = null
    if (features == null) return
    seekAncillaryLinksRawFeatures = features
    addSeekAncillaryLinksLayer({
      type: 'FeatureCollection',
      features: seekAncillaryLinksRawFeatures,
    })
  }

  function scheduleSeekAncillaryLinks() {
    if (seekAncillaryLinksTimer) window.clearTimeout(seekAncillaryLinksTimer)
    seekAncillaryLinksTimer = window.setTimeout(() => {
      seekAncillaryLinksTimer = null
      void flushSeekAncillaryLinks()
    }, SEEK_ANCILLARY_LINKS_DEBOUNCE_MS)
  }

  function seekLineFeatureIsRedundant(feature) {
    const props = feature?.properties || {}
    const coords = feature?.geometry?.coordinates
    if (!coords?.length) return false
    const [lon, lat] = coords[coords.length - 1]
    if (props.is_site && props.site_slug && isSiteInSeekPlan(props.site_slug)) return true
    if (props.is_goal || seekCoordsNearGoal(lat, lon)) return true
    return false
  }

  function filterSeekLineFeatures(features) {
    return (features || []).filter((f) => !seekLineFeatureIsRedundant(f))
  }

  function filterSeekCandidateFeatures(features) {
    return (features || []).filter((feature) => {
      const props = feature?.properties || {}
      if (props.is_goal) return true
      if (props.is_site && props.site_slug && isSiteInSeekPlan(props.site_slug)) return false
      const coords = feature?.geometry?.coordinates
      if (coords?.length >= 2 && seekCoordsNearGoal(coords[1], coords[0])) return false
      return true
    })
  }

  function applySeekLayersPayload(payload) {
    const map = getMap()
    if (!map || !getMapReady()) return
    applySeekLayersOnMap(map, payload, {
      filterLineFeatures: filterSeekLineFeatures,
      filterCandidateFeatures: filterSeekCandidateFeatures,
      candidatesForDisplay: (c) =>
        seekCandidatesGeoJsonForDisplay(
          c,
          (slug) => siteBySlug.get(slug)?.name || '',
          isSiteMapHidden,
        ),
      onSiteSlugs: (slugs) => {
        seekSiteCandidateSlugs = slugs
      },
      onPathUpdate: () => updateSeekPathOverlay(),
      onGoalLine: () => syncSeekGoalLineOnMap(),
      raiseSiteLayers,
    })
  }

  function syncSeekGoalLineOnMap() {
    const map = getMap()
    if (!map || !getMapReady()) return
    if (!seekSessionActive()) return
    const from = seekCurrentFrom()
    const goal = seekGoalCoords()
    if (!from || !goal) return
    syncSeekGoalLine(map, from, goal, seekHopRadiusM(), raiseSiteLayers)
  }

  function clearSeekMarkers() {
    for (const marker of seekMarkers) marker.remove()
    seekMarkers = []
  }

  function appendSeekHopMarkers() {
    const map = getMap()
    const s = store.seek.state
    if (!map || !s?.hops) return
    s.hops.forEach((hop, idx) => {
      if (idx === 0) return
      if (hop.site_slug) {
        const siteName = hop.site_name || siteBySlug.get(hop.site_slug)?.name || ''
        const el = document.createElement('div')
        el.className = 'seek-hop-marker-wrap'
        if (siteName) {
          const label = document.createElement('div')
          label.className = 'seek-hop-marker-label'
          label.textContent = siteName
          el.appendChild(label)
        }
        const dot = document.createElement('div')
        dot.className = 'seek-hop-marker seek-hop-marker--site'
        el.appendChild(dot)
        seekMarkers.push(
          new maplibregl.Marker({ element: el, anchor: 'center' })
            .setLngLat([hop.lon, hop.lat])
            .addTo(map),
        )
        return
      }
      let peakNum = 0
      for (let i = 1; i <= idx; i += 1) {
        if (!s.hops[i].site_slug) peakNum += 1
      }
      const el = document.createElement('div')
      el.className = 'seek-hop-marker'
      el.textContent = String(peakNum)
      seekMarkers.push(
        new maplibregl.Marker({ element: el }).setLngLat([hop.lon, hop.lat]).addTo(map),
      )
    })
  }

  function updateSeekPathOverlay() {
    const map = getMap()
    if (!map || !getMapReady()) return
    syncSeekHopViewsheds()
    const hops = store.seek.state?.hops
    updateSeekPathLayer(
      map,
      hops,
      clearSeekMarkers,
      appendSeekHopMarkers,
      () => {
        if (applySiteLayerFilters) applySiteLayerFilters()
        syncSeekGoalLineOnMap()
        if (seekSessionActive() && store.links.payload?.geojson) {
          refreshFilteredLinks()
        }
        if (raiseSiteLayers) raiseSiteLayers()
      },
    )
  }

  function seekPanHintText() {
    const hop = store.seek.state?.hops?.length || 1
    return `Hop ${hop} — click Recalculate for candidates`
  }

  function promptSeekManualRecalc() {
    if (!seekSessionActive() || store.seek.state?.complete) return
    setSeekStatus(seekPanHintText())
  }

  function syncSeekPanelUi() {
    if (mapToolSeek) {
      mapToolSeek.classList.toggle('map-toolbar-tool--active', store.ui.seekPanelOpen)
      mapToolSeek.setAttribute('aria-pressed', store.ui.seekPanelOpen ? 'true' : 'false')
    }
    if (seekPanel) seekPanel.hidden = !store.ui.seekPanelOpen
    setSeekPanelOpen(store, store.ui.seekPanelOpen)
    setSeekRunning(store, store.seek.running)
    syncSeekGoalUi()
  }

  function removeSeekGoalMarker() {
    if (seekGoalMarker) {
      seekGoalMarker.remove()
      seekGoalMarker = null
    }
  }

  function syncSeekGoalMarker() {
    const map = getMap()
    if (!map || !getMapReady()) return
    const goal = seekGoalCoords()
    if (!goal) {
      removeSeekGoalMarker()
      return
    }
    if (!seekGoalMarker) {
      const el = document.createElement('div')
      el.className = 'seek-goal-marker'
      el.setAttribute('aria-hidden', 'true')
      seekGoalMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
    }
    seekGoalMarker.setLngLat([goal.lon, goal.lat]).addTo(map)
  }

  function syncSeekGoalUi() {
    syncSeekGoalMarker()
    syncSeekGoalLineOnMap()
  }

  function setSeekGoalPlacementMode(active) {
    store.seek.goalPlacementMode = active
    setSeekGoalPlacementModeStore(store, active)
    if (mapShell) mapShell.classList.toggle('seek-goal-placement-mode', active)
    if (syncMapCursor) syncMapCursor()
    if (active) {
      setSeekStatus('Click the map to set goal')
    } else if (store.ui.seekPanelOpen && !seekSessionActive()) {
      const goal = seekGoalCoords()
      if (!goal) setSeekStatus('Pick a start site and set goal on the map')
      else setSeekStatus('Pick a start site to begin')
    }
  }

  function clearSeekRedoStack() {
    const s = store.seek.state
    if (s && Array.isArray(s.redoStack) && s.redoStack.length) {
      s.redoStack = []
    }
  }

  function loadSeekRedoStack() {
    try {
      const raw = localStorage.getItem(SEEK_REDO_KEY)
      if (!raw) return []
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed : []
    } catch (_) {
      return []
    }
  }

  function saveSeekRedoStack() {
    const s = store.seek.state
    if (!s?.redoStack?.length) {
      localStorage.removeItem(SEEK_REDO_KEY)
      return
    }
    localStorage.setItem(SEEK_REDO_KEY, JSON.stringify(s.redoStack))
  }

  function seekStateFromYamlPlan(plan) {
    if (!plan || typeof plan !== 'object') return null
    const startSlug = String(plan.start || '').trim()
    const startSite = siteBySlug.get(startSlug)
    if (!startSite) return null
    const goal = plan.goal
    if (!Array.isArray(goal) || goal.length !== 2) return null
    const goalLat = Number(goal[0])
    const goalLon = Number(goal[1])
    if (!Number.isFinite(goalLat) || !Number.isFinite(goalLon)) return null
    const hops = []
    for (const item of plan.hops || []) {
      if (!item || typeof item !== 'object') return null
      if (item.site) {
        const site = siteBySlug.get(String(item.site))
        if (!site) return null
        hops.push({
          lat: site.lat,
          lon: site.lon,
          elev_m: site.height_m ?? null,
          site_slug: site.slug,
          site_name: site.name,
        })
        continue
      }
      const loc = item.loc
      if (!Array.isArray(loc) || loc.length !== 2) return null
      const lat = Number(loc[0])
      const lon = Number(loc[1])
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
      hops.push({
        lat,
        lon,
        elev_m: item.height_m != null ? Number(item.height_m) : null,
      })
    }
    if (!hops.length) return null
    const last = hops[hops.length - 1]
    return {
      running: true,
      startSlug,
      goalLat,
      goalLon,
      hops,
      currentFrom: { lat: last.lat, lon: last.lon },
      complete: Boolean(plan.complete),
      redoStack: loadSeekRedoStack(),
    }
  }

  function seekStateToYamlPlan(state) {
    if (!state?.running || !state.startSlug) return null
    if (state.goalLat == null || state.goalLon == null) return null
    if (!Array.isArray(state.hops) || !state.hops.length) return null
    return {
      start: state.startSlug,
      goal: [state.goalLat, state.goalLon],
      complete: Boolean(state.complete),
      hops: state.hops.map((hop) => {
        if (hop.site_slug) return { site: hop.site_slug }
        return { loc: [hop.lat, hop.lon] }
      }),
    }
  }

  async function flushSeekPlanToYaml() {
    if (seekPlanSaveTimer) {
      window.clearTimeout(seekPlanSaveTimer)
      seekPlanSaveTimer = null
    }
    const seq = ++seekPlanSaveSeq
    const s = store.seek.state
    if (!s) {
      try {
        const resp = await fetch(apiUrls.seekPlanUrl(projectSlug), { method: 'DELETE' })
        if (seq !== seekPlanSaveSeq) return
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}))
          setSeekStatus(body.error || `Failed to clear seek plan (${resp.status})`)
          return
        }
        if (config.seek && typeof config.seek === 'object') config.seek.plan = null
      } catch (err) {
        if (seq !== seekPlanSaveSeq) return
        setSeekStatus(String(err))
      }
      return
    }
    const plan = seekStateToYamlPlan(s)
    if (!plan) return
    try {
      const resp = await fetch(apiUrls.seekPlanUrl(projectSlug), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(plan),
      })
      if (seq !== seekPlanSaveSeq) return
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        setSeekStatus(body.error || `Failed to save seek plan (${resp.status})`)
        return
      }
      if (config.seek && typeof config.seek === 'object') config.seek.plan = plan
    } catch (err) {
      if (seq !== seekPlanSaveSeq) return
      setSeekStatus(String(err))
    }
  }

  function persistSeekPlanToYaml({ immediate = false } = {}) {
    if (seekPlanSaveTimer) window.clearTimeout(seekPlanSaveTimer)
    if (immediate) {
      void flushSeekPlanToYaml()
      return
    }
    seekPlanSaveTimer = window.setTimeout(() => {
      seekPlanSaveTimer = null
      void flushSeekPlanToYaml()
    }, SEEK_PLAN_SAVE_MS)
  }

  function saveSeekState({ immediatePlan = false } = {}) {
    saveSeekRedoStack()
    if (!store.seek.state) {
      localStorage.removeItem(SEEK_STATE_KEY)
      persistSeekPlanToYaml({ immediate: immediatePlan })
      return
    }
    persistSeekPlanToYaml({ immediate: immediatePlan })
  }

  function migrateSeekStateGoal(parsed) {
    if (!parsed || typeof parsed !== 'object') return parsed
    if (parsed.goalLat != null && parsed.goalLon != null) return parsed
    if (parsed.goalSlug) {
      const site = getSites().find((s) => s.slug === parsed.goalSlug)
      if (site) {
        parsed.goalLat = site.lat
        parsed.goalLon = site.lon
      }
      delete parsed.goalSlug
    }
    return parsed
  }

  function loadSeekStateFromLocalStorage() {
    try {
      const raw = localStorage.getItem(SEEK_STATE_KEY)
      if (!raw) return null
      const parsed = migrateSeekStateGoal(JSON.parse(raw))
      if (!parsed || typeof parsed !== 'object') return null
      if (!Array.isArray(parsed.redoStack)) parsed.redoStack = loadSeekRedoStack()
      return parsed
    } catch (_) {
      return null
    }
  }

  function initSeekState() {
    const yamlPlan = config.seek?.plan
    const fromYaml = yamlPlan ? seekStateFromYamlPlan(yamlPlan) : null
    if (fromYaml) {
      localStorage.removeItem(SEEK_STATE_KEY)
      return fromYaml
    }
    return loadSeekStateFromLocalStorage()
  }

  function rehydrateSeekStateFromConfig() {
    if (store.seek.state?.running) return store.seek.state
    const fromYaml = config.seek?.plan ? seekStateFromYamlPlan(config.seek.plan) : null
    if (!fromYaml) return null
    setSeekStateLocal(fromYaml)
    if (fromYaml.goalLat != null && fromYaml.goalLon != null) {
      setSeekPendingGoal(store, fromYaml.goalLat, fromYaml.goalLon)
    }
    return fromYaml
  }

  function populateSeekStartSelect() {
    const eligible = getSites()
      .filter((site) => sitePassesTagFilter(site) || tagFilterBypassSlugs.has(site.slug))
      .slice()
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    const eligibleSlugs = new Set(eligible.map((site) => site.slug))
    let slug = store.seek.startSlug || ''
    const s = store.seek.state
    if (s?.startSlug && eligibleSlugs.has(s.startSlug)) slug = s.startSlug
    else if (!eligibleSlugs.has(slug) && eligible.length) slug = eligible[0].slug
    else if (!eligible.length) slug = ''
    setSeekStartSlug(store, slug)
  }

  function toggleSeekPanel(force) {
    const nextOpen = typeof force === 'boolean' ? force : !store.ui.seekPanelOpen
    if (store.ui.seekPanelOpen && !nextOpen) {
      setSeekGoalPlacementMode(false)
      if (store.seek.scanning) {
        cancelSeekScanUi()
        if (seekSessionActive()) setSeekStatus(seekPanHintText())
      }
    }
    store.ui.seekPanelOpen = nextOpen
    store.seek.panelOpen = nextOpen
    if (nextOpen) {
      populateSeekStartSelect()
      const s = store.seek.state
      if (s?.goalLat != null && s?.goalLon != null) {
        setSeekPendingGoal(store, s.goalLat, s.goalLon)
      }
      syncSeekGoalUi()
      if (s?.running && !s?.complete) {
        store.seek.running = true
        setSeekRunning(store, true)
        if (!store.seek.scanning) promptSeekManualRecalc()
      } else if (!s?.running) {
        setSeekStatus('Pick a start site and set goal on the map')
      }
    } else {
      syncSeekGoalUi()
    }
    syncSeekPanelUi()
  }

  function setSeekGoalAt(lat, lon, { refresh = true } = {}) {
    const prev = seekGoalCoords()
    const moved =
      !prev || Math.abs(prev.lat - lat) > 1e-7 || Math.abs(prev.lon - lon) > 1e-7
    setSeekPendingGoal(store, lat, lon)
    const s = store.seek.state
    if (s?.running) {
      s.goalLat = lat
      s.goalLon = lon
      setSeekStateLocal(s)
      if (moved) {
        clearSeekRedoStack()
        s.complete = false
        saveSeekState({ immediatePlan: true })
      }
    }
    setSeekGoalPlacementMode(false)
    syncSeekGoalUi()
    if (refresh && seekSessionActive()) {
      if (moved) {
        applySeekLayersPayload({
          candidates: { type: 'FeatureCollection', features: [] },
          lines: { type: 'FeatureCollection', features: [] },
        })
      }
      promptSeekManualRecalc()
    } else maybeAutoStartSeekFromSelects()
  }

  function maybeAutoStartSeekFromSelects() {
    if (seekSelectsHydrating) return
    if (store.seek.state?.running) return
    const startSlug = store.seek.startSlug || ''
    if (!startSlug) {
      setSeekStatus('Pick a start site and set goal on the map')
      return
    }
    const startSite = siteBySlug.get(startSlug)
    const goal = seekGoalCoords()
    if (!startSite || !goal) {
      if (!goal) setSeekStatus('Set goal on the map, then pick start site')
      return
    }
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      SEEK_GOAL_SAME_AS_START_M
    ) {
      setSeekStatus('Goal overlaps start site — pick a different point')
      return
    }
    startSeekRun(startSlug)
  }

  function onSeekStartChange(slug) {
    setSeekStartSlug(store, slug || '')
    maybeAutoStartSeekFromSelects()
  }

  function startSeekRun(startSlugArg) {
    const startSlug = startSlugArg || store.seek.startSlug || ''
    const goal = seekGoalCoords()
    if (!startSlug || !goal) {
      setSeekStatus('Pick a start site and set goal on the map')
      return
    }
    const startSite = siteBySlug.get(startSlug)
    if (!startSite) return
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      SEEK_GOAL_SAME_AS_START_M
    ) {
      setSeekStatus('Goal overlaps start site — pick a different point')
      return
    }
    store.seek.running = true
    const state = {
      running: true,
      startSlug,
      goalLat: goal.lat,
      goalLon: goal.lon,
      hops: [
        {
          lat: startSite.lat,
          lon: startSite.lon,
          elev_m: startSite.height_m ?? null,
          site_slug: startSlug,
        },
      ],
      currentFrom: { lat: startSite.lat, lon: startSite.lon },
      complete: false,
      redoStack: [],
    }
    setSeekStateLocal(state)
    setSeekRunning(store, true)
    setSeekStartSlug(store, startSlug)
    saveSeekState({ immediatePlan: true })
    syncSeekPanelUi()
    updateSeekPathOverlay()
    promptSeekManualRecalc()
  }

  function seekRfViable(props) {
    const v = props?.rf_viable
    return v === true || v === 'true'
  }

  function commitSeekCandidate(feature) {
    if (!seekSessionActive() || !feature?.geometry?.coordinates) return
    const props = feature.properties || {}
    if (props.is_goal) return
    if (props.site_slug) {
      if (!seekSiteCandidateSlugs.has(String(props.site_slug))) return
    } else if (!seekRfViable(props)) {
      return
    }
    abortSeekInFlight()
    clearSeekRedoStack()
    const [lon, lat] = feature.geometry.coordinates
    const hop = { lat, lon, elev_m: props.elev_m ?? null }
    if (props.site_slug) {
      hop.site_slug = props.site_slug
      hop.site_name = props.site_name || null
    }
    const s = store.seek.state
    s.hops.push(hop)
    s.currentFrom = { lat, lon }
    setSeekStateLocal(s)
    saveSeekState({ immediatePlan: true })
    syncSeekPanelUi()
    updateSeekPathOverlay()
    applySeekLayersPayload({
      candidates: { type: 'FeatureCollection', features: [] },
      lines: { type: 'FeatureCollection', features: [] },
    })
    promptSeekManualRecalc()
  }

  function undoSeekHop() {
    const s = store.seek.state
    if (!s || !Array.isArray(s.hops) || s.hops.length <= 1) return
    if (store.seek.scanning) cancelSeekScanUi()
    s.complete = false
    store.seek.running = true
    if (!Array.isArray(s.redoStack)) s.redoStack = []
    const removed = s.hops.pop()
    s.redoStack.push(removed)
    const last = s.hops[s.hops.length - 1]
    s.currentFrom = { lat: last.lat, lon: last.lon }
    setSeekStateLocal(s)
    saveSeekState({ immediatePlan: true })
    syncSeekPanelUi()
    applySeekLayersPayload({
      candidates: { type: 'FeatureCollection', features: [] },
      lines: { type: 'FeatureCollection', features: [] },
    })
    updateSeekPathOverlay()
    promptSeekManualRecalc()
  }

  function redoSeekHop() {
    const s = store.seek.state
    if (!s?.redoStack?.length) return
    if (store.seek.scanning) cancelSeekScanUi()
    const hop = s.redoStack.pop()
    s.hops.push(hop)
    s.currentFrom = { lat: hop.lat, lon: hop.lon }
    s.complete = false
    store.seek.running = true
    setSeekStateLocal(s)
    saveSeekState({ immediatePlan: true })
    syncSeekPanelUi()
    applySeekLayersPayload({
      candidates: { type: 'FeatureCollection', features: [] },
      lines: { type: 'FeatureCollection', features: [] },
    })
    updateSeekPathOverlay()
    promptSeekManualRecalc()
  }

  function resetSeekRun() {
    store.seek.running = false
    seekGoalInRange = false
    seekSiteCandidateSlugs = new Set()
    seekActiveFetchKey = null
    setSeekPendingGoal(store, null, null)
    setSeekGoalPlacementMode(false)
    cancelSeekScanUi()
    cancelSeekAncillaryLinksFetch()
    setSeekStateLocal(null)
    setSeekRunning(store, false)
    saveSeekState({ immediatePlan: true })
    const map = getMap()
    if (map) removeSeekLayers(map, clearSeekMarkers)
    clearAllSeekHopViewsheds()
    const map2 = getMap()
    if (map2) removeSeekAncillaryLinksLayer(map2)
    removeSeekGoalMarker()
    setSeekStatus('')
    syncSeekPanelUi()
    if (store.links.payload?.geojson) refreshFilteredLinks()
  }

  function applySeekCandidatePayload(payload) {
    seekGoalInRange = Boolean(
      payload.meta?.goal_in_viewshed ?? payload.meta?.goal_reachable,
    )
    applySeekLayersPayload(payload)
    syncSeekPanelUi()
    const n = payload.meta?.n_candidates ?? payload.candidates?.features?.length ?? 0
    const nSites = payload.meta?.n_site_candidates ?? 0
    let statusText
    if (payload.meta?.goal_rf_viable) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ''} — direct RF link to goal`
    } else if (payload.meta?.goal_finish_eligible) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ''} — no RF link to goal (${payload.meta.goal_distance_km ?? '?'} km)`
    } else if (payload.meta?.goal_in_hop_range === false) {
      const maxKm = payload.meta?.hop_range_km ?? '?'
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ''} — goal out of hop range (${payload.meta.goal_distance_km ?? '?'} km, max ${maxKm} km)`
    } else if (payload.meta?.goal_hop_eligible) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ''} — goal visible but not a valid hop target`
    } else {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ''} in view — click peak or site to commit hop`
    }
    setSeekStatus(statusText)
  }

  async function refreshSeekCandidates() {
    if (!seekSessionActive() || !getMapReady()) return
    const from = seekCurrentFrom()
    const goal = seekGoalCoords()
    if (!from || !goal) return
    const fetchKey = [
      from.lat.toFixed(6),
      from.lon.toFixed(6),
      goal.lat.toFixed(6),
      goal.lon.toFixed(6),
      seekViewportBbox(),
      String(seekPeakBinSizeM()),
      seekExcludeParam(),
      seekExcludeSlugsParam(),
    ].join('|')
    seekActiveFetchKey = fetchKey
    const { epoch, signal } = beginSeekFetch()
    let seekRetryScheduled = false
    setSeekScanning(true)
    updateSeekProgressUi({ phase: 'viewshed', detail: 'Warming viewshed…' })
    try {
      if (warmDraftViewshedForSeek) await warmDraftViewshedForSeek(from.lat, from.lon, signal)
    } catch (err) {
      if (err?.name === 'AbortError') return
    }
    if (epoch !== store.seek.fetchEpoch) return
    updateSeekProgressUi({ phase: 'starting' })
    const params = new URLSearchParams({
      from_lat: String(from.lat),
      from_lon: String(from.lon),
      goal_lat: String(goal.lat),
      goal_lon: String(goal.lon),
      bbox: seekViewportBbox(),
      peak_bin_size_m: String(seekPeakBinSizeM()),
    })
    const exclude = seekExcludeParam()
    if (exclude) params.set('exclude', exclude)
    const excludeSlugs = seekExcludeSlugsParam()
    if (excludeSlugs) params.set('exclude_slugs', excludeSlugs)
    try {
      const resp = await fetch(apiUrls.seekCandidatesUrl(projectSlug, params), { signal })
      const kickoff = await resp.json().catch(() => ({}))
      if (epoch !== store.seek.fetchEpoch) return
      if (!resp.ok) {
        seekGoalInRange = false
        syncSeekPanelUi()
        setSeekStatus(kickoff.error || `Seek failed (${resp.status})`)
        return
      }
      if (resp.status !== 202 || kickoff.gen == null) {
        setSeekStatus('Unexpected seek response')
        return
      }
      const outcome = await pollSeekUntilDone(kickoff.gen, signal, epoch)
      if (epoch !== store.seek.fetchEpoch) return
      if (outcome.cancelled) return
      if (outcome.error) {
        seekGoalInRange = false
        syncSeekPanelUi()
        setSeekStatus(outcome.error)
        return
      }
      applySeekCandidatePayload(outcome.payload)
    } catch (err) {
      if (err?.name === 'AbortError') return
      if (epoch !== store.seek.fetchEpoch) return
      seekGoalInRange = false
      syncSeekPanelUi()
      setSeekStatus(String(err))
    } finally {
      if (epoch === store.seek.fetchEpoch && !seekRetryScheduled) setSeekScanning(false)
    }
  }

  function restoreSeekSessionIfAny() {
    rehydrateSeekStateFromConfig()
    const s = store.seek.state
    if (!s?.running) return
    if (!config.seek?.plan) {
      const plan = seekStateToYamlPlan(s)
      if (plan) persistSeekPlanToYaml({ immediate: true })
    }
    if (applySiteLayerFilters) applySiteLayerFilters()
    store.seek.running = !s.complete
    store.ui.seekPanelOpen = true
    if (s.goalLat != null && s.goalLon != null) {
      setSeekPendingGoal(store, s.goalLat, s.goalLon)
    }
    populateSeekStartSelect()
    syncSeekGoalUi()
    syncSeekPanelUi()
    updateSeekPathOverlay()
    if (!s.complete) {
      promptSeekManualRecalc()
    } else {
      applySeekLayersPayload({
        candidates: { type: 'FeatureCollection', features: [] },
        lines: { type: 'FeatureCollection', features: [] },
      })
    }
  }

  function initFromBoot() {
    const initial = initSeekState()
    if (initial) {
      setSeekStateLocal(initial)
      if (initial.goalLat != null && initial.goalLon != null) {
        setSeekPendingGoal(store, initial.goalLat, initial.goalLon)
      }
    }
  }

  function countSeekLocHops() {
    const plan = config.seek?.plan
    if (!plan || !Array.isArray(plan.hops)) return 0
    return plan.hops.filter(
      (hop) => hop && typeof hop === 'object' && hop.loc && !hop.site,
    ).length
  }

  function countSeekUniqueLocHops() {
    const plan = config.seek?.plan
    if (!plan || !Array.isArray(plan.hops)) return 0
    const seen = new Set()
    let n = 0
    for (const hop of plan.hops) {
      if (
        !hop ||
        typeof hop !== 'object' ||
        hop.site ||
        !Array.isArray(hop.loc) ||
        hop.loc.length !== 2
      ) {
        continue
      }
      const lat = Number(hop.loc[0])
      const lon = Number(hop.loc[1])
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue
      const hm = hop.height_m != null ? Number(hop.height_m) : null
      const key = `${lat.toFixed(6)},${lon.toFixed(6)},${hm != null && Number.isFinite(hm) ? hm.toFixed(1) : ''}`
      if (seen.has(key)) continue
      seen.add(key)
      n += 1
    }
    return n
  }

  function openSeekConvertModal() {
    if (countSeekLocHops() === 0) return
    store.seek.convertModal = {
      open: true,
      namePrefix: store.seek.convertModal?.namePrefix || 'Relay',
      tags: [...(store.seek.convertModal?.tags || [])],
      tagInput: '',
      error: '',
      saving: false,
    }
  }

  function closeSeekConvertModal() {
    store.seek.convertModal = {
      ...store.seek.convertModal,
      open: false,
      error: '',
      saving: false,
    }
  }

  function seekConvertHopSummary() {
    const locHops = countSeekLocHops()
    const uniqueSites = countSeekUniqueLocHops()
    const prefix = String(store.seek.convertModal?.namePrefix || '').trim()
    if (locHops === 0) return 'No coordinate hops in the saved path.'
    const names = prefix ? `${prefix} + number` : 'prefix + number'
    if (uniqueSites === locHops) {
      return `${locHops} coordinate hop(s) will become ${uniqueSites} site(s). Names: ${names}.`
    }
    return `${locHops} coordinate hop(s) will become ${uniqueSites} site(s) (duplicate coordinates reuse one site). Names: ${names}.`
  }

  async function convertSeekPathToSites() {
    const modal = store.seek.convertModal
    const namePrefix = String(modal?.namePrefix || '').trim()
    const pending = normalizeTagInput(modal?.tagInput || '')
    const tags = [...(modal?.tags || [])]
    if (pending && !tags.includes(pending)) tags.push(pending)
    if (!namePrefix) {
      store.seek.convertModal = { ...modal, error: 'Name prefix is required.' }
      return { ok: false }
    }
    if (!tags.length) {
      store.seek.convertModal = { ...modal, error: 'Choose at least one tag.' }
      return { ok: false }
    }
    if (countSeekLocHops() === 0) {
      store.seek.convertModal = { ...modal, error: 'No coordinate hops to convert.' }
      return { ok: false }
    }
    store.seek.convertModal = { ...modal, error: '', saving: true }
    try {
      const resp = await fetch(apiUrls.seekPlanConvertToSitesUrl(projectSlug), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name_prefix: namePrefix, tags }),
      })
      const payload = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        store.seek.convertModal = {
          ...store.seek.convertModal,
          saving: false,
          error: payload.error || `Convert failed (${resp.status})`,
        }
        return { ok: false }
      }
      const imported = Array.isArray(payload.sites) ? payload.sites : []
      const plan = payload.plan
      closeSeekConvertModal()
      for (const site of imported) {
        registerSiteFromApi?.(site)
      }
      if (plan && config.seek && typeof config.seek === 'object') {
        config.seek.plan = plan
      }
      if (plan) restoreSeekSessionIfAny()
      const pathSlugs = Array.isArray(payload.path_slugs) ? payload.path_slugs : []
      for (const slug of pathSlugs) bypassSiteTagFilter?.(slug)
      mergeConvertedSeekLinks?.(payload)
      applySiteLayerFilters?.()
      refreshFilteredLinks?.()
      populateSeekStartSelect()
      const converted = payload.converted ?? 0
      const tagged = payload.tagged ?? 0
      let status = `Converted ${converted} hop(s) to sites`
      if (tagged > 0) status += `; tagged ${tagged} existing site(s)`
      setSeekStatus(status)
      const createdSlugs = Array.isArray(payload.created_slugs)
        ? payload.created_slugs.filter(Boolean)
        : imported.map((site) => site.slug).filter(Boolean)
      if (fetchOutboundLinksParallel) void fetchOutboundLinksParallel(createdSlugs)
      if (createdSlugs[0] && selectSite) selectSite(createdSlugs[0])
      return { ok: true, payload }
    } catch (err) {
      store.seek.convertModal = {
        ...store.seek.convertModal,
        saving: false,
        error: String(err),
      }
      return { ok: false }
    }
  }

  function install() {
    mapToolSeek?.addEventListener('click', () => toggleSeekPanel())
  }

  return {
    install,
    initFromBoot,
    restoreSeekSessionIfAny,
    toggleSeekPanel,
    startSeekRun,
    resetSeekRun,
    setSeekGoalAt,
    setSeekGoalPlacementMode,
    refreshSeekCandidates,
    commitSeekCandidate,
    undoSeekHop,
    redoSeekHop,
    onSeekStartChange,
    openSeekConvertModal,
    closeSeekConvertModal,
    convertSeekPathToSites,
    seekConvertHopSummary,
    countSeekLocHops,
    countSeekUniqueLocHops,
    syncSeekPanelUi,
    syncSeekGoalUi,
    updateSeekPathOverlay,
    applySeekLayersPayload,
    seekSessionActive,
    seekCurrentFrom,
    isSiteInSeekPlan,
    syncSeekHopViewsheds,
    clearAllSeekHopViewsheds,
    refreshSeekAncillaryLinksDisplay,
    getSeekHopCoordViewshedSlugs: () => seekHopCoordViewshedSlugs,
    getSeekHopCoordViewshedCoords: () => seekHopCoordViewshedCoords,
    seekSiteCandidateSlugs: () => seekSiteCandidateSlugs,
    seekGoalCoords,
    promptSeekManualRecalc,
    populateSeekStartSelect,
    seekSiteSlugNear,
    onMapMoveEndForSeek: () => {},
  }
}
