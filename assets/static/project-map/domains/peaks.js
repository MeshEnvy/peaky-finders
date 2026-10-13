// @ts-check

import * as apiUrls from '../api/urls.js'
import {
  PEAKS_ACCESS_LINE,
  PEAKS_JEEP_LINE,
  PEAKS_TRAIL_LINE,
  PEAKS_SYMBOL,
  PEAK_VIEWSHED_SLUG,
} from '../constants.js'
import { captureDeepLinkFromMap, writeDeepLink } from '../api/deep-link.js'
import {
  applyDraftLinksLayer,
  removeDraftLinksLayer,
} from '../map/links-layers.js'
import { ensurePeaksLayers, setPeaksLayerData, setPeaksCursorPoint, clearPeaksCursorPoint } from '../map/peaks-layers.js'
import {
  clearAccessPreviewFocus,
  setAccessPreviewFocus,
} from '../stores/access.js'
import {
  hiddenPeakSlugs,
  isPeakMapVisible,
  sidebarPeaks,
  visiblePeaksForMap,
} from '../stores/peaks.js'
import { flyMapToCoord } from '../geo.js'
import { beginPreview, cancelPreview, currentPreview } from '../preview-session.js'

/**
 * @param {object} opts
 * @param {object} opts.store
 * @param {string} opts.projectSlug
 * @param {() => maplibregl.Map} opts.getMap
 * @param {() => boolean} opts.getMapReady
 * @param {() => void} [opts.deselectSite]
 * @param {() => void} [opts.clearLinkSelection]
 * @param {() => object|null|undefined} [opts.getViewshed]
 * @param {(slug: string) => boolean} [opts.isSiteMapHidden]
 * @param {() => void} [opts.raiseSiteLayers]
 * @param {() => void} [opts.scheduleSaveMapState]
 * @param {object} [opts.siteAccessDomain]
 */
