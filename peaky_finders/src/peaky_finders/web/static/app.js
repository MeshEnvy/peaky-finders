/** Peaky Web — SSE op router + MapLibre */

const projectSel = document.getElementById('project')
const basemapSel = document.getElementById('basemap')
const statusEl = document.getElementById('status')
const chatEl = document.getElementById('chat')
const chatEmptyEl = document.getElementById('chat-empty')
const chatForm = document.getElementById('chat-form')
const chatInput = document.getElementById('chat-input')
const chatSendBtn = document.getElementById('chat-send')
const chatContextFill = document.getElementById('chat-context-fill')
const chatContextDetail = document.getElementById('chat-context-detail')
const chatContextPct = document.getElementById('chat-context-pct')
const chatContextTrack = document.getElementById('chat-context-track')
const chatContextNote = document.getElementById('chat-context-note')
const chatSummarizeBtn = document.getElementById('chat-summarize')
const chatCopyContextBtn = document.getElementById('chat-copy-context')

const sitesPanel = document.getElementById('sites-panel')
const sitesPanelToggle = document.getElementById('sites-panel-toggle')
const sitesPanelCount = document.getElementById('sites-panel-count')
const sitesListEl = document.getElementById('sites-list')
const sitesListEmptyEl = document.getElementById('sites-list-empty')

const siteRegistry = new Map()
let sitesPanelCollapsed = false

function siteDisplayLabel(site) {
  return (site.name || '').trim() || site.slug
}

const chatHistory = []
const chatMapPins = new Map()
let chatSummary = null
let chatContextState = { usage_pct: 0, status: 'ok', full_pct: 92 }
let chatContextRefreshTimer = null
let chatSummarizeInFlight = false

const TERRAIN_SOURCE = 'terrain-dem'
const TERRAIN_HILLSHADE = 'terrain-hillshade'

const TERRAIN_TILES = [
  'https://elevation-tiles-prod.s3.amazonaws.com/v2/terrarium/{z}/{x}/{y}.png',
]

function terrainDemSourceSpec() {
  return {
    type: 'raster-dem',
    tiles: TERRAIN_TILES,
    tileSize: 256,
    maxzoom: 15,
    encoding: 'terrarium',
  }
}

const PITCH_TERRAIN_ON = 12
const PITCH_TERRAIN_OFF = 6

const BASEMAP_REFERENCE_SOURCE = 'basemap-reference'
const BASEMAP_REFERENCE_LAYER = 'basemap-reference'

const BASEMAPS = {
  osm: {
    label: 'OpenStreetMap',
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxzoom: 19,
  },
  opentopo: {
    label: 'OpenTopoMap',
    tiles: ['https://tile.opentopomap.org/{z}/{x}/{y}.png'],
    attribution:
      '© <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA), © OpenStreetMap',
    maxzoom: 17,
  },
  satellite: {
    label: 'Satellite',
    tiles: [
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    ],
    referenceTiles: [
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    ],
    attribution: '© Esri, Maxar, Earthstar Geographics · labels © Esri',
    maxzoom: 19,
  },
}

function basemapStyle(key) {
  const bm = BASEMAPS[key] || BASEMAPS.osm
  return {
    version: 8,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      basemap: {
        type: 'raster',
        tiles: bm.tiles,
        tileSize: 256,
        attribution: bm.attribution,
        maxzoom: bm.maxzoom,
      },
    },
    layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
  }
}

const map = new maplibregl.Map({
  container: 'map',
  style: basemapStyle('osm'),
  center: [-116, 39],
  zoom: 5,
  maxPitch: 85,
  pitch: 0,
  attributionControl: { compact: true },
})

map.addControl(new maplibregl.NavigationControl(), 'top-right')

const layers = new Map()
const layerFeatureCache = new Map()
const pinFeatureCache = new Map()
const viewshedRasterLayers = []
const pendingRasters = []
let mapReady = false
const pendingPins = []
let currentProjectSlug = null
let projectMeshScopeBbox = null
let terrainActive = false

const MESH_SCOPE_LAYER_IDS = ['goals', 'sites', 'trials', 'committed', 'mesh_links', 'viewsheds']
const MAP_PIN_LAYER_IDS = ['goals', 'sites', 'trials']
const MESH_SCOPE_FIT = { padding: 64, bearing: 0, pitch: 0, maxZoom: 15 }

function ensureTerrainSource() {
  if (map.getSource(TERRAIN_SOURCE)) return
  map.addSource(TERRAIN_SOURCE, terrainDemSourceSpec())
}

function ensureHillshadeLayer() {
  if (map.getLayer(TERRAIN_HILLSHADE)) return
  ensureTerrainSource()
  map.addLayer(
    {
      id: TERRAIN_HILLSHADE,
      type: 'hillshade',
      source: TERRAIN_SOURCE,
      paint: {
        'hillshade-exaggeration': 0.35,
        'hillshade-shadow-color': '#0a0e14',
        'hillshade-highlight-color': '#ffffff',
        'hillshade-accent-color': '#64748b',
      },
    },
    'basemap',
  )
}

function removeHillshadeLayer() {
  if (map.getLayer(TERRAIN_HILLSHADE)) map.removeLayer(TERRAIN_HILLSHADE)
}

function removeTerrainSource() {
  removeHillshadeLayer()
  if (map.getSource(TERRAIN_SOURCE)) map.removeSource(TERRAIN_SOURCE)
}

function overlayLayerIds() {
  const ids = []
  for (const layerId of ['committed', 'viewsheds']) {
    const entry = layers.get(layerId)
    if (entry?.layer && map.getLayer(entry.layer)) ids.push(entry.layer)
    if (entry?.outline && map.getLayer(entry.outline)) ids.push(entry.outline)
  }
  for (const { layerId } of viewshedRasterLayers) {
    if (map.getLayer(layerId)) ids.push(layerId)
  }
  const meshLinks = layers.get('mesh_links')
  if (meshLinks?.layer && map.getLayer(meshLinks.layer)) ids.push(meshLinks.layer)
  for (const layerId of MAP_PIN_LAYER_IDS) {
    const entry = layers.get(layerId)
    if (entry?.layer && map.getLayer(entry.layer)) ids.push(entry.layer)
    if (entry?.labelLayer && map.getLayer(entry.labelLayer)) ids.push(entry.labelLayer)
  }
  return ids
}

function raiseOverlayLayers() {
  for (const id of overlayLayerIds()) {
    try {
      map.moveLayer(id)
    } catch (_) {
      /* layer may be mid-remove */
    }
  }
}

