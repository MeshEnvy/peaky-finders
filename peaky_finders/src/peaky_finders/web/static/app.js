/** Peaky Web — SSE op router + MapLibre */

const projectSel = document.getElementById('project')
const basemapSel = document.getElementById('basemap')
const statusEl = document.getElementById('status')
const chatEl = document.getElementById('chat')
const chatEmptyEl = document.getElementById('chat-empty')
const chatForm = document.getElementById('chat-form')
const chatModelSel = document.getElementById('chat-model')
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
const sitesPanelScrollEl = document.getElementById('sites-panel-scroll')
const sitesListEmptyEl = document.getElementById('sites-list-empty')

const siteRegistry = new Map()
let sitesPanelCollapsed = false
const sitesPanelSectionCollapsed = { installed: false, planned: false, goal: false }

const SITE_PANEL_SECTIONS = [
  { id: 'installed', label: 'Installed' },
  { id: 'planned', label: 'Planned' },
  { id: 'goal', label: 'Goals' },
]

function siteDisplayLabel(site) {
  return (site.name || '').trim() || site.slug
}

function isGoalSite(site) {
  return String(site?.type || '').toLowerCase() === 'goal'
}

function sitePanelCategory(site) {
  const t = String(site?.type || '').toLowerCase()
  if (t === 'goal') return 'goal'
  if (t === 'planned' || t === 'suggested') return 'planned'
  return 'installed'
}

function rfSites(sites) {
  return (sites || []).filter((s) => !isGoalSite(s))
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

const MARKER_ICON_PX = 48
const MARKER_ICON_LAYOUT = {
  'icon-size': 0.72,
  'icon-allow-overlap': true,
  'icon-anchor': 'center',
}

const MARKER_SVGS = {
  'marker-goal': `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="${MARKER_ICON_PX}" height="${MARKER_ICON_PX}"><path fill="#fb923c" d="m10.95 14l4.95-4.95l-1.425-1.4l-3.525 3.525L9.525 9.75L8.1 11.175zM5 21V5q0-.825.588-1.412T7 3h10q.825 0 1.413.588T19 5v16l-7-3z"/></svg>`,
  'marker-planned': `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="${MARKER_ICON_PX}" height="${MARKER_ICON_PX}"><g fill="none" stroke="#fbbf24" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"><path d="m13.6 21.076l5.46-3.152c.584-.337.875-.505 1.087-.74a2 2 0 0 0 .416-.72c.097-.301.097-.637.097-1.307V8.843c0-.67 0-1.006-.098-1.307a2 2 0 0 0-.416-.72c-.21-.234-.5-.402-1.079-.736L13.6 2.924c-.583-.337-.874-.505-1.184-.57a2 2 0 0 0-.832 0c-.31.065-.601.233-1.184.57L4.938 6.077c-.582.336-.873.504-1.084.739a2 2 0 0 0-.416.72c-.098.302-.098.638-.098 1.311v6.305c0 .673 0 1.01.098 1.311a2 2 0 0 0 .416.72c.211.236.503.404 1.085.74l5.46 3.153c.584.337.875.505 1.185.57c.274.059.558.059.832 0c.31-.065.602-.233 1.185-.57"/><path d="M9 12a3 3 0 1 0 6 0a3 3 0 0 0-6 0"/></g></svg>`,
  'marker-installed': `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="${MARKER_ICON_PX}" height="${MARKER_ICON_PX}"><path fill="#60a5fa" d="M6 20.5V5h7.192l.4 2H19v8h-5.192l-.4-2H7v7.5z"/></svg>`,
}

let markerImagesReady = false

function svgMarkupToImageData(svgMarkup) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(new Blob([svgMarkup], { type: 'image/svg+xml;charset=utf-8' }))
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = MARKER_ICON_PX
      canvas.height = MARKER_ICON_PX
      const ctx = canvas.getContext('2d')
      ctx.drawImage(img, 0, 0, MARKER_ICON_PX, MARKER_ICON_PX)
      URL.revokeObjectURL(url)
      resolve(ctx.getImageData(0, 0, MARKER_ICON_PX, MARKER_ICON_PX))
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('marker SVG failed to load'))
    }
    img.src = url
  })
}

