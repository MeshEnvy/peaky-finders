// @ts-check

import {
  BASEMAP_REFERENCE_LAYER,
  BASEMAP_REFERENCE_SOURCE,
  MAP_STATE_SAVE_MS,
  SITE_FIT_BUFFER_KM,
  SITE_STACK_RAISE_IDS,
  SITES_CIRCLE,
  VIEWSHED_OPACITY_DEFAULT,
  mapStateKey,
} from '../constants.js'
import { BASEMAPS } from '../map/basemap.js'
import { createProjectMap } from '../map/create-map.js'
import {
  showTerrainOverlays as showTerrainOverlaysOnMap,
  hideTerrainOverlays as hideTerrainOverlaysOnMap,
  syncTerrainFromPitch as syncTerrainFromPitchOnMap,
} from '../map/terrain.js'
import {
  persistMapState as persistMapStateToStorage,
  captureMapState as captureMapStateShape,
} from '../map/map-state.js'
import { captureDeepLinkFromMap, writeDeepLink } from '../api/deep-link.js'
import { installMapToolbar } from '../map/toolbar.js'
import { kmToDegreeDeltas } from '../geo.js'

/**
 * Create the MapLibre map, nav control, and toolbar.
 * @param {object} opts
 */
export function bootProjectMap({
  basemapKey,
  savedMapState,
  viewshedOpacity,
  getMapReady,
  onPinOverlayResize,
}) {
  const map = createProjectMap({
    container: 'map',
    basemapKey,
    savedMapState,
  })
  const navControl = new maplibregl.NavigationControl({ visualizePitch: true })
  map.addControl(navControl, 'top-right')
  const mapContainer = document.getElementById('map')
  let pinOverlayResizeRaf = 0
  if (mapContainer && typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      if (!getMapReady()) return
      const rect = mapContainer.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) return
      map.resize()
      if (pinOverlayResizeRaf) cancelAnimationFrame(pinOverlayResizeRaf)
      pinOverlayResizeRaf = requestAnimationFrame(() => {
        pinOverlayResizeRaf = 0
        onPinOverlayResize?.()
      })
    }).observe(mapContainer)
  }
  const toolbar = installMapToolbar(navControl._container, { viewshedOpacity })
  const compassButton = navControl._container.querySelector('.maplibregl-ctrl-compass')
  return { map, navControl, toolbar, compassButton }
}

/**
 * Basemap, terrain, persisted camera, layer raise, home fit.
 * @param {object} ctx
 */
