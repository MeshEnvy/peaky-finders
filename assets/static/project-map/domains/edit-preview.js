// @ts-check

import { COORD_PREFETCH_MS, DRAFT_VIEWSHED_SLUG, viewshedLayerId } from '../constants.js'
import * as apiUrls from '../api/urls.js'
import {
  applyDraftLinksLayer,
  removeDraftLinksLayer as clearDraftLinksOnMap,
} from '../map/links-layers.js'

/**
 * Draft viewshed + link prefetch for create/edit, plus the edit snapshot
 * used to hide the original site pin/links while the draft moves.
 * @param {object} ctx
 */
export function createEditPreviewDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getViewshed,
    viewshedVisible,
    viewshedLoading,
    viewshedPendingEpoch,
    getMapReady,
    raiseSiteLayers,
    applySiteLayerFilters,
    refreshFilteredLinks,
    updatePinOverlays,
    applyViewshedVisibilityForSite,
    ensureViewshedLoadedForSlug,
    isViewshedVisible,
    placeDraftMarker,
    seekSiteSlugNear,
  } = ctx

  let draftPlacementLat = null
  let draftPlacementLon = null
  let draftViewshedLoading = false
  let placementPrefetchGen = 0
  let editSnapshot = null
  let editPrefetchTimer = null
  let editPrefetchGen = 0
  let editHiddenViewshedSlug = null

  function vs() {
    return getViewshed?.()
  }

  function removeDraftLinksLayer() {
    clearDraftLinksOnMap(getMap())
  }

  function addDraftLinksLayer(geojson) {
    if (!getMapReady?.()) {
      removeDraftLinksLayer()
      return
    }
    applyDraftLinksLayer(getMap(), geojson, () => raiseSiteLayers?.())
  }

  function readEditCoords() {
    const lat = Number(store?.ui?.editLat)
    const lon = Number(store?.ui?.editLon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
    return { lat, lon }
  }

  function getDraftPlacement() {
    if (draftPlacementLat == null || draftPlacementLon == null) return null
    return { lat: draftPlacementLat, lon: draftPlacementLon }
  }

  function getEditSnapshot() {
    return editSnapshot
  }

  function coordsMatchEditSnapshot(lat, lon) {
    if (!editSnapshot) return false
    return (
      Math.abs(lat - Number(editSnapshot.lat)) < 1e-5 &&
      Math.abs(lon - Number(editSnapshot.lon)) < 1e-5
    )
  }

  function editCoordsMovedFromSnapshot() {
    const coords = readEditCoords()
    if (!coords || !editSnapshot) return false
    return !coordsMatchEditSnapshot(coords.lat, coords.lon)
  }

  function linkFeatureTouchesSnapshotCoords(feature) {
    if (!store.ui.editMode || !editSnapshot || !editCoordsMovedFromSnapshot()) {
      return false
    }
    const geom = feature.geometry
    if (!geom || geom.type !== 'LineString' || !Array.isArray(geom.coordinates)) {
      return false
    }
    const snapLon = Number(editSnapshot.lon)
    const snapLat = Number(editSnapshot.lat)
    for (const pt of geom.coordinates) {
      if (!Array.isArray(pt) || pt.length < 2) continue
      const lon = Number(pt[0])
      const lat = Number(pt[1])
      if (Math.abs(lon - snapLon) < 1e-5 && Math.abs(lat - snapLat) < 1e-5) return true
    }
    return false
  }

  function filterEditSitePrefetchPayload(payload) {
    if (!payload || !store.ui.editSlug) return payload
    const links = Array.isArray(payload.links)
      ? payload.links.filter((row) => row.slug !== store.ui.editSlug)
      : payload.links
    let linksGeojson = payload.links_geojson
    if (linksGeojson && Array.isArray(linksGeojson.features)) {
      linksGeojson = {
        ...linksGeojson,
        features: linksGeojson.features.filter(
          (feature) => (feature.properties || {}).slug !== store.ui.editSlug
        ),
      }
    }
    return { ...payload, links, links_geojson: linksGeojson }
  }

  function removeDraftViewshed() {
    try {
      vs()?.removeViewshedLayer?.(DRAFT_VIEWSHED_SLUG)
    } catch (_) {
      /* map may be mid-resize */
    }
    viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG)
    viewshedLoading.delete(DRAFT_VIEWSHED_SLUG)
    draftViewshedLoading = false
    draftPlacementLat = null
    draftPlacementLon = null
    updatePinOverlays?.()
  }

  function clearDraftViewshedLoading() {
    viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG)
    viewshedLoading.delete(DRAFT_VIEWSHED_SLUG)
    draftViewshedLoading = false
    updatePinOverlays?.()
  }

  function onDraftViewshedReady() {
    draftViewshedLoading = false
    if (draftPlacementLat != null && draftPlacementLon != null) {
      void loadPlacementPrefetchAt(draftPlacementLat, draftPlacementLon)
    }
  }

  async function tryLoadDraftViewshedFromCache(lat, lon) {
    try {
      const url = vs()?.viewshedPrefetchMetaUrl?.(lat, lon)
      if (!url) return false
      const resp = await fetch(url)
      if (!resp.ok) return false
      const overlay = await resp.json()
      if (overlay.url && overlay.coordinates) {
        vs()?.acceptViewshedOverlay?.({ ...overlay, slug: DRAFT_VIEWSHED_SLUG, status: 'ready' })
        return true
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false
  }

  async function loadDraftViewshedAt(lat, lon, options) {
    const refreshOnly = Boolean(options && options.refreshOnly)
    if (refreshOnly) {
      vs()?.removeViewshedLayer?.(DRAFT_VIEWSHED_SLUG)
    } else {
      removeDraftViewshed()
      draftPlacementLat = lat
      draftPlacementLon = lon
    }
    draftViewshedLoading = true
    viewshedLoading.add(DRAFT_VIEWSHED_SLUG)
    const epoch = vs()?.getViewshedLoadEpoch?.()
    viewshedPendingEpoch.set(DRAFT_VIEWSHED_SLUG, epoch)
    updatePinOverlays?.()
    if (await tryLoadDraftViewshedFromCache(lat, lon)) {
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) === epoch) {
        viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG)
        draftViewshedLoading = false
      }
      return
    }
    try {
      const url = vs()?.viewshedPrefetchWarmUrl?.(lat, lon)
      if (!url) {
        clearDraftViewshedLoading()
        return
      }
      const resp = await fetch(url, { method: 'POST' })
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return
      if (!resp.ok) {
        clearDraftViewshedLoading()
        return
      }
      const overlay = await resp.json()
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return
      if (overlay && overlay.status === 'ready') {
        vs()?.handleViewshedReady?.({ ...overlay, slug: DRAFT_VIEWSHED_SLUG }, epoch)
      }
    } catch (_) {
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) === epoch) {
        clearDraftViewshedLoading()
      }
    } finally {
      if (!viewshedPendingEpoch.has(DRAFT_VIEWSHED_SLUG)) {
        draftViewshedLoading = false
        updatePinOverlays?.()
      }
    }
  }

  async function loadPlacementPrefetchAt(lat, lon) {
    const gen = ++placementPrefetchGen
    removeDraftLinksLayer?.()
    try {
      const resp = await fetch(apiUrls.sitesPrefetchUrl(projectSlug, lat, lon))
      if (gen !== placementPrefetchGen) return
      if (!resp.ok) return
      const payload = await resp.json()
      if (gen !== placementPrefetchGen) return
      if (payload?.links_geojson) addDraftLinksLayer?.(payload.links_geojson)
      else removeDraftLinksLayer?.()
    } catch (_) {
      /* placement prefetch optional */
    }
  }

  function reloadDraftIfNeeded() {
    if (
      store.ui.createMode &&
      draftPlacementLat != null &&
      draftPlacementLon != null &&
      isViewshedVisible?.(DRAFT_VIEWSHED_SLUG)
    ) {
      void loadDraftViewshedAt(draftPlacementLat, draftPlacementLon, { refreshOnly: true })
    }
  }

  function restoreEditHiddenViewshed() {
    if (editHiddenViewshedSlug) {
      applyViewshedVisibilityForSite?.(editHiddenViewshedSlug)
      editHiddenViewshedSlug = null
    }
  }

  function hideViewshedLayerForEdit(slug) {
    const map = getMap()
    if (!map || !slug) return
    const layerId = viewshedLayerId(slug)
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, 'visibility', 'none')
      editHiddenViewshedSlug = slug
    }
  }

  function loadEditDraftViewshedAt(lat, lon) {
    if (!store.ui.editMode) return
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true)
    hideViewshedLayerForEdit(store.ui.editSlug)
    void loadDraftViewshedAt(lat, lon)
  }

  async function runEditPrefetchAt(lat, lon) {
    const gen = ++editPrefetchGen
    const atOriginal = coordsMatchEditSnapshot(lat, lon)
    removeDraftLinksLayer?.()
    placeDraftMarker?.(lat, lon)
    applySiteLayerFilters?.()
    refreshFilteredLinks?.()
    if (store.ui.editMode) {
      if (atOriginal) {
        removeDraftViewshed()
        restoreEditHiddenViewshed()
      } else {
        loadEditDraftViewshedAt(lat, lon)
      }
    } else {
      removeDraftViewshed()
      removeDraftLinksLayer?.()
    }
    try {
      const url = apiUrls.sitesPrefetchUrl(projectSlug, lat, lon, store.ui.editSlug)
      const resp = await fetch(url)
      if (gen !== editPrefetchGen) return
      if (!resp.ok) return
      let payload = await resp.json()
      if (gen !== editPrefetchGen) return
      payload = filterEditSitePrefetchPayload(payload)
      if (payload.links_geojson) addDraftLinksLayer?.(payload.links_geojson)
      else removeDraftLinksLayer?.()
    } catch (_) {
      /* edit prefetch optional */
    }
  }

  function scheduleEditPrefetch() {
    if (!store.ui.editMode) return
    if (editPrefetchTimer) clearTimeout(editPrefetchTimer)
    editPrefetchTimer = setTimeout(() => {
      editPrefetchTimer = null
      const coords = readEditCoords()
      if (!coords) return
      void runEditPrefetchAt(coords.lat, coords.lon)
    }, COORD_PREFETCH_MS)
  }

  function onEditCoordsChanged() {
    if (!store.ui.editMode) return
    const coords = readEditCoords()
    if (!coords) return
    const atOriginal = coordsMatchEditSnapshot(coords.lat, coords.lon)
    placeDraftMarker?.(coords.lat, coords.lon)
    if (!atOriginal) removeDraftLinksLayer?.()
    refreshFilteredLinks?.()
    scheduleEditPrefetch()
  }

  function cancelPrefetch() {
    editPrefetchGen += 1
    if (editPrefetchTimer) {
      clearTimeout(editPrefetchTimer)
      editPrefetchTimer = null
    }
  }

  function startEditSession(entity) {
    editSnapshot = { ...entity }
    cancelPrefetch()
    applySiteLayerFilters?.()
    refreshFilteredLinks?.()
    void runEditPrefetchAt(entity.lat, entity.lon)
  }

  function endEditSession() {
    editSnapshot = null
    cancelPrefetch()
    restoreEditHiddenViewshed()
    removeDraftLinksLayer?.()
  }

  function cleanupEditSave() {
    editHiddenViewshedSlug = null
    cancelPrefetch()
    editSnapshot = null
  }

  async function warmDraftViewshedForSeek(lat, lon, signal) {
    const siteSlug = seekSiteSlugNear?.(lat, lon) ?? null
    if (siteSlug) {
      viewshedVisible.set(DRAFT_VIEWSHED_SLUG, false)
      vs()?.removeViewshedLayer?.(DRAFT_VIEWSHED_SLUG)
      viewshedVisible.set(siteSlug, true)
      ensureViewshedLoadedForSlug?.(siteSlug)
      const map = getMap()
      return Boolean(map?.getLayer(viewshedLayerId(siteSlug)))
    }
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true)
    if (await tryLoadDraftViewshedFromCache(lat, lon)) return true
    try {
      const url = vs()?.viewshedPrefetchWarmUrl?.(lat, lon)
      if (!url) return false
      const resp = await fetch(url, { method: 'POST', signal })
      if (!resp.ok) return false
      const overlay = await resp.json().catch(() => null)
      if (overlay?.status === 'ready' && overlay.url && overlay.coordinates) {
        vs()?.handleViewshedReady?.(
          { ...overlay, slug: DRAFT_VIEWSHED_SLUG },
          vs()?.getViewshedLoadEpoch?.()
        )
        return true
      }
    } catch (err) {
      if (err?.name === 'AbortError') throw err
    }
    return false
  }

  return {
    getDraftPlacement,
    getEditSnapshot,
    coordsMatchEditSnapshot,
    editCoordsMovedFromSnapshot,
    linkFeatureTouchesSnapshotCoords,
    removeDraftViewshed,
    removeDraftLinksLayer,
    addDraftLinksLayer,
    loadDraftViewshedAt,
    loadPlacementPrefetchAt,
    onDraftViewshedReady,
    clearDraftViewshedLoading,
    reloadDraftIfNeeded,
    startEditSession,
    endEditSession,
    cleanupEditSave,
    onEditCoordsChanged,
    warmDraftViewshedForSeek,
    isDraftViewshedLoading: () => draftViewshedLoading,
  }
}
