#!/usr/bin/env node
/**
 * Converts project-map.js IIFE into ESM modules under assets/static/project-map/.
 * Run from repo root: node scripts/split-project-map.mjs
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const srcPath = path.join(root, 'assets/static/project-map.js')
const outDir = path.join(root, 'assets/static/project-map')

const src = fs.readFileSync(srcPath, 'utf8')
const lines = src.split('\n')

if (!lines[0].trim().startsWith('(function')) {
  console.error('Expected IIFE wrapper')
  process.exit(1)
}

// Strip IIFE wrapper lines
let body = lines.slice(1)
if (body[body.length - 1].trim() === '})();') body = body.slice(0, -1)
if (body[body.length - 1].trim() === '})();') body = body.slice(0, -1)

const bodyText = body.join('\n')

// Ranges are 1-based line numbers from original file (inclusive)
const extractions = [
  {
    file: 'land/sidebar-model.js',
    start: 3522,
    end: 3540,
    exportName: 'normalizeLandSidebarInput',
    kind: 'export-function',
  },
  {
    file: 'land/index.js',
    start: 2841,
    end: 5799,
    installName: 'installLand',
    skipRanges: [{ start: 3522, end: 3540 }],
  },
  {
    file: 'import-sites.js',
    start: 7429,
    end: 8016,
    installName: 'installImportSites',
  },
  {
    file: 'seek.js',
    start: 9120,
    end: 11188,
    installName: 'installSeek',
    prependFrom: { file: 'geo-helpers-seek.js', start: 7239, end: 7427 },
  },
]

function sliceLines(allLines, start, end, skipRanges = []) {
  const chunks = []
  let chunk = []
  for (let i = start - 1; i <= end - 1 && i < allLines.length; i++) {
    const lineNo = i + 1
    const skip = skipRanges.some((r) => lineNo >= r.start && lineNo <= r.end)
    if (skip) continue
    chunk.push(allLines[i])
  }
  if (chunk.length) chunks.push(chunk.join('\n'))
  return chunks.join('\n')
}

function removeLineRange(allLines, start, end, skipRanges = []) {
  const out = []
  for (let i = 0; i < allLines.length; i++) {
    const lineNo = i + 1
    if (lineNo >= start && lineNo <= end) {
      const skip = skipRanges.some((r) => lineNo >= r.start && lineNo <= r.end)
      if (!skip) continue
    }
    out.push(allLines[i])
  }
  return out
}

// Build sidebar model as standalone export
const sidebarBody = sliceLines(lines, 3522, 3540)
fs.writeFileSync(
  path.join(outDir, 'land/sidebar-model.js'),
  `/** Land sidebar model — pure normalize. */\n${sidebarBody.replace(/^  function normalizeLandSidebarInput/, 'export function normalizeLandSidebarInput')}\n`,
)

let remaining = body

// Remove extracted ranges from remaining (process high to low to preserve line numbers)
const sortedExtractions = [...extractions]
  .filter((e) => e.installName)
  .sort((a, b) => b.start - a.start)

for (const ext of sortedExtractions) {
  let chunk = sliceLines(lines, ext.start, ext.end, ext.skipRanges || [])
  if (ext.prependFrom) {
    const prepend = sliceLines(lines, ext.prependFrom.start, ext.prependFrom.end)
    chunk = `${prepend}\n\n${chunk}`
  }
  const modulePath = path.join(outDir, ext.file)
  fs.mkdirSync(path.dirname(modulePath), { recursive: true })
  const content = `/**
 * ${ext.file} — installed on shared map scope.
 * @param {Record<string, unknown>} scope
 */
