// @ts-check

import * as apiUrls from '../api/urls.js'
import { PEAKS_ACCESS_LINE, PEAKS_SYMBOL } from '../constants.js'
import { ensurePeaksLayers, setPeaksLayerData } from '../map/peaks-layers.js'

/**
 * @param {object} opts
 * @param {object} opts.store
 * @param {string} opts.projectSlug
 * @param {() => maplibregl.Map} opts.getMap
 * @param {() => boolean} opts.getMapReady
 * @param {() => void} [opts.deselectSite]
 * @param {() => void} [opts.syncMapViewport]
 */
export function createPeaksDomain(opts) {
  const { store, projectSlug, getMap, getMapReady, deselectSite, syncMapViewport } = opts

  function peakPanelEl() {
    return document.getElementById('peak-panel')
  }

  function updatePeakHighlight(slug) {
    if (!getMapReady()) return
    const map = getMap()
    if (!map.getLayer(PEAKS_ACCESS_LINE)) return
    const sel = slug || ''
    map.setPaintProperty(PEAKS_ACCESS_LINE, 'line-width', [
      'case',
      ['==', ['get', 'slug'], sel],
      5,
      3,
    ])
    map.setPaintProperty(PEAKS_ACCESS_LINE, 'line-color', [
      'case',
      ['==', ['get', 'slug'], sel],
      '#fde047',
      '#22c55e',
    ])
  }

  function flyToPeak(peak) {
    if (!peak || !getMapReady()) return
    const map = getMap()
    const coords = []
    if (Number.isFinite(peak.lat) && Number.isFinite(peak.lon)) {
      coords.push([peak.lon, peak.lat])
    }
    if (Number.isFinite(peak.road_lat) && Number.isFinite(peak.road_lon)) {
      coords.push([peak.road_lon, peak.road_lat])
    }
    if (!coords.length) return
    const lngs = coords.map((c) => c[0])
    const lats = coords.map((c) => c[1])
    const shortHike = Number.isFinite(peak.hike_m) && peak.hike_m > 0 && peak.hike_m < 200
    map.fitBounds(
      [
        [Math.min(...lngs), Math.min(...lats)],
        [Math.max(...lngs), Math.max(...lats)],
      ],
      { padding: shortHike ? 120 : 80, maxZoom: shortHike ? 17 : 14, duration: 600 },
    )
  }

  async function loadPeaks() {
    if (!getMapReady()) return
    const map = getMap()
    try {
      const resp = await fetch(apiUrls.peaksApiUrl(projectSlug))
      if (!resp.ok) return
      const data = await resp.json()
      store.peaks.list = data.peaks || []
      store.peaks.rules = data.rules || null
      await ensurePeaksLayers(map)
      setPeaksLayerData(map, store.peaks.list)
      updatePeakHighlight(store.ui.selectedPeakSlug)
    } catch (err) {
      console.warn('peaks: catalog load failed', err)
    }
  }

  function selectPeak(slug) {
    const peak = store.peaks.list.find((p) => p.slug === slug)
    if (!peak) return
    deselectSite?.()
    store.ui.selectedPeakSlug = slug
    const panel = peakPanelEl()
    if (panel) panel.hidden = false
    updatePeakHighlight(slug)
    flyToPeak(peak)
    syncMapViewport?.()
  }

  function deselectPeak() {
    store.ui.selectedPeakSlug = null
    const panel = peakPanelEl()
    if (panel) panel.hidden = true
    updatePeakHighlight(null)
    syncMapViewport?.()
  }

  function getPeakBySlug(slug) {
    return store.peaks.list.find((p) => p.slug === slug) || null
  }

  return {
    loadPeaks,
    selectPeak,
    deselectPeak,
    getPeakBySlug,
    updatePeakHighlight,
  }
}
