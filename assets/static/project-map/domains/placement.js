// @ts-check

import { formatCoord } from '../geo.js'
import { DRAFT_MARKER_COLOR, DRAFT_VIEWSHED_SLUG } from '../constants.js'
import { normalizeSiteFromApi } from '../stores/sites.js'
import { captureDeepLinkFromMap, writeDeepLink } from '../api/deep-link.js'

/**
 * Create/relocation draft marker, site sheet modes, select/deselect, save.
 * MapLibre marker + store.ui; Vue sheet watches the store.
 * @param {object} ctx
 */
export function createPlacementDomain(ctx) {
  const {
    store,
    sitesDomain,
    getMap,
    getMapReady,
    getSiteBySlug,
    registerSite,
    applySiteRowUpdate,
    applySiteTagChange,
    unregisterSite,
    applySiteLayerFilters,
    refreshFilteredLinks,
    updateSelectedLayer,
    raiseSiteLayers,
    syncMapViewport,
    syncMapCursor,
    syncWarmPriorities,
    loadSingleSiteLinks,
    loadSiteLinks,
    ensureSiteAccess,
    ensureDraftAccess,
    clearDraftAccess,
    ingestAccess,
    refreshSiteAccessLayers,
    scheduleViewshedLoad,
    viewshedVisible,
    removeDraftViewshed,
    removeDraftLinksLayer,
    loadDraftViewshedAt,
    onStartRelocate,
    onCancelRelocate,
    onCleanupRelocate,
    onRelocateCoordsChanged,
    deselectPeak,
    clearLinkSelection,
  } = ctx

  /** @type {maplibregl.Marker|null} */
  let draftMarker = null

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

  function mapShell() {
    return document.querySelector('.map-shell')
  }

  function sitePanelEl() {
    return document.getElementById('site-panel')
  }

  function addSiteBtn() {
    return document.getElementById('entity-panel-add-site')
  }

  function isCreateMode() {
    return !!store.ui.createMode
  }

  function isRelocateMode() {
    return !!store.ui.relocateMode
  }

  function getRelocateSlug() {
    return store.ui.relocateSlug
  }

  function getAddPlacementMode() {
    return store.ui.addPlacementMode
  }

  function setCreateDraftCoords(lat, lon) {
    store.ui.createLat = lat
    store.ui.createLon = lon
  }

  function setRelocateDraftCoords(lat, lon) {
    store.ui.relocateLat = lat
    store.ui.relocateLon = lon
  }

  function selectedSlug() {
    return store.ui.selectedSlug
  }

  function assignSelectedSlug(slug) {
    store.ui.selectedSlug = slug
  }

  function syncRelocateMapShell() {
    const shell = mapShell()
    if (shell) shell.classList.toggle('relocate-mode', isRelocateMode())
    syncMapCursor?.()
  }

  function showPanelView() {
    store.ui.createMode = false
    syncRelocateMapShell()
  }

  function showPanelCreate() {
    store.ui.createMode = true
    assignSelectedSlug(null)
    updateSelectedLayer?.()
    syncRelocateMapShell()
  }

  function removeDraftMarker() {
    if (draftMarker) {
      draftMarker.remove()
      draftMarker = null
    }
  }

  function placeDraftMarker(lat, lon) {
    const map = getMap()
    if (!map) return
    removeDraftMarker()
    draftMarker = new maplibregl.Marker({ color: DRAFT_MARKER_COLOR })
      .setLngLat([lon, lat])
      .addTo(map)
  }

  function clearAddPlacementMode() {
    store.ui.addPlacementMode = null
    addSiteBtn()?.classList.remove('active')
    const shell = mapShell()
    if (shell) shell.classList.remove('add-placement-mode', 'add-site-mode')
    syncMapCursor?.()
  }

  function setAddPlacementMode(kind) {
    store.ui.addPlacementMode = kind || null
    addSiteBtn()?.classList.toggle('active', kind === 'site')
    const shell = mapShell()
    if (shell) {
      shell.classList.toggle('add-placement-mode', !!kind)
      shell.classList.toggle('add-site-mode', !!kind)
    }
    syncMapCursor?.()
    if (!kind) cancelCreate()
  }

  function openCreatePanel(lat, lon) {
    if (isRelocateMode()) cancelRelocate()
    setCreateDraftCoords(lat, lon)
    removeDraftLinksLayer?.()
    placeDraftMarker(lat, lon)
    const panel = sitePanelEl()
    if (panel) panel.hidden = false
    syncMapViewport?.()
    showPanelCreate()
    deselectPeak?.({ syncDeepLink: false })
    clearLinkSelection?.()
    viewshedVisible?.set?.(DRAFT_VIEWSHED_SLUG, true)
    void loadDraftViewshedAt?.(lat, lon)
    ensureDraftAccess?.(lat, lon)
  }

  function cancelCreate() {
    if (!isCreateMode() && !draftMarker) return
    store.ui.createMode = false
    setCreateDraftCoords(null, null)
    removeDraftMarker()
    removeDraftViewshed?.()
    removeDraftLinksLayer?.()
    clearDraftAccess?.()
    const panel = sitePanelEl()
    const selected = selectedSlug()
    if (selected) {
      if (panel) panel.hidden = false
      syncMapViewport?.()
      showPanelView()
    } else {
      if (panel) panel.hidden = true
      syncMapViewport?.()
    }
  }

  function beginCreateAtMapPoint(lat, lon) {
    if (isRelocateMode()) return
    if (isCreateMode()) cancelCreate()
    setAddPlacementMode(null)
    openCreatePanel(lat, lon)
  }

  function toggleRelocate() {
    const slug = selectedSlug()
    const entity = getSiteBySlug?.(slug)
    if (!entity || !slug) return
    if (isRelocateMode() && store.ui.relocateSlug === slug) {
      cancelRelocate()
      return
    }
    if (isRelocateMode()) cancelRelocate()
    store.ui.relocateSlug = slug
    store.ui.relocateMode = true
    setRelocateDraftCoords(Number(entity.lat), Number(entity.lon))
    syncRelocateMapShell()
    onStartRelocate?.(entity)
  }

  function cancelRelocate() {
    if (!isRelocateMode()) return
    store.ui.relocateMode = false
    store.ui.relocateSlug = null
    setRelocateDraftCoords(null, null)
    removeDraftMarker()
    removeDraftViewshed?.()
    removeDraftLinksLayer?.()
    onCancelRelocate?.()
    applySiteLayerFilters?.()
    refreshFilteredLinks?.()
    syncRelocateMapShell()
  }

  /**
   * @param {string} slug
   * @param {Record<string, unknown>} body
   */
  async function patchSiteField(slug, body) {
    if (!slug) throw new Error('No site selected.')
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    store.ui.sheetError = ''
    const result = await sitesDomain.updateSite(slug, body)
    const { site, promoted } = result
    if (promoted && site) {
      registerSite(site)
    } else if (site) {
      applySiteRowUpdate(site)
    }
    if (site && Object.hasOwn(body, 'tags')) {
      applySiteTagChange?.(site.slug)
    }
    if (site && (body.lat != null || body.lon != null)) {
      viewshedVisible?.set?.(site.slug, true)
      scheduleViewshedLoad?.(site)
      void loadSiteLinks?.()
    }
    return site
  }

  /**
   * @param {number} lat
   * @param {number} lon
   */
  async function commitRelocateAt(lat, lon) {
    const slug = store.ui.relocateSlug
    if (!slug || !isRelocateMode()) return null
    try {
      const site = await patchSiteField(slug, { lat, lon })
      removeDraftMarker()
      removeDraftViewshed?.()
      removeDraftLinksLayer?.()
      store.ui.relocateMode = false
      store.ui.relocateSlug = null
      setRelocateDraftCoords(null, null)
      onCleanupRelocate?.()
      applySiteLayerFilters?.()
      refreshFilteredLinks?.()
      syncRelocateMapShell()
      updateSelectedLayer?.()
      raiseSiteLayers?.()
      return site
    } catch (err) {
      store.ui.sheetError = err instanceof Error ? err.message : 'Could not move site.'
      const entity = getSiteBySlug?.(slug)
      if (entity) {
        setRelocateDraftCoords(Number(entity.lat), Number(entity.lon))
        onRelocateCoordsChanged?.()
      }
      throw err
    }
  }

  function selectSite(slug, { syncDeepLink = true } = {}) {
    const site = getSiteBySlug?.(slug)
    if (!site) return
    if (isCreateMode()) cancelCreate()
    if (isRelocateMode()) cancelRelocate()
    clearLinkSelection?.()
    deselectPeak?.({ syncDeepLink: false })
    assignSelectedSlug(slug)
    const panel = sitePanelEl()
    if (panel) panel.hidden = false
    syncMapViewport?.()
    showPanelView()
    updateSelectedLayer?.()
    raiseSiteLayers?.()
    syncWarmPriorities?.()
    void loadSingleSiteLinks?.(slug)
    ensureSiteAccess?.(site)
    if (syncDeepLink) syncDeepLinkToUrl({ replace: false })
  }

  function deselectSite({ syncDeepLink = true } = {}) {
    if (isCreateMode()) {
      cancelCreate()
      return
    }
    if (isRelocateMode()) {
      cancelRelocate()
      return
    }
    assignSelectedSlug(null)
    const panel = sitePanelEl()
    if (panel) panel.hidden = true
    syncMapViewport?.()
    updateSelectedLayer?.()
    refreshSiteAccessLayers?.()
    if (syncDeepLink) syncDeepLinkToUrl({ replace: false })
  }

  function applySavedSiteToMap(site, fallbackLat, fallbackLon) {
    const row = normalizeSiteFromApi({
      ...site,
      lat: site.lat ?? fallbackLat,
      lon: site.lon ?? fallbackLon,
    })
    if (!row) return false
    const draftAccess = store.access?.bySlug?.[DRAFT_VIEWSHED_SLUG]
    if (draftAccess && row.slug) {
      ingestAccess?.(row.slug, draftAccess, row)
      clearDraftAccess?.()
    }
    registerSite(row)
    viewshedVisible?.set?.(row.slug, true)
    scheduleViewshedLoad?.(row)
    void loadSiteLinks?.()
    selectSite(row.slug)
    return true
  }

  function getCreateCoordsLabel() {
    const lat = store.ui.createLat
    const lon = store.ui.createLon
    if (lat == null || lon == null) return ''
    return `${formatCoord(lat)}, ${formatCoord(lon)}`
  }

  async function deleteSelectedSite() {
    const slug = selectedSlug() || getRelocateSlug()
    const entity = store.sites.list.find((site) => site.slug === slug) || getSiteBySlug?.(slug)
    if (!entity || !slug) throw new Error('No site selected.')
    if (store.sites.list.length <= 1) throw new Error('Cannot delete the last site.')
    if (!window.confirm(`Delete site "${entity.name}"?`)) return { cancelled: true }
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    await sitesDomain.deleteSite(slug)
    if (isRelocateMode()) {
      store.ui.relocateMode = false
      store.ui.relocateSlug = null
      setRelocateDraftCoords(null, null)
      removeDraftMarker()
      removeDraftViewshed?.()
      removeDraftLinksLayer?.()
      onCleanupRelocate?.()
      syncRelocateMapShell()
    }
    unregisterSite?.(slug)
    assignSelectedSlug(null)
    const panel = sitePanelEl()
    if (panel) panel.hidden = true
    syncMapViewport?.()
    updateSelectedLayer?.()
    return { ok: true }
  }

  async function saveCreateFromSheet({ name, tags }) {
    if (!isCreateMode()) throw new Error('Not in create mode.')
    if (!name) throw new Error('Name is required.')
    const savedLat = store.ui.createLat
    const savedLon = store.ui.createLon
    if (savedLat == null || savedLon == null) {
      throw new Error('Pick a location on the map first.')
    }
    const body = { name, lat: savedLat, lon: savedLon }
    if (tags?.length) body.tags = [...tags]
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    const { site } = await sitesDomain.createSite(body)
    if (!site?.slug) throw new Error('Unexpected server response.')
    removeDraftMarker()
    removeDraftViewshed?.()
    setCreateDraftCoords(null, null)
    store.ui.createMode = false
    clearAddPlacementMode()
    applySavedSiteToMap(site, savedLat, savedLon)
  }

  return {
    isCreateMode,
    isRelocateMode,
    getRelocateSlug,
    getAddPlacementMode,
    setCreateDraftCoords,
    setRelocateDraftCoords,
    showPanelView,
    showPanelCreate,
    removeDraftMarker,
    placeDraftMarker,
    syncRelocateMapShell,
    clearAddPlacementMode,
    setAddPlacementMode,
    openCreatePanel,
    cancelCreate,
    beginCreateAtMapPoint,
    toggleRelocate,
    cancelRelocate,
    commitRelocateAt,
    patchSiteField,
    selectSite,
    deselectSite,
    applySavedSiteToMap,
    getCreateCoordsLabel,
    saveCreateFromSheet,
    deleteSelectedSite,
  }
}