async function ensureMarkerImages() {
  if (markerImagesReady) return
  await Promise.all(
    Object.entries(MARKER_SVGS).map(async ([id, svg]) => {
      if (map.hasImage(id)) return
      const imageData = await svgMarkupToImageData(svg)
      map.addImage(id, imageData, { pixelRatio: 2 })
    }),
  )
  markerImagesReady = true
}

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

async function loadProjectMeshLinks(projectSlug) {
  const res = await fetch(`/api/projects/${projectSlug}/mesh-links`)
  if (!res.ok) return
  const gj = await res.json()
  mergeMeshLinkFeatures(gj.features)
}

function applyViewshedRasterRecords(records, refreshPanel = false) {
  const seenUrl = new Set()
  for (const r of records) {
    if (!r?.url || seenUrl.has(r.url)) continue
    seenUrl.add(r.url)
    if (mapReady) addViewshedRaster(r, refreshPanel)
    else pendingRasters.push(r)
  }
}

async function loadProjectViewsheds(projectSlug, sites) {
  const eligible = rfSites(sites)
  for (const s of eligible) {
    const entry = siteRegistry.get(s.slug)
    if (entry) entry.viewshedPending = true
  }
  renderSitesPanel()

  try {
    const batchRes = await fetch(`/api/projects/${projectSlug}/viewsheds`)
    if (batchRes.ok) {
      const items = await batchRes.json()
      if (Array.isArray(items) && items.length) {
        applyViewshedRasterRecords(items, false)
        return
      }
    }

    const results = await Promise.all(
      eligible.map(async (s) => {
        try {
          const res = await fetch(`/api/projects/${projectSlug}/viewsheds/${s.slug}?ensure=false`)
          if (!res.ok) return null
          return res.json()
        } catch {
          return null
        } finally {
          const entry = siteRegistry.get(s.slug)
          if (entry?.viewshedPending) {
            entry.viewshedPending = false
            renderSitesPanel()
          }
        }
      }),
    )
    applyViewshedRasterRecords(results.filter(Boolean), false)
  } finally {
    for (const s of eligible) {
      const entry = siteRegistry.get(s.slug)
      if (entry?.viewshedPending) entry.viewshedPending = false
    }
    renderSitesPanel()
  }
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
  const registryId = r._registryId || resolveRegistryIdForRecord(r)
  const regEntry = registryId ? siteRegistry.get(registryId) : registryEntryBySlug(slug)
  if (regEntry) {
    regEntry.hasViewshed = true
    regEntry.viewshedPending = false
  }

  const visible = regEntry ? regEntry.splatVisible : false

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
    registryId,
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
    entry.viewshedPending = false
    entry.linksPending = false
  }
  renderSitesPanel()
}