function showTerrainOverlays() {
  ensureTerrainSource()
  ensureHillshadeLayer()
  map.setTerrain({ source: TERRAIN_SOURCE, exaggeration: 1.35 })
  if (map.getLayer('basemap')) {
    map.setPaintProperty('basemap', 'raster-opacity', 0.9)
  }
  raiseOverlayLayers()
}

function hideTerrainOverlays() {
  map.setTerrain(null)
  removeTerrainSource()
  if (map.getLayer('basemap')) {
    map.setPaintProperty('basemap', 'raster-opacity', 1)
  }
}

function syncTerrainFromPitch() {
  if (!mapReady) return
  const pitch = map.getPitch()
  if (!terrainActive && pitch >= PITCH_TERRAIN_ON) {
    terrainActive = true
    showTerrainOverlays()
  } else if (terrainActive && pitch <= PITCH_TERRAIN_OFF) {
    terrainActive = false
    hideTerrainOverlays()
  }
}

function visitGeometryCoords(geometry, visit) {
  if (!geometry) return
  const { type, coordinates: c } = geometry
  if (type === 'Point') visit(c[0], c[1])
  else if (type === 'LineString' || type === 'MultiPoint') {
    for (const p of c) visit(p[0], p[1])
  } else if (type === 'Polygon') {
    for (const ring of c) for (const p of ring) visit(p[0], p[1])
  } else if (type === 'MultiLineString') {
    for (const line of c) for (const p of line) visit(p[0], p[1])
  } else if (type === 'MultiPolygon') {
    for (const poly of c) for (const ring of poly) for (const p of ring) visit(p[0], p[1])
  } else if (type === 'GeometryCollection') {
    for (const g of geometry.geometries || []) visitGeometryCoords(g, visit)
  }
}

function visitGeoJsonData(data, visit) {
  if (!data) return
  if (data.type === 'FeatureCollection') {
    for (const f of data.features || []) visitGeometryCoords(f.geometry, visit)
  } else if (data.type === 'Feature') {
    visitGeometryCoords(data.geometry, visit)
  } else {
    visitGeometryCoords(data, visit)
  }
}

function expandMeshScopeBbox(bbox, minPadDeg = 0.04) {
  let [west, south, east, north] = bbox
  const spanLon = east - west
  const spanLat = north - south
  if (spanLon < minPadDeg) {
    const extra = (minPadDeg - spanLon) / 2
    west -= extra
    east += extra
  }
  if (spanLat < minPadDeg) {
    const extra = (minPadDeg - spanLat) / 2
    south -= extra
    north += extra
  }
  return [west, south, east, north]
}

function computeMeshScopeBbox() {
  const lons = []
  const lats = []
  const add = (lon, lat) => {
    if (Number.isFinite(lon) && Number.isFinite(lat)) {
      lons.push(lon)
      lats.push(lat)
    }
  }

  for (const layerId of MESH_SCOPE_LAYER_IDS) {
    const prefix = `${layerId}:`
    for (const [key, feature] of layerFeatureCache) {
      if (key.startsWith(prefix)) visitGeometryCoords(feature.geometry, add)
    }
    for (const [key, feature] of pinFeatureCache) {
      if (key.startsWith(prefix)) visitGeometryCoords(feature.geometry, add)
    }
  }

  for (const { bounds, coordinates } of viewshedRasterLayers) {
    if (bounds?.length === 4) {
      add(bounds[0], bounds[1])
      add(bounds[2], bounds[3])
    } else if (coordinates?.length) {
      for (const c of coordinates) add(c[0], c[1])
    }
  }

  if (!lons.length) return projectMeshScopeBbox

  const padLon = Math.max((Math.max(...lons) - Math.min(...lons)) * 0.1, 0.04)
  const padLat = Math.max((Math.max(...lats) - Math.min(...lats)) * 0.1, 0.04)
  return expandMeshScopeBbox([
    Math.min(...lons) - padLon,
    Math.min(...lats) - padLat,
    Math.max(...lons) + padLon,
    Math.max(...lats) + padLat,
  ])
}

function fitMapToMeshScope(bbox, { duration } = {}) {
  if (!bbox || bbox.length !== 4) return
  const opts = { ...MESH_SCOPE_FIT }
  if (duration != null) opts.duration = duration
  map.fitBounds(
    [
      [bbox[0], bbox[1]],
      [bbox[2], bbox[3]],
    ],
    opts,
  )
}

function resetMapToMeshScope() {
  if (!mapReady) return
  const bbox = computeMeshScopeBbox()
  if (bbox) {
    fitMapToMeshScope(bbox, { duration: 400 })
  } else if (typeof map.resetNorthPitch === 'function') {
    map.resetNorthPitch({ duration: 400 })
  } else {
    map.easeTo({ bearing: 0, pitch: 0, duration: 400 })
  }
}

function hookCompassReset() {
  const compass = map.getContainer().querySelector('.maplibregl-ctrl-compass')
  if (!compass || compass.dataset.peakyReset === '1') return
  compass.dataset.peakyReset = '1'
  compass.addEventListener(
    'click',
    (ev) => {
      ev.preventDefault()
      ev.stopImmediatePropagation()
      resetMapToMeshScope()
    },
    true,
  )
}

function firstOverlayLayerId() {
  for (const id of ['goals-layer', 'sites-layer', 'trials-layer', 'committed-layer', 'mesh_links-layer']) {
    if (map.getLayer(id)) return id
  }
  return undefined
}

async function loadSiteViewshed(projectSlug, siteSlug) {
  const res = await fetch(`/api/projects/${projectSlug}/viewsheds/${siteSlug}?ensure=false`)
  if (!res.ok) return null
  return res.json()
}

async function loadProjectMeshLinks(projectSlug) {
  const res = await fetch(`/api/projects/${projectSlug}/mesh-links`)
  if (!res.ok) return
  const gj = await res.json()
  const layerId = 'mesh_links'
  for (const f of gj.features || []) {
    const id = f.properties?.id || `${f.properties?.from}--${f.properties?.to}`
    layerFeatureCache.set(`${layerId}:${id}`, {
      ...f,
      properties: { ...(f.properties || {}), id },
    })
  }
  flushLayerFeatures(layerId)
  raiseOverlayLayers()
}

