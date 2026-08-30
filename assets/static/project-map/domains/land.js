// @ts-check

import * as apiUrls from '../api/urls.js'
import { LAND_OVERLAYS, LAND_OVERLAY_SOURCE_ID, landOverlayLayerKey } from '../map/land-overlays.js'
import { landLayerHasLabelField } from '../stores/land-sidebar-view.js'
import { createLandSourceEditor } from './land-source-editor.js'
import { slugifyLandFolderId } from '../geo.js'
import { basemapStyle } from '../map/basemap.js'
import {
  landLayerKey as storeLandLayerKey,
  cloneLandSidebar,
  syncLandSidebarWithSources,
  applyLandSidebarMutation as applyLandSidebarMutationStore,
  setLandLayerVisible as setLandLayerVisibleStore,
  setLandSourceBatchVisible as setLandSourceBatchVisibleStore,
  setLandLabelsVisible as setLandLabelsVisibleStore,
  setLandFolderCollapsed as setLandFolderCollapsedStore,
  setLandRowLoading,
  isLandLayerEffectivelyVisible as isLandLayerEffectivelyVisibleStore,
  isLandOverlayVisible as isLandOverlayVisibleStore,
  isLandSourceEffectivelyVisible as isLandSourceEffectivelyVisibleStore,
  toggleLandSourceLayers as toggleLandSourceLayersStore,
  setLandFolderVisible as setLandFolderVisibleStore,
} from '../stores/land.js'
import {
  addLandOverlayPartLayer,
  addLandRegisteredLayer,
  applyLandMapLayerStyle,
  decorateLandGeoJsonProperties,
  flatStyleFromLayerSpec,
  landLayerSlug,
  landMapSourceId,
  removeLandMapLayer,
  removeLandOverlayLayer,
  syncLandMapLabelLayer,
  syncLandMapLayerOrder,
  syncLandMapLayerVisibility,
  syncLandOverlayVisibility,
} from '../map/land-layers.js'

const LAND_SIDEBAR_SAVE_MS = 400

/** @param {Map<string, unknown>} cache @param {Map<string, Promise<unknown>>} inflight @param {string} key @param {() => Promise<unknown>} fetchFn */
async function fetchCachedGeoJson(cache, inflight, key, fetchFn) {
  if (cache.has(key)) return cache.get(key)
  if (inflight.has(key)) return inflight.get(key)
  const promise = fetchFn()
    .then((data) => {
      cache.set(key, data)
      inflight.delete(key)
      return data
    })
    .catch((err) => {
      inflight.delete(key)
      throw err
    })
  inflight.set(key, promise)
  return promise
}

/** @param {unknown} layer */
export function normalizeRegisteredLayer(layer) {
  if (typeof layer === 'string') {
    const name = layer
    return {
      name,
      key: landLayerSlug(name),
      role: null,
      digest: null,
      style: null,
      styleField: null,
      labelField: null,
      include: [],
      exclude: [],
    }
  }
  const name = layer.name
  const layerId = layer.id != null && layer.id !== '' ? String(layer.id) : null
  return {
    name,
    id: layerId,
    key: layer.key || (layerId ? landLayerSlug(layerId) : landLayerSlug(name)),
    role: layer.role || null,
    digest: layer.digest || null,
    style: layer.style || null,
    styleField: layer.styleField || layer.style_field || null,
    labelField: layer.labelField || layer.label_field || null,
    include: Array.isArray(layer.include) ? layer.include : [],
    exclude: Array.isArray(layer.exclude) ? layer.exclude : [],
  }
}

/**
 * @param {object} ctx
 * @param {object} ctx.store
 * @param {string} ctx.projectSlug
 * @param {() => maplibregl.Map|null} ctx.getMap
 * @param {() => boolean} ctx.getMapReady
 * @param {() => void} ctx.scheduleSaveMapState
 * @param {() => string|undefined} ctx.viewshedLayerInsertBefore
 * @param {() => void} [ctx.raiseSiteLayers]
 * @param {() => void} [ctx.onSidebarPersisted]
 */