export function createPeaksDomain(opts) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    deselectSite,
    clearLinkSelection,
    getViewshed,
    isSiteMapHidden,
    raiseSiteLayers,
    scheduleSaveMapState,
    siteAccessDomain,
  } = opts

  let peakLinksPrefetchGen = 0
  let rfPreviewSlug = null

  function vs() {
    return getViewshed?.()
  }

  function peakPanelEl() {
    return document.getElementById('peak-panel')
  }

  function bumpPeaksPanel() {
    store.peaks.panelRevision = (store.peaks.panelRevision || 0) + 1
  }

  function pruneHiddenPeaks() {
    const known = new Set(store.peaks.list.map((peak) => peak.slug))
    for (const slug of [...store.peaks.hidden]) {
      if (!known.has(slug)) store.peaks.hidden.delete(slug)
    }
  }

  function stripPeakRouteProfiles(peak) {
    if (!peak.hike && !peak.jeep && !peak.trail) return peak
    return { ...peak, hike: null, jeep: null, trail: null }
  }

  function peaksForMapLayers() {
    const focus = store.peaks.accessSlug || store.ui.selectedPeakSlug
    return visiblePeaksForMap(store).map((peak) => {
      if (
        peak.slug === focus &&
        siteAccessDomain?.accessHasRouteProfiles?.(peak)
      ) {
        return peak
      }
      return stripPeakRouteProfiles(peak)
    })
  }

  function refreshPeakLayers() {
    if (!getMapReady()) return
    setPeaksLayerData(getMap(), peaksForMapLayers(), store.peaks.rules?.difficulty || null)
  }

  function mergePeakAccess(slug, access) {
    const idx = store.peaks.list.findIndex((p) => p.slug === slug)
    if (idx < 0) return
    const merged = {
      ...store.peaks.list[idx],
      road_lat: access.road_lat ?? store.peaks.list[idx].road_lat,
      road_lon: access.road_lon ?? store.peaks.list[idx].road_lon,
      paved_lat: access.paved_lat ?? store.peaks.list[idx].paved_lat,
      paved_lon: access.paved_lon ?? store.peaks.list[idx].paved_lon,
      hike_m: access.hike_m ?? store.peaks.list[idx].hike_m,
      jeep_m: access.jeep_m ?? store.peaks.list[idx].jeep_m,
      trail_m: access.trail_m ?? store.peaks.list[idx].trail_m,
      trail_lat: access.trail_lat ?? store.peaks.list[idx].trail_lat,
      trail_lon: access.trail_lon ?? store.peaks.list[idx].trail_lon,
      hike: access.hike ?? store.peaks.list[idx].hike,
      jeep: access.jeep ?? store.peaks.list[idx].jeep,
      trail: access.trail ?? store.peaks.list[idx].trail,
      eligible: access.eligible ?? store.peaks.list[idx].eligible,
      budget: access.budget ?? store.peaks.list[idx].budget,
      budget_route: access.budget_route ?? store.peaks.list[idx].budget_route,
    }
    store.peaks.list.splice(idx, 1, merged)
  }

  function clearPeakAccessPreview() {
    clearAccessPreviewFocus(store)
    siteAccessDomain?.refreshLayers?.()
    refreshPeakLayers()
  }

  function applyPeakVisibilityChange(slug) {
    refreshPeakLayers()
    vs()?.applyViewshedVisibilityForSite?.(slug)
    if (isPeakMapVisible(store, slug)) {
      vs()?.ensureViewshedLoadedForSlug?.(slug)
    }
    if (store.ui.selectedPeakSlug === slug && !isPeakMapVisible(store, slug)) {
      deselectPeak({ syncDeepLink: true })
    }
    bumpPeaksPanel()
    scheduleSaveMapState?.()
  }

  function syncDeepLinkToUrl({ replace = false } = {}) {
    if (store.ui.restoring || !getMapReady()) return
    writeDeepLink(
      captureDeepLinkFromMap(getMap(), {
        site: store.ui.selectedSlug || undefined,
        peak: store.ui.selectedPeakSlug || undefined,
      }),
      { replace },
    )
  }

  function updatePeakHighlight(slug) {
    if (!getMapReady()) return
    const map = getMap()
    const sel = slug || ''
    if (map.getLayer(PEAKS_ACCESS_LINE)) {
      map.setPaintProperty(PEAKS_ACCESS_LINE, 'line-width', [
        'case',
        ['==', ['get', 'slug'], sel],
        5,
        3,
      ])
      map.setPaintProperty(PEAKS_ACCESS_LINE, 'line-color', [
        'case',
        ['==', ['get', 'slug'], sel],
        '#fde047',
        '#22c55e',
      ])
    }
    if (map.getLayer(PEAKS_JEEP_LINE)) {
      map.setPaintProperty(PEAKS_JEEP_LINE, 'line-width', [
        'case',
        ['==', ['get', 'slug'], sel],
        6,
        4,
      ])
      map.setPaintProperty(PEAKS_JEEP_LINE, 'line-color', [
        'case',
        ['==', ['get', 'slug'], sel],
        '#fdba74',
        '#ea580c',
      ])
    }
    if (map.getLayer(PEAKS_TRAIL_LINE)) {
      map.setPaintProperty(PEAKS_TRAIL_LINE, 'line-width', [
        'case',
        ['==', ['get', 'slug'], sel],
        5,
        3.5,
      ])
      map.setPaintProperty(PEAKS_TRAIL_LINE, 'line-color', [
        'case',
        ['==', ['get', 'slug'], sel],
        '#fde68a',
        '#d4a574',
      ])
    }
  }

  function draftPeerVisible(feature) {
    const slug = (feature?.properties || {}).slug
    if (!slug) return false
    return !(isSiteMapHidden?.(slug) ?? false)
  }

  function removePeakDraftLinks() {
    if (!getMapReady()) return
    removeDraftLinksLayer(getMap())
  }

  function cancelPeakViewshedLoad() {
    store.viewshed.pendingEpoch.delete(PEAK_VIEWSHED_SLUG)
    store.viewshed.loading.delete(PEAK_VIEWSHED_SLUG)
    vs()?.clearViewshedLoadingState?.(PEAK_VIEWSHED_SLUG)
  }

  function clearPeakRfPreview() {
    cancelPreview()
    if (rfPreviewSlug) {
      vs()?.clearViewshedLoadingState?.(rfPreviewSlug)
      vs()?.removeViewshedLayer?.(rfPreviewSlug)
      store.viewshed.visible.delete(rfPreviewSlug)
      rfPreviewSlug = null
    }
    cancelPeakViewshedLoad()
    peakLinksPrefetchGen += 1
    vs()?.removeViewshedLayer?.(PEAK_VIEWSHED_SLUG)
    store.viewshed.visible.delete(PEAK_VIEWSHED_SLUG)
    removePeakDraftLinks()
    clearPeakAccessPreview()
    vs()?.updatePinOverlays?.()
  }

  async function loadPeakPrefetchLinks(lat, lon) {
    const gen = ++peakLinksPrefetchGen
    removePeakDraftLinks()
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    try {
      const preview = currentPreview()
      const resp = await fetch(apiUrls.sitesPrefetchUrl(projectSlug, lat, lon, undefined, preview.id || undefined), {
        signal: preview.signal,
      })
      if (gen !== peakLinksPrefetchGen) return
      if (!resp.ok) return
      const payload = await resp.json()
      if (payload?.status === 'cancelled') return
      if (gen !== peakLinksPrefetchGen) return
      if (!getMapReady()) return
      if (payload?.links_geojson) {
        applyDraftLinksLayer(getMap(), payload.links_geojson, draftPeerVisible, () =>
          raiseSiteLayers?.(),
        )
      } else {
        removePeakDraftLinks()
      }
    } catch (_) {
      /* peak link prefetch optional */
    }
  }

  async function loadPeakAccess(peak) {
    if (!peak?.slug || !siteAccessDomain) return
    const lat = Number(peak.lat)
    const lon = Number(peak.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    setAccessPreviewFocus(store, 'peaks', peak.slug)
    refreshPeakLayers()
    siteAccessDomain.refreshLayers()

    const cached = store.access?.bySlug?.[peak.slug]
    if (siteAccessDomain.accessHasRouteProfiles?.(cached)) {
      mergePeakAccess(peak.slug, cached)
      siteAccessDomain.refreshLayers()
      return
    }

    await siteAccessDomain.ensurePlaceAccessProfiles?.(
      peak.slug,
      lat,
      lon,
      { slug: peak.slug, name: peak.name || peak.slug, lat, lon },
      {
        isActive: () => store.peaks.accessSlug === peak.slug,
        onReady: () => {
          const row = store.access?.bySlug?.[peak.slug]
          if (row) mergePeakAccess(peak.slug, row)
          siteAccessDomain.refreshLayers()
          refreshPeakLayers()
        },
      },
    )
  }

  /** @param {object} peak */
  function showPeakRfPreview(peak) {
    if (store.ui.createMode || store.ui.relocateMode) return
    const lat = Number(peak?.lat)
    const lon = Number(peak?.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    beginPreview()
    if (rfPreviewSlug && rfPreviewSlug !== peak.slug) {
      vs()?.clearViewshedLoadingState?.(rfPreviewSlug)
      vs()?.removeViewshedLayer?.(rfPreviewSlug)
      store.viewshed.visible.delete(rfPreviewSlug)
    }
    rfPreviewSlug = peak.slug
    cancelPeakViewshedLoad()
    peakLinksPrefetchGen += 1
    vs()?.removeViewshedLayer?.(PEAK_VIEWSHED_SLUG)
    store.viewshed.visible.delete(PEAK_VIEWSHED_SLUG)
    removePeakDraftLinks()
    vs()?.updatePinOverlays?.()
    vs()?.setViewshedVisible?.(peak.slug, true)
    void loadPeakPrefetchLinks(lat, lon)
    void loadPeakAccess(peak)
  }

  async function loadPeaks() {
    if (!getMapReady()) return
    const map = getMap()
    try {
      const resp = await fetch(apiUrls.peaksApiUrl(projectSlug))
      if (!resp.ok) return
      const data = await resp.json()
      if (data.missing_catalog) {
        store.peaks.list = []
        store.peaks.rules = null
        store.peaks.missingCatalog = true
        store.peaks.missingCatalogHint =
          'Peaks catalog not built. Run `peaky peaks build` for this project.'
        bumpPeaksPanel()
        return
      }
      store.peaks.missingCatalog = false
      store.peaks.missingCatalogHint = ''
      store.peaks.list = data.peaks || []
      store.peaks.rules = data.rules || null
      pruneHiddenPeaks()
      await ensurePeaksLayers(map)
      refreshPeakLayers()
      updatePeakHighlight(store.ui.selectedPeakSlug)
      bumpPeaksPanel()
      raiseSiteLayers?.()
      vs()?.ensureViewshedsForNewlyVisibleSites?.()
    } catch (err) {
      console.warn('peaks: catalog load failed', err)
    }
  }

  function selectPeak(slug, { syncDeepLink = true } = {}) {
    const peak = store.peaks.list.find((p) => p.slug === slug)
    if (!peak) return
    clearLinkSelection?.()
    deselectSite?.({ syncDeepLink: false })
    store.ui.selectedPeakSlug = slug
    const panel = peakPanelEl()
    if (panel) panel.hidden = false
    updatePeakHighlight(slug)
    if (getMapReady()) clearPeaksCursorPoint(getMap())
    showPeakRfPreview(peak)
    if (syncDeepLink) syncDeepLinkToUrl({ replace: false })
  }

  function togglePeakMapVisible(slug) {
    const value = String(slug || '').trim()
    if (!value) return
    if (store.peaks.hidden.has(value)) store.peaks.hidden.delete(value)
    else store.peaks.hidden.add(value)
    applyPeakVisibilityChange(value)
  }

  function listedPeaks() {
    return sidebarPeaks(store, {
      map: getMap(),
      mapReady: getMapReady(),
    })
  }

  function showAllPeaks() {
    const listed = listedPeaks()
    let changed = false
    for (const peak of listed) {
      if (!peak?.slug || !store.peaks.hidden.has(peak.slug)) continue
      store.peaks.hidden.delete(peak.slug)
      changed = true
    }
    if (!changed) return
    refreshPeakLayers()
    bumpPeaksPanel()
    scheduleSaveMapState?.()
  }

  function hideAllPeaks() {
    const listed = listedPeaks()
    let changed = false
    for (const peak of listed) {
      if (!peak?.slug || store.peaks.hidden.has(peak.slug)) continue
      store.peaks.hidden.add(peak.slug)
      changed = true
    }
    if (!changed) return
    if (
      store.ui.selectedPeakSlug &&
      listed.some((peak) => peak.slug === store.ui.selectedPeakSlug)
    ) {
      deselectPeak({ syncDeepLink: true })
    }
    refreshPeakLayers()
    bumpPeaksPanel()
    scheduleSaveMapState?.()
  }

  function listedPeaksAllVisible() {
    const listed = listedPeaks()
    return listed.length > 0 && listed.every((peak) => peak?.slug && isPeakMapVisible(store, peak.slug))
  }

  function listedPeaksAllViewsheds() {
    const listed = listedPeaks()
    return listed.length > 0 && listed.every((peak) => peak?.slug && vs()?.isViewshedVisible?.(peak.slug) === true)
  }

  function toggleListedPeakVisibility() {
    if (!listedPeaks().length) return
    if (listedPeaksAllVisible()) hideAllPeaks()
    else showAllPeaks()
  }

  function toggleListedPeakViewsheds() {
    const listed = listedPeaks()
    if (!listed.length) return
    const next = !listedPeaksAllViewsheds()
    for (const peak of listed) {
      if (!peak?.slug) continue
      if (vs()?.isViewshedVisible?.(peak.slug) === next) continue
      vs()?.setViewshedVisible?.(peak.slug, next)
    }
    bumpPeaksPanel()
    scheduleSaveMapState?.()
  }

  /** @param {object} peak */
  function showPeakInView(peak) {
    const lat = Number(peak?.lat)
    const lon = Number(peak?.lon)
    if (!getMapReady() || !Number.isFinite(lat) || !Number.isFinite(lon)) return
    flyMapToCoord(getMap(), lat, lon)
  }

  let handedOffViewshedSlug = null

  function releaseHandedOffViewshed(bookedSlug) {
    const slug = handedOffViewshedSlug
    if (!slug || bookedSlug !== store.ui.selectedSlug) return
    handedOffViewshedSlug = null
    if (slug === bookedSlug) return
    vs()?.removeViewshedLayer?.(slug)
    store.viewshed.visible.delete(slug)
  }

  function deselectPeak({ syncDeepLink = true, keepViewshed = false } = {}) {
    if (keepViewshed) handedOffViewshedSlug = store.ui.selectedPeakSlug
    store.ui.selectedPeakSlug = null
    if (keepViewshed) {
      cancelPeakViewshedLoad()
      peakLinksPrefetchGen += 1
      removePeakDraftLinks()
      clearPeakAccessPreview()
      vs()?.updatePinOverlays?.()
    } else {
      handedOffViewshedSlug = null
      clearPeakRfPreview()
    }
    const panel = peakPanelEl()
    if (panel) panel.hidden = true
    updatePeakHighlight(null)
    if (getMapReady()) clearPeaksCursorPoint(getMap())
    if (syncDeepLink) syncDeepLinkToUrl({ replace: false })
  }

  function getPeakBySlug(slug) {
    return store.peaks.list.find((p) => p.slug === slug) || null
  }

  function flyToProfilePoint(lat, lon, zoom = 15) {
    if (!getMapReady() || !Number.isFinite(lat) || !Number.isFinite(lon)) return
    const map = getMap()
    setPeaksCursorPoint(map, lat, lon)
    map.flyTo({
      center: [lon, lat],
      zoom,
      duration: 600,
    })
  }

  return {
    loadPeaks,
    selectPeak,
    deselectPeak,
    releaseHandedOffViewshed,
    getPeakBySlug,
    updatePeakHighlight,
    flyToProfilePoint,
    clearPeakRfPreview,
    togglePeakMapVisible,
    showAllPeaks,
    hideAllPeaks,
    toggleListedPeakVisibility,
    toggleListedPeakViewsheds,
    listedPeaksAllVisible,
    listedPeaksAllViewsheds,
    showPeakInView,
    bumpPeaksPanel,
    hiddenPeakSlugs: () => hiddenPeakSlugs(store),
    isPeakMapVisible: (slug) => isPeakMapVisible(store, slug),
  }
}