async function loadProjectViewsheds(projectSlug, sites) {
  const seenUrl = new Set()
  const results = await Promise.all((sites || []).map((s) => loadSiteViewshed(projectSlug, s.slug)))
  for (const r of results) {
    if (!r || seenUrl.has(r.url)) continue
    seenUrl.add(r.url)
    if (mapReady) addViewshedRaster(r, false)
    else pendingRasters.push(r)
  }
  renderSitesPanel()
}

function addViewshedRaster(r, refreshPanel = true) {
  const sourceId = `viewshed-raster-${r.slug}`
  const layerId = `${sourceId}-layer`
  const useImage = Boolean(r.url && r.coordinates)
  const useTiles = Boolean(!useImage && r.tile_url && r.bounds)

  if (map.getLayer(layerId)) map.removeLayer(layerId)
  if (map.getSource(sourceId)) map.removeSource(sourceId)

  if (useTiles) {
    map.addSource(sourceId, {
      type: 'raster',
      tiles: [r.tile_url],
      tileSize: 256,
      bounds: r.bounds,
      minzoom: r.minzoom ?? 0,
      maxzoom: r.maxzoom ?? 22,
    })
  } else if (r.url && r.coordinates) {
    map.addSource(sourceId, {
      type: 'image',
      url: r.url,
      coordinates: r.coordinates,
    })
  } else {
    return
  }

  const slug = r.slug || null
  if (slug && siteRegistry.has(slug)) {
    siteRegistry.get(slug).hasViewshed = true
  }

  const entry = slug ? siteRegistry.get(slug) : null
  const visible = entry ? entry.visible : true

  map.addLayer(
    {
      id: layerId,
      type: 'raster',
      source: sourceId,
      layout: { visibility: visible ? 'visible' : 'none' },
      paint: {
        'raster-opacity': r.opacity ?? 0.7,
        'raster-fade-duration': 0,
        'raster-resampling': 'linear',
      },
    },
    firstOverlayLayerId(),
  )
  viewshedRasterLayers.push({
    sourceId,
    layerId,
    slug,
    bounds: r.bounds,
    coordinates: r.coordinates,
  })
  raiseOverlayLayers()
  if (refreshPanel) renderSitesPanel()
}

function clearViewshedRasters() {
  for (const { sourceId, layerId } of viewshedRasterLayers) {
    if (map.getLayer(layerId)) map.removeLayer(layerId)
    if (map.getSource(sourceId)) map.removeSource(sourceId)
  }
  viewshedRasterLayers.length = 0
  for (const entry of siteRegistry.values()) {
    entry.hasViewshed = false
  }
  renderSitesPanel()
}

const EYE_OPEN_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>'
const EYE_CLOSED_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'

function setSiteViewshedVisible(slug, visible) {
  const entry = siteRegistry.get(slug)
  if (!entry?.hasViewshed) return
  entry.visible = visible
  for (const layer of viewshedRasterLayers) {
    if (layer.slug === slug && map.getLayer(layer.layerId)) {
      map.setLayoutProperty(layer.layerId, 'visibility', visible ? 'visible' : 'none')
    }
  }
  renderSitesPanel()
}

function toggleSiteViewshedVisibility(slug) {
  const entry = siteRegistry.get(slug)
  if (!entry?.hasViewshed) return
  setSiteViewshedVisible(slug, !entry.visible)
}

function flyToSite(slug) {
  const entry = siteRegistry.get(slug)
  if (!entry || !Number.isFinite(entry.lat) || !Number.isFinite(entry.lon)) return
  map.flyTo({ center: [entry.lon, entry.lat], zoom: Math.max(map.getZoom(), 10), duration: 600 })
}

function resetSitesPanel(sites) {
  siteRegistry.clear()
  for (const s of sites || []) {
    siteRegistry.set(s.slug, {
      slug: s.slug,
      label: siteDisplayLabel(s),
      lat: s.lat,
      lon: s.lon,
      hasViewshed: false,
      visible: true,
    })
  }
  renderSitesPanel()
}

function renderSitesPanel() {
  if (!sitesListEl) return
  sitesListEl.innerHTML = ''
  const sites = [...siteRegistry.values()].sort((a, b) => a.label.localeCompare(b.label))
  const withViewshed = sites.filter((s) => s.hasViewshed).length

  if (sitesPanelCount) {
    sitesPanelCount.textContent = sites.length ? `${withViewshed}/${sites.length}` : ''
  }
  if (sitesListEmptyEl) {
    sitesListEmptyEl.hidden = sites.length > 0
  }

  for (const site of sites) {
    const li = document.createElement('li')
    li.className = `site-row${site.hasViewshed ? '' : ' no-viewshed'}`

    const visBtn = document.createElement('button')
    visBtn.type = 'button'
    visBtn.className = `site-visibility${site.hasViewshed && !site.visible ? ' hidden' : ''}`
    visBtn.disabled = !site.hasViewshed
    visBtn.innerHTML = site.hasViewshed && site.visible ? EYE_OPEN_SVG : EYE_CLOSED_SVG
    visBtn.title = site.hasViewshed
      ? site.visible
        ? 'Hide viewshed'
        : 'Show viewshed'
      : 'No viewshed loaded'
    visBtn.setAttribute(
      'aria-label',
      site.hasViewshed
        ? site.visible
          ? `Hide viewshed for ${site.label}`
          : `Show viewshed for ${site.label}`
        : `No viewshed for ${site.label}`,
    )
    visBtn.addEventListener('click', (ev) => {
      ev.stopPropagation()
      toggleSiteViewshedVisibility(site.slug)
    })

    const label = document.createElement('span')
    label.className = 'site-label'
    label.textContent = site.label
    label.title = site.label
    label.addEventListener('click', () => flyToSite(site.slug))

    li.append(visBtn, label)
    sitesListEl.appendChild(li)
  }
}

function setSitesPanelCollapsed(collapsed) {
  sitesPanelCollapsed = collapsed
  sitesPanel?.classList.toggle('collapsed', collapsed)
  sitesPanelToggle?.setAttribute('aria-expanded', collapsed ? 'false' : 'true')
}

function removeBasemapReference() {
  if (map.getLayer(BASEMAP_REFERENCE_LAYER)) map.removeLayer(BASEMAP_REFERENCE_LAYER)
  if (map.getSource(BASEMAP_REFERENCE_SOURCE)) map.removeSource(BASEMAP_REFERENCE_SOURCE)
}

