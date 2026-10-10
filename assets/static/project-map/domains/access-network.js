// @ts-check

import { ACCESS_NETWORK_ALL_LINE } from '../constants.js'
import * as apiUrls from '../api/urls.js'
import {
  appendCustomRoad,
  fetchRoadsBook,
  overrideKeyFromProps,
  saveRoadsBook,
  upsertOverride,
} from '../api/roads-book.js'
import {
  ensureAccessNetworkLayers,
  setAccessNetworkAllData,
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

/** @param {Record<string, unknown>|undefined} props */
function formatRoadProps(props) {
  if (!props) return ''
  const rows = []
  for (const [key, val] of Object.entries(props)) {
    if (val == null || val === '') continue
    const label = key.startsWith('gtlf_') ? key.slice(5) : key
    rows.push(
      `<tr><th>${escapeHtml(label)}</th><td data-prop="${escapeHtml(key)}">${escapeHtml(String(val))}</td></tr>`,
    )
  }
  if (!rows.length) return '<p>No properties</p>'
  return `<table class="road-inv-table">${rows.join('')}</table>`
}

/**
 * @param {GeoJSON.Geometry|null|undefined} geometry
 * @returns {[number, number][]}
 */
function lineCoords(geometry) {
  if (!geometry) return []
  if (geometry.type === 'LineString') return geometry.coordinates
  if (geometry.type === 'MultiLineString') return geometry.coordinates.flat()
  return []
}

/**
 * Plain text for paste: click point, every tag, and the line vertices.
 * @param {{ lat: number, lng: number }} lngLat
 * @param {maplibregl.MapGeoJSONFeature[]} features
 */
function roadCopyText(lngLat, features) {
  const lines = [`click: ${lngLat.lat.toFixed(6)}, ${lngLat.lng.toFixed(6)}`]
  features.slice(0, 8).forEach((f, i) => {
    lines.push('')
    lines.push(`segment ${i + 1}`)
    const props = f.properties || {}
    for (const [key, val] of Object.entries(props)) {
      if (val == null || val === '') continue
      const label = key.startsWith('gtlf_') ? key.slice(5) : key
      lines.push(`${label}: ${val}`)
    }
    const coords = lineCoords(f.geometry)
    if (coords.length) {
      lines.push(`vertices: ${coords.length}`)
      lines.push('line:')
      for (const pair of coords) {
        const lon = pair[0]
        const lat = pair[1]
        lines.push(`${lat.toFixed(6)}, ${lon.toFixed(6)}`)
      }
    }
  })
  return lines.join('\n')
}

const COPY_ICON =
  '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M4 2.5A1.5 1.5 0 0 1 5.5 1h6A1.5 1.5 0 0 1 13 2.5v8a1.5 1.5 0 0 1-1.5 1.5h-6A1.5 1.5 0 0 1 4 10.5v-8Zm1.5-.5a.5.5 0 0 0-.5.5v8a.5.5 0 0 0 .5.5h6a.5.5 0 0 0 .5-.5v-8a.5.5 0 0 0-.5-.5h-6Z"/><path fill="currentColor" d="M2.5 4A1.5 1.5 0 0 0 1 5.5v8A1.5 1.5 0 0 0 2.5 15h6a1.5 1.5 0 0 0 1.5-1.5V13h-1v.5a.5.5 0 0 1-.5.5h-6a.5.5 0 0 1-.5-.5v-8a.5.5 0 0 1 .5-.5H4V4H2.5Z"/></svg>'

/** @param {string} s */
function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
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
  /** @type {object|null} */
  let allCache = null
  /** @type {{ west: number, south: number, east: number, north: number }|null} */
  let allFetchBBox = null
  /** @type {Promise<void>|null} */
  let allInflight = null
  /** Bumped when ``roads.yaml`` changes so stale in-flight GeoJSON cannot repopulate the client cache. */
  let networkFetchGen = 0
  let moveHookInstalled = false
  let allClickInstalled = false
  let moveDebounceTimer = 0
  let layersRaised = false
  /** @type {maplibregl.Popup|null} */
  let roadPopup = null
  /** @type {boolean} */
  let drawRoadActive = false
  /** @type {[number, number][]} lat/lon */
  let drawVertices = []
  const DRAW_SOURCE = 'access-network-draw'
  const DRAW_LINE = 'access-network-draw-line'

  function syncDrawSource() {
    const map = getMap()
    if (!map || !map.getSource(DRAW_SOURCE)) return
    const features =
      drawVertices.length >= 2
        ? [
            {
              type: 'Feature',
              geometry: {
                type: 'LineString',
                coordinates: drawVertices.map(([lat, lon]) => [lon, lat]),
              },
              properties: {},
            },
          ]
        : []
    map.getSource(DRAW_SOURCE).setData({ type: 'FeatureCollection', features })
  }

  function ensureDrawLayer() {
    const map = getMap()
    if (!map) return
    if (!map.getSource(DRAW_SOURCE)) {
      map.addSource(DRAW_SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      map.addLayer({
        id: DRAW_LINE,
        type: 'line',
        source: DRAW_SOURCE,
        paint: { 'line-color': '#f472b6', 'line-width': 3, 'line-dasharray': [2, 1] },
      })
    }
    syncDrawSource()
  }

  function setDrawRoadActive(on) {
    drawRoadActive = on
    drawVertices = []
    const btn = document.getElementById('access-network-draw-road')
    if (btn) {
      btn.classList.toggle('is-active', on)
      btn.textContent = on ? 'Finish road (dbl-click)' : 'Draw road'
    }
    const map = getMap()
    if (map) {
      map.getCanvas().style.cursor = on ? 'crosshair' : ''
      if (on) ensureDrawLayer()
      else syncDrawSource()
    }
  }

  async function finishDrawRoad(lngLat) {
    if (lngLat) {
      drawVertices.push([lngLat.lat, lngLat.lng])
    }
    if (drawVertices.length < 2) {
      setDrawRoadActive(false)
      return
    }
    const role = window.prompt('Role for this road: paved, jeep, hiking, or other', 'jeep')
    if (!role || !['paved', 'jeep', 'hiking', 'other'].includes(role)) {
      setDrawRoadActive(false)
      return
    }
    const id = `custom-${Date.now()}`
    const savedVerts = drawVertices.slice()
    try {
      const book = await fetchRoadsBook(projectSlug)
      appendCustomRoad(
        book,
        id,
        /** @type {import('../api/roads-book.js').RoadRole} */ (role),
        savedVerts,
      )
      await saveRoadsBook(projectSlug, book)
      setDrawRoadActive(false)
      invalidateNetworkClientCache()
      paintOptimisticCustomRoad(id, /** @type {import('../api/roads-book.js').RoadRole} */ (role), savedVerts)
      window.dispatchEvent(new CustomEvent('peaky-roads-book-changed', { detail: { fromMap: true } }))
      void refreshVisibleLayers(true)
    } catch (err) {
      window.alert(err.message || String(err))
      setDrawRoadActive(false)
    }
  }

  function visState() {
    return (
      store.accessNetwork || {
        pavedVisible: false,
        jeepVisible: false,
        hikingVisible: false,
        allVisible: false,
      }
    )
  }

  function anyVisible() {
    const v = visState()
    return v.pavedVisible || v.jeepVisible || v.hikingVisible || v.allVisible
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

  function allNeedsRefetch() {
    const current = viewBBox()
    if (!current || !allFetchBBox || !allCache) return true
    return !bboxContains(allFetchBBox, current)
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
      allVisible: v.allVisible,
    })
    syncToolbarPressed()
  }

  function syncToolbarPressed() {
    const btn = document.getElementById('map-tool-access-network')
    if (!btn) return
    btn.setAttribute('aria-pressed', anyVisible() ? 'true' : 'false')
  }

  function invalidateNetworkClientCache() {
    networkFetchGen += 1
    allCache = null
    allFetchBBox = null
    allInflight = null
    driveInflight = null
    for (const kind of /** @type {const} */ (['paved', 'jeep', 'hiking'])) {
      layerCache[kind] = null
      layerFetchBBox[kind] = null
      layerFetchTier[kind] = null
      inflight[kind] = null
    }
  }

  /**
   * @param {string} id
   * @param {import('../api/roads-book.js').RoadRole} role
   * @param {[number, number][]} latLonVerts
   */
  function customRoadFeature(id, role, latLonVerts) {
    return {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: latLonVerts.map(([lat, lon]) => [lon, lat]),
      },
      properties: { role, source: 'custom', id },
    }
  }

  /**
   * Show the saved custom with real layer colors while the server rebuilds OSM routing.
   * @param {string} id
   * @param {import('../api/roads-book.js').RoadRole} role
   * @param {[number, number][]} latLonVerts
   */
  function paintOptimisticCustomRoad(id, role, latLonVerts) {
    const map = getMap()
    if (!map || latLonVerts.length < 2) return
    const feature = customRoadFeature(id, role, latLonVerts)
    const solo = { type: 'FeatureCollection', features: [feature] }
    ensureAccessNetworkLayers(map)
    syncVisibility()
    const v = visState()
    if (v.allVisible) {
      allCache = solo
      setAccessNetworkAllData(map, solo)
    }
    if (!v.allVisible) {
      if (role === 'paved' && v.pavedVisible) {
        layerCache.paved = solo
        setAccessNetworkLayerData(map, 'paved', solo)
      }
      if (role === 'jeep' && v.jeepVisible) {
        layerCache.jeep = solo
        setAccessNetworkLayerData(map, 'jeep', solo)
      }
      if (role === 'hiking' && v.hikingVisible) {
        layerCache.hiking = solo
        setAccessNetworkLayerData(map, 'hiking', solo)
      }
      if (role === 'other') {
        console.info(
          'Custom road role "other" appears on the All layer; turn on All to see it while the map reloads.',
        )
      }
    }
    afterLayerPaint()
  }

  /** After ``roads.yaml`` save: drop client cache and repaint every visible network layer. */
  async function onRoadsBookChanged() {
    invalidateNetworkClientCache()
    await refreshVisibleLayers(true)
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   * @param {{ west: number, south: number, east: number, north: number }} bbox
   */
  async function fetchLayerForBBox(kind, bbox, zoom) {
    const gen = networkFetchGen
    const tier = networkZoomTier(zoom)
    const url = apiUrls.roadsNetworkLayerGeoJsonUrl(projectSlug, kind, bbox, zoom)
    const job = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const geo = await resp.json()
        if (gen !== networkFetchGen) return
        layerCache[kind] = geo
        layerFetchBBox[kind] = { ...bbox }
        layerFetchTier[kind] = tier
      } catch (err) {
        console.warn(`access network ${kind} fetch failed`, err)
        if (gen !== networkFetchGen) return
        layerCache[kind] = { type: 'FeatureCollection', features: [] }
        layerFetchBBox[kind] = { ...bbox }
        layerFetchTier[kind] = tier
      } finally {
        if (inflight[kind] === job) inflight[kind] = null
      }
    })()
    inflight[kind] = job
    await job
    return layerCache[kind]
  }

  /**
   * @param {{ west: number, south: number, east: number, north: number }} bbox
   */
  async function fetchAllForBBox(bbox) {
    const gen = networkFetchGen
    const url = apiUrls.roadsNetworkAllGeoJsonUrl(projectSlug, bbox)
    const job = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const geo = await resp.json()
        if (gen !== networkFetchGen) return
        allCache = geo
        allFetchBBox = { ...bbox }
      } catch (err) {
        console.warn('access network all fetch failed', err)
        if (gen !== networkFetchGen) return
        allCache = { type: 'FeatureCollection', features: [] }
        allFetchBBox = { ...bbox }
      } finally {
        if (allInflight === job) allInflight = null
      }
    })()
    allInflight = job
    await job
    return allCache
  }

  /**
   * @param {{ west: number, south: number, east: number, north: number }} bbox
   */
  async function fetchDriveForBBox(bbox, zoom) {
    const gen = networkFetchGen
    const tier = networkZoomTier(zoom)
    const url = apiUrls.roadsNetworkDriveGeoJsonUrl(projectSlug, bbox, zoom)
    const job = (async () => {
      try {
        const resp = await fetch(url)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const body = await resp.json()
        if (gen !== networkFetchGen) return
        layerCache.paved = body.paved ?? { type: 'FeatureCollection', features: [] }
        layerCache.jeep = body.jeep ?? { type: 'FeatureCollection', features: [] }
        markDriveFetched(bbox, tier)
      } catch (err) {
        console.warn('access network drive fetch failed', err)
        if (gen !== networkFetchGen) return
        const empty = { type: 'FeatureCollection', features: [] }
        layerCache.paved = empty
        layerCache.jeep = empty
        markDriveFetched(bbox, tier)
      } finally {
        if (driveInflight === job) driveInflight = null
      }
    })()
    driveInflight = job
    await job
  }

  function clearMapLayer(kind) {
    const map = getMap()
    if (!map) return
    setAccessNetworkLayerData(map, kind, { type: 'FeatureCollection', features: [] })
  }

  function clearAllLayer() {
    const map = getMap()
    if (!map) return
    setAccessNetworkAllData(map, { type: 'FeatureCollection', features: [] })
  }

  function layerIsVisible(kind) {
    const v = visState()
    if (kind === 'paved') return v.pavedVisible && !v.allVisible
    if (kind === 'jeep') return v.jeepVisible && !v.allVisible
    return v.hikingVisible && !v.allVisible
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
    if (!layerIsVisible(kind)) {
      clearMapLayer(kind)
      return
    }
    setAccessNetworkLayerData(map, kind, geo)
    afterLayerPaint()
  }

  /**
   * @param {boolean} [force]
   */
  async function reloadAllLayer(force = false) {
    if (!getMapReady()) return
    if (!force && !allNeedsRefetch()) return
    const bbox = viewBBox()
    if (!bbox) return
    const map = getMap()
    if (!map) return
    ensureAccessNetworkLayers(map)
    const geo = await fetchAllForBBox(bbox)
    if (!visState().allVisible) {
      clearAllLayer()
      return
    }
    setAccessNetworkAllData(map, geo)
    installAllClickHandler()
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
    if (v.pavedVisible && !v.allVisible && layerCache.paved) {
      setAccessNetworkLayerData(map, 'paved', layerCache.paved)
    } else {
      clearMapLayer('paved')
    }
    if (v.jeepVisible && !v.allVisible && layerCache.jeep) {
      setAccessNetworkLayerData(map, 'jeep', layerCache.jeep)
    } else {
      clearMapLayer('jeep')
    }
    afterLayerPaint()
  }

  async function refreshVisibleLayers(force = false) {
    const v = visState()
    const jobs = []
    if (v.allVisible) {
      jobs.push(reloadAllLayer(force))
    } else {
      if (v.pavedVisible && v.jeepVisible) {
        jobs.push(reloadDriveLayers(force))
      } else {
        if (v.pavedVisible) jobs.push(reloadLayer('paved', force))
        if (v.jeepVisible) jobs.push(reloadLayer('jeep', force))
      }
      if (v.hikingVisible) jobs.push(reloadLayer('hiking', force))
    }
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

  function showRoadPopup(lngLat, features) {
    const map = getMap()
    if (!map || !features.length) return
    if (!roadPopup) {
      roadPopup = new maplibregl.Popup({
        closeButton: true,
        closeOnClick: false,
        maxWidth: '280px',
        className: 'road-inv-popup',
      })
      roadPopup.on('open', () => {
        const el = roadPopup?.getElement()
        if (!el || el.dataset.roadInvBound) return
        el.dataset.roadInvBound = '1'
        const stop = (ev) => ev.stopPropagation()
        el.addEventListener('mousedown', stop)
        el.addEventListener('click', stop)
        el.addEventListener('pointerdown', stop)
      })
    }
    const shown = features.slice(0, 8)
    const blocks = shown.map((f, i) => {
      const p = f.properties || {}
      const title = [p.role, p.source].filter(Boolean).join(' · ')
      return `<div class="road-inv-block"><strong class="road-inv-title">${escapeHtml(title || `Line ${i + 1}`)}</strong>${formatRoadProps(p)}</div>`
    })
    const copyText = roadCopyText(lngLat, shown)
    const firstProps = shown[0]?.properties || {}
    const ovKey = overrideKeyFromProps(firstProps)
    const customId = firstProps.source === 'custom' ? firstProps.custom_id : null
    const roles = ['paved', 'jeep', 'hiking', 'other']
    const useAs =
      ovKey || customId
        ? `<label class="road-inv-use-as"><span>Use as</span><select class="road-inv-role-select">${roles
            .map((r) => `<option value="${r}"${firstProps.role === r ? ' selected' : ''}>${r}</option>`)
            .join('')}</select></label><p class="road-inv-save-status" hidden></p>`
        : ''
    const html = `<div class="road-inv-head"><button type="button" class="road-inv-copy" title="Copy segment">${COPY_ICON}</button></div>${useAs}${blocks.join('')}`
    roadPopup.setLngLat(lngLat).setHTML(html).addTo(map)
    const popupEl = roadPopup.getElement()
    const roleSelect = popupEl?.querySelector('.road-inv-role-select')
    const saveStatus = popupEl?.querySelector('.road-inv-save-status')
    roleSelect?.addEventListener('change', () => {
      void (async () => {
        const role = roleSelect.value
        if (!role) return
        roleSelect.disabled = true
        if (saveStatus) {
          saveStatus.hidden = false
          saveStatus.textContent = 'Saving exception…'
        }
        try {
          const book = await fetchRoadsBook(projectSlug)
          if (customId) {
            const row = book.customs?.find((c) => c.id === customId)
            if (row) row.role = role
          } else if (ovKey) {
            upsertOverride(book, ovKey, /** @type {import('../api/roads-book.js').RoadRole} */ (role))
          }
          await saveRoadsBook(projectSlug, book)
          const source = firstProps.source || ''
          const title = popupEl?.querySelector('.road-inv-title')
          if (title) title.textContent = [role, source].filter(Boolean).join(' · ')
          const roleCell = popupEl?.querySelector('[data-prop="role"]')
          if (roleCell) roleCell.textContent = role
          if (saveStatus) saveStatus.textContent = 'Saved. Map updates when roads reload.'
          await onRoadsBookChanged()
          window.dispatchEvent(new CustomEvent('peaky-roads-book-changed', { detail: { fromMap: true } }))
          if (saveStatus) saveStatus.textContent = 'Saved.'
        } catch (err) {
          if (saveStatus) {
            saveStatus.hidden = false
            saveStatus.textContent = err.message || String(err)
          } else {
            window.alert(err.message || String(err))
          }
        } finally {
          roleSelect.disabled = false
        }
      })()
    })
    const btn = roadPopup.getElement()?.querySelector('.road-inv-copy')
    btn?.addEventListener('click', (ev) => {
      ev.preventDefault()
      ev.stopPropagation()
      void navigator.clipboard.writeText(copyText).then(() => {
        btn.classList.add('is-copied')
        btn.title = 'Copied'
      })
    })
  }

  function installAllClickHandler() {
    if (allClickInstalled || !getMapReady()) return
    const map = getMap()
    if (!map || !map.getLayer(ACCESS_NETWORK_ALL_LINE)) return
    allClickInstalled = true
    map.on('click', ACCESS_NETWORK_ALL_LINE, (ev) => {
      if (!visState().allVisible || !ev.features?.length) return
      ev.originalEvent?.preventDefault?.()
      showRoadPopup(ev.lngLat, ev.features)
    })
    map.on('mouseenter', ACCESS_NETWORK_ALL_LINE, () => {
      if (visState().allVisible) map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', ACCESS_NETWORK_ALL_LINE, () => {
      map.getCanvas().style.cursor = ''
    })
  }

  /**
   * @param {{ x: number, y: number }} point
   * @returns {boolean} true if a road line was hit (caller should stop propagation)
   */
  function trySelectRoadAtPoint(point) {
    if (!visState().allVisible || !getMapReady()) return false
    const map = getMap()
    if (!map || !map.getLayer(ACCESS_NETWORK_ALL_LINE)) return false
    const pad = 6
    const feats = map.queryRenderedFeatures(
      [
        [point.x - pad, point.y - pad],
        [point.x + pad, point.y + pad],
      ],
      { layers: [ACCESS_NETWORK_ALL_LINE] },
    )
    if (!feats.length) return false
    const lngLat = map.unproject([point.x, point.y])
    showRoadPopup(lngLat, feats)
    return true
  }

  /**
   * @param {'paved'|'jeep'|'hiking'} kind
   * @param {boolean} visible
   */
  async function setLayerVisible(kind, visible) {
    if (!store.accessNetwork) {
      store.accessNetwork = {
        pavedVisible: false,
        jeepVisible: false,
        hikingVisible: false,
        allVisible: false,
      }
    }
    if (kind === 'paved') store.accessNetwork.pavedVisible = visible
    else if (kind === 'jeep') store.accessNetwork.jeepVisible = visible
    else store.accessNetwork.hikingVisible = visible
    if (visible && getMapReady()) {
      installMapMoveHook()
      paintCachedLayer(kind)
      await refreshVisibleLayers(false)
    } else {
      clearMapLayer(kind)
    }
    syncVisibility()
    scheduleSaveMapState?.()
  }

  async function setAllVisible(visible) {
    if (!store.accessNetwork) {
      store.accessNetwork = {
        pavedVisible: false,
        jeepVisible: false,
        hikingVisible: false,
        allVisible: false,
      }
    }
    store.accessNetwork.allVisible = visible
    if (visible && getMapReady()) {
      installMapMoveHook()
      await reloadAllLayer(false)
    } else {
      clearAllLayer()
      roadPopup?.remove()
    }
    syncVisibility()
    if (!visible && anyVisible()) {
      await refreshVisibleLayers(false)
    }
    scheduleSaveMapState?.()
  }

  function installDrawRoad() {
    const drawBtn = document.getElementById('access-network-draw-road')
    if (!drawBtn || drawBtn.dataset.bound) return
    drawBtn.dataset.bound = '1'
    drawBtn.addEventListener('click', (ev) => {
      ev.preventDefault()
      ev.stopPropagation()
      setDrawRoadActive(!drawRoadActive)
    })
    const map = getMap()
    if (map && !map._accessDrawHook) {
      map._accessDrawHook = true
      map.on('click', (ev) => {
        if (!drawRoadActive) return
        drawVertices.push([ev.lngLat.lat, ev.lngLat.lng])
        ensureDrawLayer()
        syncDrawSource()
      })
      map.on('dblclick', (ev) => {
        if (!drawRoadActive) return
        ev.preventDefault()
        void finishDrawRoad(ev.lngLat)
      })
    }
    window.addEventListener('peaky-roads-book-changed', (ev) => {
      if (ev.detail?.fromMap) return
      void onRoadsBookChanged()
    })
  }

  function installToggles() {
    const pavedEl = document.getElementById('access-network-paved')
    const jeepEl = document.getElementById('access-network-jeep')
    const hikingEl = document.getElementById('access-network-hiking')
    const allEl = document.getElementById('access-network-all')
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
    if (allEl) {
      allEl.checked = v.allVisible
      allEl.addEventListener('change', () => {
        void setAllVisible(allEl.checked)
      })
    }
    syncToolbarPressed()
  }

  async function refreshOnMapLoad() {
    installToggles()
    installDrawRoad()
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
    setAllVisible,
    syncVisibility,
    trySelectRoadAtPoint,
  }
}
