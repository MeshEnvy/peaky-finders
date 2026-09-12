// @ts-check

import { formatCoord } from '../geo.js'
import { DRAFT_MARKER_COLOR, DRAFT_VIEWSHED_SLUG } from '../constants.js'
import { normalizeSiteFromApi, sitePassesTagFilter } from '../stores/sites.js'

/**
 * Create/edit draft marker, site sheet modes, select/deselect, save.
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
    refreshSiteAccessLayers,
    scheduleViewshedLoad,
    viewshedVisible,
    removeDraftViewshed,
    removeDraftLinksLayer,
    loadDraftViewshedAt,
    onOpenEdit,
    onCancelEdit,
    onCleanupEditSave,
    onFinishEditSave,
    deselectPeak,
  } = ctx

  /** @type {maplibregl.Marker|null} */
  let draftMarker = null

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

  function isEditMode() {
    return !!store.ui.editMode
  }

  function getEditSlug() {
    return store.ui.editSlug
  }

  function getAddPlacementMode() {
    return store.ui.addPlacementMode
  }

  function setCreateDraftCoords(lat, lon) {
    store.ui.createLat = lat
    store.ui.createLon = lon
  }

  function setEditDraftCoords(lat, lon) {
    store.ui.editLat = lat
    store.ui.editLon = lon
  }

  function selectedSlug() {
    return store.ui.selectedSlug
  }

  function assignSelectedSlug(slug) {
    store.ui.selectedSlug = slug
  }

  function syncEditMapShell() {
    const shell = mapShell()
    if (shell) shell.classList.toggle('edit-mode', isEditMode())
    syncMapCursor?.()
  }

  function showPanelView() {
    store.ui.createMode = false
    store.ui.editMode = false
    store.ui.editSlug = null
    syncEditMapShell()
  }

  function showPanelEdit() {
    store.ui.createMode = false
    store.ui.editMode = true
    syncEditMapShell()
  }

  function showPanelCreate() {
    store.ui.createMode = true
    store.ui.editMode = false
    store.ui.editSlug = null
    assignSelectedSlug(null)
    updateSelectedLayer?.()
    syncEditMapShell()
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
    setCreateDraftCoords(lat, lon)
    removeDraftLinksLayer?.()
    placeDraftMarker(lat, lon)
    const panel = sitePanelEl()
    if (panel) panel.hidden = false
    syncMapViewport?.()
    showPanelCreate()
    viewshedVisible?.set?.(DRAFT_VIEWSHED_SLUG, true)
    void loadDraftViewshedAt?.(lat, lon)
  }

  function cancelCreate() {
    if (!isCreateMode() && !draftMarker) return
    store.ui.createMode = false
    setCreateDraftCoords(null, null)
    removeDraftMarker()
    removeDraftViewshed?.()
    removeDraftLinksLayer?.()
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
    if (isEditMode()) return
    if (isCreateMode()) cancelCreate()
    setAddPlacementMode(null)
    openCreatePanel(lat, lon)
  }

  function openEditPanel() {
    const slug = selectedSlug()
    const entity = getSiteBySlug?.(slug)
    if (!entity || !slug) return
    store.ui.editSlug = slug
    setEditDraftCoords(Number(entity.lat), Number(entity.lon))
    showPanelEdit()
    onOpenEdit?.(entity)
  }

  function cancelEdit() {
    if (!isEditMode()) return
    store.ui.editMode = false
    store.ui.editSlug = null
    setEditDraftCoords(null, null)
    removeDraftMarker()
    removeDraftViewshed?.()
    removeDraftLinksLayer?.()
    onCancelEdit?.()
    applySiteLayerFilters?.()
    refreshFilteredLinks?.()
    syncEditMapShell()
    const panel = sitePanelEl()
    if (panel) panel.hidden = false
    syncMapViewport?.()
    showPanelView()
  }

  function selectSite(slug) {
    const site = getSiteBySlug?.(slug)
    if (!site) return
    if (isCreateMode()) cancelCreate()
    if (isEditMode()) cancelEdit()
    deselectPeak?.()
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
  }

  function deselectSite() {
    if (isCreateMode()) {
      cancelCreate()
      return
    }
    if (isEditMode()) {
      cancelEdit()
      return
    }
    assignSelectedSlug(null)
    const panel = sitePanelEl()
    if (panel) panel.hidden = true
    syncMapViewport?.()
    updateSelectedLayer?.()
    refreshSiteAccessLayers?.()
  }

  function applySavedSiteToMap(site, fallbackLat, fallbackLon) {
    const row = normalizeSiteFromApi({
      ...site,
      lat: site.lat ?? fallbackLat,
      lon: site.lon ?? fallbackLon,
    })
    if (!row) return false
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

  async function saveEditFromSheet({ name, lat, lon, height_m, tags }) {
    if (!isEditMode() || !store.ui.editSlug) throw new Error('Not in edit mode.')
    if (!name) throw new Error('Name is required.')
    const latNum = Number.parseFloat(lat)
    const lonNum = Number.parseFloat(lon)
    if (!Number.isFinite(latNum) || !Number.isFinite(lonNum)) {
      throw new Error('Valid latitude and longitude are required.')
    }
    const body = { name, lat: latNum, lon: lonNum, tags: [...(tags || [])] }
    if (height_m) {
      const heightM = Number(height_m)
      if (!Number.isFinite(heightM) || heightM < 1) {
        throw new Error('Antenna height must be at least 1 m.')
      }
      body.height_m = heightM
    } else {
      body.height_m = null
    }
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    const result = await sitesDomain.updateSite(store.ui.editSlug, body)
    const { site, promoted } = result
    removeDraftMarker()
    removeDraftViewshed?.()
    removeDraftLinksLayer?.()
    store.ui.editMode = false
    store.ui.editSlug = null
    setEditDraftCoords(null, null)
    onCleanupEditSave?.()
    if (promoted && site) {
      registerSite(site)
    } else if (site) {
      applySiteRowUpdate(site)
    }
    if (!site) return
    assignSelectedSlug(site.slug)
    viewshedVisible?.set?.(site.slug, true)
    scheduleViewshedLoad?.(site)
    showPanelView()
    onFinishEditSave?.()
    void loadSiteLinks?.()
    if (!sitePassesTagFilter(site, store)) deselectSite()
  }

  async function deleteSelectedSite() {
    const slug = selectedSlug() || getEditSlug()
    const entity = store.sites.list.find((site) => site.slug === slug) || getSiteBySlug?.(slug)
    if (!entity || !slug) throw new Error('No site selected.')
    if (store.sites.list.length <= 1) throw new Error('Cannot delete the last site.')
    if (!window.confirm(`Delete site "${entity.name}"?`)) return { cancelled: true }
    if (!sitesDomain) throw new Error('Sites API unavailable.')
    await sitesDomain.deleteSite(slug)
    if (isEditMode()) {
      store.ui.editMode = false
      store.ui.editSlug = null
      setEditDraftCoords(null, null)
      removeDraftMarker()
      removeDraftViewshed?.()
      removeDraftLinksLayer?.()
      onCleanupEditSave?.()
      syncEditMapShell()
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
    isEditMode,
    getEditSlug,
    getAddPlacementMode,
    setCreateDraftCoords,
    setEditDraftCoords,
    showPanelView,
    showPanelEdit,
    showPanelCreate,
    removeDraftMarker,
    placeDraftMarker,
    syncEditMapShell,
    clearAddPlacementMode,
    setAddPlacementMode,
    openCreatePanel,
    cancelCreate,
    beginCreateAtMapPoint,
    openEditPanel,
    cancelEdit,
    selectSite,
    deselectSite,
    applySavedSiteToMap,
    getCreateCoordsLabel,
    saveEditFromSheet,
    saveCreateFromSheet,
    deleteSelectedSite,
  }
}
