#!/usr/bin/env node
/**
 * Extract domain install modules from project-map.js source ranges.
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const srcPath = fs.existsSync(path.join(root, 'assets/static/project-map.js'))
  ? path.join(root, 'assets/static/project-map.js')
  : path.join(root, 'assets/static/project-map/legacy.js')
const srcLines = fs.readFileSync(srcPath, 'utf8').split('\n')

const SCOPE_VARS = new Set([
  'config',
  'projectSlug',
  'simDefaults',
  'sites',
  'siteBySlug',
  'map',
  'mapReady',
  'restoring',
  'landSources',
  'landDataGdbPaths',
  'landAoiDigest',
  'landSidebar',
  'selectedSlug',
  'editMode',
  'createMode',
  'editKind',
  'editSlug',
  'entityPanelTab',
  'entityPanelFilterByViewport',
  'scheduleSaveMapState',
  'renderEntityPanel',
  'openWaDialog',
  'registerSite',
  'applySavedSiteToMap',
  'ensureSiteVisibleAfterAdd',
  'sitesApiUrl',
  'raiseSiteLayers',
  'syncSeekWedge',
  'removeSeekWedgeLayers',
  'removeSeekGoalLineLayer',
  'seekSessionActive',
  'seekCurrentFrom',
  'seekGoalCoords',
  'loadDraftViewshedAt',
  'setViewshedVisible',
  'isViewshedVisible',
  'hideViewshedLayerForEdit',
  'viewshedVisible',
  'viewshedLoading',
  'siteViewshedReady',
  'viewshedLoadEpoch',
  'viewshedPendingEpoch',
  'viewshedRadiusKm',
  'viewshedQuality',
  'viewshedOpacity',
  'siteLinksPayload',
  'showSiteLinks',
  'siteOutboundLinksReady',
  'refreshFilteredLinks',
  'syncMapCursor',
  'setEntityPanelOpen',
  'selectSite',
  'deselectSite',
  'normalizeSiteFromApi',
  'compareHuman',
  'formatCoord',
  'parseCoordPairFromText',
  'coordsUsableForMarker',
  'slugifyName',
  'previewSlugForName',
  'arrayBufferToBase64',
  'lngLatBoundsFromPoints',
  'padMapBounds',
  'mapDataViewportBounds',
  'mapSeekScanBounds',
  'seekScanBoundsForRequest',
  'seekPeakBinSizeMForBounds',
  'isMapTiltedView',
  'mapOverheadEquivalentBounds',
  'clampRadiusKm',
  'clampViewshedQuality',
  'VIEWSHED_RADIUS_KM_MIN',
  'VIEWSHED_RADIUS_KM_MAX',
  'siteHidden',
  'activeTagFilters',
  'tagFilterMode',
  'tagFilterBypassSlugs',
  'sitePassesTagFilter',
  'allProjectTags',
  'renderTagToggleChips',
  'bulkTagModal',
  'bulkTagSave',
  'bulkTagError',
  'importSitesModal',
  'importSitesError',
  'importSitesSave',
  'importSitesPreviewMap',
  'ensureViewshedLoadedForSlug',
  'markSiteViewshedReady',
  'markSiteOutboundLinksReady',
  'applySiteLinksPayload',
  'linksApiUrl',
  'siteLinksApiUrl',
  'linksWarmApiUrl',
  'warmPrioritiesApiUrl',
  'syncWarmPriorities',
  'scheduleWarmPrioritiesSync',
  'onMapMoveEndForWarmPriorities',
  'bumpWarmPriorities',
  'warmPrioritySlugsInViewport',
  'serveEventsSource',
  'reloadViewshedsForSimChange',
  'setViewshedSimulation',
  'computeViewshedRaster',
  'haversineMeters',
  'bearingDeg',
  'destinationPointLatLon',
  'buildSeekWedgeFeature',
  'buildSeekGoalLineFeature',
  'seekWedgeHalfAngleDeg',
  'seekHopRadiusM',
  'MAP_STATE_KEY',
  'SEEK_STATE_KEY',
  'SEEK_REDO_KEY',
])

const modules = [
  {
    file: 'land/index.js',
    install: 'installLand',
    start: 2841,
    end: 5799,
    skip: [{ start: 3522, end: 3540 }],
    exportPrefix: 'land',
    extraImports: `import { normalizeLandSidebarInput } from './sidebar-model.js'`,
    sidebarImport: './sidebar-model.js',
  },
  {
    file: 'import-sites.js',
    install: 'installImportSites',
    start: 7429,
    end: 8016,
    exportNames: [
      'annotateImportPoints',
      'setImportSitesError',
      'renderImportSitesList',
      'openImportSitesModal',
      'closeImportSitesModal',
      'saveImportSitesModal',
      'ensureImportPreviewMap',
      'destroyImportPreviewMap',
      'fitImportPreviewToPoints',
      'refreshImportPreviewLayers',
    ],
  },
  {
    file: 'entity-panel-bulk-tag.js',
    install: 'installBulkTag',
    start: 1997,
    end: 2200,
    exportNames: [
      'bulkTagListTags',
      'bulkTagVisualState',
      'bulkTagHasChanges',
      'renderBulkTagTags',
      'openBulkTagModal',
      'saveBulkTagModal',
    ],
  },
  {
    file: 'map-state.js',
    install: 'installMapState',
    ranges: [
      { start: 277, end: 302 },
      { start: 5802, end: 5833 },
    ],
  },
  {
    file: 'links.js',
    install: 'installLinks',
    ranges: [
      { start: 1203, end: 1276 },
      { start: 1581, end: 1668 },
      { start: 5970, end: 6031 },
    ],
  },
  {
    file: 'viewsheds.js',
    install: 'installViewsheds',
    ranges: [
      { start: 1606, end: 1675 },
      { start: 6032, end: 6959 },
    ],
  },
  {
    file: 'sites-edit.js',
    install: 'installSitesEdit',
    start: 8017,
    end: 9082,
  },
  {
    file: 'interactions.js',
    install: 'installInteractions',
    ranges: [
      { start: 9084, end: 9118 },
      { start: 11195, end: 11280 },
    ],
  },
  {
    file: 'toolbar.js',
    install: 'installToolbar',
    ranges: [
      { start: 128, end: 248 },
      { start: 250, end: 418 },
    ],
  },
]

function sliceRanges(ranges, skip = []) {
  const out = []
  for (const { start, end } of ranges) {
    for (let i = start - 1; i <= end - 1 && i < srcLines.length; i++) {
      const n = i + 1
      if (skip.some((s) => n >= s.start && n <= s.end)) continue
      out.push(srcLines[i].replace(/^  /, ''))
    }
    out.push('')
  }
  return out.join('\n')
}

function rewriteScope(code) {
  return code.replace(/\b([A-Za-z_$][\w$]*)\b/g, (word, _m, offset, str) => {
    const before = str.slice(Math.max(0, offset - 1), offset)
    const after = str.slice(offset + word.length, offset + word.length + 1)
    if (before === '.' || after === '.') return word
    if (word === 'C') return word
    if (!SCOPE_VARS.has(word)) return word
    return `scope.${word}`
  })
}

function extractAllFnNames(code) {
  const names = []
  for (const m of code.matchAll(/(?:async )?function ([A-Za-z_$][\w$]*)\(/g)) names.push(m[1])
  return [...new Set(names)]
}

for (const mod of modules) {
  const ranges = mod.ranges || [{ start: mod.start, end: mod.end }]
  let chunk = sliceRanges(ranges, mod.skip || [])
  chunk = rewriteScope(chunk)
  const publicNames = mod.exportNames || extractAllFnNames(chunk)
  const assigns = publicNames.map((n) => `  scope.${n} = ${n}`).join('\n')

  const importPath = mod.file.includes('/') ? '../' : './'
  const sidebarImport =
    mod.file === 'land/index.js'
      ? `import { normalizeLandSidebarInput } from './sidebar-model.js'\n`
      : ''

  const content = `import * as C from '${importPath}constants.js'
import {
  haversineMeters,
  bearingDeg,
  destinationPointLatLon,
  buildSeekWedgeFeature,
  buildSeekGoalLineFeature,
  seekWedgeHalfAngleDeg,
  formatCoord,
  parseCoordPairFromText,
  coordsUsableForMarker,
  slugifyName,
  lngLatBoundsFromPoints,
  padMapBounds,
  compareHuman,
  arrayBufferToBase64,
  haversineMeters as haversineMetersGeo,
} from '${importPath}geo.js'
${sidebarImport}
/** @param {Record<string, unknown>} scope */
export function ${mod.install}(scope) {
${chunk}

${assigns}
}
`
  const outPath = path.join(root, 'assets/static/project-map', mod.file)
  fs.mkdirSync(path.dirname(outPath), { recursive: true })
  fs.writeFileSync(outPath, content)
  console.log(mod.file, publicNames.length, 'exports')
}