export function createMapChromeDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    getRestoring,
    setRestoring,
    getSites,
    siteHidden,
    activeTagFilters,
    viewshedVisible,
    accessVisible,
    landVisible,
    landSourceBatchVisible,
    landLabelsVisible,
    landFoldersCollapsed,
    getViewshed,
    toolbar,
    compassButton,
    onShowSiteLinks,
  } = ctx

  const MAP_STATE_KEY = mapStateKey(projectSlug)
  let currentBasemapKey =
    store?.ui?.basemapKey && BASEMAPS[store.ui.basemapKey]
      ? store.ui.basemapKey
      : 'street'
  let terrainActive = false
  let showSiteLinks = store?.ui?.showSiteLinks ?? true
  let viewshedOpacity = store?.ui?.viewshedOpacity ?? VIEWSHED_OPACITY_DEFAULT
  let saveTimer = null

  function getBasemapKey() {
    return currentBasemapKey
  }

  function getShowSiteLinks() {
    return showSiteLinks
  }

  function getViewshedOpacity() {
    return viewshedOpacity
  }

  function isTerrainActive() {
    return terrainActive
  }

  function raiseSiteLayers() {
    getViewshed?.()?.raiseViewshedLayers?.()
    const map = getMap()
    if (!map) return
    for (const id of SITE_STACK_RAISE_IDS) {
      if (map.getLayer(id)) {
        try {
          map.moveLayer(id)
        } catch (_) {
          /* layer may be mid-remove */
        }
      }
    }
  }

  function showTerrainOverlays() {
    showTerrainOverlaysOnMap(getMap(), {
      basemapKey: currentBasemapKey,
      onRaiseLayers: raiseSiteLayers,
    })
  }

  function hideTerrainOverlays() {
    hideTerrainOverlaysOnMap(getMap())
  }

  function syncTerrainFromPitch() {
    terrainActive = syncTerrainFromPitchOnMap(getMap(), {
      mapReady: getMapReady(),
      terrainActive,
      basemapKey: currentBasemapKey,
      onRaiseLayers: raiseSiteLayers,
    })
    if (store) store.ui.terrainActive = terrainActive
  }

  function refreshTerrainSourceIfNeeded() {
    if (!getMapReady()) return
    const wasActive = terrainActive
    if (wasActive) {
      terrainActive = false
      hideTerrainOverlays()
    }
    if (wasActive) syncTerrainFromPitch()
  }

  function ensureBasemapReference(bm) {
    const map = getMap()
    if (!bm.referenceTiles || !map) return
    if (!map.getSource(BASEMAP_REFERENCE_SOURCE)) {
      map.addSource(BASEMAP_REFERENCE_SOURCE, {
        type: 'raster',
        tiles: bm.referenceTiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      })
      map.addLayer(
        {
          id: BASEMAP_REFERENCE_LAYER,
          type: 'raster',
          source: BASEMAP_REFERENCE_SOURCE,
        },
        map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined,
      )
    } else {
      map.getSource(BASEMAP_REFERENCE_SOURCE).setTiles(bm.referenceTiles)
    }
    raiseSiteLayers()
  }

  function removeBasemapReference() {
    const map = getMap()
    if (!map) return
    if (map.getLayer(BASEMAP_REFERENCE_LAYER)) map.removeLayer(BASEMAP_REFERENCE_LAYER)
    if (map.getSource(BASEMAP_REFERENCE_SOURCE)) map.removeSource(BASEMAP_REFERENCE_SOURCE)
  }

  function syncBasemapMenu() {
    const menu = toolbar?.mapBasemapMenu
    if (!menu) return
    for (const btn of menu.querySelectorAll('[data-basemap]')) {
      const active = btn.getAttribute('data-basemap') === currentBasemapKey
      btn.classList.toggle('is-active', active)
      btn.setAttribute('aria-current', active ? 'true' : 'false')
    }
  }

  function setBasemap(key) {
    const bm = BASEMAPS[key]
    const map = getMap()
    if (!bm || !getMapReady() || !map) return
    const src = map.getSource('basemap')
    if (!src || typeof src.setTiles !== 'function') return
    src.setTiles(bm.tiles)
    map.setMaxZoom(bm.maxzoom)
    if (bm.referenceTiles) ensureBasemapReference(bm)
    else removeBasemapReference()
    raiseSiteLayers()
  }

  function setBasemapKey(key) {
    if (!BASEMAPS[key]) return
    currentBasemapKey = key
    if (store) store.ui.basemapKey = key
    syncBasemapMenu()
    setBasemap(key)
    refreshTerrainSourceIfNeeded()
    scheduleSaveMapState()
  }

  function syncOpacitySlider() {
    const opacityEl = document.getElementById('viewshed-opacity')
    if (opacityEl) opacityEl.value = String(Math.round(viewshedOpacity * 100))
  }

  function setViewshedOpacity(opacity) {
    viewshedOpacity = Math.max(0, Math.min(1, opacity))
    if (store) {
      store.ui.viewshedOpacity = viewshedOpacity
      store.viewshed.opacity = viewshedOpacity
    }
    syncOpacitySlider()
    getViewshed?.()?.applyViewshedOpacityToAllLayers?.()
  }

  function captureMapState() {
    return captureMapStateShape(getMap(), {
      basemap: currentBasemapKey,
      showLinks: showSiteLinks,
      viewshedOpacity,
      hiddenSites: [...siteHidden],
      tagFilters: [...activeTagFilters].sort((a, b) => a.localeCompare(b)),
      tagFilterMode: store?.ui?.tagFilterMode ?? 'and',
      filterByViewport: !!store?.ui?.filterByViewport,
      viewshedVisible: Object.fromEntries(viewshedVisible),
      accessVisible: Object.fromEntries(accessVisible),
      entityPanelOpen: !!store?.ui?.entityPanelOpen,
      entityPanelTab: store?.ui?.entityPanelTab === 'land' ? 'land' : 'sites',
      landVisible: Object.fromEntries(landVisible),
      landSourceBatchVisible: Object.fromEntries(landSourceBatchVisible),
      landLabelsVisible: Object.fromEntries(landLabelsVisible),
      landFoldersCollapsed: Object.fromEntries(landFoldersCollapsed),
    })
  }

  function scheduleSaveMapState() {
    if (!getMapReady() || getRestoring()) return
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveTimer = null
      persistMapStateToStorage(MAP_STATE_KEY, captureMapState())
      writeDeepLink(
        captureDeepLinkFromMap(getMap(), {
          site: store?.ui?.selectedSlug || undefined,
          peak: store?.ui?.selectedPeakSlug || undefined,
        }),
        { replace: true },
      )
    }, MAP_STATE_SAVE_MS)
  }

  function fitSites() {
    const points = [...getSites()]
    if (!points.length) return
    const map = getMap()
    const lons = points.map((p) => p.lon)
    const lats = points.map((p) => p.lat)
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2
    const { latDelta, lonDelta } = kmToDegreeDeltas(centerLat, SITE_FIT_BUFFER_KM)
    map.fitBounds(
      [
        [Math.min(...lons) - lonDelta, Math.min(...lats) - latDelta],
        [Math.max(...lons) + lonDelta, Math.max(...lats) + latDelta],
      ],
      { padding: 48, bearing: 0, pitch: 0, maxZoom: 15 },
    )
  }

  function resetHomeView() {
    if (terrainActive) {
      terrainActive = false
      hideTerrainOverlays()
    }
    getMap()?.resetNorthPitch()
  }

  function setShowSiteLinks(visible) {
    showSiteLinks = !!visible
    if (store) store.ui.showSiteLinks = showSiteLinks
    onShowSiteLinks?.(showSiteLinks)
  }

  function install() {
    syncOpacitySlider()
    if (compassButton) {
      compassButton.addEventListener(
        'click',
        (e) => {
          e.preventDefault()
          e.stopImmediatePropagation()
          resetHomeView()
        },
        true,
      )
    }
    toolbar?.mapBasemapMenu?.addEventListener('wa-select', (ev) => {
      const item = ev.detail.item
      if (!item) return
      setBasemapKey(item.value || item.getAttribute('data-basemap') || 'street')
    })
    toolbar?.viewshedOpacityInput?.addEventListener('input', (ev) => {
      setViewshedOpacity(Number(ev.target.value) / 100)
      scheduleSaveMapState()
    })
    const map = getMap()
    map.on('pitch', () => {
      syncTerrainFromPitch()
      scheduleSaveMapState()
    })
    map.on('moveend', scheduleSaveMapState)
    map.on('rotateend', scheduleSaveMapState)
  }

  return {
    getBasemapKey,
    getShowSiteLinks,
    getViewshedOpacity,
    isTerrainActive,
    raiseSiteLayers,
    showTerrainOverlays,
    hideTerrainOverlays,
    syncTerrainFromPitch,
    refreshTerrainSourceIfNeeded,
    syncBasemapMenu,
    setBasemap,
    setBasemapKey,
    setViewshedOpacity,
    syncOpacitySlider,
    scheduleSaveMapState,
    captureMapState,
    fitSites,
    resetHomeView,
    setShowSiteLinks,
    install,
  }
}
