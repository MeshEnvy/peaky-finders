// @ts-check

import * as apiUrls from '../api/urls.js'
import {
  ensureGtlfInventoryLayers,
  setGtlfInventoryData,
  syncGtlfInventoryVisibility,
} from '../map/gtlf-layers.js'

/**
 * BLM GTLF inventory overlays (all designated lines in AOI cache).
 * @param {object} opts
 */
export function createGtlfDomain(opts) {
  const { store, projectSlug, getMap, getMapReady, raiseSiteLayers, scheduleSaveMapState } = opts

  /** @type {Record<'roads'|'trails', object|null>} */
  const cache = { roads: null, trails: null }
  /** @type {Record<'roads'|'trails', Promise<void>|null>} */
  const inflight = { roads: null, trails: null }

  function isRoadsVisible() {
    return store.gtlf?.roadsVisible === true
  }

  function isTrailsVisible() {
    return store.gtlf?.trailsVisible === true
  }

  function syncVisibility() {
    if (!getMapReady()) return
    const map = getMap()
    if (!map) return
    ensureGtlfInventoryLayers(map)
    syncGtlfInventoryVisibility(map, {
      roads: isRoadsVisible(),
      trails: isTrailsVisible(),
    })
    raiseSiteLayers?.()
  }

  /**
   * @param {'roads'|'trails'} kind
   */
  async function fetchInventory(kind) {
    if (cache[kind]) return cache[kind]
    if (inflight[kind]) {
      await inflight[kind]
      return cache[kind]
    }
    const url =
      kind === 'trails'
        ? apiUrls.gtlfTrailsInventoryGeoJsonUrl(projectSlug)
        : apiUrls.gtlfRoadsInventoryGeoJsonUrl(projectSlug)
    inflight[kind] = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        cache[kind] = await resp.json()
      } catch (err) {
        console.warn(`gtlf ${kind} inventory fetch failed`, err)
        cache[kind] = { type: 'FeatureCollection', features: [] }
      } finally {
        inflight[kind] = null
      }
    })()
    await inflight[kind]
    return cache[kind]
  }

  /**
   * @param {'roads'|'trails'} kind
   * @param {boolean} visible
   */
  async function setInventoryVisible(kind, visible) {
    if (!store.gtlf) store.gtlf = { roadsVisible: false, trailsVisible: false }
    if (kind === 'roads') store.gtlf.roadsVisible = visible
    else store.gtlf.trailsVisible = visible
    if (visible && getMapReady()) {
      const map = getMap()
      ensureGtlfInventoryLayers(map)
      const geo = await fetchInventory(kind)
      setGtlfInventoryData(map, kind, geo)
    }
    syncVisibility()
    scheduleSaveMapState?.()
  }

  function installToggles() {
    const roadsEl = document.getElementById('gtlf-inventory-roads')
    const trailsEl = document.getElementById('gtlf-inventory-trails')
    if (roadsEl) {
      roadsEl.checked = isRoadsVisible()
      roadsEl.addEventListener('change', () => {
        void setInventoryVisible('roads', roadsEl.checked)
      })
    }
    if (trailsEl) {
      trailsEl.checked = isTrailsVisible()
      trailsEl.addEventListener('change', () => {
        void setInventoryVisible('trails', trailsEl.checked)
      })
    }
  }

  async function refreshOnMapLoad() {
    installToggles()
    if (!getMapReady()) return
    const map = getMap()
    ensureGtlfInventoryLayers(map)
    if (isRoadsVisible()) {
      const geo = await fetchInventory('roads')
      setGtlfInventoryData(map, 'roads', geo)
    }
    if (isTrailsVisible()) {
      const geo = await fetchInventory('trails')
      setGtlfInventoryData(map, 'trails', geo)
    }
    syncVisibility()
  }

  return {
    refreshOnMapLoad,
    setInventoryVisible,
    syncVisibility,
  }
}