const SPLAT_ICON_SVG =
  '<svg viewBox="0 0 128 128" aria-hidden="true"><path fill="#907BF3" d="M59.4 21.9c3.8-2.7 6.9-.2 8.3 3.2c2.1 4.9 4.9 6.7 10.7 6.5c5.9-.2 7.6-2.6 8.1-6.5c0 0 2.8-13.7 3.6-16.1c2.8-8.9 15.7-6.3 12.9 4c-3.2 11.5-16.7 19.7-7.6 27.7c3.7 3.2 8.3 3.1 11.2.8c8.3-6.5 17.3-3.4 18.5 2.7c1.2 5.9-2.9 7.7-5.4 8.5c-4.6 1.5-15.8 2-16.3 12.4c-.3 6.7 6.9 8.1 8.1 13s-.9 7-2.5 8.7s-1.7 4-.2 6.1c5.1 7.2 12.7 10.7 10.3 17.3c-2.4 6.5-14.9 8.9-19-1.4c-8.8-22-14.8-12.4-19.6-11.4c-12.6 2.6-13.1-6.1-22.4-1.3c-9.4 4.9-6.6 28.1-20.9 26.4c-5.3-.6-8.9-7.8-4.2-14.2c5.9-8 21.5-16.7 16.5-22c-2.5-2.6-5.9-.4-7 .2c-11.5 6.2-23.7-9.6-11.9-19.7c6.1-5.2 15.3-4 14.7-10.4c-.4-4.7-6-5.9-11.9-9.3c-12.4-7.1-19-10.2-21.2-15C6.9 20.9 22.9 11.6 30 22.5c3.4 5.3 5.6 5.7 8.3 5.9s11.3.7 11.3-4.1c0-1.8-1.7-3.2-3.1-5c-1.9-2.5-3.3-5.8-2.2-8.6c1.6-4.3 7.3-3.9 9-1.5s6.1 12.7 6.1 12.7m18.2 85.8c0 1.7-.3 7.4.6 10.5c3.6 12.8 18.6 5 12.8-6.8c-.9-1.8-3-5.6-3.7-6.7c-2.9-4.5-9.7-2.1-9.7 3M8 58.6c-7 2.8-4.7 14.1 2.7 13.6c4.3-.3 15-8 15-8c2.9-2 1.7-7.1-2.3-7c-1.2-.1-11-.3-15.4 1.4"/><path fill="#004FAC" d="M108.8 92c-3.7.5-4.6-1.9-4.7-3.5c-.2-4.2 4.5-5.1 4-8c-.3-1.8 2-3.8 4.5-1.6c4.6 3.8-.1 12.6-3.8 13.1m11.2 14.1c-.4-1.7-4.5-2.3-4.4 1.2c.1 2.1-.6 4.6-5.7 4.6c-1.5 0-4.4.5-3.1 4.3c1.3 3.9 16.5 2.5 13.2-10.1M91.4 115c-1.1-.1-2.5.7-2.9 2.7s-2.1 2.9-3.2 2.8s-4 .1-4 3.1s12.2 3.9 11.9-6.6c0-1-.6-1.8-1.8-2m-2.8-19c2.4-1.8 1-7.6-4.3-6.1c-3.6 1-3.5 5.1-13.4 7.7c-2.9.8-2.3 3.6 2.1 3.5c10.5-.4 14.4-4.1 15.6-5.1m-28.7.8c.7-2.8-1.5-4.4-4.4-2.8c-9.2 5.4-6.2 17.4-14.6 23.5c-2.7 1.9-2.8 4.7-.2 6c7.7 3.9 19-25.9 19.2-26.7M28.3 60.3c-.7-2-4.2-1.9-4.4.3c-.2 2.7-6.4 4.4-10.7 6.8c-.9.5-3.3 2.5-.2 5.1c3.1 2.7 17.7-5 15.3-12.2m76.2-51.5c-.9-2.7-5-2.5-4.6.8c.3 2-.6 4.6-1.6 6.4c-2.5 4.3-4.8 7.5-7.3 11.5c-1.2 1.9-2.8 6.5 2.2 6.3c4.9-.2 14.4-15.4 11.3-25m21.1 36.7c-.5-5.1-5.9-3.3-5.3-.4c1 5.4-11.6 3.3-16.6 9c-2.2 2.5-.9 6.5 3.3 7.2c4.1.5 19.7-3.8 18.6-15.8"/><path fill="#D8BDF4" d="M10.9 62.5c-.3.4-1.1.9-1.4 2.2c-.3 1.6-3.1 1.3-3.2-.4s1.2-3.4 3.3-3.6c1.6-.2 1.9 1.1 1.3 1.8m23.6 6.7c-5.3.5-6.5 5.2-6 7.2c.3 1.4 3.5 1.3 3.4-.6c0-3.3 2.7-4.4 3.3-5.1c.5-.6.1-1.6-.7-1.5m14.6-9.9c-.8 2.4-2.3 3.3-3 4.1c-1.3 1.6.9 3.6 2.6 2.5c1.3-.8 2.9-3.4 2.4-6.3c-.2-.8-1.7-1.2-2-.3M23.3 21.2c-.8-.6-5.7-2.5-8.3 1.9c-.6 1-.3 2.1.4 2.7c.8.6 2.1.5 2.6-.3c1.6-2.5 3.6-2.5 5-2.6c.7 0 1.1-1.1.3-1.7m28.2 6.3c-.2.6-2 2.2-3 2.6c-2.9 1-.9 4.5 1.3 3.1c3.3-2 3.1-4.7 3.1-5.4c.1-.8-1.1-1.2-1.4-.3M49.1 9.3c-3.4.2-3.3 3.9-3.2 5.1c.2 1.7 3.1 1.5 2.9-.3c-.2-2 .9-2.7 1.4-3.2c.7-.8-.1-1.7-1.1-1.6M43 102.1c-1.5 1.1-6.8 6.3-7.6 7.2c-1.8 1.8-2.1 4.1-1.4 5.9c.6 1.8 3.2.9 3.1-.6c-.1-1.2-.1-2.1.4-2.8s6.5-7.6 6.8-7.9c.8-1.5-.4-2.5-1.3-1.8M94.5 5.9c-3.4 2.5-3.3 5.5-3 6.8s2 .8 2.1.2c.5-2 2.6-4.5 3-5c.9-1.3-.6-3.1-2.1-2"/></svg>'