export function createLandDomain(ctx) {
  const {
    store,
    projectSlug,
    getMap,
    getMapReady,
    scheduleSaveMapState,
    viewshedLayerInsertBefore,
    raiseSiteLayers,
    onSidebarPersisted,
    getBasemapKey,
    getMapCenter,
    getMapZoom,
    openWaDialog,
    setAddPlacementMode,
    setEntityPanelOpen,
    setEntityTab,
  } = ctx

  const landPreviewGeoJsonCache = new Map()
  const landPreviewGeoJsonInflight = new Map()
  const landLayerGeoJsonCache = new Map()
  const landLayerGeoJsonInflight = new Map()
  const landOverlayGeoJsonCache = new Map()
  const landOverlayGeoJsonInflight = new Map()
  const landOverlayLoadedDigest = new Map()
  let landSidebarSaveTimer = null
  let landSidebarMigrationPending = false
  let landDataGdbPaths = Array.isArray(store.config?.land?.dataGdbPaths)
    ? [...store.config.land.dataGdbPaths]
    : []
  /** @type {ReturnType<typeof createLandSourceEditor>|null} */
  let landSourceEditor = null

  function landSources() {
    return store.land.sources || []
  }

  function landSourceRecord(sourceId) {
    return landSources().find((source) => source.id === sourceId) || null
  }

  function sourceLayerSpecs(sourceId) {
    const source = landSourceRecord(sourceId)
    if (!source || !Array.isArray(source.layers)) return []
    return source.layers.map((raw) => normalizeRegisteredLayer(raw))
  }

  function resolveLandLayerSpec(sourceId, layerKey) {
    return sourceLayerSpecs(sourceId).find((spec) => spec.key === layerKey) || null
  }

  function landLayerHasLabels(sourceId, layerKey) {
    return landLayerHasLabelField(resolveLandLayerSpec(sourceId, layerKey))
  }

  function isLandLayerLabelsVisible(sourceId, layerKey) {
    if (!landLayerHasLabels(sourceId, layerKey)) return false
    const key = storeLandLayerKey(sourceId, layerKey)
    if (!store.land.labelsVisible.has(key)) return true
    return store.land.labelsVisible.get(key) === true
  }

  function orderedLandSourceIds() {
    const sidebar = store.land.sidebar || { folders: [], unfiledSources: [] }
    const ids = []
    for (const folder of sidebar.folders || []) {
      for (const sid of folder.sources || []) ids.push(sid)
    }
    for (const sid of sidebar.unfiledSources || []) ids.push(sid)
    return ids
  }

  function landLayerRows() {
    const rows = []
    for (const sourceId of orderedLandSourceIds()) {
      const source = landSourceRecord(sourceId)
      if (!source) continue
      for (const rawLayer of source.layers || []) {
        const spec = normalizeRegisteredLayer(rawLayer)
        rows.push({
          sourceId: source.id,
          label: source.label || source.id,
          path: source.path,
          layerKey: spec.key,
          spec,
        })
      }
    }
    return rows
  }

  function bumpLandPanel() {
    store.land.layerRows = landLayerRows()
    store.land.panelRevision += 1
  }

  function landOverlayPartIds() {
    const ids = []
    for (const src of landSources()) {
      const layers = Array.isArray(src.layers) ? src.layers : []
      if (layers.some((layer) => layer.role === 'include') && src.id) {
        ids.push(String(src.id))
      }
    }
    ids.sort()
    return ids
  }

  function clearLandLayerGeoJsonCacheForLayer(sourceId, layerKey) {
    const prefix = `layer|${sourceId}|${layerKey}`
    for (const key of [...landLayerGeoJsonCache.keys()]) {
      if (key === prefix || key.startsWith(`${prefix}|`)) {
        landLayerGeoJsonCache.delete(key)
        landLayerGeoJsonInflight.delete(key)
      }
    }
  }

  async function fetchLandLayerGeoJson(sourceId, layerKey, digest) {
    return fetchCachedGeoJson(
      landLayerGeoJsonCache,
      landLayerGeoJsonInflight,
      apiUrls.landLayerCacheKey(sourceId, layerKey, digest),
      async () => {
        const resp = await fetch(apiUrls.landLayerGeoJsonUrl(projectSlug, sourceId, layerKey))
        if (!resp.ok) throw new Error(`GeoJSON failed (${resp.status})`)
        const data = await resp.json()
        const respDigest = resp.headers.get('X-Peaky-Digest')
        if (respDigest && respDigest !== digest) {
          landLayerGeoJsonCache.set(
            apiUrls.landLayerCacheKey(sourceId, layerKey, respDigest),
            data,
          )
        }
        return data
      },
    )
  }

  async function fetchLandOverlayPartGeoJson(overlayId, partId) {
    const cacheKey = `${overlayId}|${partId}|${store.land.overlayDigest}`
    return fetchCachedGeoJson(
      landOverlayGeoJsonCache,
      landOverlayGeoJsonInflight,
      cacheKey,
      async () => {
        const resp = await fetch(apiUrls.landOverlayPartGeoJsonUrl(projectSlug, overlayId, partId))
        if (!resp.ok) throw new Error(`Overlay failed (${resp.status})`)
        const data = await resp.json()
        const respDigest = resp.headers.get('X-Peaky-Digest')
        if (respDigest) store.land.overlayDigest = respDigest
        return data
      },
    )
  }

  function syncLandMapLayerVisibilityOnMap(sourceId, layerKey) {
    const map = getMap()
    if (!map || !getMapReady()) return
    const visible = isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey)
    syncLandMapLayerVisibility(map, sourceId, layerKey, visible)
    syncLandMapLabelLayerOnMap(sourceId, layerKey)
  }

  function syncLandMapLabelLayerOnMap(sourceId, layerKey) {
    const map = getMap()
    if (!map || !getMapReady()) return
    const spec = resolveLandLayerSpec(sourceId, layerKey)
    syncLandMapLabelLayer(
      map,
      sourceId,
      layerKey,
      spec,
      isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey),
      isLandLayerLabelsVisible(sourceId, layerKey),
      landLayerHasLabels(sourceId, layerKey),
    )
  }

  async function ensureLandMapLayer(sourceId, layerKey, { force = false } = {}) {
    const map = getMap()
    if (!map || !getMapReady()) return
    const sourceMapId = landMapSourceId(sourceId, layerKey)
    const spec = resolveLandLayerSpec(sourceId, layerKey)
    const visible = isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey)
    if (map.getSource(sourceMapId) && !force) {
      applyLandMapLayerStyle(map, sourceId, layerKey, spec)
      syncLandMapLayerVisibility(map, sourceId, layerKey, visible)
      syncLandMapLabelLayerOnMap(sourceId, layerKey)
      return
    }
    if (force && map.getSource(sourceMapId)) {
      removeLandMapLayer(map, sourceId, layerKey)
    }
    try {
      const geojson = decorateLandGeoJsonProperties(
        await fetchLandLayerGeoJson(sourceId, layerKey, spec?.digest || ''),
        {
          labelField: spec?.labelField || '',
          styleField: spec?.styleField || '',
        },
      )
      addLandRegisteredLayer(
        map,
        sourceId,
        layerKey,
        geojson,
        spec,
        visible,
        viewshedLayerInsertBefore(),
      )
      syncLandMapLabelLayerOnMap(sourceId, layerKey)
      syncLandMapLayerOrderOnMap()
    } catch (_) {
      /* network */
    }
  }

  function syncLandMapLayerOrderOnMap() {
    const map = getMap()
    if (!map || !getMapReady()) return
    const rows = [
      ...landLayerRows(),
      ...LAND_OVERLAYS.flatMap((spec) =>
        landOverlayPartIds().map((partId) => ({
          sourceId: LAND_OVERLAY_SOURCE_ID,
          layerKey: `${spec.id}-${partId}`,
          spec,
        })),
      ),
    ]
    syncLandMapLayerOrder(map, rows, viewshedLayerInsertBefore(), raiseSiteLayers)
  }

  async function ensureLandOverlayLayer(overlayId, { force = false } = {}) {
    const map = getMap()
    if (!map || !getMapReady() || !isLandOverlayVisibleStore(store, overlayId)) return
    const spec = LAND_OVERLAYS.find((row) => row.id === overlayId)
    if (!spec) return
    const partIds = landOverlayPartIds()
    const needsFetch =
      force ||
      landOverlayLoadedDigest.get(overlayId) !== store.land.overlayDigest ||
      landOverlayPartMapIdsNeedFetch(overlayId, partIds)
    if (!needsFetch) {
      syncLandOverlayVisibilityOnMap(overlayId, partIds)
      return
    }
    setLandRowLoading(store, landOverlayLayerKey(overlayId), true)
    try {
      if (!partIds.length) {
        const resp = await fetch(apiUrls.landOverlayGeoJsonUrl(projectSlug, overlayId))
        if (!resp.ok) throw new Error(`Overlay failed (${resp.status})`)
        addLandOverlayPartLayer(
          map,
          overlayId,
          '',
          await resp.json(),
          spec,
          viewshedLayerInsertBefore(),
        )
      } else {
        await Promise.all(
          partIds.map(async (partId) => {
            const geojson = await fetchLandOverlayPartGeoJson(overlayId, partId)
            addLandOverlayPartLayer(
              map,
              overlayId,
              partId,
              geojson,
              spec,
              viewshedLayerInsertBefore(),
            )
            syncLandOverlayVisibilityOnMap(overlayId, partIds)
          }),
        )
      }
      landOverlayLoadedDigest.set(overlayId, store.land.overlayDigest)
      syncLandOverlayVisibilityOnMap(overlayId, partIds)
      syncLandMapLayerOrderOnMap()
    } catch (err) {
      console.error(`land overlay ${overlayId} failed`, err)
    } finally {
      setLandRowLoading(store, landOverlayLayerKey(overlayId), false)
    }
  }

  function landOverlayPartMapIdsNeedFetch(overlayId, partIds) {
    const map = getMap()
    if (!map) return true
    const ids = partIds.length ? partIds : ['']
    return ids.some((partId) => {
      const layerKey = partId ? `${overlayId}-${partId}` : overlayId
      const sourceMapId = landMapSourceId(LAND_OVERLAY_SOURCE_ID, layerKey)
      return !map.getSource(sourceMapId)
    })
  }

  function syncLandOverlayVisibilityOnMap(overlayId, partIds) {
    const map = getMap()
    if (!map || !getMapReady()) return
    syncLandOverlayVisibility(
      map,
      overlayId,
      partIds,
      isLandOverlayVisibleStore(store, overlayId),
    )
  }

  async function refreshLandMapLayer(sourceId, layerKey) {
    if (!getMapReady()) return
    if (!isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey)) return
    setLandRowLoading(store, storeLandLayerKey(sourceId, layerKey), true)
    try {
      clearLandLayerGeoJsonCacheForLayer(sourceId, layerKey)
      await ensureLandMapLayer(sourceId, layerKey, { force: true })
    } finally {
      setLandRowLoading(store, storeLandLayerKey(sourceId, layerKey), false)
    }
  }

  async function refreshLandMapLayers() {
    await Promise.all([
      ...landLayerRows()
        .filter((row) => isLandLayerEffectivelyVisibleStore(store, row.sourceId, row.layerKey))
        .map((row) => ensureLandMapLayer(row.sourceId, row.layerKey)),
      ...LAND_OVERLAYS.filter((row) => isLandOverlayVisibleStore(store, row.id)).map((row) =>
        ensureLandOverlayLayer(row.id, { force: false }),
      ),
    ])
    syncLandMapLayerOrderOnMap()
  }

  function schedulePersistLandSidebar() {
    if (landSidebarSaveTimer) clearTimeout(landSidebarSaveTimer)
    landSidebarSaveTimer = setTimeout(() => {
      landSidebarSaveTimer = null
      void persistLandSidebar()
    }, LAND_SIDEBAR_SAVE_MS)
  }

  async function persistLandSidebar({ refreshMap = false } = {}) {
    store.land.sidebar = syncLandSidebarWithSources(
      cloneLandSidebar(store.land.sidebar),
      landSources(),
    )
    try {
      const resp = await fetch(apiUrls.landSidebarApiUrl(projectSlug), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(store.land.sidebar),
      })
      const payload = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        window.alert(payload.error || `Save sidebar failed (${resp.status})`)
        return false
      }
      if (payload.sidebar) {
        store.land.sidebar = syncLandSidebarWithSources(
          normalizeLandSidebarInput(payload.sidebar),
          landSources(),
        )
      }
      bumpLandPanel()
      if (refreshMap) await refreshLandMapLayers()
      if (onSidebarPersisted) onSidebarPersisted()
      return true
    } catch (_) {
      window.alert('Could not reach server.')
      return false
    }
  }

  /** @param {object} raw */
  function normalizeLandSidebarInput(raw) {
    const folders = Array.isArray(raw?.folders)
      ? raw.folders
          .map((folder) => ({
            id: String(folder?.id || '').trim(),
            label: String(folder?.label || folder?.id || '').trim(),
            sources: Array.isArray(folder?.sources)
              ? folder.sources.map((sid) => String(sid).trim()).filter(Boolean)
              : [],
          }))
          .filter((folder) => folder.id)
      : []
    const unfiledSources = Array.isArray(raw?.unfiledSources)
      ? raw.unfiledSources.map((sid) => String(sid).trim()).filter(Boolean)
      : Array.isArray(raw?.unfiled_sources)
        ? raw.unfiled_sources.map((sid) => String(sid).trim()).filter(Boolean)
        : []
    return { folders, unfiledSources }
  }

  function applyLandSidebarMutation(mutator) {
    applyLandSidebarMutationStore(store, mutator, {
      onMutated: () => {
        syncLandMapLayerOrderOnMap()
        schedulePersistLandSidebar()
      },
    })
  }

  function setLandLayerVisible(sourceId, layerKey, visible) {
    setLandLayerVisibleStore(store, storeLandLayerKey(sourceId, layerKey), visible)
    scheduleSaveMapState()
    syncLandMapLayerVisibilityOnMap(sourceId, layerKey)
  }

  function setLandSourceBatchVisible(sourceId, visible) {
    setLandSourceBatchVisibleStore(store, sourceId, visible)
    scheduleSaveMapState()
    for (const spec of sourceLayerSpecs(sourceId)) {
      syncLandMapLayerVisibilityOnMap(sourceId, spec.key)
      syncLandMapLabelLayerOnMap(sourceId, spec.key)
    }
  }

  function setLandLayerLabelsVisible(sourceId, layerKey, visible) {
    setLandLabelsVisibleStore(store, storeLandLayerKey(sourceId, layerKey), visible)
    scheduleSaveMapState()
    syncLandMapLabelLayerOnMap(sourceId, layerKey)
  }

  function setLandOverlayVisible(overlayId, visible) {
    store.land.visible.set(landOverlayLayerKey(overlayId), !!visible)
    bumpLandPanel()
    scheduleSaveMapState()
    syncLandOverlayVisibilityOnMap(overlayId, landOverlayPartIds())
  }

  function toggleLandLayerVisible(sourceId, layerKey) {
    const next = !isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey)
    setLandLayerVisible(sourceId, layerKey, next)
    if (next) void ensureLandMapLayer(sourceId, layerKey)
  }

  function toggleLandOverlayVisible(overlayId) {
    const next = !isLandOverlayVisibleStore(store, overlayId)
    setLandOverlayVisible(overlayId, next)
    if (next) void ensureLandOverlayLayer(overlayId)
  }

  function toggleLandSourceLayers(sourceId) {
    toggleLandSourceLayersStore(store, sourceId, sourceLayerSpecs(sourceId).map((s) => s.key), {
      onToggle: (show) => {
        if (show) {
          for (const spec of sourceLayerSpecs(sourceId)) {
            void ensureLandMapLayer(sourceId, spec.key)
          }
        } else {
          for (const spec of sourceLayerSpecs(sourceId)) {
            syncLandMapLayerVisibilityOnMap(sourceId, spec.key)
          }
        }
      },
    })
    scheduleSaveMapState()
  }

  function setLandFolderVisible(folderId, visible) {
    const folder = (store.land.sidebar?.folders || []).find((f) => f.id === folderId)
    if (!folder) return
    const layerKeysBySource = {}
    for (const sourceId of folder.sources || []) {
      layerKeysBySource[sourceId] = sourceLayerSpecs(sourceId).map((s) => s.key)
    }
    setLandFolderVisibleStore(store, folderId, visible, {
      sourceIds: folder.sources || [],
      layerKeysBySource,
      onLayer: (sourceId, layerKey, vis) => {
        syncLandMapLayerVisibilityOnMap(sourceId, layerKey)
        if (vis) void ensureLandMapLayer(sourceId, layerKey)
      },
    })
    scheduleSaveMapState()
  }

  function setLandFolderLabelsVisible(folderId, visible) {
    const folder = (store.land.sidebar?.folders || []).find((f) => f.id === folderId)
    if (!folder) return
    for (const sourceId of folder.sources || []) {
      for (const spec of sourceLayerSpecs(sourceId)) {
        if (landLayerHasLabels(sourceId, spec.key)) {
          setLandLayerLabelsVisible(sourceId, spec.key, visible)
        }
      }
    }
  }

  function revealLandLayerOnMap(sourceId, layerKey) {
    setLandSourceBatchVisible(sourceId, true)
    setLandLayerVisible(sourceId, layerKey, true)
    void ensureLandMapLayer(sourceId, layerKey)
  }

  function applySourcesFromApi(payload) {
    store.land.sources = Array.isArray(payload.sources) ? payload.sources : []
    if (payload.sidebar) {
      store.land.sidebar = syncLandSidebarWithSources(
        normalizeLandSidebarInput(payload.sidebar),
        store.land.sources,
      )
    } else {
      store.land.sidebar = syncLandSidebarWithSources(
        cloneLandSidebar(store.land.sidebar),
        store.land.sources,
      )
    }
    if (typeof payload.aoiDigest === 'string') store.land.aoiDigest = payload.aoiDigest
    if (typeof payload.overlayDigest === 'string') store.land.overlayDigest = payload.overlayDigest
    bumpLandPanel()
  }

  async function fetchLandPreviewGeoJson(path, layer, layerConfig) {
    const cacheKey = `${apiUrls.landPreviewCacheKey(path, layer)}|${JSON.stringify(layerConfig || {})}`
    return fetchCachedGeoJson(
      landPreviewGeoJsonCache,
      landPreviewGeoJsonInflight,
      cacheKey,
      async () => {
        if (
          layerConfig &&
          (layerConfig.include?.length ||
            layerConfig.exclude?.length ||
            layerConfig.styleField ||
            layerConfig.labelField)
        ) {
          const body = {
            path,
            layer,
            include: layerConfig.include,
            exclude: layerConfig.exclude,
            labelField: layerConfig.labelField || undefined,
            styleField: layerConfig.styleField || undefined,
            role: layerConfig.role || undefined,
          }
          const resp = await fetch(apiUrls.landPreviewGeoJsonPostUrl(projectSlug), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          })
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok || !payload.geojson) {
            throw new Error(payload.error || `Preview failed (${resp.status})`)
          }
          return decorateLandGeoJsonProperties(payload.geojson, layerConfig || {})
        }
        const resp = await fetch(apiUrls.landPreviewGeoJsonUrl(projectSlug, path, layer))
        const payload = await resp.json().catch(() => ({}))
        if (!resp.ok || !payload.geojson) {
          throw new Error(payload.error || `Preview failed (${resp.status})`)
        }
        return decorateLandGeoJsonProperties(payload.geojson, layerConfig || {})
      },
    )
  }

  async function reloadClippedLandLayers() {
    const rows = landLayerRows().filter((row) => {
      if (row.spec.role === 'aoi') return false
      return isLandLayerEffectivelyVisibleStore(store, row.sourceId, row.layerKey)
    })
    await Promise.all(rows.map((row) => refreshLandMapLayer(row.sourceId, row.layerKey)))
  }

  async function refreshVisibleLandOverlays({ force = false } = {}) {
    await Promise.all(
      LAND_OVERLAYS.filter((row) => isLandOverlayVisibleStore(store, row.id)).map((row) =>
        ensureLandOverlayLayer(row.id, { force }),
      ),
    )
  }

  async function reloadLandSources({ refreshMap = true } = {}) {
    const prevAoiDigest = store.land.aoiDigest
    const prevOverlayDigest = store.land.overlayDigest
    try {
      const resp = await fetch(apiUrls.landApiUrl(projectSlug))
      const payload = await resp.json().catch(() => ({}))
      if (!resp.ok) return
      applySourcesFromApi(payload)
      if (!refreshMap) return
      const aoiChanged = store.land.aoiDigest !== prevAoiDigest
      const overlayChanged = store.land.overlayDigest !== prevOverlayDigest
      if (aoiChanged || overlayChanged) {
        landOverlayGeoJsonCache.clear()
        landOverlayGeoJsonInflight.clear()
        landOverlayLoadedDigest.clear()
        for (const spec of LAND_OVERLAYS) {
          removeLandOverlayFromMap(spec.id)
        }
      }
      if (aoiChanged) {
        landLayerGeoJsonCache.clear()
        landLayerGeoJsonInflight.clear()
        await reloadClippedLandLayers()
        await refreshVisibleLandOverlays({ force: true })
      } else {
        await refreshLandMapLayers()
      }
    } catch (_) {
      /* network */
    }
  }

  function removeLandOverlayFromMap(overlayId) {
    const map = getMap()
    if (!map) return
    removeLandOverlayLayer(map, overlayId, landOverlayPartIds())
  }

  async function deleteLandSource(sourceId) {
    if (!window.confirm(`Remove land source "${sourceId}"?`)) return
    const prevAoiDigest = store.land.aoiDigest
    try {
      const resp = await fetch(apiUrls.landSourceApiUrl(projectSlug, sourceId), {
        method: 'DELETE',
      })
      const payload = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        window.alert(payload.error || `Delete failed (${resp.status})`)
        return
      }
      const source = landSourceRecord(sourceId)
      const map = getMap()
      if (source && Array.isArray(source.layers)) {
        for (const rawLayer of source.layers) {
          const spec = normalizeRegisteredLayer(rawLayer)
          if (map) removeLandMapLayer(map, sourceId, spec.key)
          store.land.visible.delete(storeLandLayerKey(sourceId, spec.key))
          store.land.sourceBatchVisible.delete(sourceId)
          store.land.labelsVisible.delete(storeLandLayerKey(sourceId, spec.key))
          clearLandLayerGeoJsonCacheForLayer(sourceId, spec.key)
        }
      }
      await reloadLandSources({ refreshMap: false })
      if (store.land.aoiDigest !== prevAoiDigest) {
        landLayerGeoJsonCache.clear()
        landLayerGeoJsonInflight.clear()
        await reloadClippedLandLayers()
      }
      scheduleSaveMapState()
    } catch (_) {
      window.alert('Could not reach server.')
    }
  }

  async function handleLandSourceImportSaved(payload) {
    const prevAoiDigest = store.land.aoiDigest
    await reloadLandSources({ refreshMap: false })
    const source = payload?.source
    if (source?.id && Array.isArray(source.layers)) {
      for (const rawLayer of source.layers) {
        const spec = normalizeRegisteredLayer(rawLayer)
        setLandLayerVisible(source.id, spec.key, true)
      }
      setLandSourceBatchVisible(source.id, true)
      if (store.land.aoiDigest !== prevAoiDigest) {
        landLayerGeoJsonCache.clear()
        landLayerGeoJsonInflight.clear()
        await reloadClippedLandLayers()
      } else {
        await Promise.all(
          source.layers.map((rawLayer) => {
            const spec = normalizeRegisteredLayer(rawLayer)
            return refreshLandMapLayer(source.id, spec.key)
          }),
        )
      }
    } else {
      await reloadLandSources()
    }
    scheduleSaveMapState()
  }

  async function handleLandSourceEditSaved(sourceId, layers, payload) {
    const prevAoiDigest = store.land.aoiDigest
    const prevSource = landSourceRecord(sourceId)
    const prevKeys = new Set(
      (prevSource?.layers || []).map((layer) => normalizeRegisteredLayer(layer).key),
    )
    const updated = payload?.source || { id: sourceId, layers: layers || [] }
    const newKeys = new Set(
      (updated.layers || []).map((layer) => normalizeRegisteredLayer(layer).key),
    )
    const map = getMap()
    for (const key of prevKeys) {
      if (!newKeys.has(key)) {
        if (map) removeLandMapLayer(map, sourceId, key)
        store.land.visible.delete(storeLandLayerKey(sourceId, key))
        store.land.labelsVisible.delete(storeLandLayerKey(sourceId, key))
        clearLandLayerGeoJsonCacheForLayer(sourceId, key)
      }
    }
    for (const key of newKeys) {
      if (!prevKeys.has(key)) setLandLayerVisible(sourceId, key, true)
    }
    await reloadLandSources({ refreshMap: false })
    if (store.land.aoiDigest !== prevAoiDigest) {
      landLayerGeoJsonCache.clear()
      landLayerGeoJsonInflight.clear()
      await reloadClippedLandLayers()
    } else {
      await Promise.all([...newKeys].map((key) => refreshLandMapLayer(sourceId, key)))
    }
    scheduleSaveMapState()
  }

  function createLandFolder(label) {
    const trimmed = String(label || '').trim()
    if (!trimmed) return { ok: false, error: 'Name is required.' }
    const id = slugifyLandFolderId(trimmed, store.land.sidebar?.folders || [])
    applyLandSidebarMutation((sidebar) => {
      sidebar.folders.push({ id, label: trimmed, sources: [] })
    })
    return { ok: true }
  }

  function renameLandFolder(folderId, label) {
    const trimmed = String(label || '').trim()
    if (!trimmed) return { ok: false, error: 'Name is required.' }
    const folder = (store.land.sidebar?.folders || []).find((item) => item.id === folderId)
    if (!folder) return { ok: false, error: 'Folder not found.' }
    if (trimmed === folder.label) return { ok: true }
    applyLandSidebarMutation((sidebar) => {
      const target = sidebar.folders.find((item) => item.id === folderId)
      if (target) target.label = trimmed
    })
    return { ok: true }
  }

  function deleteLandFolder(folderId) {
    const folder = (store.land.sidebar?.folders || []).find((item) => item.id === folderId)
    if (!folder) return
    if (
      !window.confirm(`Delete folder "${folder.label}"? Sources will move to Unfiled.`)
    ) {
      return
    }
    applyLandSidebarMutation((sidebar) => {
      const idx = sidebar.folders.findIndex((item) => item.id === folderId)
      if (idx < 0) return
      const [removed] = sidebar.folders.splice(idx, 1)
      sidebar.unfiledSources.push(...(removed.sources || []))
    })
  }

  function openLandFolderModal(mode, folderId = null) {
    store.land.folderModal = {
      open: true,
      mode: mode === 'rename' ? 'rename' : 'create',
      folderId,
      name:
        mode === 'rename'
          ? (store.land.sidebar?.folders || []).find((f) => f.id === folderId)?.label || ''
          : '',
      error: '',
    }
  }

  function closeLandFolderModal() {
    store.land.folderModal = {
      open: false,
      mode: 'create',
      folderId: null,
      name: '',
      error: '',
    }
  }

  function saveLandFolderModal() {
    const modal = store.land.folderModal || {}
    const result =
      modal.mode === 'rename'
        ? renameLandFolder(modal.folderId, modal.name)
        : createLandFolder(modal.name)
    if (!result.ok) {
      store.land.folderModal = { ...modal, error: result.error || 'Save failed.' }
      return false
    }
    closeLandFolderModal()
    return true
  }

  function installLandSourceEditor() {
    if (landSourceEditor) return landSourceEditor
    landSourceEditor = createLandSourceEditor({
      elements: {
        modal: document.getElementById('land-source-modal'),
        errorEl: document.getElementById('land-source-error'),
        fileSelect: document.getElementById('land-source-file'),
        fileField: document.getElementById('land-source-file-field'),
        fileStatusEl: document.getElementById('land-source-file-status'),
        pathEl: document.getElementById('land-source-path'),
        labelInput: document.getElementById('land-source-label'),
        editorBody: document.getElementById('land-source-editor-body'),
        mapSection: document.getElementById('land-source-map-section'),
        previewMapEl: document.getElementById('land-source-preview-map'),
        rulesListEl: document.getElementById('land-source-rules-list'),
        rulesCountEl: document.getElementById('land-source-rules-count'),
        addRuleBtn: document.getElementById('land-source-add-rule'),
        saveBtn: document.getElementById('land-source-save'),
      },
      api: {
        fetchDataFiles: async () => {
          if (landDataGdbPaths.length) return landDataGdbPaths
          const resp = await fetch(apiUrls.landDataGdbsUrl(projectSlug))
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || 'Could not load file list')
          landDataGdbPaths = Array.isArray(payload.paths) ? payload.paths : []
          return landDataGdbPaths
        },
        fetchPreview: async (path) => {
          const resp = await fetch(apiUrls.landImportPreviewApiUrl(projectSlug), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path }),
          })
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || `Preview failed (${resp.status})`)
          return payload
        },
        fetchFields: async (path, layer) => {
          const resp = await fetch(apiUrls.landFieldsApiUrl(projectSlug, path, layer))
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || `Fields failed (${resp.status})`)
          return payload
        },
        fetchValues: async (path, layer, field) => {
          const resp = await fetch(apiUrls.landValuesApiUrl(projectSlug, path, layer, field))
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || `Values failed (${resp.status})`)
          return payload
        },
        fetchPreviewGeoJson: (path, layer, layerConfig) =>
          fetchLandPreviewGeoJson(path, layer, layerConfig),
        postImport: async (body) => {
          const resp = await fetch(apiUrls.landImportApiUrl(projectSlug), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          })
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || `Import failed (${resp.status})`)
          await handleLandSourceImportSaved(payload)
          return payload
        },
        patchSource: async (sourceId, body) => {
          const resp = await fetch(apiUrls.landSourceApiUrl(projectSlug, sourceId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          })
          const payload = await resp.json().catch(() => ({}))
          if (!resp.ok) throw new Error(payload.error || `Save failed (${resp.status})`)
          await handleLandSourceEditSaved(sourceId, body.layers, payload)
          return payload
        },
      },
      callbacks: {
        getSource: (sourceId) => landSourceRecord(sourceId),
        onImportOpen: () => {
          setAddPlacementMode?.(null)
          setEntityPanelOpen?.(true)
          setEntityTab?.('land')
        },
        onSaved: async () => {
          bumpLandPanel()
        },
      },
      utils: {
        normalizeRegisteredLayer,
        flatStyleFromLayerSpec,
        basemapStyle,
        currentBasemapKey: () => getBasemapKey?.() || 'street',
        mainMapCenter: () => getMapCenter?.(),
        mainMapZoom: () => getMapZoom?.(),
        openWaDialog,
      },
    })
    return landSourceEditor
  }

  return {
    normalizeRegisteredLayer,
    landSourceRecord,
    landLayerRows,
    landLayerHasLabels,
    isLandLayerEffectivelyVisible: (sourceId, layerKey) =>
      isLandLayerEffectivelyVisibleStore(store, sourceId, layerKey),
    isLandOverlayVisible: (overlayId) => isLandOverlayVisibleStore(store, overlayId),
    isLandLayerLabelsVisible,
    ensureLandMapLayer,
    ensureLandOverlayLayer,
    refreshLandMapLayer,
    refreshLandMapLayers,
    syncLandMapLayerOrder: syncLandMapLayerOrderOnMap,
    setLandLayerVisible,
    setLandSourceBatchVisible,
    setLandLayerLabelsVisible,
    setLandFolderCollapsed: (folderId, collapsed) => {
      setLandFolderCollapsedStore(store, folderId, collapsed)
      scheduleSaveMapState()
    },
    setLandFolderVisible,
    setLandFolderLabelsVisible,
    toggleLandLayerVisible,
    toggleLandOverlayVisible,
    toggleLandSourceLayers,
    revealLandLayerOnMap,
    applyLandSidebarMutation,
    persistLandSidebar,
    schedulePersistLandSidebar,
    clearAllLandLayerGeoJsonCache: () => {
      landLayerGeoJsonCache.clear()
      landLayerGeoJsonInflight.clear()
    },
    clearLandOverlayGeoJsonCache: () => {
      landOverlayGeoJsonCache.clear()
      landOverlayGeoJsonInflight.clear()
      landOverlayLoadedDigest.clear()
    },
    removeLandOverlayFromMap,
    normalizeLandSidebarInput,
    landSidebarMigrationPending: () => landSidebarMigrationPending,
    setLandSidebarMigrationPending: (v) => {
      landSidebarMigrationPending = v
    },
    reloadLandSources,
    deleteLandSource,
    createLandFolder,
    renameLandFolder,
    deleteLandFolder,
    openLandFolderModal,
    closeLandFolderModal,
    saveLandFolderModal,
    installLandSourceEditor,
    installChrome() {
      document.getElementById('entity-panel-import-land')?.addEventListener('click', () => {
        void installLandSourceEditor().openImport()
      })
      document.getElementById('entity-panel-add-land-folder')?.addEventListener('click', () => {
        openLandFolderModal('create')
      })
    },
    bumpLandPanel,
    openLandSourceEditor: (sourceId) => void installLandSourceEditor().openEdit(sourceId),
    openLandSourceImport: () => void installLandSourceEditor().openImport(),
    reorderLandFolder: (fromFolderId, beforeFolderId) => {
      if (!fromFolderId || fromFolderId === beforeFolderId) return
      applyLandSidebarMutation((sidebar) => {
        const fromIdx = sidebar.folders.findIndex((folder) => folder.id === fromFolderId)
        const toIdx = sidebar.folders.findIndex((folder) => folder.id === beforeFolderId)
        if (fromIdx < 0 || toIdx < 0) return
        const [folder] = sidebar.folders.splice(fromIdx, 1)
        sidebar.folders.splice(toIdx, 0, folder)
      })
    },
    moveLandSource: (sourceId, { folderId = null, beforeSourceId = null } = {}) => {
      if (!sourceId) return
      applyLandSidebarMutation((sidebar) => {
        for (const folder of sidebar.folders) {
          folder.sources = (folder.sources || []).filter((sid) => sid !== sourceId)
        }
        sidebar.unfiledSources = (sidebar.unfiledSources || []).filter((sid) => sid !== sourceId)
        if (folderId) {
          const folder = sidebar.folders.find((item) => item.id === folderId)
          if (!folder) return
          if (beforeSourceId) {
            const idx = folder.sources.indexOf(beforeSourceId)
            folder.sources.splice(idx >= 0 ? idx : folder.sources.length, 0, sourceId)
          } else {
            folder.sources.push(sourceId)
          }
          return
        }
        if (beforeSourceId) {
          const idx = sidebar.unfiledSources.indexOf(beforeSourceId)
          sidebar.unfiledSources.splice(idx >= 0 ? idx : sidebar.unfiledSources.length, 0, sourceId)
        } else {
          sidebar.unfiledSources.push(sourceId)
        }
      })
    },
  }
}