function ensureBasemapReference(bm) {
  if (!bm.referenceTiles) {
    removeBasemapReference()
    return
  }
  const refSrc = map.getSource(BASEMAP_REFERENCE_SOURCE)
  if (!refSrc) {
    map.addSource(BASEMAP_REFERENCE_SOURCE, {
      type: 'raster',
      tiles: bm.referenceTiles,
      tileSize: 256,
      maxzoom: bm.maxzoom,
    })
  } else if (typeof refSrc.setTiles === 'function') {
    refSrc.setTiles(bm.referenceTiles)
  }
  if (!map.getLayer(BASEMAP_REFERENCE_LAYER)) {
    map.addLayer(
      {
        id: BASEMAP_REFERENCE_LAYER,
        type: 'raster',
        source: BASEMAP_REFERENCE_SOURCE,
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 0 },
      },
      firstOverlayLayerId(),
    )
  }
}

function setBasemap(key) {
  const bm = BASEMAPS[key]
  if (!bm || !mapReady) return
  const src = map.getSource('basemap')
  if (!src || typeof src.setTiles !== 'function') return
  src.setTiles(bm.tiles)
  map.setMaxZoom(bm.maxzoom)
  if (bm.referenceTiles) ensureBasemapReference(bm)
  else removeBasemapReference()
}

