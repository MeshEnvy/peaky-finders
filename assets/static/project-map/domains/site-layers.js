// @ts-check

import {
  ALTERNATE_VIEWSHED_SLUG,
  FORTIFY_VIEWSHED_SLUG,
  DRAFT_VIEWSHED_SLUG,
  PEAK_VIEWSHED_SLUG,
  SEEK_HOP_VIEWSHED_PREFIX,
  SITES_CIRCLE,
  SITES_LABELS,
  SITES_SELECTED,
  SITES_SOURCE,
} from '../constants.js'
import {
  normalizeSiteFromApi,
  registerSite as registerSiteInStore,
  unregisterSite as unregisterSiteInStore,
  sitePassesTagFilter as sitePassesTagFilterStore,
  allProjectTags as allProjectTagsFromStore,
} from '../stores/sites.js'
import {
  sitesGeoJson as buildSitesGeoJson,
  ensureSiteLayers,
  setSitesSourceData,
} from '../map/sites-layers.js'
import { downloadSitesKml } from '../kml-export.js'
import { coordVisibleInMapViewport as coordVisibleInMapViewportAt } from '../map/viewport.js'

/**
 * Site GeoJSON layers, tag/hide filters, and in-memory site registry.
 * @param {object} ctx
 */
export function createSiteLayersDomain(ctx) {
  const {
    store,
    getMap,
    getMapReady,
    getSites,
    siteBySlug,
    siteHidden,
    tagFilterBypassSlugs,
    activeTagFilters,
    getSeek,
    deselectSite,
    raiseSiteLayers,
    renderEntityPanel,
    refreshSeekStartSelectIfOpen,
    scheduleSaveMapState,
    refreshFilteredLinks,
    removeViewshedLayer,
    purgeSiteLinksForSlug,
    linkFeatureTouchesSnapshotCoords,
    applyEntityVisibility,
    syncEditMapShell,
    refreshSiteAccessLayers,
  } = ctx

  function sites() {
    return getSites()
  }

  function sitesGeoJson() {
    return buildSitesGeoJson(sites())
  }

  function combineLayerFilters(...parts) {
    const filters = parts.filter(Boolean)
    if (!filters.length) return true
    if (filters.length === 1) return filters[0]
    return ['all', ...filters]
  }

  function editSiteLayerFilter() {
    if (store.ui.editMode && store.ui.editSlug) {
      return ['!=', ['get', 'slug'], store.ui.editSlug]
    }
    return null
  }

  function sitePassesTagFilter(site) {
    return sitePassesTagFilterStore(site, {
      ui: {
        tagFilters: activeTagFilters,
        tagFilterMode: store?.ui?.tagFilterMode ?? 'and',
      },
    })
  }

  function isEphemeralViewshedSlug(slug) {
    return (
      slug === DRAFT_VIEWSHED_SLUG ||
      slug === ALTERNATE_VIEWSHED_SLUG ||
      slug === FORTIFY_VIEWSHED_SLUG ||
      slug === PEAK_VIEWSHED_SLUG ||
      String(slug).startsWith(SEEK_HOP_VIEWSHED_PREFIX)
    )
  }

  function isSiteMapHidden(slug) {
    if (isEphemeralViewshedSlug(slug)) return siteHidden.has(slug)
    if (siteHidden.has(slug)) return true
    const site = siteBySlug.get(slug)
    if (!site) return true
    if (getSeek?.()?.isSiteInSeekPlan?.(slug)) return false
    if (tagFilterBypassSlugs.has(slug)) return false
    return !sitePassesTagFilter(site)
  }

  function siteVisibilityFilter() {
    const hidden = []
    for (const site of sites()) {
      if (isSiteMapHidden(site.slug)) hidden.push(site.slug)
    }
    if (!hidden.length) return null
    return ['!', ['in', ['get', 'slug'], ['literal', hidden]]]
  }

  function updateSelectedLayer() {
    const map = getMap()
    if (!map?.getLayer(SITES_SELECTED)) return
    const selectedSlug = store.ui.selectedSlug
    if (store.ui.editMode && selectedSlug === store.ui.editSlug) {
      map.setFilter(SITES_SELECTED, ['==', ['get', 'slug'], ''])
      return
    }
    const filter = combineLayerFilters(
      ['==', ['get', 'slug'], selectedSlug || ''],
      siteVisibilityFilter(),
    )
    map.setFilter(SITES_SELECTED, filter)
  }

  function applySiteLayerFilters() {
    const map = getMap()
    if (!getMapReady() || !map) return
    const filter = combineLayerFilters(siteVisibilityFilter(), editSiteLayerFilter())
    for (const layerId of [SITES_CIRCLE, SITES_LABELS]) {
      if (!map.getLayer(layerId)) continue
      map.setFilter(layerId, filter)
    }
    updateSelectedLayer()
    refreshSiteAccessLayers?.()
  }

  function addSiteLayers() {
    const map = getMap()
    ensureSiteLayers(map)
    setSitesSourceData(map, sitesGeoJson())
    applySiteLayerFilters()
  }

  function siteVisibleInMap(site) {
    if (!getMapReady() || !site) return true
    return coordVisibleInMapViewportAt(getMap(), site.lon, site.lat, {
      mapReady: getMapReady(),
    })
  }

  function allProjectTags() {
    if (store) return allProjectTagsFromStore(store)
    const found = new Set()
    for (const site of sites()) {
      for (const tag of site.tags || []) found.add(tag)
    }
    return [...found].sort((a, b) => a.localeCompare(b))
  }

  function pruneActiveTagFilters() {
    const valid = new Set(allProjectTags())
    for (const tag of activeTagFilters) {
      if (!valid.has(tag)) activeTagFilters.delete(tag)
    }
  }

  function bypassSiteTagFilter(slug) {
    if (!slug) return
    siteHidden.delete(slug)
    tagFilterBypassSlugs.add(slug)
  }

  function ensureSiteVisibleAfterAdd(site) {
    if (!site?.slug) return
    siteHidden.delete(site.slug)
    if (!sitePassesTagFilter(site)) tagFilterBypassSlugs.add(site.slug)
  }

  function filterSiteLinksGeoJson(geojson) {
    if (!geojson || !geojson.features) return geojson
    const features = geojson.features.filter((feature) => {
      const props = feature.properties || {}
      if (isSiteMapHidden(props.a) || isSiteMapHidden(props.b)) return false
      if (store.ui.editMode && store.ui.editSlug) {
        if (props.a === store.ui.editSlug || props.b === store.ui.editSlug) return false
      }
      if (linkFeatureTouchesSnapshotCoords?.(feature)) return false
      return true
    })
    return { type: geojson.type || 'FeatureCollection', features }
  }

  function applySiteRowUpdate(site, { refreshGeoJson = true } = {}) {
    const row = normalizeSiteFromApi(site)
    if (!row) return
    const list = sites()
    const ix = list.findIndex((s) => s.slug === row.slug)
    if (ix >= 0) list[ix] = row
    else list.push(row)
    siteBySlug.set(row.slug, row)
    const map = getMap()
    if (refreshGeoJson && map?.getSource(SITES_SOURCE)) {
      setSitesSourceData(map, sitesGeoJson())
    }
    if (refreshGeoJson) {
      applySiteLayerFilters()
      updateSelectedLayer()
      raiseSiteLayers?.()
    }
  }

  function registerSite(site) {
    const row = normalizeSiteFromApi(site)
    if (!row) return
    const list = sites()
    const ix = list.findIndex((s) => s.slug === row.slug)
    if (ix >= 0) list[ix] = row
    else list.push(row)
    siteBySlug.set(row.slug, row)
    ensureSiteVisibleAfterAdd(row)
    if (store) registerSiteInStore(store, row, { revealIfFiltered: true })
    addSiteLayers()
    applyEntityVisibility?.()
    renderEntityPanel?.()
    updateSelectedLayer()
    raiseSiteLayers?.()
    refreshSeekStartSelectIfOpen?.()
    scheduleSaveMapState?.()
  }

  function unregisterSite(slug) {
    const list = sites()
    const idx = list.findIndex((s) => s.slug === slug)
    if (idx >= 0) list.splice(idx, 1)
    siteBySlug.delete(slug)
    siteHidden.delete(slug)
    tagFilterBypassSlugs.delete(slug)
    store?.viewshed?.visible?.delete?.(slug)
    removeViewshedLayer?.(slug)
    purgeSiteLinksForSlug?.(slug)
    if (store.ui.selectedSlug === slug) deselectSite?.()
    if (store) unregisterSiteInStore(store, slug)
    pruneActiveTagFilters()
    addSiteLayers()
    renderEntityPanel?.()
    refreshSeekStartSelectIfOpen?.()
  }

  function applySiteTagChange(slug) {
    if (!siteBySlug.get(slug)) {
      tagFilterBypassSlugs.delete(slug)
      return
    }
    tagFilterBypassSlugs.delete(slug)
    applyEntityVisibility?.()
    renderEntityPanel?.()
  }

  function finishEditSaveUi() {
    applySiteLayerFilters()
    refreshFilteredLinks?.()
    syncEditMapShell?.()
    updateSelectedLayer()
    raiseSiteLayers?.()
  }

  function viewportExportableSites() {
    return sites()
      .filter((site) => siteVisibleInMap(site) && !isSiteMapHidden(site.slug))
      .sort((a, b) => b.lat - a.lat || String(a.slug).localeCompare(String(b.slug)))
  }

  /** @param {string} [projectSlug] */
  function exportViewportSitesKml(projectSlug) {
    if (!getMapReady() || !getMap()) {
      throw new Error('Map not ready')
    }
    const visible = viewportExportableSites()
    if (!visible.length) {
      throw new Error('No sites in the current map view')
    }
    const slug = projectSlug || 'viewport'
    downloadSitesKml(visible, `${slug}-viewport.kml`, 'viewport')
    return visible.length
  }

  return {
    sitesGeoJson,
    combineLayerFilters,
    sitePassesTagFilter,
    isEphemeralViewshedSlug,
    isSiteMapHidden,
    siteVisibilityFilter,
    updateSelectedLayer,
    applySiteLayerFilters,
    addSiteLayers,
    siteVisibleInMap,
    allProjectTags,
    pruneActiveTagFilters,
    bypassSiteTagFilter,
    ensureSiteVisibleAfterAdd,
    filterSiteLinksGeoJson,
    applySiteRowUpdate,
    registerSite,
    unregisterSite,
    applySiteTagChange,
    finishEditSaveUi,
    viewportExportableSites,
    exportViewportSitesKml,
  }
}
