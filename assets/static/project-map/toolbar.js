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
export function installToolbar(scope) {
function syncOpacitySlider() {
  const opacityEl = document.getElementById("viewshed-opacity");
  if (opacityEl) opacityEl.value = String(Math.round(scope.viewshedOpacity * 100));
}

function mapToolIcon(name, label) {
  return `<wa-icon name="${name}" label="${label}"></wa-icon>`;
}

function installMapToolbar(navGroup) {
  const basemapDropdown = document.createElement("wa-dropdown");
  basemapDropdown.className = "map-toolbar-dropdown";
  basemapDropdown.placement = "bottom-end";

  const basemapBtn = document.createElement("button");
  basemapBtn.type = "button";
  basemapBtn.slot = "trigger";
  basemapBtn.id = "map-tool-basemap";
  basemapBtn.className = "map-toolbar-tool";
  basemapBtn.setAttribute("aria-label", "Base map");
  basemapBtn.title = "Base map";
  basemapBtn.innerHTML = mapToolIcon("layer-group", "Base map");

  for (const [key, label] of [
    ["street", "Street"],
    ["topo", "USGS Topo"],
    ["skadi", "Skadi relief (analysis DEM)"],
    ["satellite", "Satellite"],
  ]) {
    const item = document.createElement("wa-dropdown-item");
    item.setAttribute("data-basemap", key);
    item.value = key;
    item.textContent = label;
    basemapDropdown.appendChild(item);
  }
  basemapDropdown.insertBefore(basemapBtn, basemapDropdown.firstChild);

  const seekBtn = document.createElement("button");
  seekBtn.type = "button";
  seekBtn.id = "map-tool-seek";
  seekBtn.className = "map-toolbar-tool";
  seekBtn.setAttribute("aria-pressed", "false");
  seekBtn.setAttribute("aria-controls", "seek-panel");
  seekBtn.setAttribute("aria-label", "Goal seek");
  seekBtn.title = "Goal seek";
  seekBtn.innerHTML = mapToolIcon("route", "Goal seek");

  const sitesBtn = document.createElement("button");
  sitesBtn.type = "button";
  sitesBtn.id = "map-tool-sites";
  sitesBtn.className = "map-toolbar-tool";
  sitesBtn.setAttribute("aria-pressed", "false");
  sitesBtn.setAttribute("aria-controls", "entity-panel");
  sitesBtn.setAttribute("aria-label", "Sites");
  sitesBtn.title = "Sites";
  sitesBtn.innerHTML = mapToolIcon("tower-broadcast", "Sites");

  const settingsBtn = document.createElement("button");
  settingsBtn.type = "button";
  settingsBtn.id = "home-settings-open";
  settingsBtn.className = "map-toolbar-tool";
  settingsBtn.setAttribute("aria-label", "Settings");
  settingsBtn.title = "Settings";
  settingsBtn.innerHTML = mapToolIcon("gear", "Settings");

  const opacityDropdown = document.createElement("wa-dropdown");
  opacityDropdown.className = "map-toolbar-dropdown";
  opacityDropdown.placement = "bottom-end";

  const opacityBtn = document.createElement("button");
  opacityBtn.type = "button";
  opacityBtn.slot = "trigger";
  opacityBtn.id = "map-tool-opacity";
  opacityBtn.className = "map-toolbar-tool";
  opacityBtn.setAttribute("aria-label", "Viewshed opacity");
  opacityBtn.title = "Viewshed opacity";
  opacityBtn.innerHTML = mapToolIcon("droplet", "Viewshed opacity");

  const opacityMenu = document.createElement("div");
  opacityMenu.id = "map-opacity-menu";
  opacityMenu.className = "map-opacity-menu";

  const opacityLabel = document.createElement("label");
  opacityLabel.className = "pf-label";
  opacityLabel.htmlFor = "viewshed-opacity";
  opacityLabel.textContent = "Viewshed opacity";

  const opacitySlider = document.createElement("input");
  opacitySlider.type = "range";
  opacitySlider.id = "viewshed-opacity";
  opacitySlider.className = "pf-range";
  opacitySlider.min = "0";
  opacitySlider.max = "100";
  opacitySlider.value = String(Math.round(scope.viewshedOpacity * 100));
  opacitySlider.title = "Viewshed opacity";

  opacityMenu.appendChild(opacityLabel);
  opacityMenu.appendChild(opacitySlider);
  opacityDropdown.appendChild(opacityBtn);
  opacityDropdown.appendChild(opacityMenu);

  for (const el of [
    basemapDropdown,
    seekBtn,
    sitesBtn,
    opacityDropdown,
    settingsBtn,
  ]) {
    navGroup.appendChild(el);
  }

  syncOpacitySlider();

  return {
    mapBasemapMenu: basemapDropdown,
    mapToolSeek: seekBtn,
    mapToolSites: sitesBtn,
    viewshedOpacityInput: opacitySlider,
    mapToolSettings: settingsBtn,
  };
}

const BASEMAPS = {
  street: {
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    maxzoom: 19,
  },
  topo: {
    tiles: [
      "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
    ],
    maxzoom: 16,
  },
  skadi: {
    tiles: ["/api/dem/hillshade/{z}/{x}/{y}"],
    maxzoom: 13,
    analysisDem: true,
  },
  satellite: {
    tiles: [
      "https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    ],
    referenceTiles: [
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    ],
    maxzoom: 19,
  },
};

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

const savedMapState = loadMapState();
let currentBasemapKey =
  savedMapState && BASEMAPS[savedMapState.basemap]
    ? savedMapState.basemap
    : "street";
scope.showSiteLinks = savedMapState?.showLinks ?? true;
scope.viewshedOpacity =
  savedMapState?.viewshedOpacity ?? VIEWSHED_OPACITY_DEFAULT;
const defaultRadiusKm = Number(simDefaults.radius_km) || 60;
const defaultViewshedQuality = Number(simDefaults.viewshed_quality) || 3;
const defaultTxHeightM = Number(simDefaults.transmitter?.height_m) || 2;
scope.viewshedRadiusKm = defaultRadiusKm;
scope.viewshedQuality = defaultViewshedQuality;
scope.viewshedRadiusKm = Math.max(
  scope.VIEWSHED_RADIUS_KM_MIN,
  Math.min(scope.VIEWSHED_RADIUS_KM_MAX, scope.viewshedRadiusKm),
);
scope.viewshedQuality = Math.max(
  VIEWSHED_QUALITY_MIN,
  Math.min(VIEWSHED_QUALITY_MAX, Math.round(scope.viewshedQuality)),
);

function syncToolbarFromSaved(saved) {
  if (!saved) return;
  if (BASEMAPS[saved.basemap]) currentBasemapKey = saved.basemap;
  if (typeof saved.showLinks === "boolean") scope.showSiteLinks = saved.showLinks;
  if (typeof saved.viewshedOpacity === "number") {
    scope.viewshedOpacity = saved.viewshedOpacity;
    syncOpacitySlider();
  }
}

function clampRadiusKm(km) {
  return Math.max(
    scope.VIEWSHED_RADIUS_KM_MIN,
    Math.min(scope.VIEWSHED_RADIUS_KM_MAX, Number(km)),
  );
}

function clampViewshedQuality(quality) {
  return Math.max(
    VIEWSHED_QUALITY_MIN,
    Math.min(VIEWSHED_QUALITY_MAX, Math.round(Number(quality))),
  );
}

function setViewshedSimulation(radiusKm, quality) {
  const nextRadius = scope.clampRadiusKm(radiusKm);
  const nextQuality = scope.clampViewshedQuality(quality);
  const changed =
    nextRadius !== scope.viewshedRadiusKm || nextQuality !== scope.viewshedQuality;
  scope.viewshedRadiusKm = nextRadius;
  scope.viewshedQuality = nextQuality;
  if (
    window.PEAKY_HOME_SETTINGS &&
    typeof window.PEAKY_HOME_SETTINGS.updateGearSummary === "function"
  ) {
    window.PEAKY_HOME_SETTINGS.updateGearSummary();
  }
  return changed;
}

syncToolbarFromSaved(savedMapState);
scope.setViewshedSimulation(defaultRadiusKm, defaultViewshedQuality);

function basemapStyle(key) {
  const bm = BASEMAPS[key] || BASEMAPS.street;
  const layers = [];
  if (bm.analysisDem) {
    layers.push({
      id: "background",
      type: "background",
      paint: { "background-color": "#3d4654" },
    });
  }
  layers.push({ id: "basemap", type: "raster", source: "basemap" });
  return {
    version: 8,
    glyphs: MAP_GLYPHS_URL,
    sources: {
      basemap: {
        type: "raster",
        tiles: bm.tiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      },
    },
    layers,
  };
}

function usesSkadiAnalysisDem() {
  return Boolean(BASEMAPS[currentBasemapKey]?.analysisDem);
}

function terrainDemSourceSpec() {
  if (usesSkadiAnalysisDem()) {
    return {
      type: "raster-dem",
      tiles: ["/api/dem/terrarium/{z}/{x}/{y}"],
      tileSize: 256,
      maxzoom: 13,
      encoding: "terrarium",
    };
  }
  return {
    type: "raster-dem",
    tiles: [
      "https://elevation-tiles-prod.s3.amazonaws.com/v2/terrarium/{z}/{x}/{y}.png",
    ],
    tileSize: 256,
    maxzoom: 15,
    encoding: "terrarium",
  };
}


  scope.syncOpacitySlider = syncOpacitySlider
  scope.mapToolIcon = mapToolIcon
  scope.installMapToolbar = installMapToolbar
  scope.isValidSavedState = isValidSavedState
  scope.loadMapState = loadMapState
  scope.persistMapState = persistMapState
  scope.syncToolbarFromSaved = syncToolbarFromSaved
  scope.basemapStyle = basemapStyle
  scope.usesSkadiAnalysisDem = usesSkadiAnalysisDem
  scope.terrainDemSourceSpec = terrainDemSourceSpec
}