function ensureLayer(layerId) {
  if (layers.has(layerId)) return layers.get(layerId)
  const ids = { source: `${layerId}-src`, layer: `${layerId}-layer` }
  if (!map.getSource(ids.source)) {
    map.addSource(ids.source, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
    const isLine = layerId === 'mesh_links'
    const isCircle = layerId === 'sites' || layerId === 'goals' || layerId === 'trials'
    map.addLayer({
      id: ids.layer,
      type: isLine ? 'line' : isCircle ? 'circle' : 'fill',
      source: ids.source,
      paint:
        layerId === 'mesh_links'
          ? { 'line-color': '#38bdf8', 'line-width': 2.5, 'line-opacity': 0.85 }
          : layerId === 'goals'
            ? {
                'circle-radius': 8,
                'circle-color': '#fb923c',
                'circle-stroke-width': 2,
                'circle-stroke-color': '#fff',
              }
            : layerId === 'trials'
              ? {
                  'circle-radius': 7,
                  'circle-color': '#c084fc',
                  'circle-stroke-width': 2,
                  'circle-stroke-color': '#fff',
                }
              : layerId === 'sites'
                ? {
                    'circle-radius': 6,
                    'circle-color': '#60a5fa',
                    'circle-stroke-width': 2,
                    'circle-stroke-color': '#fff',
                  }
                : layerId === 'viewsheds'
                  ? {
                      'fill-color': '#38bdf8',
                      'fill-opacity': 0.28,
                      'fill-outline-color': '#7dd3fc',
                    }
                  : { 'fill-color': '#34d399', 'fill-opacity': 0.22, 'fill-outline-color': '#34d399' },
    })
    if (layerId === 'viewsheds') {
      map.addLayer({
        id: `${layerId}-outline`,
        type: 'line',
        source: ids.source,
        paint: {
          'line-color': '#7dd3fc',
          'line-width': 1.5,
          'line-opacity': 0.75,
        },
      })
      ids.outline = `${layerId}-outline`
    }
    if (layerId === 'sites') {
      ids.labelLayer = 'sites-labels-layer'
      map.addLayer({
        id: ids.labelLayer,
        type: 'symbol',
        source: ids.source,
        layout: {
          'text-field': ['get', 'label'],
          'text-anchor': 'top',
          'text-offset': [0, 0.8],
          'text-size': 11,
          'text-font': ['Open Sans Regular'],
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': '#f1f5f9',
          'text-halo-color': '#0f172a',
          'text-halo-width': 1.5,
        },
      })
    }
  }
  layers.set(layerId, ids)
  return ids
}

function flushLayerFeatures(layerId) {
  const ids = ensureLayer(layerId)
  const prefix = `${layerId}:`
  const features = []
  for (const [key, feature] of layerFeatureCache) {
    if (key.startsWith(prefix)) features.push(feature)
  }
  map.getSource(ids.source).setData({ type: 'FeatureCollection', features })
}

function flushPinLayer(layerId) {
  const ids = ensureLayer(layerId)
  const prefix = `${layerId}:`
  const features = []
  for (const [key, feature] of pinFeatureCache) {
    if (key.startsWith(prefix)) features.push(feature)
  }
  map.getSource(ids.source).setData({ type: 'FeatureCollection', features })
  raiseOverlayLayers()
}

function setStatus(state, label) {
  statusEl.className = `status-pill${state ? ` ${state}` : ''}`
  statusEl.textContent = label
}

function appendChat(text, kind = 'build', label = null) {
  if (chatEmptyEl) chatEmptyEl.hidden = true

  const div = document.createElement('div')
  div.className = `msg ${kind}`

  if (label) {
    const lbl = document.createElement('span')
    lbl.className = 'msg-label'
    lbl.textContent = label
    div.appendChild(lbl)
  }

  const body = document.createElement('span')
  body.className = 'msg-body'
  body.textContent = text
  div.appendChild(body)

  chatEl.appendChild(div)
  chatEl.scrollTop = chatEl.scrollHeight
  return div
}

let chatPendingEl = null
let chatInFlight = false
let chatAbortController = null
let chatRequestSeq = 0

function setChatPendingLabel(label) {
  if (!chatPendingEl) return
  const body = chatPendingEl.querySelector('.chat-pending-text')
  if (!body) return
  body.textContent = label
  const dots = document.createElement('span')
  dots.className = 'chat-pending-dots'
  dots.setAttribute('aria-hidden', 'true')
  dots.textContent = '…'
  body.appendChild(dots)
}

function setChatComposerBusy(active) {
  chatInFlight = active
  chatSendBtn.type = active ? 'button' : 'submit'
  chatSendBtn.textContent = active ? 'Stop' : 'Send'
  chatSendBtn.classList.toggle('stop', active)
  chatSendBtn.disabled = false
}

function setChatPending(active) {
  if (!active) {
    chatPendingEl?.remove()
    chatPendingEl = null
    return
  }
  chatPendingEl?.remove()
  chatPendingEl = null

  if (chatEmptyEl) chatEmptyEl.hidden = true
  const div = document.createElement('div')
  div.className = 'msg assistant pending'
  div.setAttribute('aria-live', 'polite')
  div.setAttribute('aria-busy', 'true')

  const lbl = document.createElement('span')
  lbl.className = 'msg-label'
  lbl.textContent = 'Assistant'
  div.appendChild(lbl)

  const body = document.createElement('span')
  body.className = 'chat-pending-text'
  body.textContent = 'Thinking'
  const dots = document.createElement('span')
  dots.className = 'chat-pending-dots'
  dots.setAttribute('aria-hidden', 'true')
  dots.textContent = '…'
  body.appendChild(dots)
  div.appendChild(body)

  chatEl.appendChild(div)
  chatEl.scrollTop = chatEl.scrollHeight
  chatPendingEl = div
}

function beginAssistantReply() {
  setChatPending(false)
  if (chatEmptyEl) chatEmptyEl.hidden = true
  const div = document.createElement('div')
  div.className = 'msg assistant streaming'
  div.setAttribute('aria-live', 'polite')

  const lbl = document.createElement('span')
  lbl.className = 'msg-label'
  lbl.textContent = 'Assistant'
  div.appendChild(lbl)

  const body = document.createElement('span')
  body.className = 'msg-body'
  div.appendChild(body)

  chatEl.appendChild(div)
  chatEl.scrollTop = chatEl.scrollHeight
  return body
}

function beginThinkingBlock() {
  setChatPending(false)
  if (chatEmptyEl) chatEmptyEl.hidden = true
  const details = document.createElement('details')
  details.className = 'msg thinking streaming'
  details.open = false

  const summary = document.createElement('summary')
  summary.className = 'chat-thinking-summary'
  summary.textContent = 'Thinking'
  details.appendChild(summary)

  const body = document.createElement('div')
  body.className = 'chat-thinking-body'
  body.setAttribute('aria-live', 'polite')
  details.appendChild(body)

  chatEl.appendChild(details)
  chatEl.scrollTop = chatEl.scrollHeight
  return { details, body }
}

const pendingToolCallBlocks = new Map()

function formatToolJson(value) {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function beginToolCallBlock(callId, name, args) {
  setChatPending(false)
  if (chatEmptyEl) chatEmptyEl.hidden = true

  const details = document.createElement('details')
  details.className = 'msg tool-call pending'
  details.open = false
  details.dataset.callId = callId

  const summary = document.createElement('summary')
  summary.className = 'chat-tool-summary'

  const nameEl = document.createElement('span')
  nameEl.className = 'chat-tool-name'
  nameEl.textContent = name || 'tool'

  const statusEl = document.createElement('span')
  statusEl.className = 'chat-tool-status'
  statusEl.setAttribute('aria-hidden', 'true')
  summary.append(nameEl, statusEl)
  details.appendChild(summary)

  const body = document.createElement('div')
  body.className = 'chat-tool-body'
  const argsSection = document.createElement('div')
  argsSection.className = 'chat-tool-section'
  const argsLabel = document.createElement('div')
  argsLabel.className = 'chat-tool-section-label'
  argsLabel.textContent = 'Arguments'
  const argsPre = document.createElement('pre')
  argsPre.className = 'chat-tool-pre'
  argsPre.textContent = formatToolJson(args)
  argsSection.append(argsLabel, argsPre)
  body.appendChild(argsSection)
  details.appendChild(body)

  chatEl.appendChild(details)
  chatEl.scrollTop = chatEl.scrollHeight
  pendingToolCallBlocks.set(callId, { details, statusEl, body })
  return details
}

function completeToolCallBlock(callId, name, result) {
  const block = pendingToolCallBlocks.get(callId)
  if (!block) {
    appendChat(`${name}: ${formatToolJson(result)}`, 'tool', 'Tool result')
    return
  }
  pendingToolCallBlocks.delete(callId)

  const { details, statusEl, body } = block
  details.classList.remove('pending')
  details.classList.add('done')
  statusEl.textContent = '✓'
  statusEl.setAttribute('aria-label', 'Completed')

  const resultSection = document.createElement('div')
  resultSection.className = 'chat-tool-section'
  const resultLabel = document.createElement('div')
  resultLabel.className = 'chat-tool-section-label'
  resultLabel.textContent = 'Result'
  const resultPre = document.createElement('pre')
  resultPre.className = 'chat-tool-pre'
  resultPre.textContent = formatToolJson(result)
  resultSection.append(resultLabel, resultPre)
  body.appendChild(resultSection)

  chatEl.scrollTop = chatEl.scrollHeight
}

function finishPendingToolCalls(mark = '—') {
  for (const { details, statusEl } of pendingToolCallBlocks.values()) {
    details.classList.remove('pending')
    statusEl.textContent = mark
  }
  pendingToolCallBlocks.clear()
}

async function consumeChatStream(res, onEvent) {
  const reader = res.body?.getReader()
  if (!reader) throw new Error('Streaming not supported')

  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let splitAt
    while ((splitAt = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, splitAt)
      buffer = buffer.slice(splitAt + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data: ')) continue
        onEvent(JSON.parse(line.slice(6)))
      }
    }
  }
  if (buffer.trim()) {
    for (const line of buffer.split('\n')) {
      if (!line.startsWith('data: ')) continue
      onEvent(JSON.parse(line.slice(6)))
    }
  }
}

const handlers = {
  'build.started': (m) => {
    setStatus('running', 'building')
    appendChat(`Project: ${m.project}`, 'build', 'Build started')
  },
  'build.phase': (m) => {
    setStatus('running', m.name)
    appendChat(m.name, 'build', 'Phase')
  },
  'build.finished': (m) => {
    setStatus(m.ok ? 'done' : 'error', m.ok ? 'complete' : 'failed')
    appendChat(m.ok ? 'Build finished successfully.' : `Exit code ${m.exit_code}`, 'build', 'Finished')
  },
  'build.error': (m) => {
    setStatus('error', 'error')
    appendChat(m.message, 'error', 'Error')
  },
  'build.target': (m) => appendChat(`${m.target} — ${m.status}`, 'tool', 'Target'),
  'chat.system': (m) => appendChat(m.text, 'system', 'System'),
  'chat.user': (m) => appendChat(m.text, 'user', 'User'),
  'chat.assistant': (m) => appendChat(m.text, 'assistant', 'Assistant'),
  'chat.tool_call': (m) => beginToolCallBlock(m.call_id, m.name, m.arguments),
  'chat.tool_result': (m) => completeToolCallBlock(m.call_id, m.name, m.result),
  'chat.divider': (m) => appendChat(m.label, 'divider'),
  'log.line': (m) => appendChat(m.text, 'tool', 'Log'),
  'map.clear_layer': (m) => {
    const prefix = `${m.layer_id}:`
    for (const key of [...layerFeatureCache.keys()]) {
      if (key.startsWith(prefix)) layerFeatureCache.delete(key)
    }
    for (const key of [...pinFeatureCache.keys()]) {
      if (key.startsWith(prefix)) pinFeatureCache.delete(key)
    }
    flushLayerFeatures(m.layer_id)
    flushPinLayer(m.layer_id)
  },
  'map.pin': (m) => {
    if (!mapReady) {
      pendingPins.push(m)
      return
    }
    const layerId = m.layer_id
    const feature = {
      type: 'Feature',
      properties: { id: m.id, label: m.label || m.id },
      geometry: { type: 'Point', coordinates: [m.lon, m.lat] },
    }
    pinFeatureCache.set(`${layerId}:${m.id}`, feature)
    if (layerId === 'trials') rememberChatTrialPin({ id: m.id, label: m.label || m.id, lat: m.lat, lon: m.lon })
    flushPinLayer(layerId)
  },
  'map.line': (m) => {
    if (!mapReady) return
    const layerId = m.layer_id
    const id = m.id || `${layerId}:${m.coordinates?.length || 0}`
    layerFeatureCache.set(`${layerId}:${id}`, {
      type: 'Feature',
      properties: { id, ...(m.properties || {}) },
      geometry: { type: 'LineString', coordinates: m.coordinates },
    })
    flushLayerFeatures(layerId)
    raiseOverlayLayers()
  },
  'map.polygon': (m) => {
    if (!mapReady) return
    const layerId = m.layer_id
    const id = m.id || layerId
    const gj = m.geojson
    const incoming =
      gj?.type === 'FeatureCollection'
        ? gj.features
        : gj?.type === 'Feature'
          ? [gj]
          : []
    for (const f of incoming) {
      const featId = f.properties?.id || id
      layerFeatureCache.set(`${layerId}:${featId}`, {
        ...f,
        properties: { ...(f.properties || {}), id: featId },
      })
    }
    flushLayerFeatures(layerId)
  },
  'map.fit_bounds': (m) => {
    if (!m.bbox) return
    projectMeshScopeBbox = m.bbox
    fitMapToMeshScope(m.bbox)
  },
  'map.viewshed': (m) => {
    if (m.lat != null && m.lon != null) markChatTrialPinViewshed(m.lat, m.lon)
    if (mapReady) addViewshedRaster(m)
    else pendingRasters.push(m)
  },
}

function routeOp(msg) {
  const fn = handlers[msg.op]
  if (fn) fn(msg)
}

function clearOverlayLayers() {
  clearViewshedRasters()
  pinFeatureCache.clear()
  for (const layerId of ['viewsheds', 'goals', 'sites', 'trials', 'committed', 'mesh_links']) {
    const outlineId = `${layerId}-outline`
    if (map.getLayer(outlineId)) map.removeLayer(outlineId)
    if (layerId === 'sites' && map.getLayer('sites-labels-layer')) map.removeLayer('sites-labels-layer')
    if (map.getLayer(`${layerId}-layer`)) map.removeLayer(`${layerId}-layer`)
    if (map.getSource(`${layerId}-src`)) map.removeSource(`${layerId}-src`)
    layers.delete(layerId)
    const prefix = `${layerId}:`
    for (const key of [...layerFeatureCache.keys()]) {
      if (key.startsWith(prefix)) layerFeatureCache.delete(key)
    }
  }
}

function clearChat() {
  finishPendingToolCalls()
  chatEl.querySelectorAll('.msg').forEach((el) => el.remove())
  if (chatEmptyEl) chatEmptyEl.hidden = false
  resetChatHistory()
}

function rememberChatTrialPin({ id, label, lat, lon, site_slug = null }) {
  chatMapPins.set(id, {
    pin_id: id,
    label: label || id,
    lat,
    lon,
    site_slug: site_slug || chatMapPins.get(id)?.site_slug || null,
    has_viewshed: chatMapPins.get(id)?.has_viewshed || false,
  })
}

function markChatTrialPinViewshed(lat, lon) {
  for (const pin of chatMapPins.values()) {
    if (Math.abs(pin.lat - lat) < 0.001 && Math.abs(pin.lon - lon) < 0.001) {
      pin.has_viewshed = true
    }
  }
}

function trialMapPinsPayload() {
  return [...chatMapPins.values()]
}

function resetChatHistory() {
  chatHistory.length = 0
  chatMapPins.clear()
  chatSummary = null
  scheduleChatContextRefresh('')
}

function rememberChatTurn(userText, assistantText) {
  const user = String(userText || '').trim()
  const assistant = String(assistantText || '').trim()
  if (!user) return
  chatHistory.push({ role: 'user', content: user })
  if (assistant) chatHistory.push({ role: 'assistant', content: assistant })
  scheduleChatContextRefresh('')
}

let pendingChatTurn = null

function syncPendingAssistantText(text, turn = pendingChatTurn) {
  if (!turn || turn.committed) return
  turn.assistantText = String(text || '').trim()
}

function markPendingChatTurnCancelled(turn = pendingChatTurn) {
  if (turn) turn.cancelled = true
}

function markPendingChatTurnErrored(turn = pendingChatTurn) {
  if (turn) turn.errored = true
}

function commitPendingChatTurn(turn = pendingChatTurn) {
  if (!turn || turn.committed || turn.cancelled || turn.errored) {
    return
  }
  const { userText, assistantText } = turn
  if (!userText) return
  turn.committed = true
  rememberChatTurn(userText, assistantText)
}

function finalizePendingChatTurn() {
  commitPendingChatTurn()
  pendingChatTurn = null
}

function chatContextPayload(pendingMessage = '') {
  return {
    project_slug: projectSel.value || null,
    history: chatHistory,
    summary: chatSummary,
    map_pins: trialMapPinsPayload(),
    message: pendingMessage || undefined,
  }
}

function buildChatContextExport() {
  const payload = {
    exported_at: new Date().toISOString(),
    ...chatContextPayload(),
    context_window: { ...chatContextState },
  }
  if (pendingChatTurn && !pendingChatTurn.committed) {
    payload.pending_turn = {
      user: pendingChatTurn.userText,
      assistant: pendingChatTurn.assistantText || null,
      cancelled: pendingChatTurn.cancelled,
      errored: pendingChatTurn.errored,
    }
  }
  return payload
}

let chatCopyContextResetTimer = null

function flashChatCopyContextFeedback() {
  if (!chatCopyContextBtn) return
  chatCopyContextBtn.classList.add('copied')
  chatCopyContextBtn.title = 'Copied!'
  if (chatCopyContextResetTimer) clearTimeout(chatCopyContextResetTimer)
  chatCopyContextResetTimer = setTimeout(() => {
    chatCopyContextResetTimer = null
    chatCopyContextBtn.classList.remove('copied')
    chatCopyContextBtn.title = 'Copy context as JSON'
  }, 1500)
}

async function copyChatContextJson() {
  const json = JSON.stringify(buildChatContextExport(), null, 2)
  try {
    await navigator.clipboard.writeText(json)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = json
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    ta.remove()
  }
  flashChatCopyContextFeedback()
}

function formatTokenCount(n) {
  const v = Number(n)
  if (!Number.isFinite(v) || v < 0) return '—'
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 10_000) return `${Math.round(v / 1000)}k`
  return String(Math.round(v))
}

