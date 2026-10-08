// @ts-check

import * as apiUrls from '../api/urls.js'
import {
  ensureAccessNetworkLayers,
  setAccessNetworkLayerData,
  syncAccessNetworkVisibility,
} from '../map/access-network-layers.js'

const VIEW_PAD_DEG = 0.04
/** Refetch when the view moves this far past the last fetch bbox (degrees). */
const REFETCH_MARGIN_DEG = 0.015
const MOVE_DEBOUNCE_MS = 350

/** Cache key. Generalization changes on each integer zoom. */
function networkZoomTier(zoom) {
  return Math.round(zoom)
}

/**
 * Paved / jeep / hiking routing network overlays (toolbar toggles).
 * @param {object} opts
 */
export function createAccessNetworkDomain(opts) {
  const { store, projectSlug, getMap, getMapReady, raiseSiteLayers, scheduleSaveMapState } = opts

  /** @type {Record<'paved'|'jeep'|'hiking', object|null>} */
  const layerCache = { paved: null, jeep: null, hiking: null }
  /** @type {Record<'paved'|'jeep'|'hiking', { west: number, south: number, east: number, north: number }|null>} */
  const layerFetchBBox = { paved: null, jeep: null, hiking: null }
  /** @type {Record<'paved'|'jeep'|'hiking', number|null>} last fetch zoom key */
  const layerFetchTier = { paved: null, jeep: null, hiking: null }
  /** @type {Record<'paved'|'jeep'|'hiking', Promise<void>|null>} */
  const inflight = { paved: null, jeep: null, hiking: null }
  /** @type {Promise<void>|null} */
  let driveInflight = null
  let moveHookInstalled = false
  let moveDebounceTimer = 0
  let layersRaised = false

  function visState() {
    return store.accessNetwork || { pavedVisible: false, jeepVisible: false, hikingVisible: false }
  }

  function anyVisible() {
    const v = visState()
    return v.pavedVisible || v.jeepVisible || v.hikingVisible
  }

  function viewBBox() {
    const map = getMap()
    if (!map) return null
    const b = map.getBounds()
    return {
      west: b.getWest() - VIEW_PAD_DEG,
      south: b.getSouth() - VIEW_PAD_DEG,
      east: b.getEast() + VIEW_PAD_DEG,
      north: b.getNorth() + VIEW_PAD_DEG,
    }
  }

  /**
   * @param {{ west: number, south: number, east: number, north: number }} outer
   * @param {{ west: number, south: number, east: number, north: number }} inner
   */
  function bboxContains(outer, inner) {
    const m = REFETCH_MARGIN_DEG
    return (
      inner.west >= outer.west + m &&
      inner.east <= outer.east - m &&
      inner.south >= outer.south + m &&
      inner.north <= outer.north - m
    )
  }

  function currentZoomTier() {
    const map = getMap()
    if (!map) return null
    return networkZoomTier(map.getZoom())
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   */
  function needsRefetch(kind) {
    const current = viewBBox()
    const fetched = layerFetchBBox[kind]
    const tier = currentZoomTier()
    if (!current || !fetched || !layerCache[kind] || tier == null) return true
    if (layerFetchTier[kind] !== tier) return true
    return !bboxContains(fetched, current)
  }

  function driveLayersNeedRefetch() {
    return needsRefetch('paved') || needsRefetch('jeep')
  }

  function markDriveFetched(bbox, tier) {
    const box = { ...bbox }
    layerFetchBBox.paved = box
    layerFetchBBox.jeep = box
    layerFetchTier.paved = tier
    layerFetchTier.jeep = tier
  }

  function syncVisibility() {
    if (!getMapReady()) return
    const map = getMap()
    if (!map) return
    ensureAccessNetworkLayers(map)
    const v = visState()
    syncAccessNetworkVisibility(map, {
      paved: v.pavedVisible,
      jeep: v.jeepVisible,
      hiking: v.hikingVisible,
    })
    syncToolbarPressed()
  }

  function syncToolbarPressed() {
    const btn = document.getElementById('map-tool-access-network')
    if (!btn) return
    btn.setAttribute('aria-pressed', anyVisible() ? 'true' : 'false')
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   * @param {{ west: number, south: number, east: number, north: number }} bbox
   */
  async function fetchLayerForBBox(kind, bbox, zoom) {
    if (inflight[kind]) {
      await inflight[kind]
      return layerCache[kind]
    }
    const tier = networkZoomTier(zoom)
    const url = apiUrls.roadsNetworkLayerGeoJsonUrl(projectSlug, kind, bbox, zoom)
    inflight[kind] = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        layerCache[kind] = await resp.json()
        layerFetchBBox[kind] = { ...bbox }
        layerFetchTier[kind] = tier
      } catch (err) {
        console.warn(`access network ${kind} fetch failed`, err)
        layerCache[kind] = { type: 'FeatureCollection', features: [] }
        layerFetchBBox[kind] = { ...bbox }
        layerFetchTier[kind] = tier
      } finally {
        inflight[kind] = null
      }
    })()
    await inflight[kind]
    return layerCache[kind]
  }

  /**
   * @param {{ west: number, south: number, east: number, north: number }} bbox
   */
  async function fetchDriveForBBox(bbox, zoom) {
    if (driveInflight) {
      await driveInflight
      return
    }
    const tier = networkZoomTier(zoom)
    const url = apiUrls.roadsNetworkDriveGeoJsonUrl(projectSlug, bbox, zoom)
    driveInflight = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const body = await resp.json()
        layerCache.paved = body.paved ?? { type: 'FeatureCollection', features: [] }
        layerCache.jeep = body.jeep ?? { type: 'FeatureCollection', features: [] }
        markDriveFetched(bbox, tier)
      } catch (err) {
        console.warn('access network drive fetch failed', err)
        const empty = { type: 'FeatureCollection', features: [] }
        layerCache.paved = empty
        layerCache.jeep = empty
        markDriveFetched(bbox, tier)
      } finally {
        driveInflight = null
      }
    })()
    await driveInflight
  }

  function paintCachedLayer(kind) {
    const map = getMap()
    if (!map || !layerCache[kind] || needsRefetch(kind)) return
    ensureAccessNetworkLayers(map)
    setAccessNetworkLayerData(map, kind, layerCache[kind])
  }

  function afterLayerPaint() {
    if (!layersRaised) {
      layersRaised = true
      raiseSiteLayers?.()
    }
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   * @param {boolean} [force]
   */
  async function reloadLayer(kind, force = false) {
    if (!getMapReady()) return
    if (!force && !needsRefetch(kind)) return
    const bbox = viewBBox()
    if (!bbox) return
    const map = getMap()
    if (!map) return
    ensureAccessNetworkLayers(map)
    const geo = await fetchLayerForBBox(kind, bbox, map.getZoom())
    setAccessNetworkLayerData(map, kind, geo)
    afterLayerPaint()
  }

  /**
   * @param {boolean} [force]
   */
  async function reloadDriveLayers(force = false) {
    if (!getMapReady()) return
    if (!force && !driveLayersNeedRefetch()) return
    const bbox = viewBBox()
    if (!bbox) return
    const map = getMap()
    if (!map) return
    ensureAccessNetworkLayers(map)
    await fetchDriveForBBox(bbox, map.getZoom())
    const v = visState()
    if (v.pavedVisible && layerCache.paved) {
      setAccessNetworkLayerData(map, 'paved', layerCache.paved)
    }
    if (v.jeepVisible && layerCache.jeep) {
      setAccessNetworkLayerData(map, 'jeep', layerCache.jeep)
    }
    afterLayerPaint()
  }

  async function refreshVisibleLayers(force = false) {
    const v = visState()
    const jobs = []
    if (v.pavedVisible && v.jeepVisible) {
      jobs.push(reloadDriveLayers(force))
    } else {
      if (v.pavedVisible) jobs.push(reloadLayer('paved', force))
      if (v.jeepVisible) jobs.push(reloadLayer('jeep', force))
    }
    if (v.hikingVisible) jobs.push(reloadLayer('hiking', force))
    if (jobs.length) await Promise.all(jobs)
  }

  function onMapMoveEnd() {
    if (!getMapReady() || !anyVisible()) return
    if (moveDebounceTimer) clearTimeout(moveDebounceTimer)
    moveDebounceTimer = window.setTimeout(() => {
      moveDebounceTimer = 0
      void refreshVisibleLayers(false)
    }, MOVE_DEBOUNCE_MS)
  }

  function installMapMoveHook() {
    if (moveHookInstalled || !getMapReady()) return
    const map = getMap()
    if (!map) return
    moveHookInstalled = true
    map.on('moveend', onMapMoveEnd)
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   * @param {boolean} visible
   */
  async function setLayerVisible(kind, visible) {
    if (!store.accessNetwork) {
      store.accessNetwork = { pavedVisible: false, jeepVisible: false, hikingVisible: false }
    }
    if (kind === 'paved') store.accessNetwork.pavedVisible = visible
    else if (kind === 'jeep') store.accessNetwork.jeepVisible = visible
    else store.accessNetwork.hikingVisible = visible
    if (visible && getMapReady()) {
      installMapMoveHook()
      paintCachedLayer(kind)
      await refreshVisibleLayers(false)
    }
    syncVisibility()
    scheduleSaveMapState?.()
  }

  function installToggles() {
    const pavedEl = document.getElementById('access-network-paved')
    const jeepEl = document.getElementById('access-network-jeep')
    const hikingEl = document.getElementById('access-network-hiking')
    const v = visState()
    if (pavedEl) {
      pavedEl.checked = v.pavedVisible
      pavedEl.addEventListener('change', () => {
        void setLayerVisible('paved', pavedEl.checked)
      })
    }
    if (jeepEl) {
      jeepEl.checked = v.jeepVisible
      jeepEl.addEventListener('change', () => {
        void setLayerVisible('jeep', jeepEl.checked)
      })
    }
    if (hikingEl) {
      hikingEl.checked = v.hikingVisible
      hikingEl.addEventListener('change', () => {
        void setLayerVisible('hiking', hikingEl.checked)
      })
    }
    syncToolbarPressed()
  }

  async function refreshOnMapLoad() {
    installToggles()
    if (!getMapReady()) return
    installMapMoveHook()
    if (anyVisible()) {
      await refreshVisibleLayers(true)
    }
    syncVisibility()
  }

  return {
    refreshOnMapLoad,
    setLayerVisible,
    syncVisibility,
  }
}
