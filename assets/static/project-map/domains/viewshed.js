// @ts-check

import * as apiUrls from '../api/urls.js'
import { currentPreview } from '../preview-session.js'
import {
  ALTERNATE_VIEWSHED_SLUG,
  FORTIFY_VIEWSHED_SLUG,
  PEAK_VIEWSHED_SLUG,
  DRAFT_VIEWSHED_SLUG,
  isPreviewViewshedSlug,
  VIEWSHED_OVERLAY_BATCH,
} from '../constants.js'
import { viewshedLayerId } from '../constants.js'
import { coordsUsableForMarker } from '../geo.js'
import { osmRoadsProgressLabel } from '../stores/access.js'
import { buildViewshedSimQueryParams } from '../stores/simulation.js'
import {
  raiseViewshedLayersBelowStack,
  stackFloorBeforeId,
} from '../map/layer-stack.js'
import {
  addViewshedRasterLayer,
  removeViewshedLayer as removeViewshedRasterLayer,
  setViewshedLayerVisibility,
} from '../map/viewshed-layers.js'
import {
  hideInactivePinMarkers,
  hidePinLoadMarker,
  initPinMarkers,
  renderPinLoadOverlay,
} from '../map/pins.js'
import {
  setPinProgress as setPinProgressInStore,
  clearPinProgress as clearPinProgressInStore,
  markViewshedReady as markViewshedReadyInStore,
  markOutboundLinksReady as markOutboundLinksReadyInStore,
  resetSiteProgress as resetSiteProgressInStore,
  isViewshedVisible as isViewshedVisibleInStore,
  isViewshedHostVisible as isViewshedHostVisibleInStore,
  siteRowBySlug,
  setViewshedVisible as setViewshedVisibleInStore,
  clearViewshedLoadingState as clearViewshedLoadingStateInStore,
  clearPendingEpoch as clearPendingEpochInStore,
  setPendingCoords as setPendingCoordsInStore,
  bumpLoadEpoch as bumpLoadEpochInStore,
  clearViewshedLoading as clearViewshedLoadingInStore,
} from '../stores/viewshed.js'

const OUTBOUND_LINKS_PARALLEL = 3

/**
 * @param {object} opts
 * @param {object} [opts.store]
 * @param {maplibregl.Map} opts.map
 * @param {string} opts.projectSlug
 * @param {() => boolean} opts.getMapReady
 * @param {() => object[]} opts.getSites
 * @param {() => string|null} opts.getSelectedSlug
 * @param {() => number} opts.getViewshedOpacity
 * @param {() => { radiusKm: number, quality: number }} opts.getSimParams
 * @param {(slug: string) => boolean} opts.isSiteMapHidden
 * @param {(slug: string) => boolean} opts.isEphemeralViewshedSlug
 * @param {(slug: string) => void} opts.loadSingleSiteLinks
 * @param {(slugs: string[], priority: number) => void} opts.bumpWarmPriorities
 * @param {() => void} opts.syncWarmPriorities
 * @param {number} opts.warmPriorityInteractive
 * @param {number} opts.warmPriorityViewport
 * @param {() => void} opts.raiseSiteLayers
 * @param {(marker: maplibregl.Marker, lon: number, lat: number) => boolean} opts.setMarkerLngLatSafe
 * @param {() => void} opts.syncViewshedCheckbox
 * @param {() => void} [opts.onDraftViewshedReady]
 * @param {() => void} [opts.onDraftViewshedLoadingClear]
 * @param {() => void} [opts.onDraftViewshedLayerAdded]
 * @param {(slug: string) => void} [opts.onBookedViewshedShown]
 * @param {() => boolean} [opts.viewshedLoadingHasDraft]
 * @param {() => { lat: number, lon: number }|null} [opts.getDraftPlacement]
 * @param {() => Iterable<string>} [opts.getLinkSolverHopViewshedSlugs]
 * @param {() => Map<string, { lat: number, lon: number }>} [opts.getLinkSolverHopViewshedCoords]
 * @param {() => Iterable<string>} [opts.getExtraOverlaySlugs]
 */