function applyChatContext(ctx) {
  if (!ctx || !chatContextFill) return
  chatContextState = ctx
  const pct = Math.max(0, Math.min(100, Number(ctx.usage_pct) || 0))
  const used = ctx.used_tokens ?? ctx.estimated_tokens
  const total = ctx.num_ctx
  chatContextFill.style.width = `${pct}%`
  chatContextFill.classList.toggle('warn', ctx.status === 'warn')
  chatContextFill.classList.toggle('full', ctx.status === 'full')
  if (chatContextDetail && used != null && total != null) {
    const avail = ctx.available_tokens ?? Math.max(0, total - used)
    chatContextDetail.textContent = `${formatTokenCount(used)} / ${formatTokenCount(total)} (${formatTokenCount(avail)} free)`
    chatContextDetail.title = [
      `Used: ${used}`,
      `Allocated: ${total}`,
      `Available: ${avail}`,
      ctx.token_count_source ? `Count: ${ctx.token_count_source}` : '',
      ctx.limit_source ? `Limit: ${ctx.limit_source}` : '',
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (chatContextPct) chatContextPct.textContent = `${pct.toFixed(1)}%`
  if (chatContextTrack) {
    chatContextTrack.setAttribute('aria-valuenow', String(Math.round(pct)))
    chatContextTrack.setAttribute(
      'aria-valuetext',
      used != null && total != null ? `${used} of ${total} tokens` : `${pct}%`,
    )
  }

  const showSummarize = ctx.status === 'warn' || ctx.status === 'full'
  if (chatSummarizeBtn) chatSummarizeBtn.hidden = !showSummarize

  if (!chatContextNote) return
  if (ctx.status === 'full') {
    chatContextNote.hidden = false
    chatContextNote.className = 'chat-context-note full'
    chatContextNote.textContent =
      'Context window nearly full. Summarize to compress earlier turns before continuing.'
  } else if (ctx.status === 'warn') {
    chatContextNote.hidden = false
    chatContextNote.className = 'chat-context-note'
    chatContextNote.textContent = 'Context window filling up. Summarize soon to keep the full thread.'
  } else {
    chatContextNote.hidden = true
    chatContextNote.textContent = ''
  }
}

async function refreshChatContext(pendingMessage = '') {
  if (!chatContextFill) return chatContextState
  try {
    const res = await fetch('/api/chat/context', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(chatContextPayload(pendingMessage)),
    })
    if (!res.ok) return chatContextState
    applyChatContext(await res.json())
  } catch (err) {
    console.warn('refreshChatContext failed:', err)
  }
  return chatContextState
}

function scheduleChatContextRefresh(pendingMessage = '') {
  if (chatContextRefreshTimer) clearTimeout(chatContextRefreshTimer)
  chatContextRefreshTimer = setTimeout(() => {
    chatContextRefreshTimer = null
    void refreshChatContext(pendingMessage)
  }, 180)
}

async function summarizeChatContext({ announce = true } = {}) {
  if (chatSummarizeInFlight) return false
  if (!chatHistory.length && !chatSummary) return false

  chatSummarizeInFlight = true
  if (chatSummarizeBtn) chatSummarizeBtn.disabled = true
  setStatus('thinking', 'summarizing')
  try {
    const res = await fetch('/api/chat/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(chatContextPayload()),
    })
    const payload = await res.json().catch(() => null)
    if (!res.ok) {
      appendChat(payload?.detail || res.statusText || 'Summarize failed', 'error', 'Error')
      return false
    }
    chatSummary = payload.summary || chatSummary
    chatHistory.length = 0
    if (announce) {
      appendChat('Earlier conversation summarized and compressed into context memory.', 'divider', 'Context')
    }
    applyChatContext(payload.context || {})
    return true
  } catch (err) {
    appendChat(String(err), 'error', 'Error')
    return false
  } finally {
    chatSummarizeInFlight = false
    if (chatSummarizeBtn) chatSummarizeBtn.disabled = false
    if (!buildBtn.disabled) setStatus('', 'idle')
    else setStatus('running', 'building')
  }
}

