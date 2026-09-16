import { DRAFT_VIEWSHED_SLUG, viewshedRadiusBounds } from '../constants.js'
import { copyCoordPair, formatSiteHeight, coordsUsableForMarker } from '../geo.js'
import { BASEMAPS } from '../map/basemap.js'
import { loadMapState as loadMapStateFromStorage } from '../map/map-state.js'
import { isMapTiltedView as isMapTiltedViewAt } from '../map/viewport.js'
import { connectProjectEvents as openProjectEventsStream } from '../api/events.js'
import { openWaDialog } from '../api/wa-dialog.js'
import {
  captureDeepLinkFromMap,
  deepLinkHasCamera,
  parseDeepLink,
  readDeepLinkFromLocation,
  writeDeepLink,
} from '../api/deep-link.js'
import { setSimulation } from '../stores/simulation.js'
import {
  sidebarSites,
  syncTagFilterVisibility,
  toggleSiteManualHidden,
} from '../stores/sites.js'
import { createSitesDomain } from './sites.js'
import { createViewshedDomain } from './viewshed.js'
import { createLinksDomain } from './links.js'
import { createLandDomain } from './land.js'
import { createLinkSolverDomain } from './link-solver.js'
import { createAlternatesDomain } from './alternates.js'
import { createFortifyDomain } from './fortify.js'
import { createSiteModalsDomain } from './site-modals.js'
import { createPlacementDomain } from './placement.js'
import { createEditPreviewDomain } from './edit-preview.js'
import { createMapInteractionsDomain } from './map-interactions.js'
import { createSiteLayersDomain } from './site-layers.js'
import { createPeaksDomain } from './peaks.js'
import { createSiteAccessDomain } from './site-access.js'
import { createEntityChromeDomain } from './entity-chrome.js'
import { bootProjectMap, createMapChromeDomain } from './map-chrome.js'

