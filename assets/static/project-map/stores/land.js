// @ts-check

import { landOverlayLayerKey } from '../map/land-overlays.js'

/** @param {string} sourceId @param {string} layer */
export function landLayerKey(sourceId, layer) {
  return `${sourceId}/${layer}`
}

/** @param {object} store */
function bumpLandPanel(store) {
  store.land.panelRevision = (store.land.panelRevision || 0) + 1
}

/**
 * @param {{ folders?: object[], unfiledSources?: string[] }} sidebar
 */
export function cloneLandSidebar(sidebar) {
  const folders = Array.isArray(sidebar?.folders) ? sidebar.folders : []
  const unfiledSources = Array.isArray(sidebar?.unfiledSources)
    ? sidebar.unfiledSources
    : []
  return {
    folders: folders.map((folder) => ({
      id: folder.id,
      label: folder.label,
      sources: [...(folder.sources || [])],
    })),
    unfiledSources: [...unfiledSources],
  }
}

/**
 * @param {object} sidebar
 * @param {{ id: string }[]} sources
 */
export function syncLandSidebarWithSources(sidebar, sources) {
  const valid = new Set((sources || []).map((s) => s.id))
  const assigned = new Set()
  const folders = []
  for (const folder of sidebar.folders || []) {
    const folderSources = (folder.sources || []).filter(
      (sid) => valid.has(sid) && !assigned.has(sid)
    )
    folderSources.forEach((sid) => assigned.add(sid))
    folders.push({ id: folder.id, label: folder.label, sources: folderSources })
  }
  const unfiledSources = (sidebar.unfiledSources || []).filter(
    (sid) => valid.has(sid) && !assigned.has(sid)
  )
  unfiledSources.forEach((sid) => assigned.add(sid))
  for (const source of sources || []) {
    if (!assigned.has(source.id)) unfiledSources.push(source.id)
  }
  return { folders, unfiledSources }
}

/** @param {object} store @param {object} sidebar */
export function setLandSidebar(store, sidebar) {
  store.land.sidebar = syncLandSidebarWithSources(
    cloneLandSidebar(sidebar),
    store.land.sources || []
  )
  bumpLandPanel(store)
}

/** @param {object} store @param {string} key @param {boolean} visible */
export function setLandLayerVisible(store, key, visible) {
  store.land.visible.set(key, !!visible)
  bumpLandPanel(store)
}

/** @param {object} store @param {string} sourceId @param {boolean} visible */
export function setLandSourceBatchVisible(store, sourceId, visible) {
  store.land.sourceBatchVisible.set(sourceId, !!visible)
  bumpLandPanel(store)
}

/** @param {object} store @param {string} key @param {boolean} visible */
export function setLandLabelsVisible(store, key, visible) {
  store.land.labelsVisible.set(key, !!visible)
  bumpLandPanel(store)
}

/** @param {object} store @param {string} folderId @param {boolean} collapsed */
export function setLandFolderCollapsed(store, folderId, collapsed) {
  store.land.foldersCollapsed.set(folderId, !!collapsed)
  bumpLandPanel(store)
}

/** @param {object} store @param {string} key @param {boolean} loading */
export function setLandRowLoading(store, key, loading) {
  if (!store.land.loadingKeys) store.land.loadingKeys = new Set()
  if (loading) store.land.loadingKeys.add(key)
  else store.land.loadingKeys.delete(key)
  bumpLandPanel(store)
}

/**
 * @param {object} store
 * @param {string} sourceId
 * @param {string} layer
 */
export function isLandLayerVisible(store, sourceId, layer) {
  const key = landLayerKey(sourceId, layer)
  if (!store.land.visible.has(key)) return false
  return store.land.visible.get(key) === true
}

/** @param {object} store @param {string} sourceId */
export function isLandSourceBatchVisible(store, sourceId) {
  if (!store.land.sourceBatchVisible.has(sourceId)) return true
  return store.land.sourceBatchVisible.get(sourceId) === true
}

/**
 * @param {object} store
 * @param {string} sourceId
 * @param {string} layer
 */