async function loadProjects() {
  const res = await fetch('/api/projects')
  const projects = await res.json()
  projectSel.innerHTML = ''
  for (const p of projects) {
    const opt = document.createElement('option')
    opt.value = p.slug
    opt.textContent = `${p.slug} · ${p.strategy} · ${p.site_count} sites`
    projectSel.appendChild(opt)
  }
  if (projects[0]) await loadContext(projects[0].slug)
}

async function loadContext(slug) {
  const projectChanged = currentProjectSlug !== slug
  currentProjectSlug = slug
  projectMeshScopeBbox = null
  if (projectChanged) {
    finalizePendingChatTurn()
    resetChatHistory()
  }
  clearOverlayLayers()
  const res = await fetch(`/api/projects/${slug}/context`)
  const ctx = await res.json()
  resetSitesPanel(ctx.sites)

  for (const g of ctx.goals || []) {
    handlers['map.pin']({ layer_id: 'goals', id: g.key, lat: g.lat, lon: g.lon, label: g.key })
  }
  for (const s of ctx.sites || []) {
    handlers['map.pin']({
      layer_id: 'sites',
      id: s.slug,
      lat: s.lat,
      lon: s.lon,
      label: siteDisplayLabel(s),
    })
  }
  raiseOverlayLayers()
  projectMeshScopeBbox = ctx.bbox || null
  try {
    if (ctx.bbox) fitMapToMeshScope(ctx.bbox)
  } catch (err) {
    console.warn('fitMapToMeshScope failed:', err)
  }
  await loadProjectViewsheds(slug, ctx.sites)
  await loadProjectMeshLinks(slug)
  scheduleChatContextRefresh('')
}