export function ${ext.installName}(scope) {
${chunk}
}
`
  fs.writeFileSync(modulePath, content)
  remaining = removeLineRange(
    remaining.split('\n'),
    ext.start,
    ext.end,
    ext.skipRanges || [],
  ).join('\n')
}

// Remove pure functions from remaining (already in geo/viewshed-raster modules)
const removeFunctionPatterns = [
  /^  function demNativeRasterDimension/,
  /^  function rasterUpgradeLadder/,
  /^  function computeViewshedRaster/,
  /^  function compareHuman/,
  /^  function kmToDegreeDeltas/,
  /^  function lngLatBoundsFromPoints/,
  /^  function padMapBounds/,
  /^  function coordsUsableForMarker/,
  /^  function arrayBufferToBase64/,
  /^  function slugifyName/,
  /^  function previewSlugForName/,
  /^  function formatCoord/,
  /^  function parseCoordPairFromText/,
  /^  function coordsMatchPair/,
  /^  function coordSeparationM/,
  /^  function haversineMeters/,
  /^  function bearingDeg/,
  /^  function destinationPointLatLon/,
  /^  function seekWedgeHalfAngleDeg/,
  /^  function buildSeekWedgeFeature/,
  /^  function buildSeekGoalLineFeature/,
  /^  function isMapTiltedView/,
  /^  function mapOverheadEquivalentBounds/,
  /^  function mapDataViewportBounds/,
  /^  function mapSeekScanBounds/,
  /^  function seekPeakBinSizeMForBounds/,
  /^  function seekScanBoundsForRequest/,
  /^  function normalizeLandSidebarInput/,
]

function removeFunctionBlock(textLines, startPattern) {
  const out = []
  let skipping = false
  let depth = 0
  for (const line of textLines) {
    if (!skipping && startPattern.test(line)) {
      skipping = true
      depth = 0
    }
    if (skipping) {
      for (const ch of line) {
        if (ch === '{') depth++
        if (ch === '}') depth--
      }
      if (depth <= 0 && line.includes('}')) {
        skipping = false
      }
      continue
    }
    out.push(line)
  }
  return out
}

let remainingLines = remaining.split('\n')
for (const pat of removeFunctionPatterns) {
  remainingLines = removeFunctionBlock(remainingLines, pat)
}

// Remove const blocks moved to constants.js (lines 15-87 approx)
const constNames = new Set([
  'TERRAIN_SOURCE',
  'TERRAIN_HILLSHADE',
  'BASEMAP_REFERENCE_SOURCE',
  'BASEMAP_REFERENCE_LAYER',
  'SITES_SOURCE',
  'SITES_CIRCLE',
  'SITES_LABELS',
  'SITES_SELECTED',
  'LINKS_SOURCE',
  'LINKS_LAYER',
  'LINKS_LABELS_LAYER',
  'DRAFT_LINKS_SOURCE',
  'DRAFT_LINKS_LAYER',
  'DRAFT_LINKS_LABELS_LAYER',
  'EDIT_HISTORY_LINKS_SOURCE',
  'EDIT_HISTORY_LINKS_LAYER',
  'EDIT_HISTORY_LINKS_LABELS_LAYER',
  'SEEK_CANDIDATES_SOURCE',
  'SEEK_CANDIDATES_LAYER',
  'SEEK_CANDIDATES_LABELS_LAYER',
  'SEEK_LINES_SOURCE',
  'SEEK_LINES_LAYER',
  'SEEK_LINES_LABELS_LAYER',
  'SEEK_PATH_SOURCE',
  'SEEK_PATH_LAYER',
  'SEEK_GOAL_LINE_SOURCE',
  'SEEK_GOAL_LINE_LAYER',
  'SEEK_WEDGE_SOURCE',
  'SEEK_WEDGE_FILL_LAYER',
  'SEEK_WEDGE_OUTLINE_LAYER',
  'SEEK_ANCILLARY_LINES_SOURCE',
  'SEEK_ANCILLARY_LINES_LAYER',
  'SEEK_ANCILLARY_LINES_LABELS_LAYER',
  'SEEK_WEDGE_NEAR_DEG',
  'SEEK_WEDGE_FAR_DEG',
  'SEEK_ANCILLARY_LINKS_DEBOUNCE_MS',
  'SEEK_PLAN_SAVE_MS',
  'SEEK_PEAK_BIN_MIN_M',
  'SEEK_PEAK_BIN_MAX_M',
  'SEEK_PEAK_BINS_ACROSS_VIEWPORT',
  'SEEK_SCAN_PIN',
  'SEEK_PROGRESS_POLL_MS',
  'SEEK_GOAL_SAME_AS_START_M',
  'SEEK_HOP_VIEWSHED_PREFIX',
  'LAND_DEFAULT_FILL_COLOR',
  'LAND_DEFAULT_FILL_OPACITY',
  'LAND_DEFAULT_LINE_COLOR',
  'LAND_LINE_WIDTH',
  'LAND_PREVIEW_LINE_WIDTH',
  'VIEWSHED_OPACITY_DEFAULT',
  'DRAFT_VIEWSHED_SLUG',
  'VIEWSHED_PREVIEW_QUALITY',
  'SKADI_DEM_SPACING_M',
  'VIEWSHED_QUALITY_MIN',
  'VIEWSHED_QUALITY_MAX',
  'VIEWSHED_RASTER_MIN',
  'VIEWSHED_RASTER_MAX',
  'COORD_PREFETCH_MS',
  'DRAFT_MARKER_COLOR',
  'PITCH_TERRAIN_ON',
  'PITCH_TERRAIN_OFF',
  'SITE_FIT_BUFFER_KM',
  'MAP_STATE_SAVE_MS',
  'MAP_GLYPHS_URL',
  'MAP_TEXT_FONT',
  'MAP_LABEL_FONT',
  'PIN_LOAD_MARKER_OFFSET',
])

remainingLines = remainingLines.filter((line) => {
  const m = line.match(/^  const ([A-Z_][A-Z0-9_]*) =/)
  if (m && constNames.has(m[1])) return false
  if (line.match(/^  const SEEK_STATE_KEY =/)) return false
  if (line.match(/^  const SEEK_REDO_KEY =/)) return false
  if (line.match(/^  const MAP_STATE_KEY =/)) return false
  if (line.match(/^  const VIEWSHED_RADIUS_KM_MIN =/)) return false
  if (line.match(/^  const VIEWSHED_RADIUS_KM_MAX =/)) return false
  return true
})

// Replace clamp functions with geo imports usage - remove local clamp defs
remainingLines = removeFunctionBlock(remainingLines, /^  function clampRadiusKm/)
remainingLines = removeFunctionBlock(remainingLines, /^  function clampViewshedQuality/)

const legacyHeader = `import * as C from './constants.js'
import {
  computeViewshedRaster,
} from './viewshed-raster.js'
import {
  compareHuman,
  kmToDegreeDeltas,
  lngLatBoundsFromPoints,
  padMapBounds,
  coordsUsableForMarker,
  arrayBufferToBase64,
  slugifyName,
  previewSlugForName,
  formatCoord,
  parseCoordPairFromText,
  coordsMatchPair,
  coordSeparationM,
  haversineMeters,
  bearingDeg,
  destinationPointLatLon,
  buildSeekWedgeFeature,
  buildSeekGoalLineFeature,
  seekWedgeHalfAngleDeg,
  isMapTiltedView,
  mapOverheadEquivalentBounds,
  mapDataViewportBounds,
  mapSeekScanBounds,
  seekPeakBinSizeMForBounds,
  seekScanBoundsForRequest,
  clampRadiusKm,
  clampViewshedQuality,
} from './geo.js'
import { normalizeLandSidebarInput } from './land/sidebar-model.js'
import { installLand } from './land/index.js'
import { installImportSites } from './import-sites.js'
import { installSeek } from './seek.js'