export function isLandLayerEffectivelyVisible(store, sourceId, layer) {
  if (!isLandSourceBatchVisible(store, sourceId)) return false
  return isLandLayerVisible(store, sourceId, layer)
}

/** @param {object} store @param {string} overlayId */
export function isLandOverlayVisible(store, overlayId) {
  return store.land.visible.get(landOverlayLayerKey(overlayId)) === true
}

/**
 * @param {object} store
 * @param {string} sourceId
 * @param {string[]} layerKeys
 */
export function isLandSourceEffectivelyVisible(store, sourceId, layerKeys) {
  if (!layerKeys.length) return isLandSourceBatchVisible(store, sourceId)
  return layerKeys.every((key) => isLandLayerEffectivelyVisible(store, sourceId, key))
}

/**
 * Toggle all layers for a source. Updates store, then calls `onToggle(show)`.
 * @param {object} store
 * @param {string} sourceId
 * @param {string[]} layerKeys
 * @param {{ onToggle?: (show: boolean) => void }} [opts]
 */
export function toggleLandSourceLayers(store, sourceId, layerKeys, opts = {}) {
  const show = !isLandSourceEffectivelyVisible(store, sourceId, layerKeys)
  setLandSourceBatchVisible(store, sourceId, show)
  for (const key of layerKeys) {
    setLandLayerVisible(store, landLayerKey(sourceId, key), show)
  }
  opts.onToggle?.(show)
  return show
}

/**
 * Set every layer under a folder visible / hidden.
 * @param {object} store
 * @param {string} folderId
 * @param {boolean} visible
 * @param {{
 *   sourceIds: string[],
 *   layerKeysBySource: Record<string, string[]>,
 *   onLayer?: (sourceId: string, layerKey: string, visible: boolean) => void,
 * }} opts
 */
export function setLandFolderVisible(store, folderId, visible, opts) {
  void folderId
  for (const sourceId of opts.sourceIds || []) {
    setLandSourceBatchVisible(store, sourceId, visible)
    for (const layerKey of opts.layerKeysBySource?.[sourceId] || []) {
      setLandLayerVisible(store, landLayerKey(sourceId, layerKey), visible)
      opts.onLayer?.(sourceId, layerKey, visible)
    }
  }
}

/**
 * Clone sidebar, run mutator, write back, then optional callbacks (map order / persist).
 * @param {object} store
 * @param {(sidebar: object) => void} mutator
 * @param {{ onMutated?: (sidebar: object) => void }} [opts]
 */
export function applyLandSidebarMutation(store, mutator, opts = {}) {
  const next = cloneLandSidebar(store.land.sidebar)
  mutator(next)
  store.land.sidebar = syncLandSidebarWithSources(next, store.land.sources || [])
  bumpLandPanel(store)
  opts.onMutated?.(store.land.sidebar)
  return store.land.sidebar
}

/**
 * Copy imperative Maps into the reactive store (for Vue panel reads).
 * @param {object} store
 * @param {{
 *   visible?: Map<string, boolean>,
 *   sourceBatchVisible?: Map<string, boolean>,
 *   labelsVisible?: Map<string, boolean>,
 *   foldersCollapsed?: Map<string, boolean>,
 * }} maps
 */
export function syncLandVisibilityMaps(store, maps) {
  if (maps.visible) {
    store.land.visible.clear()
    for (const [k, v] of maps.visible) store.land.visible.set(k, !!v)
  }
  if (maps.sourceBatchVisible) {
    store.land.sourceBatchVisible.clear()
    for (const [k, v] of maps.sourceBatchVisible) {
      store.land.sourceBatchVisible.set(k, !!v)
    }
  }
  if (maps.labelsVisible) {
    store.land.labelsVisible.clear()
    for (const [k, v] of maps.labelsVisible) store.land.labelsVisible.set(k, !!v)
  }
  if (maps.foldersCollapsed) {
    store.land.foldersCollapsed.clear()
    for (const [k, v] of maps.foldersCollapsed) {
      store.land.foldersCollapsed.set(k, !!v)
    }
  }
}