sitesPanelToggle?.addEventListener('click', () => {
  setSitesPanelCollapsed(!sitesPanelCollapsed)
})

projectSel.addEventListener('change', () => loadContext(projectSel.value))
basemapSel.addEventListener('change', () => setBasemap(basemapSel.value))

function stopChatAgent() {
  chatAbortController?.abort()
}

async function sendChatMessage(text) {
  finalizePendingChatTurn()
  const mySeq = ++chatRequestSeq
  chatAbortController?.abort()

  const ctx = await refreshChatContext(text)
  if (mySeq !== chatRequestSeq) return
  if (ctx.status === 'full') {
    appendChat(
      'Context window is nearly full. Summarize the conversation first, then send your message again.',
      'error',
      'Context',
    )
    chatInput.focus()
    return
  }

  finishPendingToolCalls('—')

  appendChat(text, 'user', 'You')
  const turn = {
    userText: String(text || '').trim(),
    assistantText: '',
    errored: false,
    cancelled: false,
    committed: false,
  }
  pendingChatTurn = turn
  setChatComposerBusy(true)
  setChatPending(true)
  setStatus('thinking', 'thinking')
  chatAbortController = new AbortController()
  let assistantBody = null
  let thinkingDetails = null
  let thinkingBody = null
  let hadChatError = false
  let wasCancelled = false
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        project_slug: projectSel.value || null,
        history: chatHistory,
        summary: chatSummary,
        map_pins: trialMapPinsPayload(),
      }),
      signal: chatAbortController.signal,
    })
    if (!res.ok) {
      markPendingChatTurnErrored(turn)
      const payload = await res.json().catch(() => null)
      const detail = payload?.detail || res.statusText || 'Chat request failed'
      appendChat(String(detail), 'error', 'Error')
      return
    }
    await consumeChatStream(res, (msg) => {
      if (msg.op === 'chat.cancelled') {
        wasCancelled = true
        markPendingChatTurnCancelled(turn)
        return
      }
      if (msg.op === 'chat.error') {
        hadChatError = true
        markPendingChatTurnErrored(turn)
        if (mySeq === chatRequestSeq) appendChat(String(msg.message), 'error', 'Error')
        return
      }
      if (msg.op === 'chat.done') {
        if (mySeq === chatRequestSeq) setChatPendingLabel('')
        if (!wasCancelled && !hadChatError) {
          syncPendingAssistantText(assistantBody?.textContent || '', turn)
          commitPendingChatTurn(turn)
        }
        return
      }
      if (mySeq !== chatRequestSeq) return
      if (msg.op === 'chat.started' || msg.op === 'chat.context') {
        if (msg.op === 'chat.started') setChatPendingLabel('Waiting for model')
        if (msg.context) applyChatContext(msg.context)
      } else if (msg.op === 'chat.status') {
        setChatPendingLabel(String(msg.text || 'Working…'))
      } else if (msg.op === 'chat.thinking.delta') {
        if (!thinkingBody) {
          const block = beginThinkingBlock()
          thinkingDetails = block.details
          thinkingBody = block.body
        }
        thinkingBody.textContent += msg.text
        chatEl.scrollTop = chatEl.scrollHeight
      } else if (msg.op === 'chat.delta') {
        if (!assistantBody) assistantBody = beginAssistantReply()
        assistantBody.textContent += msg.text
        syncPendingAssistantText(assistantBody.textContent, turn)
        chatEl.scrollTop = chatEl.scrollHeight
      } else if (msg.op.startsWith('map.')) {
        routeOp(msg)
      } else if (msg.op === 'chat.tool_call' || msg.op === 'chat.tool_result') {
        routeOp(msg)
      }
    })
    if (mySeq !== chatRequestSeq) return
    thinkingDetails?.classList.remove('streaming')
    assistantBody?.closest('.msg')?.classList.remove('streaming')
    if (wasCancelled) {
      finishPendingToolCalls()
      appendChat('Stopped.', 'system', 'System')
      scheduleChatContextRefresh('')
    } else if (!hadChatError) {
      syncPendingAssistantText(assistantBody?.textContent || '', turn)
      commitPendingChatTurn(turn)
    } else {
      scheduleChatContextRefresh('')
    }
  } catch (err) {
    if (mySeq !== chatRequestSeq) return
    if (err?.name === 'AbortError') {
      markPendingChatTurnCancelled(turn)
      finishPendingToolCalls()
      thinkingDetails?.classList.remove('streaming')
      assistantBody?.closest('.msg')?.classList.remove('streaming')
      appendChat('Stopped.', 'system', 'System')
      scheduleChatContextRefresh('')
    } else {
      markPendingChatTurnErrored(turn)
      appendChat(String(err), 'error', 'Error')
    }
  } finally {
    if (mySeq !== chatRequestSeq) return
    if (pendingChatTurn === turn) pendingChatTurn = null
    chatAbortController = null
    setChatPending(false)
    setChatComposerBusy(false)
    setStatus('', 'idle')
    chatInput.focus()
  }
}

chatSendBtn.addEventListener('click', (ev) => {
  if (!chatInFlight) return
  ev.preventDefault()
  stopChatAgent()
})

chatForm.addEventListener('submit', (ev) => {
  ev.preventDefault()
  const text = chatInput.value.trim()
  if (!text) return
  if (chatInFlight) stopChatAgent()
  chatInput.value = ''
  void sendChatMessage(text)
})

chatSummarizeBtn?.addEventListener('click', () => {
  void summarizeChatContext({ announce: true })
})

chatCopyContextBtn?.addEventListener('click', () => {
  void copyChatContextJson()
})

chatInput?.addEventListener('input', () => {
  scheduleChatContextRefresh(chatInput.value.trim())
})

map.on('load', () => {
  mapReady = true
  setBasemap(basemapSel.value)
  hookCompassReset()
  for (const pin of pendingPins) handlers['map.pin'](pin)
  pendingPins.length = 0
  for (const r of pendingRasters) addViewshedRaster(r, false)
  pendingRasters.length = 0
  renderSitesPanel()
  loadProjects()
})

map.on('pitch', syncTerrainFromPitch)
map.on('moveend', syncTerrainFromPitch)
