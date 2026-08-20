#!/usr/bin/env node
/**
 * Phase 1: Regenerate legacy.js from monolith snapshot (legacy.js is canonical after split).
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const srcPath = path.join(root, 'assets/static/project-map/legacy.pre-split.js')
const fallback = path.join(root, 'assets/static/project-map/legacy.js')
const outPath = path.join(root, 'assets/static/project-map/legacy.js')

if (!fs.existsSync(srcPath)) {
  console.error('No legacy.pre-split.js snapshot; legacy.js is already the ESM source of truth.')
  process.exit(0)
}

const lines = fs.readFileSync(srcPath, 'utf8').split('\n')
let body = lines.slice(1)
if (body.at(-1)?.trim() === '})();') body = body.slice(0, -1)

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
  /^  function clampRadiusKm/,
  /^  function clampViewshedQuality/,
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
      if (depth <= 0 && line.includes('}')) skipping = false
      continue
    }
    out.push(line)
  }
  return out
}

let remaining = body
for (const pat of removeFunctionPatterns) {
  remaining = removeFunctionBlock(remaining, pat)
}

// Replace previewSlugForName to use geo helper
remaining = remaining.map((line) => {
  if (line.match(/^  function previewSlugForName\(name\)/)) {
    return '  function previewSlugForName(name) {'
  }
  return line
})

const fnStart = remaining.findIndex((l) => l.includes('function previewSlugForName(name)'))
if (fnStart >= 0) {
  remaining = removeFunctionBlock(remaining, /^  function previewSlugForName/)
  remaining.splice(
    fnStart,
    0,
    '  function previewSlugForName(name) {',
    '    return previewSlugForNameFromGeo(name, new Set([...siteBySlug.keys()]))',
    '  }',
  )
}

const header = `import * as C from './constants.js'
import { computeViewshedRaster } from './viewshed-raster.js'
import {
  compareHuman,
  kmToDegreeDeltas,
  lngLatBoundsFromPoints,
  padMapBounds,
  coordsUsableForMarker,
  arrayBufferToBase64,
  slugifyName,
  previewSlugForName as previewSlugForNameFromGeo,
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
  isMapTiltedView as isMapTiltedViewGeo,
  mapOverheadEquivalentBounds,
  mapDataViewportBounds as mapDataViewportBoundsGeo,
  mapSeekScanBounds as mapSeekScanBoundsGeo,
  seekPeakBinSizeMForBounds,
  seekScanBoundsForRequest as seekScanBoundsForRequestGeo,
  clampRadiusKm,
  clampViewshedQuality,
} from './geo.js'
import { normalizeLandSidebarInput } from './land/sidebar-model.js'

/** @typedef {import('./ctx.js').MapContext} MapContext */

/**
 * @returns {{ reloadViewshedsForSimChange: () => void, setViewshedSimulation: (radiusKm: number, quality: number) => boolean }}
 */