/** @param {{ store?: object, onStoreSync?: () => void }} [ctx] */
export function runApp(ctx = {}) {
  const store = ctx.store
  const config = window.PEAKY_PROJECT || {}
  const projectSlug = config.slug
  const sitesDomain = store
    ? createSitesDomain({ store, projectSlug: String(store.projectSlug || projectSlug || '') })
    : null
  const sites = store.sites.list
  const simDefaults = config.simulation || {}
  const { min: VIEWSHED_RADIUS_KM_MIN, max: VIEWSHED_RADIUS_KM_MAX } =
    viewshedRadiusBounds(simDefaults)
  const defaultTxHeightM = Number(simDefaults.transmitter?.height_m) || 2
  const savedMapState = store?.mapState?.saved ?? loadMapStateFromStorage(projectSlug)

  let projectEventsConn = null
  /** @type {ReturnType<typeof createLandDomain>|null} */
  let landDomain = null
  /** @type {ReturnType<typeof createLinkSolverDomain>|null} */
  let linkSolverDomain = null
  /** @type {ReturnType<typeof createAlternatesDomain>|null} */
  let alternatesDomain = null
  /** @type {ReturnType<typeof createFortifyDomain>|null} */
  let fortifyDomain = null
  /** @type {ReturnType<typeof createSiteModalsDomain>|null} */
  let siteModalsDomain = null
  /** @type {ReturnType<typeof createPlacementDomain>|null} */
  let placementDomain = null
  /** @type {ReturnType<typeof createEditPreviewDomain>|null} */
  let editPreviewDomain = null
  /** @type {ReturnType<typeof createMapInteractionsDomain>|null} */
  let mapInteractionsDomain = null
  /** @type {ReturnType<typeof createViewshedDomain>|null} */
  let viewshedDomain = null
  /** @type {ReturnType<typeof createLinksDomain>|null} */
  let linksDomain = null
  /** @type {ReturnType<typeof createSiteLayersDomain>|null} */
  let siteLayers = null
  /** @type {ReturnType<typeof createPeaksDomain>|null} */
  let peaksDomain = null
  /** @type {ReturnType<typeof createSiteAccessDomain>|null} */
  let siteAccessDomain = null
  /** @type {ReturnType<typeof createEntityChromeDomain>|null} */
  let entityChrome = null
  /** @type {ReturnType<typeof createMapChromeDomain>|null} */
  let mapChrome = null

  const viewshedVisible = store.viewshed.visible
  const accessVisible = store.access.visible
  const viewshedLoading = store.viewshed.loading
  const viewshedPendingEpoch = store.viewshed.pendingEpoch
  const siteBySlug = new Map(sites.map((s) => [s.slug, s]))
  const siteHidden = store.sites.hidden
  const activeTagFilters = store.ui.tagFilters
  const landVisible = store.land.visible
  const landSourceBatchVisible = store.land.sourceBatchVisible
  const landLabelsVisible = store.land.labelsVisible
  const landFoldersCollapsed = store.land.foldersCollapsed

  const currentBasemapKey =
    savedMapState && BASEMAPS[savedMapState.basemap] ? savedMapState.basemap : 'street'
  if (store) store.ui.basemapKey = currentBasemapKey
  const viewshedOpacity = savedMapState?.viewshedOpacity ?? store.ui.viewshedOpacity

  store.simulation.radiusMin = VIEWSHED_RADIUS_KM_MIN
  store.simulation.radiusMax = VIEWSHED_RADIUS_KM_MAX
  setSimulation(
    store,
    Number(simDefaults.radius_km) || store.simulation.radiusKm,
    Number(simDefaults.viewshed_quality) || store.simulation.quality,
  )

  const { map, toolbar, compassButton } = bootProjectMap({
    basemapKey: currentBasemapKey,
    savedMapState,
    viewshedOpacity,
    getMapReady: () => store.ui.mapReady,
    onPinOverlayResize: () => viewshedDomain?.updatePinOverlays(),
  })

  function syncMapCursor() {
    if (!store.ui.mapReady) return
    map.getCanvas().style.cursor =
      store.ui.addPlacementMode || store.ui.editMode ? 'crosshair' : ''
  }

  function isMapTiltedView(mapInstance = map) {
    return isMapTiltedViewAt(mapInstance, store.ui.mapReady)
  }

  siteAccessDomain = createSiteAccessDomain({
    store,
    projectSlug: String(store.projectSlug || projectSlug || ''),
    getMap: () => map,
    getMapReady: () => store.ui.mapReady,
    getSites: () => sites,
    isSiteMapHidden: (slug) => siteLayers?.isSiteMapHidden(slug) ?? false,
    raiseSiteLayers: () => mapChrome?.raiseSiteLayers(),
  })

  peaksDomain = createPeaksDomain({
    store,
    projectSlug: String(store.projectSlug || projectSlug || ''),
    getMap: () => map,
    getMapReady: () => store.ui.mapReady,
    deselectSite: (...args) => placementDomain?.deselectSite(...args),
    syncMapViewport: () => entityChrome?.syncMapViewport(),
    clearLinkSelection: () => linksDomain?.clearLinkSelection(),
    getViewshed: () => viewshedDomain,
    isSiteMapHidden: (slug) => siteLayers?.isSiteMapHidden(slug) ?? false,
    raiseSiteLayers: () => mapChrome?.raiseSiteLayers(),
    scheduleSaveMapState: () => mapChrome?.scheduleSaveMapState(),
    siteAccessDomain,
  })

  siteLayers = createSiteLayersDomain({
    store,
    getMap: () => map,
    getMapReady: () => store.ui.mapReady,
    getSites: () => sites,
    siteBySlug,
    siteHidden,
    activeTagFilters,
    deselectSite: (...args) => placementDomain?.deselectSite(...args),
    raiseSiteLayers: () => mapChrome?.raiseSiteLayers(),
    scheduleSaveMapState: () => mapChrome?.scheduleSaveMapState(),
    refreshFilteredLinks: () => linksDomain?.refreshFilteredLinks(),
    removeViewshedLayer: (slug) => viewshedDomain?.removeViewshedLayer(slug),
    purgeSiteLinksForSlug: (slug) => linksDomain?.purgeSiteLinksForSlug(slug),
    linkFeatureTouchesSnapshotCoords: (feature) =>
      editPreviewDomain?.linkFeatureTouchesSnapshotCoords(feature) ?? false,
    applyEntityVisibility: () => entityChrome?.applyEntityVisibility(),
    syncEditMapShell: () => placementDomain?.syncEditMapShell(),
    refreshSiteAccessLayers: () => siteAccessDomain?.refreshLayers(),
  })

  function initMapDomains() {
    linksDomain = createLinksDomain({
      store,
      map,
      projectSlug,
      getMapReady: () => store.ui.mapReady,
      getSites: () => sites,
      isSiteMapHidden: (slug) => siteLayers.isSiteMapHidden(slug),
      isViewshedVisible: (slug) => viewshedDomain.isViewshedVisible(slug),
      siteVisibleInMap: (site) => siteLayers.siteVisibleInMap(site),
      isMapTiltedView,
      filterSiteLinksGeoJson: (geojson) => siteLayers.filterSiteLinksGeoJson(geojson),
      raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
      getShowSiteLinks: () => mapChrome.getShowSiteLinks(),
      markSiteOutboundLinksReady: (slug) => viewshedDomain?.markSiteOutboundLinksReady(slug),
      sitePinSpinning: (slug) => viewshedDomain?.sitePinSpinning(slug) ?? false,
      isSiteOutboundLinksReady: (slug) =>
        viewshedDomain?.isSiteOutboundLinksReady(slug) ?? false,
      renderSelectedPanel: () => {},
      getSiteBySlug: (slug) => siteBySlug.get(slug),
      syncMapViewport: () => entityChrome?.syncMapViewport(),
      deselectSite: () => placementDomain?.deselectSite(),
      deselectPeak: () => peaksDomain?.deselectPeak(),
      ensureSiteAccess: (site) => site && siteAccessDomain?.ensureSiteAccess(site),
      refreshSiteAccessLayers: () => siteAccessDomain?.refreshLayers(),
    })

    viewshedDomain = createViewshedDomain({
      store,
      map,
      projectSlug,
      getMapReady: () => store.ui.mapReady,
      getSites: () => sites,
      getViewshedOpacity: () => mapChrome.getViewshedOpacity(),
      getLoadDraftAt: () =>
        (...args) =>
          editPreviewDomain?.loadDraftViewshedAt(...args),
      isSiteMapHidden: (slug) => siteLayers.isSiteMapHidden(slug),
      isEphemeralViewshedSlug: (slug) => siteLayers.isEphemeralViewshedSlug(slug),
      loadSingleSiteLinks: (slug) => linksDomain.loadSingleSiteLinks(slug),
      bumpWarmPriorities: (slugs, pri) => linksDomain.bumpWarmPriorities(slugs, pri),
      syncWarmPriorities: () => linksDomain.syncWarmPriorities(),
      warmPriorityInteractive: linksDomain.WARM_PRIORITY_INTERACTIVE,
      warmPriorityViewport: linksDomain.WARM_PRIORITY_VIEWPORT,
      raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
      setMarkerLngLatSafe(marker, lon, lat) {
        if (!marker || !coordsUsableForMarker(lon, lat)) return false
        try {
          marker.setLngLat([lon, lat])
          if (store.ui.mapReady) marker.addTo(map)
          return true
        } catch (_) {
          return false
        }
      },
      syncViewshedCheckbox() {
        if (store.ui.selectedSlug) viewshedDomain?.updatePinOverlays()
      },
      onDraftViewshedReady: () => editPreviewDomain?.onDraftViewshedReady(),
      onDraftViewshedLoadingClear: () => editPreviewDomain?.clearDraftViewshedLoading(),
      onDraftViewshedLayerAdded: () => editPreviewDomain?.onDraftViewshedReady(),
      viewshedLoadingHasDraft: () => viewshedLoading.has(DRAFT_VIEWSHED_SLUG),
      getDraftPlacement: () => editPreviewDomain?.getDraftPlacement() ?? null,
      getLinkSolverHopViewshedSlugs: () =>
        linkSolverDomain?.getLinkSolverHopViewshedSlugs?.() ?? [],
      getLinkSolverHopViewshedCoords: () =>
        linkSolverDomain?.getLinkSolverHopViewshedCoords?.() ?? new Map(),
    })
  }

  mapChrome = createMapChromeDomain({
    store,
    projectSlug,
    getMap: () => map,
    getMapReady: () => store.ui.mapReady,
    getRestoring: () => store.ui.restoring,
    getSites: () => sites,
    siteHidden,
    activeTagFilters,
    viewshedVisible,
    accessVisible,
    landVisible,
    landSourceBatchVisible,
    landLabelsVisible,
    landFoldersCollapsed,
    getViewshed: () => viewshedDomain,
    toolbar,
    compassButton,
    onShowSiteLinks: (visible) => linksDomain?.setSiteLinksVisible(visible),
  })

  const mapShell = document.querySelector('.map-shell')
  const sitePanel = document.getElementById('site-panel')
  const peakPanel = document.getElementById('peak-panel')
  const linkPanel = document.getElementById('link-panel')
  entityChrome = createEntityChromeDomain({
    store,
    getMap: () => map,
    getMapReady: () => store.ui.mapReady,
    getSites: () => sites,
    mapShell,
    sitePanel,
    peakPanel,
    linkPanel,
    entityPanel: document.getElementById('entity-panel'),
    entityPanelToggle: document.getElementById('entity-panel-toggle'),
    entityPanelSitesPane: document.getElementById('entity-panel-sites-pane'),
    entityPanelPeaksPane: document.getElementById('entity-panel-peaks-pane'),
    entityPanelLandPane: document.getElementById('entity-panel-land-pane'),
    entityPanelTabs: document.querySelectorAll('[data-entity-tab]'),
    mapToolSites: toolbar.mapToolSites,
    activeTagFilters,
    scheduleSaveMapState: () => mapChrome.scheduleSaveMapState(),
    applySiteLayerFilters: () => siteLayers.applySiteLayerFilters(),
    applyViewshedVisibilityForSite: (slug) =>
      viewshedDomain?.applyViewshedVisibilityForSite(slug),
    refreshFilteredLinks: () => {
      linksDomain?.refreshFilteredLinks()
      editPreviewDomain?.refreshFilteredDraftLinks()
    },
    updatePinOverlays: () => viewshedDomain?.updatePinOverlays(),
    ensureViewshedsForNewlyVisibleSites: () =>
      viewshedDomain?.ensureViewshedsForNewlyVisibleSites(),
    ensureAccessForVisibleSites: () =>
      siteAccessDomain?.ensureAccessForVisibleSites(),
    pruneActiveTagFilters: () => siteLayers.pruneActiveTagFilters(),
    onEscape() {
      if (store.ui.createMode) {
        placementDomain.cancelCreate()
        return
      }
      if (store?.linkSolver?.panelOpen) {
        linkSolverDomain?.toggleLinkSolverPanel(false)
        return
      }
      if (store?.fortify?.active) {
        fortifyDomain?.clearFortify()
        return
      }
      if (store?.alternates?.active) {
        alternatesDomain?.clearAlternates()
        return
      }
      if (store.ui.selectedLink) {
        linksDomain?.deselectLink()
        return
      }
      if (store.ui.editMode) {
        placementDomain.cancelEdit()
        return
      }
      if (store.ui.selectedPeakSlug) peaksDomain?.deselectPeak()
      else if (store.ui.selectedSlug) placementDomain.deselectSite()
    },
  })

  function ensureDomains() {
    if (!store) return
    if (!editPreviewDomain) {
      editPreviewDomain = createEditPreviewDomain({
        store,
        projectSlug,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        getViewshed: () => viewshedDomain,
        viewshedVisible,
        viewshedLoading,
        viewshedPendingEpoch,
        raiseSiteLayers: () => mapChrome?.raiseSiteLayers(),
        applySiteLayerFilters: () => siteLayers.applySiteLayerFilters(),
        refreshFilteredLinks: () => linksDomain?.refreshFilteredLinks(),
        updatePinOverlays: () => viewshedDomain?.updatePinOverlays(),
        applyViewshedVisibilityForSite: (slug) =>
          viewshedDomain?.applyViewshedVisibilityForSite(slug),
        ensureViewshedLoadedForSlug: (slug) =>
          viewshedDomain?.ensureViewshedLoadedForSlug(slug, (...args) =>
            editPreviewDomain.loadDraftViewshedAt(...args),
          ),
        isViewshedVisible: (slug) => viewshedDomain.isViewshedVisible(slug),
        placeDraftMarker: (lat, lon) => placementDomain?.placeDraftMarker(lat, lon),
        isSiteMapHidden: (slug) => siteLayers.isSiteMapHidden(slug),
      })
    }
    if (!placementDomain) {
      placementDomain = createPlacementDomain({
        store,
        sitesDomain,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        getSiteBySlug: (slug) => siteBySlug.get(slug),
        registerSite: (site) => siteLayers.registerSite(site),
        applySiteRowUpdate: (...args) => siteLayers.applySiteRowUpdate(...args),
        unregisterSite: (slug) => siteLayers.unregisterSite(slug),
        applySiteLayerFilters: () => siteLayers.applySiteLayerFilters(),
        refreshFilteredLinks: () => linksDomain?.refreshFilteredLinks(),
        updateSelectedLayer: () => siteLayers.updateSelectedLayer(),
        raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
        syncMapViewport: () => entityChrome.syncMapViewport(),
        syncMapCursor,
        syncWarmPriorities: () => linksDomain?.syncWarmPriorities(),
        loadSingleSiteLinks: (slug) => linksDomain?.loadSingleSiteLinks(slug),
        loadSiteLinks: () => linksDomain?.loadSiteLinks(),
        ensureSiteAccess: (site) => siteAccessDomain?.ensureSiteAccess(site),
        refreshSiteAccessLayers: () => siteAccessDomain?.refreshLayers(),
        scheduleViewshedLoad: (site) => viewshedDomain?.scheduleViewshedLoad(site),
        viewshedVisible,
        removeDraftViewshed: (...args) => editPreviewDomain.removeDraftViewshed(...args),
        removeDraftLinksLayer: () => editPreviewDomain.removeDraftLinksLayer(),
        loadDraftViewshedAt: (...args) => editPreviewDomain.loadDraftViewshedAt(...args),
        onOpenEdit: (entity) => editPreviewDomain.startEditSession(entity),
        onCancelEdit: () => editPreviewDomain.endEditSession(),
        onCleanupEditSave: () => editPreviewDomain.cleanupEditSave(),
        onFinishEditSave: () => siteLayers.finishEditSaveUi(),
        deselectPeak: (...args) => peaksDomain?.deselectPeak(...args),
        clearLinkSelection: () => linksDomain?.clearLinkSelection(),
      })
    }
    if (!landDomain) {
      landDomain = createLandDomain({
        store,
        projectSlug,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        scheduleSaveMapState: () => mapChrome.scheduleSaveMapState(),
        viewshedLayerInsertBefore: () => viewshedDomain?.viewshedLayerInsertBefore?.(),
        raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
        getBasemapKey: () => mapChrome.getBasemapKey(),
        getMapCenter: () => map.getCenter(),
        getMapZoom: () => map.getZoom(),
        openWaDialog,
        setAddPlacementMode: (...args) => placementDomain.setAddPlacementMode(...args),
        setEntityPanelOpen: (open) => entityChrome.setEntityPanelOpen(open),
        setEntityTab: (tab) => entityChrome.setEntityTab(tab),
      })
      landDomain.installLandSourceEditor()
      landDomain.installChrome()
    }
    if (!linkSolverDomain) {
      linkSolverDomain = createLinkSolverDomain({
        store,
        projectSlug,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        clearAlternates: () => alternatesDomain?.clearAlternates(),
        clearFortify: () => fortifyDomain?.clearFortify(),
        registerSiteFromApi: (site) => siteLayers.registerSite(site),
        mergeSeededLinks: (payload) => linksDomain?.mergeSeededLinks(payload),
        loadSingleSiteLinks: (slug) => linksDomain?.loadSingleSiteLinks(slug),
        loadSiteLinks: () => linksDomain?.loadSiteLinks(),
        raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
        getSiteBySlug: (slug) => siteBySlug.get(slug),
        findSiteLinkFeature: (a, b) => linksDomain?.findSiteLinkFeature(a, b),
        selectSite: (...args) => placementDomain.selectSite(...args),
        getViewshed: () => viewshedDomain,
        applyViewshedVisibilityForSite: (slug) =>
          viewshedDomain?.applyViewshedVisibilityForSite(slug),
        siteAccessDomain,
        getHiddenPeakSlugs: () => peaksDomain?.hiddenPeakSlugs?.() || [],
        mapToolLinkSolver: toolbar.mapToolLinkSolver,
        linkSolverPanel: document.getElementById('link-solver-panel'),
        updatePinOverlays: () => viewshedDomain?.updatePinOverlays(),
      })
      linkSolverDomain.install()
      if (store.ui.mapReady) linkSolverDomain.installMapHandlers(map)
    }
    if (!alternatesDomain) {
      alternatesDomain = createAlternatesDomain({
        store,
        projectSlug,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        clearLinkSolver: () => linkSolverDomain?.clearLinkSolver(),
        sitesDomain,
        applySavedSiteToMap: (...args) => placementDomain?.applySavedSiteToMap(...args),
        loadSingleSiteLinks: (slug) => linksDomain?.loadSingleSiteLinks(slug),
        raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
        getAlternatesAnchorSlugs: (slug) => linksDomain?.visibleLinkedPeersForSite(slug) || [],
        getViewshed: () => viewshedDomain,
        applyViewshedVisibilityForSite: (slug) =>
          viewshedDomain?.applyViewshedVisibilityForSite(slug),
        clearFortify: () => fortifyDomain?.clearFortify(),
      })
      if (store.ui.mapReady) alternatesDomain.installMapHandlers(map)
    }
    if (!fortifyDomain) {
      fortifyDomain = createFortifyDomain({
        store,
        projectSlug,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        clearAlternates: () => alternatesDomain?.clearAlternates(),
        clearLinkSolver: () => linkSolverDomain?.clearLinkSolver(),
        sitesDomain,
        applySavedSiteToMap: (...args) => placementDomain?.applySavedSiteToMap(...args),
        loadSingleSiteLinks: (slug) => linksDomain?.loadSingleSiteLinks(slug),
        raiseSiteLayers: () => mapChrome.raiseSiteLayers(),
        getSiteBySlug: (slug) => siteBySlug.get(slug),
        getViewshed: () => viewshedDomain,
        applyViewshedVisibilityForSite: (slug) =>
          viewshedDomain?.applyViewshedVisibilityForSite(slug),
        siteAccessDomain,
        getHiddenPeakSlugs: () => peaksDomain?.hiddenPeakSlugs?.() || [],
      })
      if (store.ui.mapReady) fortifyDomain.installMapHandlers(map)
    }
    if (!siteModalsDomain) {
      siteModalsDomain = createSiteModalsDomain({
        store,
        sitesDomain,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        getBasemapKey: () => mapChrome.getBasemapKey(),
        applySavedSiteToMap: (...args) => placementDomain.applySavedSiteToMap(...args),
        applySiteRowUpdate: (...args) => siteLayers.applySiteRowUpdate(...args),
        applyEntityVisibility: () => entityChrome.applyEntityVisibility(),
        scheduleSaveMapState: () => mapChrome.scheduleSaveMapState(),
        selectSite: (...args) => placementDomain.selectSite(...args),
        deselectSite: (...args) => placementDomain.deselectSite(...args),
        setAddPlacementMode: (...args) => placementDomain.setAddPlacementMode(...args),
        setEntityPanelOpen: (open) => entityChrome.setEntityPanelOpen(open),
      })
    }
    if (!mapInteractionsDomain) {
      mapInteractionsDomain = createMapInteractionsDomain({
        store,
        getMap: () => map,
        getMapReady: () => store.ui.mapReady,
        getLinkSolver: () => linkSolverDomain,
        getAlternates: () => alternatesDomain,
        getFortify: () => fortifyDomain,
        getLinks: () => linksDomain,
        getSiteBySlug: (slug) => siteBySlug.get(slug),
        syncMapCursor,
        setEditDraftCoords: (...args) => placementDomain.setEditDraftCoords(...args),
        onEditCoordsChanged: () => editPreviewDomain.onEditCoordsChanged(),
        setAddPlacementMode: (...args) => placementDomain.setAddPlacementMode(...args),
        selectSite: (...args) => placementDomain.selectSite(...args),
        deselectSite: (...args) => placementDomain.deselectSite(...args),
        selectPeak: (...args) => peaksDomain.selectPeak(...args),
        deselectPeak: (...args) => peaksDomain.deselectPeak(...args),
        selectLink: (...args) => linksDomain.selectLink(...args),
        deselectLink: (...args) => linksDomain.deselectLink(...args),
        openCreatePanel: (...args) => placementDomain.openCreatePanel(...args),
        beginCreateAtMapPoint: (...args) => placementDomain.beginCreateAtMapPoint(...args),
      })
    }
  }

  initMapDomains()
  ensureDomains()
  mapChrome.install()
  entityChrome.install()

  function connectProjectEvents() {
    if (projectEventsConn) {
      projectEventsConn.close()
      projectEventsConn = null
    }
    projectEventsConn = openProjectEventsStream(projectSlug, {
      onHello: () => viewshedDomain?.reconcilePendingViewsheds(),
      onViewshed: (data) => viewshedDomain?.handleViewshedEvent(data),
      onLinks: (data) => {
        if (!data) return
        linksDomain?.applySiteLinksPayload(data)
      },
      onAccess: (data) => siteAccessDomain?.handleAccessEvent(data),
    })
  }

  function reloadViewshedsForSimChange() {
    viewshedDomain?.reloadViewshedsForSimChange(() => {
      editPreviewDomain?.reloadDraftIfNeeded()
      if (store.linkSolver?.selectedRouteId) {
        linkSolverDomain?.syncLinkSolverHopViewsheds?.()
      }
    })
  }

  async function applyDeepLinkSelection(link, { replaceHistory = false } = {}) {
    if (!link.site && !link.peak) return
    store.ui.restoring = true
    try {
      await peaksDomain.loadPeaks()
      if (link.peak && peaksDomain.getPeakBySlug(link.peak)) {
        peaksDomain.selectPeak(link.peak, { syncDeepLink: false })
      } else if (link.site && siteBySlug.get(link.site)) {
        placementDomain.selectSite(link.site, { syncDeepLink: false })
      }
      if (replaceHistory) {
        writeDeepLink(
          captureDeepLinkFromMap(map, {
            site: store.ui.selectedSlug || undefined,
            peak: store.ui.selectedPeakSlug || undefined,
          }),
          { replace: true },
        )
      }
    } finally {
      store.ui.restoring = false
    }
  }

  async function restoreDeepLinkFromHistory() {
    const link = readDeepLinkFromLocation()
    store.ui.restoring = true
    try {
      if (deepLinkHasCamera(link)) {
        map.jumpTo({
          center: link.center,
          zoom: link.zoom,
          bearing: link.bearing ?? 0,
          pitch: link.pitch ?? 0,
          duration: 0,
        })
      }
      if (link.peak) {
        if (!store.peaks.list.length) await peaksDomain.loadPeaks()
        if (peaksDomain.getPeakBySlug(link.peak)) {
          peaksDomain.selectPeak(link.peak, { syncDeepLink: false })
        } else if (store.ui.selectedPeakSlug) {
          peaksDomain.deselectPeak({ syncDeepLink: false })
        }
      } else if (link.site) {
        if (siteBySlug.get(link.site)) {
          placementDomain.selectSite(link.site, { syncDeepLink: false })
        } else if (store.ui.selectedSlug) {
          placementDomain.deselectSite({ syncDeepLink: false })
        }
      } else {
        if (store.ui.selectedPeakSlug) peaksDomain.deselectPeak({ syncDeepLink: false })
        else if (store.ui.selectedSlug) placementDomain.deselectSite({ syncDeepLink: false })
      }
    } finally {
      store.ui.restoring = false
    }
  }

  window.addEventListener('popstate', () => {
    if (!store.ui.mapReady) return
    void restoreDeepLinkFromHistory()
  })

  map.on('load', () => {
    store.ui.mapReady = true
    entityChrome.syncMapViewport()
    map.resize()
    siteLayers.addSiteLayers()
    siteAccessDomain?.refreshLayers()
    mapInteractionsDomain.install()
    connectProjectEvents()
    landDomain.bumpLandPanel()
    entityChrome.setEntityTab(store.ui.entityPanelTab)
    syncTagFilterVisibility(store)
    entityChrome.applyEntityVisibility()
    mapChrome.syncBasemapMenu()
    mapChrome.setBasemap(mapChrome.getBasemapKey())
    if (savedMapState) {
      mapChrome.setShowSiteLinks(mapChrome.getShowSiteLinks())
      mapChrome.syncTerrainFromPitch()
    } else {
      mapChrome.fitSites()
    }
    void landDomain.refreshLandMapLayers()
    const bootLink = parseDeepLink()
    if (bootLink.site || bootLink.peak) {
      void applyDeepLinkSelection(bootLink, { replaceHistory: true })
    } else {
      void peaksDomain.loadPeaks()
      store.ui.restoring = false
    }
    map.once('idle', () => {
      void viewshedDomain.loadViewshedIndex()
      void linksDomain.loadSiteLinks()
    })
  })

  map.on('moveend', () => entityChrome.onMapMoveEndForEntityPanel())
  map.on('moveend', () => linksDomain?.onMapMoveEndForWarmPriorities())
  map.on('moveend', () => siteAccessDomain?.ensureAccessForVisibleSites())

  function bind(getDomain, names) {
    const api = {}
    for (const name of names) {
      api[name] = (...args) => {
        ensureDomains()
        return getDomain()[name](...args)
      }
    }
    return api
  }

  return {
    reloadViewshedsForSimChange,
    setViewshedSimulation: (radiusKm, quality) => setSimulation(store, radiusKm, quality),
    setViewshedVisible: (slug, visible) => viewshedDomain.setViewshedVisible(slug, visible),
    getMap: () => map,
    getStore: () => store,
    selectSite: (...args) => placementDomain.selectSite(...args),
    deselectSite: (...args) => placementDomain.deselectSite(...args),
    selectPeak: (...args) => peaksDomain.selectPeak(...args),
    deselectPeak: (...args) => peaksDomain.deselectPeak(...args),
    togglePeakMapVisible: (...args) => peaksDomain.togglePeakMapVisible(...args),
    showAllPeaks: () => peaksDomain.showAllPeaks(),
    hideAllPeaks: () => peaksDomain.hideAllPeaks(),
    toggleSiteMapVisible(slug) {
      toggleSiteManualHidden(store, slug)
      entityChrome.applyEntityVisibility()
      mapChrome.scheduleSaveMapState()
    },
    showAllSites() {
      const listed = sidebarSites(store, {
        map,
        mapReady: store.ui.mapReady,
      })
      let changed = false
      for (const site of listed) {
        if (!site?.slug || !store.sites.manualHidden.has(site.slug)) continue
        store.sites.manualHidden.delete(site.slug)
        changed = true
      }
      if (!changed) return
      syncTagFilterVisibility(store)
      entityChrome.applyEntityVisibility()
      mapChrome.scheduleSaveMapState()
    },
    hideAllSites() {
      const listed = sidebarSites(store, {
        map,
        mapReady: store.ui.mapReady,
      })
      let changed = false
      for (const site of listed) {
        if (!site?.slug || store.sites.manualHidden.has(site.slug)) continue
        store.sites.manualHidden.add(site.slug)
        changed = true
      }
      if (!changed) return
      syncTagFilterVisibility(store)
      entityChrome.applyEntityVisibility()
      mapChrome.scheduleSaveMapState()
    },
    showPeakInView: (...args) => peaksDomain.showPeakInView(...args),
    renderPeaksPanel: () => peaksDomain.bumpPeaksPanel(),
    deselectLink: (...args) => linksDomain?.deselectLink(...args),
    findSiteLinkFeature: (a, b) => linksDomain?.findSiteLinkFeature(a, b),
    flyToPeakProfilePoint: (...args) => peaksDomain.flyToProfilePoint(...args),
    sitesDomain,
    applySavedSiteToMap: (...args) => placementDomain?.applySavedSiteToMap(...args),
    selectSite: (...args) => placementDomain?.selectSite(...args),
    openEditPanel: () => placementDomain.openEditPanel(),
    cancelEdit: () => placementDomain.cancelEdit(),
    cancelCreate: () => placementDomain.cancelCreate(),
    onEditCoordsChanged: () => editPreviewDomain.onEditCoordsChanged(),
    saveEdit: (payload) => placementDomain.saveEditFromSheet(payload),
    saveCreate: (payload) => placementDomain.saveCreateFromSheet(payload),
    deleteSelectedSite: () => placementDomain.deleteSelectedSite(),
    syncMapViewport: () => entityChrome.syncMapViewport(),
    toggleViewshedForSelected() {
      const slug = store.ui.selectedSlug
      if (!slug) return
      viewshedDomain.setViewshedVisible(slug, !viewshedDomain.isViewshedVisible(slug))
      mapChrome.scheduleSaveMapState()
    },
    toggleAccessForSelected() {
      const slug = store.ui.selectedSlug
      if (!slug) return
      siteAccessDomain.setAccessVisible(slug, !siteAccessDomain.isAccessVisible(slug))
      mapChrome.scheduleSaveMapState()
    },
    isViewshedVisible: (slug) => viewshedDomain.isViewshedVisible(slug),
    isAccessVisible: (slug) => siteAccessDomain.isAccessVisible(slug),
    ensureSiteAccess: (site) => siteAccessDomain?.ensureSiteAccess(site),
    ingestSiteAccess: (...args) => siteAccessDomain?.ingestAccess(...args),
    copyCoordPair,
    getCreateCoordsLabel: () => placementDomain.getCreateCoordsLabel(),
    formatSiteHeight: (site) => formatSiteHeight(site, defaultTxHeightM),
    getSelectedSiteLinks: () => linksDomain.selectedSiteLinks({ visibleOnly: true }),
    toggleTagFilter: (tag) => entityChrome.toggleTagFilter(tag),
    onTagFilterChange: () => entityChrome.onTagFilterChange(),
    applyEntityVisibility: () => entityChrome.applyEntityVisibility(),
    onViewportFilterChange: () => entityChrome.onViewportFilterChange(),
    renderLandPanel: () => landDomain.bumpLandPanel(),
    renderEntityPanel: () => {},
    viewportExportableSites: () => siteLayers.viewportExportableSites(),
    exportViewportSitesKml: () => siteLayers.exportViewportSitesKml(projectSlug),
    ...bind(() => siteModalsDomain, [
      'openBulkTagModal',
      'closeBulkTagModal',
      'toggleBulkTag',
      'addBulkTagFromInput',
      'saveBulkTagModal',
      'bulkTagStatusText',
      'listedSidebarSites',
      'openAddSiteModal',
      'closeAddSiteModal',
      'toggleAddSiteTag',
      'addAddSiteTagFromInput',
      'saveAddSiteModal',
      'openImportSitesModal',
      'closeImportSitesModal',
      'resetImportSitesModal',
      'previewImportFile',
      'saveImportSitesModal',
      'toggleImportTag',
      'addImportTagFromInput',
      'setImportPointIgnored',
      'setAllImportPointsIgnored',
      'focusImportPreviewPoint',
      'resizeImportPreviewMap',
      'importablePoints',
      'effectiveImportTags',
      'importPointVisible',
    ]),
    ...bind(() => landDomain, [
      'toggleLandLayerVisible',
      'toggleLandOverlayVisible',
      'toggleLandSourceLayers',
      'setLandLayerVisible',
      'setLandLayerLabelsVisible',
      'setLandFolderVisible',
      'setLandFolderLabelsVisible',
      'setLandFolderCollapsed',
      'revealLandLayerOnMap',
      'refreshLandMapLayer',
      'isLandLayerEffectivelyVisible',
      'isLandOverlayVisible',
      'isLandLayerLabelsVisible',
      'landSourceRecord',
      'openLandSourceEditor',
      'deleteLandSource',
      'deleteLandFolder',
      'saveLandFolderModal',
      'closeLandFolderModal',
    ]),
    renameLandFolder: (folderId) => {
      ensureDomains()
      return landDomain.openLandFolderModal('rename', folderId)
    },
    createLandFolder: () => {
      ensureDomains()
      return landDomain.openLandFolderModal('create')
    },
    ...bind(() => linkSolverDomain, [
      'toggleLinkSolverPanel',
      'clearLinkSolver',
      'solve',
      'selectRoute',
      'loadMore',
      'moreLikeThis',
      'acceptSelectedRoute',
      'displayRoutes',
      'peakAccessBySlug',
      'checkAlreadyLinked',
    ]),
    solveLinkPair: (a, b) => {
      ensureDomains()
      return linkSolverDomain.solve(a, b)
    },
    selectLinkSolverRoute: (routeId) => {
      ensureDomains()
      return linkSolverDomain.selectRoute(routeId)
    },
    moreLikeLinkSolverRoute: (routeId) => {
      ensureDomains()
      return linkSolverDomain.moreLikeThis(routeId)
    },
    loadMoreLinkSolverRoutes: () => {
      ensureDomains()
      return linkSolverDomain.loadMore()
    },
    acceptLinkSolverRoute: () => {
      ensureDomains()
      return linkSolverDomain.acceptSelectedRoute()
    },
    displayLinkSolverRoutes: () => {
      ensureDomains()
      return linkSolverDomain.displayRoutes()
    },
    linkSolverPeakAccess: () => {
      ensureDomains()
      return linkSolverDomain.peakAccessBySlug()
    },
    checkLinkSolverAlreadyLinked: () => {
      ensureDomains()
      const { a, b } = store.linkSolver
      return linkSolverDomain.checkAlreadyLinked(a, b)
    },
    ...bind(() => alternatesDomain, [
      'findAlternatesForSite',
      'clearAlternates',
      'addSelectedAlternateAsSite',
      'alternatesActive',
    ]),
    ...bind(() => fortifyDomain, [
      'fortifyLink',
      'clearFortify',
      'addSelectedFortifyAsSite',
      'selectFortifyCandidateById',
      'fortifyActive',
    ]),
  }
}