const LINKS_ICON_SVG =
  '<svg viewBox="0 0 14 14" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.2"><circle cx="2.5" cy="7" r="2"/><circle cx="11.5" cy="2.5" r="2"/><circle cx="11.5" cy="11.5" r="2"/><path d="m3.71 5.41l5.85-2.43M3.71 8.59l5.85 2.43"/></g></svg>'

function registryEntryBySlug(slug) {
  if (!slug) return null
  if (siteRegistry.has(slug)) return siteRegistry.get(slug)
  const goalId = `goal:${slug}`
  if (siteRegistry.has(goalId)) return siteRegistry.get(goalId)
  return null
}

function resolveRegistryIdForRecord(r) {
  if (r.slug) {
    const bySlug = registryEntryBySlug(r.slug)
    if (bySlug) return bySlug.id
    if (siteRegistry.has(r.slug)) return r.slug
  }
  if (r.lat != null && r.lon != null) {
    for (const entry of siteRegistry.values()) {
      if (Math.abs(entry.lat - r.lat) < 1e-4 && Math.abs(entry.lon - r.lon) < 1e-4) {
        return entry.id
      }
    }
  }
  return r.slug || null
}

async function ensureViewshedForEntry(id) {
  const entry = siteRegistry.get(id)
  if (!entry || !currentProjectSlug || entry.hasViewshed) return true

  try {
    let res
    if (entry.category === 'goal' || (entry.slug && !siteRegistry.has(entry.slug))) {
      res = await fetch(
        `/api/projects/${currentProjectSlug}/viewsheds/at?lat=${entry.lat}&lon=${entry.lon}`,
      )
    } else if (entry.slug) {
      res = await fetch(
        `/api/projects/${currentProjectSlug}/viewsheds/${encodeURIComponent(entry.slug)}?ensure=true`,
      )
    } else {
      return false
    }
    if (!res.ok) return false
    const record = await res.json()
    record._registryId = id
    if (mapReady) addViewshedRaster(record, true)
    else pendingRasters.push(record)
    return true
  } catch {
    return false
  }
}

function siteHasCachedLinks(slug) {
  if (!slug) return false
  const prefix = 'mesh_links:'
  for (const [key, f] of layerFeatureCache) {
    if (!key.startsWith(prefix)) continue
    if (f.properties?.from === slug || f.properties?.to === slug) return true
  }
  return false
}

function mergeMeshLinkFeatures(features) {
  const layerId = 'mesh_links'
  for (const f of features || []) {
    const featId = f.properties?.id || `${f.properties?.from}--${f.properties?.to}`
    layerFeatureCache.set(`${layerId}:${featId}`, {
      ...f,
      properties: { ...(f.properties || {}), id: featId },
    })
  }
  flushLayerFeatures(layerId)
  raiseOverlayLayers()
}

async function ensureLinksForEntry(id) {
  const entry = siteRegistry.get(id)
  if (!entry || !currentProjectSlug) return true
  if (entry.slug && siteHasCachedLinks(entry.slug)) return true
  if (!entry.slug && !Number.isFinite(entry.lat)) return false

  try {
    let res = null
    if (entry.slug) {
      res = await fetch(
        `/api/projects/${currentProjectSlug}/mesh-links/from/${encodeURIComponent(entry.slug)}`,
      )
    }
    if (!res?.ok) {
      const fromSlug = encodeURIComponent(entry.slug || entry.id)
      res = await fetch(
        `/api/projects/${currentProjectSlug}/mesh-links/at?lat=${entry.lat}&lon=${entry.lon}&from_slug=${fromSlug}`,
      )
    }
    if (!res.ok) return false
    const gj = await res.json()
    mergeMeshLinkFeatures(gj.features)
    return true
  } catch {
    return false
  }
}