export function initProjectMap() {
`

const footer = `
}
`

let text = remaining.join('\n')

text = text.replace(
  /const projectSlug = config\.slug;/,
  `const projectSlug = config.slug
  const simDefaults = config.simulation || {}
  const radiusBounds = C.viewshedRadiusBounds(simDefaults)
  const VIEWSHED_RADIUS_KM_MIN = radiusBounds.min
  const VIEWSHED_RADIUS_KM_MAX = radiusBounds.max
  const MAP_STATE_KEY = C.mapStateKey(projectSlug)
  const SEEK_STATE_KEY = C.seekStateKey(projectSlug)
  const SEEK_REDO_KEY = C.seekRedoKey(projectSlug)`,
)

text = text.replace(/\n  const simDefaults = config\.simulation \|\| \{\};\n/g, '\n')
text = text.replace(/\n  const VIEWSHED_RADIUS_KM_MIN = Number\(simDefaults\.radius_km_min\) \|\| 1;\n/g, '\n')
text = text.replace(/\n  const VIEWSHED_RADIUS_KM_MAX = Number\(simDefaults\.radius_km_max\) \|\| 100;\n/g, '\n')
text = text.replace(/\n  const MAP_STATE_KEY = `peaky\.map\.v1\.\$\{projectSlug\}`;\n/g, '\n')
text = text.replace(/\n  const SEEK_STATE_KEY = `peaky\.seek\.v1\.\$\{projectSlug\}`;\n/g, '\n')
text = text.replace(/\n  const SEEK_REDO_KEY = `peaky\.seek\.redo\.v1\.\$\{projectSlug\}`;\n/g, '\n')

// Constants from C module
const constReplacements = [
  ['TERRAIN_SOURCE', 'C.TERRAIN_SOURCE'],
  ['TERRAIN_HILLSHADE', 'C.TERRAIN_HILLSHADE'],
  ['BASEMAP_REFERENCE_SOURCE', 'C.BASEMAP_REFERENCE_SOURCE'],
  ['BASEMAP_REFERENCE_LAYER', 'C.BASEMAP_REFERENCE_LAYER'],
  ['SITES_SOURCE', 'C.SITES_SOURCE'],
  ['SITES_CIRCLE', 'C.SITES_CIRCLE'],
  ['SITES_LABELS', 'C.SITES_LABELS'],
  ['SITES_SELECTED', 'C.SITES_SELECTED'],
  ['LINKS_SOURCE', 'C.LINKS_SOURCE'],
  ['LINKS_LAYER', 'C.LINKS_LAYER'],
  ['LINKS_LABELS_LAYER', 'C.LINKS_LABELS_LAYER'],
  ['DRAFT_LINKS_SOURCE', 'C.DRAFT_LINKS_SOURCE'],
  ['DRAFT_LINKS_LAYER', 'C.DRAFT_LINKS_LAYER'],
  ['DRAFT_LINKS_LABELS_LAYER', 'C.DRAFT_LINKS_LABELS_LAYER'],
  ['EDIT_HISTORY_LINKS_SOURCE', 'C.EDIT_HISTORY_LINKS_SOURCE'],
  ['EDIT_HISTORY_LINKS_LAYER', 'C.EDIT_HISTORY_LINKS_LAYER'],
  ['EDIT_HISTORY_LINKS_LABELS_LAYER', 'C.EDIT_HISTORY_LINKS_LABELS_LAYER'],
  ['SEEK_CANDIDATES_SOURCE', 'C.SEEK_CANDIDATES_SOURCE'],
  ['SEEK_CANDIDATES_LAYER', 'C.SEEK_CANDIDATES_LAYER'],
  ['SEEK_CANDIDATES_LABELS_LAYER', 'C.SEEK_CANDIDATES_LABELS_LAYER'],
  ['SEEK_LINES_SOURCE', 'C.SEEK_LINES_SOURCE'],
  ['SEEK_LINES_LAYER', 'C.SEEK_LINES_LAYER'],
  ['SEEK_LINES_LABELS_LAYER', 'C.SEEK_LINES_LABELS_LAYER'],
  ['SEEK_PATH_SOURCE', 'C.SEEK_PATH_SOURCE'],
  ['SEEK_PATH_LAYER', 'C.SEEK_PATH_LAYER'],
  ['SEEK_GOAL_LINE_SOURCE', 'C.SEEK_GOAL_LINE_SOURCE'],
  ['SEEK_GOAL_LINE_LAYER', 'C.SEEK_GOAL_LINE_LAYER'],
  ['SEEK_WEDGE_SOURCE', 'C.SEEK_WEDGE_SOURCE'],
  ['SEEK_WEDGE_FILL_LAYER', 'C.SEEK_WEDGE_FILL_LAYER'],
  ['SEEK_WEDGE_OUTLINE_LAYER', 'C.SEEK_WEDGE_OUTLINE_LAYER'],
  ['SEEK_ANCILLARY_LINES_SOURCE', 'C.SEEK_ANCILLARY_LINES_SOURCE'],
  ['SEEK_ANCILLARY_LINES_LAYER', 'C.SEEK_ANCILLARY_LINES_LAYER'],
  ['SEEK_ANCILLARY_LINES_LABELS_LAYER', 'C.SEEK_ANCILLARY_LINES_LABELS_LAYER'],
  ['VIEWSHED_OPACITY_DEFAULT', 'C.VIEWSHED_OPACITY_DEFAULT'],
  ['DRAFT_VIEWSHED_SLUG', 'C.DRAFT_VIEWSHED_SLUG'],
  ['VIEWSHED_PREVIEW_QUALITY', 'C.VIEWSHED_PREVIEW_QUALITY'],
  ['VIEWSHED_QUALITY_MIN', 'C.VIEWSHED_QUALITY_MIN'],
  ['VIEWSHED_QUALITY_MAX', 'C.VIEWSHED_QUALITY_MAX'],
  ['DRAFT_MARKER_COLOR', 'C.DRAFT_MARKER_COLOR'],
  ['PITCH_TERRAIN_ON', 'C.PITCH_TERRAIN_ON'],
  ['PITCH_TERRAIN_OFF', 'C.PITCH_TERRAIN_OFF'],
  ['SITE_FIT_BUFFER_KM', 'C.SITE_FIT_BUFFER_KM'],
  ['MAP_STATE_SAVE_MS', 'C.MAP_STATE_SAVE_MS'],
  ['MAP_GLYPHS_URL', 'C.MAP_GLYPHS_URL'],
  ['MAP_TEXT_FONT', 'C.MAP_TEXT_FONT'],
  ['MAP_LABEL_FONT', 'C.MAP_LABEL_FONT'],
  ['PIN_LOAD_MARKER_OFFSET', 'C.PIN_LOAD_MARKER_OFFSET'],
  ['SEEK_WEDGE_NEAR_DEG', 'C.SEEK_WEDGE_NEAR_DEG'],
  ['SEEK_WEDGE_FAR_DEG', 'C.SEEK_WEDGE_FAR_DEG'],
  ['SEEK_ANCILLARY_LINKS_DEBOUNCE_MS', 'C.SEEK_ANCILLARY_LINKS_DEBOUNCE_MS'],
  ['SEEK_PLAN_SAVE_MS', 'C.SEEK_PLAN_SAVE_MS'],
  ['SEEK_PEAK_BIN_MIN_M', 'C.SEEK_PEAK_BIN_MIN_M'],
  ['SEEK_PEAK_BIN_MAX_M', 'C.SEEK_PEAK_BIN_MAX_M'],
  ['SEEK_PEAK_BINS_ACROSS_VIEWPORT', 'C.SEEK_PEAK_BINS_ACROSS_VIEWPORT'],
  ['SEEK_SCAN_PIN', 'C.SEEK_SCAN_PIN'],
  ['SEEK_PROGRESS_POLL_MS', 'C.SEEK_PROGRESS_POLL_MS'],
  ['SEEK_GOAL_SAME_AS_START_M', 'C.SEEK_GOAL_SAME_AS_START_M'],
  ['SEEK_HOP_VIEWSHED_PREFIX', 'C.SEEK_HOP_VIEWSHED_PREFIX'],
  ['LAND_DEFAULT_FILL_COLOR', 'C.LAND_DEFAULT_FILL_COLOR'],
  ['LAND_DEFAULT_FILL_OPACITY', 'C.LAND_DEFAULT_FILL_OPACITY'],
  ['LAND_DEFAULT_LINE_COLOR', 'C.LAND_DEFAULT_LINE_COLOR'],
  ['LAND_LINE_WIDTH', 'C.LAND_LINE_WIDTH'],
  ['LAND_PREVIEW_LINE_WIDTH', 'C.LAND_PREVIEW_LINE_WIDTH'],
  ['COORD_PREFETCH_MS', 'C.COORD_PREFETCH_MS'],
]

// Remove const declarations for moved constants
text = text
  .split('\n')
  .filter((line) => {
    for (const [name] of constReplacements) {
      if (line.match(new RegExp(`^  const ${name} =`))) return false
    }
    return true
  })
  .join('\n')

for (const [name, repl] of constReplacements) {
  text = text.replace(new RegExp(`\\b${name}\\b`, 'g'), repl)
}

text = text.replace(
  /clampRadiusKm\(([^)]+)\)/g,
  'clampRadiusKm($1, VIEWSHED_RADIUS_KM_MIN, VIEWSHED_RADIUS_KM_MAX)',
)

text = text.replace(/function isMapTiltedView\(mapInstance = map\)/, 'function isMapTiltedView(mapInstance = map) { return isMapTiltedViewGeo(mapInstance, mapReady) }')
text = text.replace(
  /function isMapTiltedView\(mapInstance = map\) \{ return isMapTiltedViewGeo\(mapInstance, mapReady\) \}[\s\S]*?\n  \}/,
  'function isMapTiltedView(mapInstance = map) {\n    return isMapTiltedViewGeo(mapInstance, mapReady)\n  }',
)

text = text.replace(
  /function mapDataViewportBounds\(mapInstance = map\) \{[\s\S]*?\n  \}/,
  'function mapDataViewportBounds(mapInstance = map) {\n    return mapDataViewportBoundsGeo(mapInstance, mapReady)\n  }',
)

text = text.replace(
  /function mapSeekScanBounds\(mapInstance = map\) \{[\s\S]*?\n  \}/,
  'function mapSeekScanBounds(mapInstance = map) {\n    return mapSeekScanBoundsGeo(mapInstance, mapReady)\n  }',
)

text = text.replace(
  /function seekScanBoundsForRequest\(\) \{[\s\S]*?\n  \}/,
  'function seekScanBoundsForRequest() {\n    return seekScanBoundsForRequestGeo(map, mapReady)\n  }',
)

// normalizeLandSidebarInput hoisted to module - remove local def
text = removeFunctionBlock(text.split('\n'), /^  function normalizeLandSidebarInput/).join('\n')

// Remove window.PEAKY_MAP assignment at end - main.js handles it
text = text.replace(/\n  window\.PEAKY_MAP = \{[\s\S]*?\n  \};\n\}\)\(\);?/, '')

text = text.replace(
  /return \{\s*\n    reloadViewshedsForSimChange,\s*\n    setViewshedSimulation,\s*\n  \};/,
  '',
)

// Inject geo wrapper helpers after landSidebar init
text = text.replace(
  /let landSidebar = normalizeLandSidebarInput\(config\.land\?\.sidebar\);/,
  `let landSidebar = normalizeLandSidebarInput(config.land?.sidebar);

  function isMapTiltedView(mapInstance = map) {
    return isMapTiltedViewGeo(mapInstance, mapReady)
  }

  function mapDataViewportBounds(mapInstance = map) {
    return mapDataViewportBoundsGeo(mapInstance, mapReady)
  }

  function mapSeekScanBounds(mapInstance = map) {
    return mapSeekScanBoundsGeo(mapInstance, mapReady)
  }

  function seekScanBoundsForRequest() {
    return seekScanBoundsForRequestGeo(map, mapReady)
  }`,
)

text = text.replace(
  /\/\*\* Geographic bounds for the same center[\s\S]*?function sitesGeoJson\(\)/,
  'function sitesGeoJson()',
)

fs.writeFileSync(
  outPath,
  `${header}${text}
  return {
    reloadViewshedsForSimChange,
    setViewshedSimulation,
  }
${footer}`,
)

console.log('Wrote', outPath, 'lines:', fs.readFileSync(outPath, 'utf8').split('\n').length)