/** @typedef {Record<string, unknown>} MapScope */

/**
 * Boot the project map UI.
 * @returns {{ reloadViewshedsForSimChange: () => void, setViewshedSimulation: (radiusKm: number, quality: number) => boolean }}
 */
export function initProjectMap() {
  const scope = /** @type {MapScope} */ ({})
`

const legacyFooter = `
  installLand(scope)
  installImportSites(scope)
  installSeek(scope)

  return {
    reloadViewshedsForSimChange,
    setViewshedSimulation,
  }
}
`

// Inject constants/setup after config block start
let legacyBody = remainingLines.join('\n')
legacyBody = legacyBody.replace(
  /const config = window\.PEAKY_PROJECT \|\| \{\};/,
  `const config = window.PEAKY_PROJECT || {}
  const projectSlug = config.slug
  const simDefaults = config.simulation || {}
  const radiusBounds = C.viewshedRadiusBounds(simDefaults)
  const VIEWSHED_RADIUS_KM_MIN = radiusBounds.min
  const VIEWSHED_RADIUS_KM_MAX = radiusBounds.max
  const MAP_STATE_KEY = C.mapStateKey(projectSlug)
  const SEEK_STATE_KEY = C.seekStateKey(projectSlug)
  const SEEK_REDO_KEY = C.seekRedoKey(projectSlug)
  Object.assign(scope, {
    C,
    config,
    projectSlug,
    simDefaults,
    VIEWSHED_RADIUS_KM_MIN,
    VIEWSHED_RADIUS_KM_MAX,
    MAP_STATE_KEY,
    SEEK_STATE_KEY,
    SEEK_REDO_KEY,
  })`,
)

// Fix duplicate projectSlug/simDefaults if still present
legacyBody = legacyBody.replace(/\n  const projectSlug = config\.slug\n/, '\n')
legacyBody = legacyBody.replace(/\n  const simDefaults = config\.simulation \|\| \{\};\n/g, '\n')

// Wrap isMapTiltedView calls to pass mapReady
legacyBody = legacyBody.replace(/isMapTiltedView\(([^)]*)\)/g, (m, arg) => {
  const a = arg.trim()
  if (a === 'map' || a === 'mapInstance' || a === 'mapInstance = map') {
    return `isMapTiltedView(${a.includes('=') ? arg : a || 'map'}, mapReady)`
  }
  return m
})
legacyBody = legacyBody.replace(/mapDataViewportBounds\(([^)]*)\)/g, (m, arg) => {
  const a = arg.trim()
  if (!a.includes('mapReady')) {
    return `mapDataViewportBounds(${arg || 'map'}, mapReady)`
  }
  return m
})
legacyBody = legacyBody.replace(/mapSeekScanBounds\(([^)]*)\)/g, () => 'mapSeekScanBounds(map, mapReady)')
legacyBody = legacyBody.replace(/seekScanBoundsForRequest\(\)/g, 'seekScanBoundsForRequest(map, mapReady)')

// previewSlugForName now needs taken slugs set
legacyBody = legacyBody.replace(
  /function previewSlugForName\(name\) \{\s*const base = slugifyName\(name\);\s*const taken = new Set\(\[\.\.\.siteBySlug\.keys\(\)\]\);[\s\S]*?\n  \}/,
  `function previewSlugForNameLocal(name) {
    return previewSlugForName(name, new Set([...siteBySlug.keys()]))
  }`,
)

legacyBody = legacyBody.replace(/\bpreviewSlugForName\(/g, 'previewSlugForNameLocal(')

// clamp calls need bounds
legacyBody = legacyBody.replace(
  /clampRadiusKm\(([^)]+)\)/g,
  'clampRadiusKm($1, VIEWSHED_RADIUS_KM_MIN, VIEWSHED_RADIUS_KM_MAX)',
)

// Assign scope members before install calls - collect key vars at end before return
const scopeAssign = `
  Object.assign(scope, {
    map,
    mapReady,
    sites,
    siteBySlug,
    landSources,
    landDataGdbPaths,
    landAoiDigest,
    landSidebar,
    registerSite,
    unregisterSite,
    scheduleSaveMapState,
    renderEntityPanel,
    selectSite,
    deselectSite,
    syncMapCursor,
    setEntityPanelOpen,
    entityPanelTab,
    loadDraftViewshedAt,
    setViewshedVisible,
    isViewshedVisible,
    hideViewshedLayerForEdit,
    viewshedVisible,
    viewshedLoading,
    siteViewshedReady,
    viewshedLoadEpoch,
    viewshedPendingEpoch,
    viewshedRadiusKm,
    viewshedQuality,
    viewshedOpacity,
    siteLinksPayload,
    showSiteLinks,
    siteOutboundLinksReady,
    refreshFilteredLinks,
    ensureViewshedLoadedForSlug,
    markSiteViewshedReady,
    markSiteOutboundLinksReady,
    applySavedSiteToMap,
    ensureSiteVisibleAfterAdd,
    normalizeSiteFromApi,
    sitesApiUrl,
    openWaDialog,
    raiseSiteLayers,
    syncSeekWedge,
    removeSeekWedgeLayers,
    removeSeekGoalLineLayer,
    seekSessionActive,
    seekCurrentFrom,
    seekGoalCoords,
    seekHopRadiusM,
    simDefaults,
    projectSlug,
    mapReady,
    SITES_CIRCLE,
    SEEK_GOAL_LINE_LAYER,
    SEEK_GOAL_LINE_SOURCE,
    SEEK_WEDGE_SOURCE,
    SEEK_WEDGE_FILL_LAYER,
    SEEK_WEDGE_OUTLINE_LAYER,
    haversineMeters,
    bearingDeg,
    buildSeekWedgeFeature,
    buildSeekGoalLineFeature,
    destinationPointLatLon,
    seekWedgeHalfAngleDeg,
  })
`

legacyBody = legacyBody.replace(
  /window\.PEAKY_MAP = \{/,
  `${scopeAssign}\n\n  window.PEAKY_MAP = {`,
)

fs.writeFileSync(path.join(outDir, 'legacy.js'), legacyHeader + legacyBody + legacyFooter)

fs.writeFileSync(
  path.join(outDir, 'main.js'),
  `import { initProjectMap } from './legacy.js'

const api = initProjectMap()
window.PEAKY_MAP = api
`,
)

fs.writeFileSync(
  path.join(outDir, 'ctx.js'),
  `/**
 * Shared map context — populated by legacy boot and domain install modules.
 * @typedef {Record<string, unknown>} MapContext
 */

/** @returns {MapContext} */
export function createMapContext() {
  return {}
}
`,
)

console.log('Wrote project-map modules to', outDir)