function setSiteSplatVisible(id, visible) {
  const entry = siteRegistry.get(id)
  if (!entry) return
  entry.splatVisible = visible

  const applyRasterVisibility = () => {
    for (const layer of viewshedRasterLayers) {
      if (layer.registryId === id && map.getLayer(layer.layerId)) {
        map.setLayoutProperty(layer.layerId, 'visibility', visible ? 'visible' : 'none')
      }
    }
    renderSitesPanel()
  }

  if (!visible) {
    applyRasterVisibility()
    return
  }

  if (entry.hasViewshed) {
    applyRasterVisibility()
    return
  }

  entry.viewshedPending = true
  renderSitesPanel()
  void ensureViewshedForEntry(id).then((ok) => {
    entry.viewshedPending = false
    if (!ok) entry.splatVisible = false
    else applyRasterVisibility()
    renderSitesPanel()
  })
}

function toggleSiteSplatVisibility(id) {
  const entry = siteRegistry.get(id)
  if (!entry || entry.viewshedPending) return
  setSiteSplatVisible(id, !entry.splatVisible)
}

function isSiteLinksShown(slug) {
  if (!slug) return true
  const entry = registryEntryBySlug(slug)
  if (!entry) return true
  return entry.linksVisible !== false
}

function setSiteLinksVisible(id, visible) {
  const entry = siteRegistry.get(id)
  if (!entry?.slug && !Number.isFinite(entry?.lat)) return
  entry.linksVisible = visible

  const applyLinkVisibility = () => {
    flushLayerFeatures('mesh_links')
    renderSitesPanel()
  }

  if (!visible) {
    applyLinkVisibility()
    return
  }

  if (entry.slug && siteHasCachedLinks(entry.slug)) {
    applyLinkVisibility()
    return
  }

  entry.linksPending = true
  renderSitesPanel()
  void ensureLinksForEntry(id).then((ok) => {
    entry.linksPending = false
    if (!ok) entry.linksVisible = false
    applyLinkVisibility()
  })
}

function toggleSiteLinksVisibility(id) {
  const entry = siteRegistry.get(id)
  if (!entry || entry.linksPending) return
  if (!entry.slug && !Number.isFinite(entry.lat)) return
  setSiteLinksVisible(id, !entry.linksVisible)
}

function flyToSite(id) {
  const entry = siteRegistry.get(id)
  if (!entry || !Number.isFinite(entry.lat) || !Number.isFinite(entry.lon)) return
  map.flyTo({ center: [entry.lon, entry.lat], zoom: Math.max(map.getZoom(), 10), duration: 600 })
}

function resetSitesPanel(sites, goals) {
  siteRegistry.clear()
  for (const s of sites || []) {
    if (isGoalSite(s)) continue
    siteRegistry.set(s.slug, {
      id: s.slug,
      slug: s.slug,
      category: sitePanelCategory(s),
      label: siteDisplayLabel(s),
      lat: s.lat,
      lon: s.lon,
      hasViewshed: false,
      viewshedPending: false,
      linksPending: false,
      splatVisible: true,
      linksVisible: true,
    })
  }
  for (const g of goals || []) {
    const id = `goal:${g.key}`
    siteRegistry.set(id, {
      id,
      slug: g.key || null,
      category: 'goal',
      label: (g.label || '').trim() || g.key,
      lat: g.lat,
      lon: g.lon,
      hasViewshed: false,
      viewshedPending: false,
      linksPending: false,
      splatVisible: false,
      linksVisible: false,
    })
  }
  renderSitesPanel()
}

