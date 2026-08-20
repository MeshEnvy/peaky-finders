import * as C from './constants.js'
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
} from './geo.js'

/** @param {Record<string, unknown>} scope */
export function installMapState(scope) {
function isValidSavedState(saved) {
  if (!saved || !Array.isArray(saved.center) || saved.center.length !== 2)
    return false;
  if (!BASEMAPS[saved.basemap]) return false;
  if (typeof saved.zoom !== "number") return false;
  return true;
}

function loadMapState() {
  try {
    const raw = localStorage.getItem(scope.MAP_STATE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    return isValidSavedState(saved) ? saved : null;
  } catch (_) {
    return null;
  }
}

function persistMapState(state) {
  try {
    localStorage.setItem(scope.MAP_STATE_KEY, JSON.stringify(state));
  } catch (_) {
    /* private mode or quota */
  }
}

function captureMapState() {
  const c = map.getCenter();
  return {
    v: 1,
    center: [c.lng, c.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
    basemap: currentBasemapKey,
    showLinks: scope.showSiteLinks,
    scope.viewshedOpacity,
    hiddenSites: [...siteHidden],
    tagFilters: [...activeTagFilters].sort((a, b) => a.localeCompare(b)),
    scope.tagFilterMode,
    filterByViewport: scope.entityPanelFilterByViewport,
    scope.viewshedVisible: Object.fromEntries(scope.viewshedVisible),
    entityPanelOpen,
    scope.entityPanelTab,
    landVisible: Object.fromEntries(landVisible),
    landLabelsVisible: Object.fromEntries(landLabelsVisible),
    landFoldersCollapsed: Object.fromEntries(landFoldersCollapsed),
  };
}

function scheduleSaveMapState() {
  if (!scope.mapReady || scope.restoring) return;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    persistMapState(captureMapState());
  }, MAP_STATE_SAVE_MS);
}


  scope.isValidSavedState = isValidSavedState
  scope.loadMapState = loadMapState
  scope.persistMapState = persistMapState
  scope.captureMapState = captureMapState
}
