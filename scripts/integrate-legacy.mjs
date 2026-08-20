#!/usr/bin/env node
/**
 * Integrate extracted domain modules into legacy.js.
 */
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const legacyPath = path.join(root, 'assets/static/project-map/legacy.js')

const fnGroups = {
  land: /^  (?:async )?function land[A-Za-z0-9_]*\(/,
  seek: /^  (?:async )?function seek[A-Za-z0-9_]*\(/,
  seekGeo: /^  (?:async )?function (seekHopRadiusM|syncSeekWedge|removeSeekWedgeLayers|removeSeekGoalLineLayer|ensureSeekCandidateLayers|syncSeekGoalLineLayer)\(/,
  mapState: /^  (?:async )?function (loadMapState|persistMapState|isValidSavedState|captureMapState|scheduleSaveMapState)\(/,
  links: /^  (?:async )?function (linksApiUrl|siteLinksApiUrl|linksWarmApiUrl|warmPrioritiesApiUrl|warmPrioritySlugsInViewport|bumpWarmPriorities|syncWarmPriorities|scheduleWarmPrioritiesSync|onMapMoveEndForWarmPriorities|filterSiteLinksGeoJson|refreshFilteredLinks|applySiteLinksPayload|setSiteLinksVisible|linkedPeersForSite|linkFeatureTouchesSnapshotCoords|combineLayerFilters|editSiteLayerFilter|siteVisibilityFilter|applySiteLayerFilters)\(/,
  viewsheds: /^  (?:async )?function (applyViewshedVisibilityForSite|ensureViewshedLoadedForSlug|resetSiteProgress|markSiteViewshedReady|markSiteOutboundLinksReady|sitePinSpinning|ensureViewshedsForNewlyVisibleSites|applyViewshedOpacityToAllLayers|applyViewshedOverlaysBatched|viewshedMetaUrl|loadViewshedIndex|loadViewshedOverlayForSlug|removeViewshedOverlay|setViewshedVisible|isViewshedVisible|hideViewshedLayerForEdit|loadDraftViewshedAt|reloadViewshedsForSimChange|connectServeEvents|handleServeEvent|ensureOutboundLinksForSlug|loadSingleSiteLinks|warmViewshedForSlug|warmOutboundLinksForSlug|loadPlacementPrefetchAt|renderPinLoadOverlay|ensurePinLoadMarker|hidePinLoadMarker|setSitePinProgress|clearSitePinProgress|sitePinProgressLabel|sitePinProgressFraction)\(/,
  sitesEdit: /^  (?:async )?function (showPanelView|showPanelCreate|showPanelEdit|setCreateError|removeDraftMarker|syncEditMapShell|syncMapCursor|clearAddPlacementMode|setAddPlacementMode|syncBasemapMenu|syncEntityPanelToggles|syncMapViewport|setEntityPanelOpen|toggleEntityPanel|editCoordsMovedFromSnapshot|editWantsDraftViewshed|ensureEditDraftViewshedEnabled|loadEditDraftViewshedAt|updateEditDraftMarker|syncEditViewshedCheckbox|readEditCoords|applyCoordPaste|editShowsSitePreview|coordsMatchEditSnapshot|onEditCoordsChanged|pushEditCoordHistory|clearEditCoordHistory|renderEditCoordHistory|cancelEdit|saveEdit|openCreatePanel|cancelCreate|saveCreate|beginCreateAtMapPoint|openEditPanel|selectSite|deselectSite|applySavedSiteToMap|registerSite|unregisterSite|patchSiteTags|renderSitePanel|syncSitePanelLinks|renderSiteTags|openAddSiteModal|closeAddSiteModal|saveAddSiteModal|resetAddSiteModal|addDraftTagFromInput|renderAddSiteTags|syncAddSiteTagSuggestions|setAddSiteError|addSiteLayers|raiseSiteLayers|ensureSiteLayerOrder)\(/,
  interactions: /^  (?:async )?function (wireMapLongPress|wireMapInteractions)\(/,
  toolbar: /^  (?:async )?function (syncOpacitySlider|mapToolIcon|installMapToolbar|basemapStyle|usesSkadiAnalysisDem|terrainDemSourceSpec|setBasemap|ensureTerrainSource|refreshTerrainSourceIfNeeded|ensureHillshadeLayer|removeHillshadeLayer|removeTerrainSource)\(/,
  importSites: /^  (?:async )?function (?:setImportSitesError|annotateImportPoints|importablePreviewPoints|pendingImportTagInput|effectiveImportDraftTags|refreshImportPreviewMapData|setImportPointIgnored|setAllImportPointsIgnored|syncImportBulkActions|syncImportSaveButton|renderImportSiteTags|syncImportSiteTagSuggestions|destroyImportPreviewMap|importPointVisibleInMap|syncImportListCount|syncImportPreviewStatus|renderImportPointList|focusImportPreviewPoint|onImportPreviewMapMoveEnd|ensureImportPreviewMapHandlers|importPreviewGeoJson|fitImportPreviewBounds|renderImportPreview|resetImportSitesModal|previewImportFile|openImportSitesModal|closeImportSitesModal|addImportDraftTagFromInput|saveImportSitesModal)\(/,
  bulkTag: /^  (?:async )?function bulkTag[A-Za-z0-9_]*\(/,
}

const moduleFiles = [
  'land/index.js',
  'seek.js',
  'import-sites.js',
  'entity-panel-bulk-tag.js',
  'map-state.js',
  'links.js',
  'viewsheds.js',
  'sites-edit.js',
  'interactions.js',
  'toolbar.js',
]

function removeMatchingFunctions(lines, patterns) {
  const out = []
  let skipping = false
  let depth = 0
  for (const line of lines) {
    if (!skipping) {
      for (const pat of patterns) {
        if (pat.test(line)) {
          skipping = true
          depth = 0
          break
        }
      }
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

let lines = fs.readFileSync(legacyPath, 'utf8').split('\n')
for (const pattern of Object.values(fnGroups)) {
  lines = removeMatchingFunctions(lines, [pattern])
}
let text = lines.join('\n')

const scopeFnNames = []
for (const file of moduleFiles) {
  const p = path.join(root, 'assets/static/project-map', file)
  if (!fs.existsSync(p)) continue
  const mod = fs.readFileSync(p, 'utf8')
  for (const m of mod.matchAll(/scope\.([A-Za-z0-9_]*) =/g)) scopeFnNames.push(m[1])
  for (const m of mod.matchAll(/(?:async )?function ([A-Za-z_$][\w$]*)\(/g)) scopeFnNames.push(m[1])
}
const uniqueFns = [...new Set(scopeFnNames)].sort((a, b) => b.length - a.length)

if (!text.includes("import { installLand }")) {
  text = text.replace(
    "import { normalizeLandSidebarInput } from './land/sidebar-model.js'",
    `import { normalizeLandSidebarInput } from './land/sidebar-model.js'
import { installLand } from './land/index.js'
import { installImportSites } from './import-sites.js'
import { installBulkTag } from './entity-panel-bulk-tag.js'
import { installSeek } from './seek.js'
import { installMapState } from './map-state.js'
import { installLinks } from './links.js'
import { installViewsheds } from './viewsheds.js'
import { installSitesEdit } from './sites-edit.js'
import { installInteractions } from './interactions.js'
import { installToolbar } from './toolbar.js'`,
  )
}

if (!text.includes('const scope =')) {
  text = text.replace(
    'export function initProjectMap() {',
    'export function initProjectMap() {\n  /** @type {Record<string, unknown>} */\n  const scope = {}',
  )
}

for (const fn of uniqueFns) {
  text = text.replace(new RegExp(`(?<!function )(?<!scope\\.)\\b${fn}\\(`, 'g'), `scope.${fn}(`)
}

const installBlock = `
  Object.assign(scope, {
    config,
    projectSlug,
    simDefaults,
    sites,
    siteBySlug,
    map,
    mapReady,
    restoring,
    landSources,
    landDataGdbPaths,
    landAoiDigest,
    landSidebar,
    selectedSlug,
    editMode,
    createMode,
    editKind,
    editSlug,
    entityPanelTab,
    entityPanelFilterByViewport,
    scheduleSaveMapState,
    renderEntityPanel,
    openWaDialog,
    registerSite,
    applySavedSiteToMap,
    ensureSiteVisibleAfterAdd,
    sitesApiUrl,
    raiseSiteLayers,
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
    syncMapCursor,
    setEntityPanelOpen,
    selectSite,
    deselectSite,
    normalizeSiteFromApi,
    compareHuman,
    formatCoord,
    parseCoordPairFromText,
    coordsUsableForMarker,
    slugifyName,
    previewSlugForName,
    arrayBufferToBase64,
    lngLatBoundsFromPoints,
    padMapBounds,
    mapDataViewportBounds,
    mapSeekScanBounds,
    seekScanBoundsForRequest,
    seekPeakBinSizeMForBounds,
    isMapTiltedView,
    mapOverheadEquivalentBounds,
    clampRadiusKm,
    clampViewshedQuality,
    VIEWSHED_RADIUS_KM_MIN,
    VIEWSHED_RADIUS_KM_MAX,
    siteHidden,
    activeTagFilters,
    tagFilterMode,
    tagFilterBypassSlugs,
    sitePassesTagFilter,
    allProjectTags,
    renderTagToggleChips,
    bulkTagModal,
    bulkTagSave,
    bulkTagError,
    importSitesModal,
    importSitesError,
    importSitesSave,
    ensureViewshedLoadedForSlug,
    markSiteViewshedReady,
    markSiteOutboundLinksReady,
    applySiteLinksPayload,
    linksApiUrl,
    siteLinksApiUrl,
    linksWarmApiUrl,
    warmPrioritiesApiUrl,
    syncWarmPriorities,
    scheduleWarmPrioritiesSync,
    onMapMoveEndForWarmPriorities,
    bumpWarmPriorities,
    warmPrioritySlugsInViewport,
    serveEventsSource,
    reloadViewshedsForSimChange,
    setViewshedSimulation,
    computeViewshedRaster,
    haversineMeters,
    bearingDeg,
    destinationPointLatLon,
    buildSeekWedgeFeature,
    buildSeekGoalLineFeature,
    seekWedgeHalfAngleDeg,
    MAP_STATE_KEY,
    SEEK_STATE_KEY,
    SEEK_REDO_KEY,
    C,
  })
  installLand(scope)
  installImportSites(scope)
  installBulkTag(scope)
  installSeek(scope)
  installMapState(scope)
  installLinks(scope)
  installViewsheds(scope)
  installSitesEdit(scope)
  installInteractions(scope)
  installToolbar(scope)
`

if (!text.includes('installLand(scope)')) {
  text = text.replace(
    /\n  return \{\n    reloadViewshedsForSimChange,/,
    `${installBlock}\n\n  return {\n    reloadViewshedsForSimChange,`,
  )
}

fs.writeFileSync(legacyPath, text)
console.log('Integrated legacy.js; scope fns:', uniqueFns.length)