function renderSiteRow(site) {
  const li = document.createElement('li')
  li.className = `site-row${site.hasViewshed ? '' : site.viewshedPending || site.linksPending ? ' viewshed-pending' : ' no-viewshed'}`
  if (site.category === 'goal') li.classList.add('site-row-goal')
  if (site.category === 'planned') li.classList.add('site-row-planned')

  const visGroup = document.createElement('div')
  visGroup.className = 'site-vis-group'

  const splatBtn = document.createElement('button')
  splatBtn.type = 'button'
  splatBtn.className = `site-visibility splat${site.splatVisible ? '' : ' hidden'}`
  splatBtn.disabled = site.viewshedPending
  splatBtn.innerHTML = SPLAT_ICON_SVG
  splatBtn.title = site.viewshedPending
    ? 'Computing viewshed…'
    : site.splatVisible
      ? 'Hide splat'
      : 'Show splat'
  splatBtn.setAttribute(
    'aria-label',
    site.viewshedPending
      ? `Computing viewshed for ${site.label}`
      : site.splatVisible
        ? `Hide splat for ${site.label}`
        : `Show splat for ${site.label}`,
  )
  splatBtn.addEventListener('click', (ev) => {
    ev.stopPropagation()
    toggleSiteSplatVisibility(site.id)
  })

  const linksBtn = document.createElement('button')
  linksBtn.type = 'button'
  linksBtn.className = `site-visibility links${site.linksVisible ? '' : ' hidden'}`
  linksBtn.disabled = site.linksPending || (!site.slug && !Number.isFinite(site.lat))
  linksBtn.innerHTML = LINKS_ICON_SVG
  linksBtn.title = site.linksPending
    ? 'Computing links…'
    : site.linksVisible
      ? 'Hide links'
      : 'Show links'
  linksBtn.setAttribute(
    'aria-label',
    site.linksPending
      ? `Computing links for ${site.label}`
      : site.linksVisible
        ? `Hide links for ${site.label}`
        : `Show links for ${site.label}`,
  )
  linksBtn.addEventListener('click', (ev) => {
    ev.stopPropagation()
    toggleSiteLinksVisibility(site.id)
  })

  visGroup.append(splatBtn, linksBtn)

  const label = document.createElement('span')
  label.className = 'site-label'
  label.textContent = site.label
  label.title = site.label
  label.addEventListener('click', () => flyToSite(site.id))

  li.append(visGroup, label)
  return li
}