export function createViewshedDomain(opts) {
  const {
    store,
    map,
    projectSlug,
    getMapReady,
    getSites,
    getViewshedOpacity,
    getLoadDraftAt,
    isSiteMapHidden,
    isEphemeralViewshedSlug,
    loadSingleSiteLinks,
    bumpWarmPriorities,
    syncWarmPriorities,
    warmPriorityInteractive,
    warmPriorityViewport,
    raiseSiteLayers,
    setMarkerLngLatSafe,
    syncViewshedCheckbox,
    onDraftViewshedReady,
    onDraftViewshedLoadingClear,
    onBookedViewshedShown,
    onDraftViewshedLayerAdded,
    viewshedLoadingHasDraft,
    getDraftPlacement,
    getLinkSolverHopViewshedSlugs,
    getLinkSolverHopViewshedCoords,
    getExtraOverlaySlugs,
  } = opts

  initPinMarkers({ map, getMapReady, setMarkerLngLatSafe })

  const viewshedVisible = store ? store.viewshed.visible : new Map()
  const viewshedLoading = store ? store.viewshed.loading : new Set()
  const siteViewshedReady = store ? store.viewshed.ready : new Set()
  const siteOutboundLinksReady = store ? store.viewshed.outboundLinksReady : new Set()
  /** @type {Map<string, { raster?: number, target?: number, step?: number, total?: number, phase?: string }>} */
  const sitePinProgress = store ? store.viewshed.pinProgress : new Map()
  const viewshedPendingEpoch = store ? store.viewshed.pendingEpoch : new Map()
  let viewshedLoadEpoch = store ? store.viewshed.loadEpoch : 0

  function setSitePinProgress(slug, partial) {
    if (!slug) return
    if (store) setPinProgressInStore(store, slug, partial)
    else
      sitePinProgress.set(slug, {
        ...(sitePinProgress.get(slug) || {}),
        ...partial,
      })
  }

  function clearSitePinProgress(slug) {
    if (!slug) return
    if (store) clearPinProgressInStore(store, slug)
    else sitePinProgress.delete(slug)
  }

  function sitePinProgressLabel(slug) {
    if (siteViewshedReady.has(slug) && !siteOutboundLinksReady.has(slug)) {
      return 'links…'
    }
    const p = sitePinProgress.get(slug)
    if (p?.phase === 'terrain') return 'terrain…'
    if (p?.phase === 'raster') return 'raster…'
    if (p?.raster > 0) {
      return `${p.raster}px`
    }
    return 'warm…'
  }

  function sitePinProgressFraction(slug) {
    if (siteViewshedReady.has(slug) && siteOutboundLinksReady.has(slug)) return 1
    if (siteViewshedReady.has(slug) && !siteOutboundLinksReady.has(slug)) return 0.92
    const p = sitePinProgress.get(slug)
    if (typeof p?.fraction === 'number' && p.fraction > 0) {
      return Math.min(0.88, 0.06 + 0.82 * p.fraction)
    }
    return 0.06
  }

  function isViewshedVisible(slug) {
    if (store) return isViewshedVisibleInStore(store, slug)
    return viewshedVisible.get(slug) !== false
  }

  function getSiteBySlug(slug) {
    if (store) return siteRowBySlug(store, slug) || null
    return getSites().find((site) => site.slug === slug) || null
  }

  function isCatalogPeakSlug(slug) {
    const value = String(slug || '')
    if (store) {
      if (siteRowBySlug(store, value)) return false
      return (store.peaks?.list || []).some((peak) => peak.slug === value)
    }
    return !!(store?.peaks?.list || []).find((peak) => peak.slug === slug)
  }

  function isViewshedHostVisible(slug) {
    return isViewshedHostVisibleInStore(store, slug, {
      isSiteMapHidden,
      isEphemeralViewshedSlug,
      getSiteBySlug,
    })
  }

  /**
   * Site rasters require a visible pin, even when the slug is also a catalog peak.
   * A peak-only raster follows the v flag alone.
   */
  function viewshedLayerShouldShow(slug) {
    if (!isViewshedVisible(slug)) return false
    if (getSiteBySlug(slug)) return isViewshedHostVisible(slug)
    if (isCatalogPeakSlug(slug)) return true
    return isViewshedHostVisible(slug)
  }

  function skipOutboundLinksForViewshedSlug(slug) {
    return isEphemeralViewshedSlug(slug) || isCatalogPeakSlug(slug)
  }

  function resetSiteProgress(slug) {
    if (store) resetSiteProgressInStore(store, slug)
    else {
      siteViewshedReady.delete(slug)
      siteOutboundLinksReady.delete(slug)
      clearSitePinProgress(slug)
    }
  }

  function markSiteViewshedReady(slug) {
    if (store) {
      markViewshedReadyInStore(store, slug)
      clearPendingEpochInStore(store, slug)
    } else {
      siteViewshedReady.add(slug)
      viewshedLoading.delete(slug)
      viewshedPendingEpoch.delete(slug)
    }
    if (siteOutboundLinksReady.has(slug)) {
      clearSitePinProgress(slug)
    } else {
      setSitePinProgress(slug, { phase: 'links' })
    }
    updatePinOverlays()
    if (slug === store.ui.selectedSlug) syncViewshedCheckbox()
  }

  function markSiteOutboundLinksReady(slug) {
    if (store) markOutboundLinksReadyInStore(store, slug)
    else siteOutboundLinksReady.add(slug)
    if (siteViewshedReady.has(slug)) {
      clearSitePinProgress(slug)
    }
    updatePinOverlays()
  }

  function sitePinSpinning(slug) {
    if (!isViewshedHostVisible(slug)) return false
    if (!isViewshedVisible(slug)) return false
    if (siteViewshedReady.has(slug) && siteOutboundLinksReady.has(slug)) {
      return false
    }
    if (siteViewshedReady.has(slug) && !siteOutboundLinksReady.has(slug)) {
      return true
    }
    return viewshedLoading.has(slug) || viewshedPendingEpoch.has(slug)
  }

  function isSiteOutboundLinksReady(slug) {
    return siteOutboundLinksReady.has(slug)
  }

  function viewshedSimQueryParams() {
    return buildViewshedSimQueryParams(store.simulation.radiusKm)
  }

  function viewshedPrefetchWarmUrl(lat, lon) {
    const params = viewshedSimQueryParams()
    params.set('lat', String(lat))
    params.set('lon', String(lon))
    const preview = currentPreview()
    if (preview.id) params.set('preview', String(preview.id))
    return apiUrls.viewshedPrefetchWarmUrl(projectSlug, params)
  }

  function viewshedPrefetchMetaUrl(lat, lon) {
    const params = viewshedSimQueryParams()
    params.set('lat', String(lat))
    params.set('lon', String(lon))
    return apiUrls.viewshedPrefetchMetaUrl(projectSlug, params)
  }

  function viewshedOverlaySlugs() {
    const slugs = getSites().map((site) => site.slug)
    const vis = store?.viewshed?.visible
    for (const peak of store?.peaks?.list || []) {
      const slug = peak.slug
      const explicitOn = vis?.has(slug) && vis.get(slug) !== false
      if (
        explicitOn ||
        viewshedPendingEpoch.has(slug) ||
        viewshedLoading.has(slug) ||
        (getMapReady() && map.getLayer(viewshedLayerId(slug)))
      ) {
        slugs.push(slug)
      }
    }
    slugs.push(
      DRAFT_VIEWSHED_SLUG,
      ALTERNATE_VIEWSHED_SLUG,
      FORTIFY_VIEWSHED_SLUG,
      PEAK_VIEWSHED_SLUG,
    )
    if (getLinkSolverHopViewshedSlugs) {
      for (const slug of getLinkSolverHopViewshedSlugs()) slugs.push(slug)
    }
    if (getExtraOverlaySlugs) {
      for (const slug of getExtraOverlaySlugs()) slugs.push(slug)
    }
    return slugs
  }

  function viewshedLayerInsertBefore() {
    if (!getMapReady()) return undefined
    return stackFloorBeforeId(map)
  }

  function raiseViewshedLayers() {
    if (!getMapReady()) return
    const beforeId = stackFloorBeforeId(map)
    if (!beforeId) return
    const layerIds = viewshedOverlaySlugs()
      .map((slug) => viewshedLayerId(slug))
      .filter((id) => map.getLayer(id))
    raiseViewshedLayersBelowStack(map, beforeId, layerIds)
  }

  function applyViewshedOpacityToAllLayers() {
    if (!getMapReady()) return
    const opacity = getViewshedOpacity()
    for (const slug of viewshedOverlaySlugs()) {
      const layerId = viewshedLayerId(slug)
      if (map.getLayer(layerId)) {
        map.setPaintProperty(layerId, 'raster-opacity', opacity)
      }
    }
  }

  function applyViewshedVisibilityForSite(slug) {
    setViewshedLayerVisibility(map, slug, viewshedLayerShouldShow(slug))
  }

  function applyViewshedVisibilityForMountedLayers() {
    if (!getMapReady()) return
    const style = map.getStyle()
    if (!style?.layers) return
    for (const layer of style.layers) {
      const id = layer.id
      if (!id.startsWith('viewshed-') || !id.endsWith('-raster')) continue
      const slug = id.slice('viewshed-'.length, -'-raster'.length)
      applyViewshedVisibilityForSite(slug)
    }
  }

  function setViewshedVisible(slug, visible) {
    setViewshedVisibleInStore(store, slug, visible)
    if (map.getLayer(viewshedLayerId(slug))) {
      applyViewshedVisibilityForSite(slug)
    } else if (visible) {
      ensureViewshedLoadedForSlug(slug, (...args) => getLoadDraftAt?.()?.(...args))
    }
    updatePinOverlays()
  }

  /** @type {Map<string, number>} */
  const viewshedAddTicket = new Map()

  function removeViewshedLayer(slug) {
    viewshedAddTicket.set(slug, (viewshedAddTicket.get(slug) || 0) + 1)
    removeViewshedRasterLayer(map, slug)
    if (store) clearViewshedLoadingInStore(store, slug)
    else viewshedLoading.delete(slug)
    updatePinOverlays()
  }

  function addViewshedLayer(vs) {
    const ticket = (viewshedAddTicket.get(vs.slug) || 0) + 1
    viewshedAddTicket.set(vs.slug, ticket)
    void addViewshedRasterLayer(
      map,
      vs.slug,
      { url: vs.url, coordinates: vs.coordinates },
      getViewshedOpacity(),
      viewshedLayerInsertBefore(),
      () => viewshedAddTicket.get(vs.slug) === ticket && viewshedLayerShouldShow(vs.slug),
    ).then(() => {
      if (viewshedAddTicket.get(vs.slug) !== ticket) return
      if (!isEphemeralViewshedSlug(vs.slug)) onBookedViewshedShown?.(vs.slug)
      setViewshedLayerVisibility(map, vs.slug, viewshedLayerShouldShow(vs.slug))
      if (store) clearViewshedLoadingInStore(store, vs.slug)
      else viewshedLoading.delete(vs.slug)
      updatePinOverlays()
      if (vs.slug === store.ui.selectedSlug) syncViewshedCheckbox()
      if (vs.slug === DRAFT_VIEWSHED_SLUG) {
        if (onDraftViewshedLayerAdded) onDraftViewshedLayerAdded()
      }
      raiseSiteLayers()
    }).catch(() => {})
  }

  function clearViewshedLoadingState(slug) {
    if (store) clearViewshedLoadingStateInStore(store, slug)
    else {
      viewshedPendingEpoch.delete(slug)
      viewshedLoading.delete(slug)
      clearSitePinProgress(slug)
    }
    if (slug === DRAFT_VIEWSHED_SLUG && onDraftViewshedLoadingClear) {
      onDraftViewshedLoadingClear()
    }
    updatePinOverlays()
    if (slug === store.ui.selectedSlug) syncViewshedCheckbox()
  }

  function bumpViewshedLoadEpoch() {
    if (store) {
      viewshedLoadEpoch = bumpLoadEpochInStore(store)
    } else {
      viewshedLoadEpoch += 1
    }
    return viewshedLoadEpoch
  }

  function finalizeViewshedReady(vs) {
    if (vs?.slug) {
      setSitePinProgress(vs.slug, {
        raster: Number(vs.raster_dimension) || 0,
        fraction: 1,
      })
    }
    markSiteViewshedReady(vs.slug)
    if (vs.slug === DRAFT_VIEWSHED_SLUG && onDraftViewshedReady) {
      onDraftViewshedReady()
    }
  }

  function handleViewshedReady(vs, epoch) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) {
      if (vs?.slug) clearViewshedLoadingState(vs.slug)
      return
    }
    const pendingEpoch = viewshedPendingEpoch.get(vs.slug)
    if (epoch != null && pendingEpoch != null && pendingEpoch !== epoch) return
    addViewshedLayer(vs)
    finalizeViewshedReady(vs)
    if (!skipOutboundLinksForViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug)
    } else {
      markSiteOutboundLinksReady(vs.slug)
    }
  }

  function acceptViewshedOverlay(vs) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) return
    if (!isViewshedHostVisible(vs.slug) || !isViewshedVisible(vs.slug)) return
    addViewshedLayer(vs)
    finalizeViewshedReady(vs)
    if (!skipOutboundLinksForViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug)
    } else {
      markSiteOutboundLinksReady(vs.slug)
    }
  }

  function coordsMatchDraftEvent(coords, data) {
    return (
      Math.abs(coords.lat - data.lat) <= 1e-5 && Math.abs(coords.lon - data.lon) <= 1e-5
    )
  }

  /** Server coord warms publish as `_draft`; remap SSE to the client slug that owns the pending epoch. */
  function routeDraftViewshedToPendingSlugs(data) {
    if (data.slug !== DRAFT_VIEWSHED_SLUG || data.status !== 'ready') return false
    if (data.lat == null || data.lon == null) return false
    let routed = false
    const linkSolverHopCoords = getLinkSolverHopViewshedCoords?.()
    if (linkSolverHopCoords) {
      for (const [slug, coords] of linkSolverHopCoords) {
        if (!viewshedPendingEpoch.has(slug)) continue
        if (!coordsMatchDraftEvent(coords, data)) continue
        const epoch = viewshedPendingEpoch.get(slug)
        handleViewshedReady({ ...data, slug }, epoch)
        routed = true
      }
    }
    const pendingCoords = store?.viewshed?.pendingCoords
    if (pendingCoords) {
      for (const [slug, coords] of pendingCoords) {
        if (!viewshedPendingEpoch.has(slug)) continue
        if (!coordsMatchDraftEvent(coords, data)) continue
        const epoch = viewshedPendingEpoch.get(slug)
        handleViewshedReady({ ...data, slug }, epoch)
        routed = true
      }
    }
    return routed
  }

  let pinOverlayFrame = 0

  function schedulePinOverlays() {
    if (pinOverlayFrame) return
    pinOverlayFrame = requestAnimationFrame(() => {
      pinOverlayFrame = 0
      updatePinOverlays()
    })
  }

  function handleViewshedEvent(data) {
    if (!data || !data.slug) return
    if (data.status === 'running') {
      const partial = {
        fraction: Number(data.fraction) || 0,
        phase: data.phase || '',
      }
      const apply = (slug) => {
        if (!viewshedLoading.has(slug) && !viewshedPendingEpoch.has(slug)) return
        setSitePinProgress(slug, partial)
      }
      if (data.slug === DRAFT_VIEWSHED_SLUG && data.lat != null && data.lon != null) {
        const linkSolverHopCoords = getLinkSolverHopViewshedCoords?.()
        if (linkSolverHopCoords) {
          for (const [slug, coords] of linkSolverHopCoords) {
            if (!viewshedPendingEpoch.has(slug)) continue
            if (!coordsMatchDraftEvent(coords, data)) continue
            apply(slug)
          }
        }
        const pendingCoords = store?.viewshed?.pendingCoords
        if (pendingCoords) {
          for (const [slug, coords] of pendingCoords) {
            if (!viewshedPendingEpoch.has(slug)) continue
            if (!coordsMatchDraftEvent(coords, data)) continue
            apply(slug)
          }
        }
        const place = getDraftPlacement?.()
        if (place && coordsMatchDraftEvent(place, data)) apply(DRAFT_VIEWSHED_SLUG)
      } else {
        apply(data.slug)
      }
      schedulePinOverlays()
      return
    }
    if (data.status === 'ready') {
      if (data.slug === DRAFT_VIEWSHED_SLUG) {
        routeDraftViewshedToPendingSlugs(data)
        const place = getDraftPlacement?.()
        const forDraft =
          place && data.lat != null && data.lon != null && coordsMatchDraftEvent(place, data)
        if (!forDraft) return
      }
      if (data.raster_dimension != null) {
        setSitePinProgress(data.slug, {
          raster: Number(data.raster_dimension) || 0,
          fraction: 1,
        })
        updatePinOverlays()
      }
      const epoch = viewshedPendingEpoch.get(data.slug)
      if (epoch != null) {
        handleViewshedReady(data, epoch)
      }
      if (!siteViewshedReady.has(data.slug)) {
        acceptViewshedOverlay(data)
      }
      return
    }
    if (data.status === 'error') {
      clearViewshedLoadingState(data.slug)
    }
  }

  function updatePinOverlays() {
    if (!getMapReady()) return
    try {
      const active = new Set()
      const progressFor = (slug) => ({
        progressLabel: sitePinProgressLabel(slug),
        progressFraction: sitePinProgressFraction(slug),
      })
      for (const site of getSites()) {
        if (!sitePinSpinning(site.slug)) continue
        if (!coordsUsableForMarker(site.lon, site.lat)) continue
        active.add(site.slug)
        renderPinLoadOverlay(site.slug, site.lon, site.lat, progressFor(site.slug))
      }
      const osm = store?.access?.osm
      const osmBusy = osm?.status === 'parsing'
      const accessBusy = !!store?.access?.draftLoading || osmBusy
      if ((viewshedLoadingHasDraft?.() || accessBusy) && getDraftPlacement) {
        const draft = getDraftPlacement()
        if (draft) {
          active.add(DRAFT_VIEWSHED_SLUG)
          const vsBusy =
            viewshedLoadingHasDraft?.() && !siteViewshedReady.has(DRAFT_VIEWSHED_SLUG)
          const progress = vsBusy
            ? progressFor(DRAFT_VIEWSHED_SLUG)
            : osmBusy
              ? {
                  progressLabel: osmRoadsProgressLabel(osm) || 'roads…',
                  progressFraction: Number(osm.fraction) > 0 ? Number(osm.fraction) : 0.12,
                }
              : { progressLabel: 'access…', progressFraction: 0.9 }
          renderPinLoadOverlay(DRAFT_VIEWSHED_SLUG, draft.lon, draft.lat, progress)
        }
      }
      if (getLinkSolverHopViewshedSlugs && getLinkSolverHopViewshedCoords) {
        for (const slug of getLinkSolverHopViewshedSlugs()) {
          if (!viewshedLoading.has(slug)) continue
          const coords = getLinkSolverHopViewshedCoords().get(slug)
          if (!coords || !coordsUsableForMarker(coords.lon, coords.lat)) continue
          active.add(slug)
          renderPinLoadOverlay(slug, coords.lon, coords.lat, progressFor(slug))
        }
      }
      const fortifyCoords = store?.viewshed?.pendingCoords?.get(FORTIFY_VIEWSHED_SLUG)
      if (viewshedLoading.has(FORTIFY_VIEWSHED_SLUG) && fortifyCoords) {
        if (coordsUsableForMarker(fortifyCoords.lon, fortifyCoords.lat)) {
          active.add(FORTIFY_VIEWSHED_SLUG)
          renderPinLoadOverlay(
            FORTIFY_VIEWSHED_SLUG,
            fortifyCoords.lon,
            fortifyCoords.lat,
            progressFor(FORTIFY_VIEWSHED_SLUG),
          )
        }
      }
      hideInactivePinMarkers(active)
    } catch (_) {
      /* MapLibre can throw during resize; never block site save/UI */
    }
  }

  function waitForMapIdle(maxMs = 120) {
    return new Promise((resolve) => {
      if (!getMapReady()) {
        window.setTimeout(resolve, 0)
        return
      }
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        resolve()
      }
      map.once('idle', finish)
      window.setTimeout(finish, maxMs)
    })
  }

  async function applyViewshedOverlaysBatched(overlays) {
    for (let i = 0; i < overlays.length; i += VIEWSHED_OVERLAY_BATCH) {
      const batch = overlays.slice(i, i + VIEWSHED_OVERLAY_BATCH)
      for (const overlay of batch) {
        acceptViewshedOverlay(overlay)
      }
      if (i + VIEWSHED_OVERLAY_BATCH < overlays.length) {
        await waitForMapIdle()
      }
    }
  }

  async function tryLoadViewshedFromCache(slug, coords) {
    try {
      const lat = Number(coords?.lat)
      const lon = Number(coords?.lon)
      const resp = await fetch(
        apiUrls.viewshedMetaUrl(
          projectSlug,
          slug,
          viewshedSimQueryParams(),
          Number.isFinite(lat) ? lat : undefined,
          Number.isFinite(lon) ? lon : undefined,
        ),
      )
      if (!resp.ok) return false
      const overlay = await resp.json()
      if (overlay.url && overlay.coordinates) {
        acceptViewshedOverlay({ ...overlay, slug, status: 'ready' })
        return true
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false
  }

  async function tryLoadCoordViewshedFromCache(_slug, lat, lon) {
    try {
      const resp = await fetch(viewshedPrefetchMetaUrl(lat, lon))
      if (!resp.ok) return null
      const overlay = await resp.json()
      if (overlay.url && overlay.coordinates) return overlay
    } catch (_) {
      /* cache probe optional */
    }
    return null
  }

  async function fetchOutboundLinksParallel(slugs) {
    if (!slugs.length) return
    let cursor = 0
    async function worker() {
      while (cursor < slugs.length) {
        const slug = slugs[cursor]
        cursor += 1
        await loadSingleSiteLinks(slug)
      }
    }
    const workers = Math.min(OUTBOUND_LINKS_PARALLEL, slugs.length)
    await Promise.all(Array.from({ length: workers }, () => worker()))
  }

  function probeViewshedCacheForSite(site) {
    removeViewshedLayer(site.slug)
    resetSiteProgress(site.slug)
    viewshedLoading.add(site.slug)
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch)
    void tryLoadViewshedFromCache(site.slug)
  }

  function ensureViewshedsForNewlyVisibleSites(opts = {}) {
    const sitesOnly = opts.sitesOnly === true
    let queued = false
    for (const site of getSites()) {
      if (!viewshedLayerShouldShow(site.slug)) continue
      if (map.getLayer(viewshedLayerId(site.slug))) continue
      if (sitePinSpinning(site.slug) || viewshedPendingEpoch.has(site.slug)) continue
      probeViewshedCacheForSite(site)
      queued = true
    }
    if (!sitesOnly) {
      for (const peak of store?.peaks?.list || []) {
        if (getSiteBySlug(peak.slug)) continue
        if (!viewshedLayerShouldShow(peak.slug)) continue
        if (map.getLayer(viewshedLayerId(peak.slug))) continue
        if (sitePinSpinning(peak.slug) || viewshedPendingEpoch.has(peak.slug)) continue
        const lat = Number(peak.lat)
        const lon = Number(peak.lon)
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue
        scheduleCoordViewshedLoad(peak.slug, lat, lon)
        queued = true
      }
    }
    if (queued) {
      updatePinOverlays()
      syncWarmPriorities()
    }
  }

  function scheduleCoordViewshedLoad(slug, lat, lon) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    removeViewshedLayer(slug)
    resetSiteProgress(slug)
    viewshedLoading.add(slug)
    const epoch = bumpViewshedLoadEpoch()
    viewshedPendingEpoch.set(slug, epoch)
    if (store) setPendingCoordsInStore(store, slug, lat, lon)
    updatePinOverlays()
    if (slug === store.ui.selectedSlug) syncViewshedCheckbox()
    void (async () => {
      const cached = await tryLoadCoordViewshedFromCache(slug, lat, lon)
      if (viewshedPendingEpoch.get(slug) !== epoch) return
      if (cached) {
        acceptViewshedOverlay({ ...cached, slug, status: 'ready' })
        raiseViewshedLayers()
        return
      }
      try {
        const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), {
          method: 'POST',
          signal: currentPreview().signal,
        })
        if (viewshedPendingEpoch.get(slug) !== epoch) return
        if (!resp.ok) {
          clearViewshedLoadingState(slug)
          updatePinOverlays()
          return
        }
        const ready = await resp.json()
        if (viewshedPendingEpoch.get(slug) !== epoch) return
        if (ready && ready.status === 'ready') {
          handleViewshedReady({ ...ready, slug }, epoch)
          raiseViewshedLayers()
        }
      } catch (_) {
        if (viewshedPendingEpoch.get(slug) === epoch) {
          clearViewshedLoadingState(slug)
          updatePinOverlays()
        }
      }
    })()
  }

  function ensureViewshedLoadedForSlug(slug, loadDraftAt) {
    if (!viewshedLayerShouldShow(slug)) return
    if (map.getLayer(viewshedLayerId(slug))) return
    if (sitePinSpinning(slug) || viewshedPendingEpoch.has(slug)) return
    if (slug === DRAFT_VIEWSHED_SLUG && loadDraftAt) {
      const draft = getDraftPlacement?.()
      if (draft) void loadDraftAt(draft.lat, draft.lon)
      return
    }
    const site = getSites().find((s) => s.slug === slug)
    if (site) {
      scheduleViewshedLoad(site)
      return
    }
    const peak = (store?.peaks?.list || []).find((row) => row.slug === slug)
    if (peak) {
      scheduleCoordViewshedLoad(slug, Number(peak.lat), Number(peak.lon))
    }
  }

  function scheduleViewshedLoad(site) {
    resetSiteProgress(site.slug)
    viewshedLoading.add(site.slug)
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch)
    const lat = Number(site.lat)
    const lon = Number(site.lon)
    if (store && Number.isFinite(lat) && Number.isFinite(lon)) {
      setPendingCoordsInStore(store, site.slug, lat, lon)
    }
    updatePinOverlays()
    if (site.slug === store.ui.selectedSlug) syncViewshedCheckbox()
    const priority =
      site.slug === store.ui.selectedSlug ? warmPriorityInteractive : warmPriorityViewport
    void bumpWarmPriorities([site.slug], priority)
    void tryLoadViewshedFromCache(site.slug)
  }

  async function loadViewshedIndex() {
    try {
      const resp = await fetch(apiUrls.viewshedIndexUrl(projectSlug, viewshedSimQueryParams()))
      if (!resp.ok) {
        ensureViewshedsForNewlyVisibleSites()
        return
      }
      const index = await resp.json()
      const siteEntries = index.sites || {}
      const readySlugs = []
      const readyOverlays = []
      const missing = []
      for (const site of getSites()) {
        if (!isViewshedHostVisible(site.slug) || !isViewshedVisible(site.slug)) continue
        const entry = siteEntries[site.slug]
        if (entry && entry.ready && entry.url && entry.coordinates) {
          readyOverlays.push({
            slug: site.slug,
            url: entry.url,
            coordinates: entry.coordinates,
            ...entry,
          })
          readySlugs.push(site.slug)
        } else {
          missing.push(site)
        }
      }
      await applyViewshedOverlaysBatched(readyOverlays)
      void fetchOutboundLinksParallel(readySlugs)
      for (const site of missing) {
        scheduleViewshedLoad(site)
      }
      if (missing.length) syncWarmPriorities()
    } catch (_) {
      ensureViewshedsForNewlyVisibleSites()
    }
  }

  function reloadViewshedsForSimChange(onAfterReload) {
    bumpViewshedLoadEpoch()
    for (const site of getSites()) {
      resetSiteProgress(site.slug)
      if (isViewshedHostVisible(site.slug) && isViewshedVisible(site.slug)) {
        removeViewshedLayer(site.slug)
        viewshedLoading.add(site.slug)
        viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch)
      } else {
        removeViewshedLayer(site.slug)
        viewshedLoading.delete(site.slug)
        viewshedPendingEpoch.delete(site.slug)
      }
    }
    for (const peak of store?.peaks?.list || []) {
      if (getSiteBySlug(peak.slug)) continue
      resetSiteProgress(peak.slug)
      if (viewshedLayerShouldShow(peak.slug)) {
        removeViewshedLayer(peak.slug)
        viewshedLoading.add(peak.slug)
        viewshedPendingEpoch.set(peak.slug, viewshedLoadEpoch)
      } else {
        removeViewshedLayer(peak.slug)
        viewshedLoading.delete(peak.slug)
        viewshedPendingEpoch.delete(peak.slug)
      }
    }
    updatePinOverlays()
    void loadViewshedIndex()
    if (onAfterReload) onAfterReload()
  }

  function reconcilePendingViewsheds() {
    syncWarmPriorities()
  }

  return {
    viewshedVisible,
    viewshedLoading,
    siteViewshedReady,
    siteOutboundLinksReady,
    sitePinProgress,
    viewshedPendingEpoch,
    getViewshedLoadEpoch: () => viewshedLoadEpoch,
    setSitePinProgress,
    clearSitePinProgress,
    sitePinProgressLabel,
    sitePinProgressFraction,
    isViewshedVisible,
    resetSiteProgress,
    markSiteViewshedReady,
    markSiteOutboundLinksReady,
    sitePinSpinning,
    isSiteOutboundLinksReady,
    viewshedSimQueryParams,
    viewshedPrefetchWarmUrl,
    viewshedPrefetchMetaUrl,
    viewshedOverlaySlugs,
    viewshedLayerInsertBefore,
    raiseViewshedLayers,
    applyViewshedOpacityToAllLayers,
    applyViewshedVisibilityForSite,
    applyViewshedVisibilityForMountedLayers,
    setViewshedVisible,
    removeViewshedLayer,
    addViewshedLayer,
    clearViewshedLoadingState,
    bumpViewshedLoadEpoch,
    handleViewshedEvent,
    handleViewshedReady,
    acceptViewshedOverlay,
    updatePinOverlays,
    tryLoadViewshedFromCache,
    tryLoadCoordViewshedFromCache,
    probeViewshedCacheForSite,
    ensureViewshedsForNewlyVisibleSites,
    ensureViewshedLoadedForSlug,
    scheduleViewshedLoad,
    scheduleCoordViewshedLoad,
    loadViewshedIndex,
    reloadViewshedsForSimChange,
    reconcilePendingViewsheds,
    fetchOutboundLinksParallel,
  }
}
