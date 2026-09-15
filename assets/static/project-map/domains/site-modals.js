// @ts-check

import { arrayBufferToBase64, formatCoord, normalizeTagInput, parseCoordPairFromText } from '../geo.js'
import {
  allProjectTags,
  annotateImportPoints,
  bulkTagInitialCounts,
  computeBulkTagOps,
  sidebarSites,
  syncTagFilterVisibility,
  toggleTag,
} from '../stores/sites.js'
import {
  applyImportPreviewData,
  createImportPreviewMap,
  destroyImportPreviewMap,
  importPreviewGeoJson,
} from '../map/import-preview.js'
import { coordVisibleInMapViewport } from '../map/viewport.js'

/**
 * Bulk-tag, add-site, and KML import dialogs. Mutate store.modals; Vue panels
 * drive the existing wa-dialog markup.
 * @param {object} ctx
 */
export function createSiteModalsDomain(ctx) {
  const {
    store,
    sitesDomain,
    getMap,
    getMapReady,
    getBasemapKey,
    applySavedSiteToMap,
    applySiteRowUpdate,
    applyEntityVisibility,
    renderEntityPanel,
    scheduleSaveMapState,
    selectSite,
    deselectSite,
    setAddPlacementMode,
    setEntityPanelOpen,
  } = ctx

  /** @type {maplibregl.Map|null} */
  let importPreviewMap = null
  /** @type {Record<string, unknown>|null} */
  let importPreviewPayload = null
  /** @type {((ev: unknown) => void)|null} */
  let importPreviewMoveHandler = null

  function listedSidebarSites() {
    return sidebarSites(store, { map: getMap?.(), mapReady: getMapReady?.() })
  }

  function bulkTagStatusText(listed) {
    const count = listed.length
    const filters = store.ui.tagFilters
    const scopeParts = []
    if (filters?.size) {
      const mode =
        store.ui.tagFilterMode === 'or' ? 'with any selected tag' : 'with all selected tags'
      scopeParts.push(filters.size < 2 ? 'with selected tags' : mode)
    }
    if (store.ui.filterByViewport) scopeParts.push('in the current map view')
    const scope = scopeParts.length ? ` ${scopeParts.join(' and ')}` : ''
    if (count === 0) return 'No sites match the current sidebar filters.'
    if (count === 1) return `Apply to 1 site${scope}`
    return `Apply to ${count} sites${scope}`
  }

  function openBulkTagModal() {
    store.modals.bulkTag = {
      open: true,
      error: '',
      saving: false,
      tagInput: '',
      pending: {},
    }
  }

  function closeBulkTagModal() {
    store.modals.bulkTag = {
      ...store.modals.bulkTag,
      open: false,
      error: '',
      saving: false,
    }
  }

  function toggleBulkTag(tag) {
    const listed = listedSidebarSites()
    const counts = bulkTagInitialCounts(listed)
    const modal = store.modals.bulkTag
    const pending = { ...(modal.pending || {}) }
    const total = listed.length
    const current = pending[tag]
    let visual = 'none'
    if (current === 'all') visual = 'full'
    else if (current === 'none') visual = 'none'
    else {
      const count = counts[tag] || 0
      if (count >= total && total) visual = 'full'
      else if (count > 0) visual = 'partial'
    }
    pending[tag] = visual === 'full' ? 'none' : 'all'
    store.modals.bulkTag = { ...modal, pending, error: '' }
  }

  function addBulkTagFromInput() {
    const tag = normalizeTagInput(store.modals.bulkTag.tagInput)
    store.modals.bulkTag.tagInput = ''
    if (!tag) return
    const pending = { ...(store.modals.bulkTag.pending || {}), [tag]: 'all' }
    store.modals.bulkTag = { ...store.modals.bulkTag, pending }
  }

  async function saveBulkTagModal() {
    addBulkTagFromInput()
    const listed = listedSidebarSites()
    const slugs = listed.map((site) => site.slug)
    const counts = bulkTagInitialCounts(listed)
    const { addTags, removeTags } = computeBulkTagOps(
      slugs,
      store.modals.bulkTag.pending,
      counts,
    )
    if (!slugs.length || (!addTags.length && !removeTags.length)) {
      store.modals.bulkTag = { ...store.modals.bulkTag, error: 'Change at least one tag.' }
      return
    }
    store.modals.bulkTag = { ...store.modals.bulkTag, error: '', saving: true }
    try {
      if (!sitesDomain) throw new Error('Sites API unavailable.')
      const { sites: updated } = await sitesDomain.bulkTagSites({
        slugs,
        add_tags: addTags,
        remove_tags: removeTags,
      })
      closeBulkTagModal()
      for (const site of updated) {
        applySiteRowUpdate?.(site, { refreshGeoJson: false })
      }
      syncTagFilterVisibility(store)
      applyEntityVisibility?.()
      renderEntityPanel?.()
      scheduleSaveMapState?.()
    } catch (err) {
      store.modals.bulkTag = {
        ...store.modals.bulkTag,
        saving: false,
        error: err instanceof Error ? err.message : 'Could not reach server.',
      }
    }
  }

  function resetAddSiteModal() {
    store.modals.addSite = {
      open: false,
      name: '',
      coords: '',
      tags: [],
      tagInput: '',
      error: '',
      saving: false,
    }
  }

  function openAddSiteModal() {
    setAddPlacementMode?.(null)
    setEntityPanelOpen?.(true)
    store.modals.addSite = {
      open: true,
      name: '',
      coords: '',
      tags: [],
      tagInput: '',
      error: '',
      saving: false,
    }
  }

  function closeAddSiteModal() {
    store.modals.addSite = { ...store.modals.addSite, open: false, error: '', saving: false }
  }

  function toggleAddSiteTag(tag) {
    store.modals.addSite.tags = toggleTag(store.modals.addSite.tags, tag)
  }

  function addAddSiteTagFromInput() {
    const tag = normalizeTagInput(store.modals.addSite.tagInput)
    store.modals.addSite.tagInput = ''
    if (!tag) return
    if (!store.modals.addSite.tags.includes(tag)) {
      store.modals.addSite.tags = [...store.modals.addSite.tags, tag]
    }
  }

  async function saveAddSiteModal() {
    addAddSiteTagFromInput()
    const name = String(store.modals.addSite.name || '').trim()
    if (!name) {
      store.modals.addSite = { ...store.modals.addSite, error: 'Name is required.' }
      return
    }
    const pair = parseCoordPairFromText(store.modals.addSite.coords || '')
    if (!pair) {
      store.modals.addSite = {
        ...store.modals.addSite,
        error: 'Coordinates required — paste lat, lng like 40.65495, -119.35161.',
      }
      return
    }
    if (pair.lat < -90 || pair.lat > 90 || pair.lon < -180 || pair.lon > 180) {
      store.modals.addSite = { ...store.modals.addSite, error: 'Coordinates out of range.' }
      return
    }
    store.modals.addSite = { ...store.modals.addSite, error: '', saving: true }
    try {
      if (!sitesDomain) throw new Error('Sites API unavailable.')
      const body = { name, lat: pair.lat, lon: pair.lon }
      if (store.modals.addSite.tags.length) body.tags = [...store.modals.addSite.tags]
      const { site } = await sitesDomain.createSite(body)
      if (!site?.slug) throw new Error('Unexpected server response.')
      closeAddSiteModal()
      applySavedSiteToMap?.(site, pair.lat, pair.lon)
      const map = getMap?.()
      if (getMapReady?.() && map) {
        map.flyTo({
          center: [site.lon ?? pair.lon, site.lat ?? pair.lat],
          zoom: Math.max(map.getZoom(), 11),
        })
      }
    } catch (err) {
      store.modals.addSite = {
        ...store.modals.addSite,
        saving: false,
        error: err instanceof Error ? err.message : 'Could not reach server.',
      }
    }
  }

  function importablePoints() {
    return (store.modals.importSites.points || []).filter((point) => !point.ignored)
  }

  function effectiveImportTags() {
    const tags = [...(store.modals.importSites.tags || [])]
    const pending = normalizeTagInput(store.modals.importSites.tagInput)
    if (pending && !tags.includes(pending)) tags.push(pending)
    return tags
  }

  function importPreviewStatus(points, fileName, skipped) {
    if (!fileName || !points.length) return 'Choose a file with Point placemarks.'
    const skippedNote = skipped > 0 ? ` (${skipped} placemark(s) skipped)` : ''
    let text = `${points.length} point(s) ready from ${fileName}${skippedNote}`
    const dupes = points.filter((point) => point.duplicate && point.ignored).length
    if (dupes > 0) text += ` · ${dupes} near existing site(s), unchecked`
    return text
  }

  function destroyPreviewMap() {
    if (importPreviewMap && importPreviewMoveHandler) {
      importPreviewMap.off('moveend', importPreviewMoveHandler)
    }
    destroyImportPreviewMap(importPreviewMap)
    importPreviewMap = null
    importPreviewMoveHandler = null
  }

  function onImportPreviewMoveEnd() {
    if (store.modals.importSites.filterVisible) {
      store.modals.importSites = { ...store.modals.importSites }
    }
  }

  function ensurePreviewMap() {
    const container = document.getElementById('import-sites-preview-map')
    if (!container) return null
    if (!importPreviewMap) {
      importPreviewMap = createImportPreviewMap(container, getBasemapKey?.() || 'street')
      importPreviewMoveHandler = onImportPreviewMoveEnd
      importPreviewMap.on('moveend', importPreviewMoveHandler)
    }
    return importPreviewMap
  }

  function paintImportPreview() {
    const points = store.modals.importSites.points || []
    if (!points.length) {
      destroyPreviewMap()
      return
    }
    const map = ensurePreviewMap()
    if (!map) return
    const apply = () => applyImportPreviewData(map, points)
    if (map.loaded()) apply()
    else map.once('load', apply)
  }

  function resetImportSitesModal() {
    destroyPreviewMap()
    importPreviewPayload = null
    store.modals.importSites = {
      open: store.modals.importSites.open,
      error: '',
      status: 'Choose a file with Point placemarks.',
      tags: ['imported'],
      tagInput: '',
      points: [],
      skipped: 0,
      busy: false,
      saving: false,
      filterVisible: true,
      selectedIndex: -1,
      hasPayload: false,
      fileName: '',
      previewHidden: true,
    }
  }

  function openImportSitesModal() {
    setAddPlacementMode?.(null)
    setEntityPanelOpen?.(true)
    resetImportSitesModal()
    store.modals.importSites.open = true
  }

  function closeImportSitesModal() {
    store.modals.importSites.open = false
  }

  function toggleImportTag(tag) {
    store.modals.importSites.tags = toggleTag(store.modals.importSites.tags, tag)
  }

  function addImportTagFromInput() {
    const tag = normalizeTagInput(store.modals.importSites.tagInput)
    store.modals.importSites.tagInput = ''
    if (!tag) return
    if (!store.modals.importSites.tags.includes(tag)) {
      store.modals.importSites.tags = [...store.modals.importSites.tags, tag]
    }
  }

  function setImportPointIgnored(index, ignored) {
    const points = [...(store.modals.importSites.points || [])]
    if (!points[index]) return
    points[index] = { ...points[index], ignored: !!ignored }
    store.modals.importSites.points = points
    if (importPreviewMap) {
      const source = importPreviewMap.getSource('import-preview-points')
      if (source) source.setData(importPreviewGeoJson(points))
    }
  }

  function setAllImportPointsIgnored(ignored) {
    const points = (store.modals.importSites.points || []).map((point) => ({
      ...point,
      ignored: !!ignored,
    }))
    store.modals.importSites.points = points
    if (importPreviewMap) {
      const source = importPreviewMap.getSource('import-preview-points')
      if (source) source.setData(importPreviewGeoJson(points))
    }
  }

  function importPointVisible(point) {
    if (!importPreviewMap || !point) return true
    return coordVisibleInMapViewport(importPreviewMap, point.lon, point.lat)
  }

  function visibleImportPoints() {
    const points = store.modals.importSites.points || []
    if (!store.modals.importSites.filterVisible || !importPreviewMap) return points
    return points.filter((point) => importPointVisible(point))
  }

  function focusImportPreviewPoint(index) {
    const point = store.modals.importSites.points?.[index]
    if (!point || !importPreviewMap) return
    store.modals.importSites.selectedIndex = index
    importPreviewMap.flyTo({
      center: [point.lon, point.lat],
      zoom: Math.max(importPreviewMap.getZoom(), 12),
      duration: 400,
    })
  }

  function resizeImportPreviewMap() {
    importPreviewMap?.resize()
  }

  async function previewImportFile(file) {
    if (!file) return
    const name = String(file.name || '').toLowerCase()
    const isKmz = name.endsWith('.kmz')
    const isKml = name.endsWith('.kml')
    if (!isKml && !isKmz) {
      store.modals.importSites = {
        ...store.modals.importSites,
        error: 'Choose a .kml or .kmz file.',
        points: [],
        previewHidden: true,
        hasPayload: false,
        status: 'No points found.',
      }
      destroyPreviewMap()
      return
    }
    store.modals.importSites = {
      ...store.modals.importSites,
      busy: true,
      error: '',
      status: `Parsing ${file.name}…`,
    }
    try {
      const body = isKmz
        ? { kmz_b64: arrayBufferToBase64(await file.arrayBuffer()) }
        : { kml: await file.text() }
      if (!sitesDomain) throw new Error('Sites API unavailable.')
      const payload = await sitesDomain.previewImportJson(body)
      const rawPoints = Array.isArray(payload.points) ? payload.points : []
      const points = annotateImportPoints(rawPoints, store.sites.list)
      importPreviewPayload = body
      store.modals.importSites = {
        ...store.modals.importSites,
        busy: false,
        points,
        skipped: Number(payload.skipped) || 0,
        fileName: file.name,
        hasPayload: true,
        previewHidden: !points.length,
        selectedIndex: -1,
        status: points.length
          ? importPreviewStatus(points, file.name, Number(payload.skipped) || 0)
          : 'No points found.',
      }
      paintImportPreview()
    } catch (err) {
      importPreviewPayload = null
      store.modals.importSites = {
        ...store.modals.importSites,
        busy: false,
        hasPayload: false,
        points: [],
        previewHidden: true,
        error: err instanceof Error ? err.message : 'Could not reach server.',
        status: 'Preview failed.',
      }
      destroyPreviewMap()
    }
  }

  async function saveImportSitesModal() {
    addImportTagFromInput()
    const pointsToImport = importablePoints()
    const tags = effectiveImportTags()
    if (!tags.length || !pointsToImport.length) {
      store.modals.importSites = {
        ...store.modals.importSites,
        error: 'Choose at least one point to import and at least one tag.',
      }
      return
    }
    store.modals.importSites = { ...store.modals.importSites, error: '', saving: true }
    try {
      if (!sitesDomain) throw new Error('Sites API unavailable.')
      const { sites: imported } = await sitesDomain.importSites({
        tags,
        points: pointsToImport.map((point) => ({
          name: point.name,
          lat: point.lat,
          lon: point.lon,
        })),
      })
      if (!imported.length) throw new Error('Import returned no sites.')
      closeImportSitesModal()
      resetImportSitesModal()
      const firstTag = tags[0]
      if (firstTag) {
        store.ui.tagFilters.clear()
        store.ui.tagFilters.add(firstTag)
      }
      syncTagFilterVisibility(store)
      applyEntityVisibility?.()
      renderEntityPanel?.()
      const map = getMap?.()
      if (getMapReady?.() && map && imported.length) {
        const bounds = new maplibregl.LngLatBounds()
        for (const site of imported) bounds.extend([site.lon, site.lat])
        if (imported.length === 1) {
          const site = imported[0]
          map.flyTo({
            center: [site.lon, site.lat],
            zoom: Math.max(map.getZoom(), 11),
          })
        } else {
          map.fitBounds(bounds, { padding: 80, maxZoom: 12, duration: 800 })
        }
      }
      scheduleSaveMapState?.()
      if (imported[0]?.slug) selectSite?.(imported[0].slug)
    } catch (err) {
      store.modals.importSites = {
        ...store.modals.importSites,
        saving: false,
        error: err instanceof Error ? err.message : 'Could not reach server.',
      }
    }
  }

  return {
    openBulkTagModal,
    closeBulkTagModal,
    toggleBulkTag,
    addBulkTagFromInput,
    saveBulkTagModal,
    bulkTagStatusText,
    listedSidebarSites,
    openAddSiteModal,
    closeAddSiteModal,
    resetAddSiteModal,
    toggleAddSiteTag,
    addAddSiteTagFromInput,
    saveAddSiteModal,
    openImportSitesModal,
    closeImportSitesModal,
    resetImportSitesModal,
    previewImportFile,
    saveImportSitesModal,
    toggleImportTag,
    addImportTagFromInput,
    setImportPointIgnored,
    setAllImportPointsIgnored,
    focusImportPreviewPoint,
    resizeImportPreviewMap,
    importablePoints,
    effectiveImportTags,
    visibleImportPoints,
    importPointVisible,
    allProjectTags: () => allProjectTags(store),
    formatCoord,
  }
}