function renderSitesPanel() {
  if (!sitesPanelScrollEl) return
  sitesPanelScrollEl.innerHTML = ''

  const entries = [...siteRegistry.values()]
  const rfEntries = entries.filter((s) => s.category !== 'goal')
  const withViewshed = rfEntries.filter((s) => s.hasViewshed).length

  if (sitesPanelCount) {
    sitesPanelCount.textContent = entries.length
      ? rfEntries.length
        ? `${withViewshed}/${entries.length}`
        : String(entries.length)
      : ''
  }
  if (sitesListEmptyEl) {
    sitesListEmptyEl.hidden = entries.length > 0
  }

  for (const section of SITE_PANEL_SECTIONS) {
    const sectionSites = entries
      .filter((s) => s.category === section.id)
      .sort((a, b) => a.label.localeCompare(b.label))
    if (!sectionSites.length) continue

    const sectionEl = document.createElement('section')
    sectionEl.className = `sites-section${sitesPanelSectionCollapsed[section.id] ? ' collapsed' : ''}`
    sectionEl.dataset.section = section.id

    const toggle = document.createElement('button')
    toggle.type = 'button'
    toggle.className = 'sites-section-toggle'
    toggle.setAttribute('aria-expanded', sitesPanelSectionCollapsed[section.id] ? 'false' : 'true')
    toggle.innerHTML = `<span class="sites-section-chevron" aria-hidden="true"></span><span class="sites-section-title">${section.label}</span><span class="sites-section-count">${sectionSites.length}</span>`
    toggle.addEventListener('click', () => {
      sitesPanelSectionCollapsed[section.id] = !sitesPanelSectionCollapsed[section.id]
      renderSitesPanel()
    })

    const list = document.createElement('ul')
    list.className = 'sites-section-list'
    for (const site of sectionSites) list.appendChild(renderSiteRow(site))

    sectionEl.append(toggle, list)
    sitesPanelScrollEl.appendChild(sectionEl)
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
    const isSymbol = layerId === 'sites' || layerId === 'goals'
    const isCircle = layerId === 'trials'
    if (isSymbol) {
      map.addLayer({
        id: ids.layer,
        type: 'symbol',
        source: ids.source,
        layout:
          layerId === 'goals'
            ? { ...MARKER_ICON_LAYOUT, 'icon-image': 'marker-goal' }
            : {
                ...MARKER_ICON_LAYOUT,
                'icon-image': [
                  'match',
                  ['get', 'site_type'],
                  'goal',
                  'marker-goal',
                  'planned',
                  'marker-planned',
                  'suggested',
                  'marker-planned',
                  'marker-installed',
                ],
              },
        paint: {
          'icon-halo-color': '#0f172a',
          'icon-halo-width': 1.25,
        },
      })
    } else {
      map.addLayer({
        id: ids.layer,
        type: isLine ? 'line' : isCircle ? 'circle' : 'fill',
        source: ids.source,
        paint:
          layerId === 'mesh_links'
            ? { 'line-color': '#38bdf8', 'line-width': 2.5, 'line-opacity': 0.85 }
            : layerId === 'trials'
              ? {
                  'circle-radius': 7,
                  'circle-color': '#c084fc',
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
    }
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
    if (layerId === 'sites' || layerId === 'goals') {
      ids.labelLayer = `${layerId}-labels-layer`
      map.addLayer({
        id: ids.labelLayer,
        type: 'symbol',
        source: ids.source,
        layout: {
          'text-field': ['get', 'label'],
          'text-anchor': 'top',
          'text-offset': [0, 1.15],
          'text-size': 11,
          'text-font': ['Open Sans Regular'],
          'text-allow-overlap': true,
        },
        paint: {
          'text-color': layerId === 'goals' ? '#fdba74' : '#f1f5f9',
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
  let features = []
  for (const [key, feature] of layerFeatureCache) {
    if (key.startsWith(prefix)) features.push(feature)
  }
  if (layerId === 'mesh_links') {
    features = features.filter(
      (f) => isSiteLinksShown(f.properties?.from) && isSiteLinksShown(f.properties?.to),
    )
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
  if (chatModelSel) chatModelSel.disabled = active
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
      properties: {
        id: m.id,
        label: m.label || m.id,
        site_type: m.site_type || (layerId === 'sites' ? 'installed' : ''),
      },
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
    const labelLayerId = `${layerId}-labels-layer`
    if (map.getLayer(labelLayerId)) map.removeLayer(labelLayerId)
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

function selectedChatModel() {
  if (!chatModelSel?.value) return null
  return chatModelSel.value
}

function chatContextPayload(pendingMessage = '') {
  return {
    project_slug: projectSel.value || null,
    model: selectedChatModel(),
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

async function loadChatModels(slug) {
  if (!chatModelSel) return
  chatModelSel.disabled = true
  chatModelSel.replaceChildren()
  try {
    const qs = slug ? `?project_slug=${encodeURIComponent(slug)}` : ''
    const res = await fetch(`/api/chat/models${qs}`)
    if (!res.ok) return
    const payload = await res.json()
    const models = payload.models || []
    const defaultModel = payload.default_model || ''
    for (const name of models) {
      const opt = document.createElement('option')
      opt.value = name
      opt.textContent = name
      chatModelSel.appendChild(opt)
    }
    if (defaultModel) {
      chatModelSel.value = defaultModel
    }
  } catch (err) {
    console.warn('loadChatModels failed:', err)
  } finally {
    if (!chatInFlight) chatModelSel.disabled = false
  }
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
  resetSitesPanel(ctx.sites, ctx.goals)

  for (const g of ctx.goals || []) {
    handlers['map.pin']({
      layer_id: 'goals',
      id: g.key,
      lat: g.lat,
      lon: g.lon,
      label: (g.label || '').trim() || g.key,
    })
  }
  for (const s of ctx.sites || []) {
    if (s.type === 'goal') continue
    handlers['map.pin']({
      layer_id: 'sites',
      id: s.slug,
      lat: s.lat,
      lon: s.lon,
      label: siteDisplayLabel(s),
      site_type: s.type || 'installed',
    })
  }
  raiseOverlayLayers()
  projectMeshScopeBbox = ctx.bbox || null
  try {
    if (ctx.bbox) fitMapToMeshScope(ctx.bbox)
  } catch (err) {
    console.warn('fitMapToMeshScope failed:', err)
  }
  void loadProjectViewsheds(slug, rfSites(ctx.sites))
  await loadProjectMeshLinks(slug)
  await loadChatModels(slug)
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
        model: selectedChatModel(),
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

chatModelSel?.addEventListener('change', () => {
  scheduleChatContextRefresh(chatInput?.value.trim() || '')
})

map.on('load', async () => {
  mapReady = true
  setBasemap(basemapSel.value)
  hookCompassReset()
  try {
    await ensureMarkerImages()
  } catch (err) {
    console.warn('ensureMarkerImages failed:', err)
  }
  for (const pin of pendingPins) handlers['map.pin'](pin)
  pendingPins.length = 0
  for (const r of pendingRasters) addViewshedRaster(r, false)
  pendingRasters.length = 0
  renderSitesPanel()
  loadProjects()
})

map.on('pitch', syncTerrainFromPitch)
map.on('moveend', syncTerrainFromPitch)
