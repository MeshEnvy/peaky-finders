// @ts-check

import * as apiUrls from '../api/urls.js'
import {
  PEAKS_ACCESS_LINE,
  PEAKS_JEEP_LINE,
  PEAKS_SYMBOL,
} from '../constants.js'
import { ensurePeaksLayers, setPeaksLayerData, setPeaksCursorPoint, clearPeaksCursorPoint } from '../map/peaks-layers.js'

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
    const sel = slug || ''
    if (map.getLayer(PEAKS_ACCESS_LINE)) {
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
    if (map.getLayer(PEAKS_JEEP_LINE)) {
      map.setPaintProperty(PEAKS_JEEP_LINE, 'line-width', [
        'case',
        ['==', ['get', 'slug'], sel],
        6,
        4,
      ])
      map.setPaintProperty(PEAKS_JEEP_LINE, 'line-color', [
        'case',
        ['==', ['get', 'slug'], sel],
        '#fdba74',
        '#ea580c',
      ])
    }
  }

  function flyToPeak(peak) {
    if (!peak || !getMapReady()) return
    const map = getMap()
    const coords = []
    const addCoord = (lon, lat) => {
      if (Number.isFinite(lon) && Number.isFinite(lat)) coords.push([lon, lat])
    }
    addCoord(peak.lon, peak.lat)
    addCoord(peak.road_lon, peak.road_lat)
    addCoord(peak.paved_lon, peak.paved_lat)
    const jeepProfile = peak.jeep?.profile
    if (Array.isArray(jeepProfile)) {
      for (const p of jeepProfile) addCoord(p.lon, p.lat)
    }
    const hikeProfile = peak.hike?.profile
    if (Array.isArray(hikeProfile)) {
      for (const p of hikeProfile) addCoord(p.lon, p.lat)
    }
    if (!coords.length) return
    const lngs = coords.map((c) => c[0])
    const lats = coords.map((c) => c[1])
    const shortAccess =
      (Number.isFinite(peak.hike_m) && peak.hike_m > 0 && peak.hike_m < 200) ||
      (Number.isFinite(peak.jeep_m) && peak.jeep_m > 0 && peak.jeep_m < 500)
    map.fitBounds(
      [
        [Math.min(...lngs), Math.min(...lats)],
        [Math.max(...lngs), Math.max(...lats)],
      ],
      { padding: shortAccess ? 120 : 80, maxZoom: shortAccess ? 17 : 14, duration: 600 },
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
    if (getMapReady()) clearPeaksCursorPoint(getMap())
    flyToPeak(peak)
    syncMapViewport?.()
    void enrichPeakAccess(peak)
  }

  /** @param {object} peak */
  async function enrichPeakAccess(peak) {
    if (!peak?.slug) return
    if (peak.hike?.profile || peak.jeep?.profile) {
      refreshPeakLayers()
      return
    }
    try {
      const resp = await fetch(apiUrls.placeAccessApiUrl(projectSlug, peak.slug))
      if (!resp.ok) return
      const access = await resp.json()
      const idx = store.peaks.list.findIndex((p) => p.slug === peak.slug)
      if (idx < 0) return
      const merged = {
        ...store.peaks.list[idx],
        road_lat: access.road_lat ?? store.peaks.list[idx].road_lat,
        road_lon: access.road_lon ?? store.peaks.list[idx].road_lon,
        paved_lat: access.paved_lat ?? store.peaks.list[idx].paved_lat,
        paved_lon: access.paved_lon ?? store.peaks.list[idx].paved_lon,
        hike_m: access.hike_m ?? store.peaks.list[idx].hike_m,
        jeep_m: access.jeep_m ?? store.peaks.list[idx].jeep_m,
        hike: access.hike ?? store.peaks.list[idx].hike,
        jeep: access.jeep ?? store.peaks.list[idx].jeep,
      }
      store.peaks.list.splice(idx, 1, merged)
      if (store.ui.selectedPeakSlug === peak.slug) {
        flyToPeak(merged)
      }
      refreshPeakLayers()
      updatePeakHighlight(store.ui.selectedPeakSlug)
    } catch (err) {
      console.warn('peaks: access load failed', err)
    }
  }

  function refreshPeakLayers() {
    if (!getMapReady()) return
    setPeaksLayerData(getMap(), store.peaks.list)
  }

  function deselectPeak() {
    store.ui.selectedPeakSlug = null
    const panel = peakPanelEl()
    if (panel) panel.hidden = true
    updatePeakHighlight(null)
    if (getMapReady()) clearPeaksCursorPoint(getMap())
    syncMapViewport?.()
  }

  function getPeakBySlug(slug) {
    return store.peaks.list.find((p) => p.slug === slug) || null
  }

  function flyToProfilePoint(lat, lon, zoom = 15) {
    if (!getMapReady() || !Number.isFinite(lat) || !Number.isFinite(lon)) return
    const map = getMap()
    setPeaksCursorPoint(map, lat, lon)
    map.flyTo({
      center: [lon, lat],
      zoom,
      duration: 600,
    })
    syncMapViewport?.()
  }

  return {
    loadPeaks,
    selectPeak,
    deselectPeak,
    getPeakBySlug,
    updatePeakHighlight,
    flyToProfilePoint,
  }
}
