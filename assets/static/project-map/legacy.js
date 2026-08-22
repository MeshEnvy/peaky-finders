import * as C from './constants.js'
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
import { installLand } from './land/index.js'
import { installImportSites } from './import-sites.js'
import { installBulkTag } from './entity-panel-bulk-tag.js'
import { installSeek } from './seek.js'
import { installMapState } from './map-state.js'
import { installLinks } from './links.js'
import { installViewsheds } from './viewsheds.js'
import { installSitesEdit } from './sites-edit.js'
import { installInteractions } from './interactions.js'
import { installToolbar } from './toolbar.js'

/** @typedef {import('./ctx.js').MapContext} MapContext */

/**
 * @returns {{ reloadViewshedsForSimChange: () => void, setViewshedSimulation: (radiusKm: number, quality: number) => boolean }}
 */
export function initProjectMap() {
  const config = window.PEAKY_PROJECT || {};
  const projectSlug = config.slug
  const simDefaults = config.simulation || {}
  const radiusBounds = C.viewshedRadiusBounds(simDefaults)
  const VIEWSHED_RADIUS_KM_MIN = radiusBounds.min
  const VIEWSHED_RADIUS_KM_MAX = radiusBounds.max
  const MAP_STATE_KEY = C.mapStateKey(projectSlug)
  const SEEK_STATE_KEY = C.seekStateKey(projectSlug)
  const SEEK_REDO_KEY = C.seekRedoKey(projectSlug)
  let sites = config.sites || [];
  let landSources = Array.isArray(config.land?.sources)
    ? [...config.land.sources]
    : [];
  let landDataGdbPaths = Array.isArray(config.land?.dataGdbPaths)
    ? [...config.land.dataGdbPaths]
    : [];
  let landAoiDigest =
    typeof config.land?.aoiDigest === "string" ? config.land.aoiDigest : "none";
  let landSidebar = normalizeLandSidebarInput(config.land?.sidebar);

  function isMapTiltedView(mapInstance = map) {
    return isMapTiltedViewGeo(mapInstance, mapReady)
  }

  function mapDataViewportBounds(mapInstance = map) {
    return mapDataViewportBoundsGeo(mapInstance, mapReady)
  }

  function mapSeekScanBounds(mapInstance = map) {
    return mapSeekScanBoundsGeo(mapInstance, mapReady)
  }


  const SKADI_DEM_SPACING_M = 30;
  const VIEWSHED_RASTER_MIN = 128;
  const VIEWSHED_RASTER_MAX = 4096;
    "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf";








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




  const savedMapState = scope.loadMapState();
  let currentBasemapKey =
    savedMapState && BASEMAPS[savedMapState.basemap]
      ? savedMapState.basemap
      : "street";
  let showSiteLinks = savedMapState?.showLinks ?? true;
  let viewshedOpacity =
    savedMapState?.viewshedOpacity ?? C.VIEWSHED_OPACITY_DEFAULT;
  const defaultRadiusKm = Number(simDefaults.radius_km) || 60;
  const defaultViewshedQuality = Number(simDefaults.viewshed_quality) || 3;
  const defaultTxHeightM = Number(simDefaults.transmitter?.height_m) || 2;
  let viewshedRadiusKm = defaultRadiusKm;
  let viewshedQuality = defaultViewshedQuality;
  viewshedRadiusKm = Math.max(
    VIEWSHED_RADIUS_KM_MIN,
    Math.min(VIEWSHED_RADIUS_KM_MAX, viewshedRadiusKm),
  );
  viewshedQuality = Math.max(
    C.VIEWSHED_QUALITY_MIN,
    Math.min(C.VIEWSHED_QUALITY_MAX, Math.round(viewshedQuality)),
  );

  function syncToolbarFromSaved(saved) {
    if (!saved) return;
    if (BASEMAPS[saved.basemap]) currentBasemapKey = saved.basemap;
    if (typeof saved.showLinks === "boolean") showSiteLinks = saved.showLinks;
    if (typeof saved.viewshedOpacity === "number") {
      viewshedOpacity = saved.viewshedOpacity;
      scope.syncOpacitySlider();
    }
  }



  function setViewshedSimulation(radiusKm, quality) {
    const nextRadius = clampRadiusKm(radiusKm, VIEWSHED_RADIUS_KM_MIN, VIEWSHED_RADIUS_KM_MAX);
    const nextQuality = clampViewshedQuality(quality);
    const changed =
      nextRadius !== viewshedRadiusKm || nextQuality !== viewshedQuality;
    viewshedRadiusKm = nextRadius;
    viewshedQuality = nextQuality;
    if (
      window.PEAKY_HOME_SETTINGS &&
      typeof window.PEAKY_HOME_SETTINGS.updateGearSummary === "function"
    ) {
      window.PEAKY_HOME_SETTINGS.updateGearSummary();
    }
    return changed;
  }

  scope.syncToolbarFromSaved(savedMapState);
  setViewshedSimulation(defaultRadiusKm, defaultViewshedQuality);





  /** True when the map is pitched into 3D terrain view. */

  function sitesGeoJson() {
    return {
      type: "FeatureCollection",
      features: sites
        .filter(
          (site) => Number.isFinite(site.lat) && Number.isFinite(site.lon),
        )
        .map((site) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [site.lon, site.lat] },
          properties: { name: site.name, slug: site.slug },
        })),
    };
  }

  function normalizeSiteFromApi(site) {
    if (!site || !site.slug) return null;
    const lat = Number(site.lat);
    const lon = Number(site.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return {
      ...site,
      slug: String(site.slug),
      name: String(site.name || site.slug),
      lat,
      lon,
      tags: Array.isArray(site.tags)
        ? site.tags.map(String).filter(Boolean)
        : [],
    };
  }

  sites = sites.map(normalizeSiteFromApi).filter(Boolean);

  function ensureSiteVisibleAfterAdd(site) {
    siteHidden.delete(site.slug);
    if (!sitePassesTagFilter(site)) {
      tagFilterBypassSlugs.add(site.slug);
    }
  }

  const map = new maplibregl.Map({
    container: "map",
    style: scope.basemapStyle(currentBasemapKey),
    center: savedMapState ? savedMapState.center : [-98.35, 39.5],
    zoom: savedMapState ? savedMapState.zoom : 4,
    maxPitch: 85,
    bearing: savedMapState ? savedMapState.bearing || 0 : 0,
    pitch: savedMapState ? savedMapState.pitch || 0 : 0,
    maxParallelImageRequests: 64,
    attributionControl: { compact: true },
  });
  const navControl = new maplibregl.NavigationControl({ visualizePitch: true });
  map.addControl(navControl, "top-right");
  const mapContainer = document.getElementById("map");
  let pinOverlayResizeRaf = 0;
  if (mapContainer && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      if (!mapReady) return;
      const rect = mapContainer.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      map.resize();
      if (pinOverlayResizeRaf) cancelAnimationFrame(pinOverlayResizeRaf);
      pinOverlayResizeRaf = requestAnimationFrame(() => {
        pinOverlayResizeRaf = 0;
        scope.updatePinOverlays();
      });
    }).observe(mapContainer);
  }
  const mapToolbarRefs = scope.installMapToolbar(navControl._container);
  const mapBasemapMenu = mapToolbarRefs.mapBasemapMenu;
  const mapToolSeek = mapToolbarRefs.mapToolSeek;
  const mapToolSites = mapToolbarRefs.mapToolSites;
  const viewshedOpacityInput = mapToolbarRefs.viewshedOpacityInput;
  const seekPanel = document.getElementById("seek-panel");
  const seekPanelClose = document.getElementById("seek-panel-close");
  const seekStartSelect = document.getElementById("seek-start-site");
  const seekGoalCoordsEl = document.getElementById("seek-goal-coords");
  const seekSetGoalBtn = document.getElementById("seek-set-goal-btn");
  const seekUndoBtn = document.getElementById("seek-undo-btn");
  const seekRedoBtn = document.getElementById("seek-redo-btn");
  const seekResetBtn = document.getElementById("seek-reset-btn");
  const seekConvertSitesBtn = document.getElementById("seek-convert-sites-btn");
  const seekRefreshBtn = document.getElementById("seek-refresh-btn");
  const seekConvertSitesModal = document.getElementById(
    "seek-convert-sites-modal",
  );
  const seekConvertNamePrefix = document.getElementById(
    "seek-convert-name-prefix",
  );
  const seekConvertHopCount = document.getElementById("seek-convert-hop-count");
  const seekConvertTagsEl = document.getElementById("seek-convert-tags");
  const seekConvertTagForm = document.getElementById("seek-convert-tag-form");
  const seekConvertTagInput = document.getElementById("seek-convert-tag-input");
  const seekConvertTagSuggestions = document.getElementById(
    "seek-convert-tag-suggestions",
  );
  const seekConvertError = document.getElementById("seek-convert-sites-error");
  const seekConvertSave = document.getElementById("seek-convert-save");
  const seekStatusEl = document.getElementById("seek-status");
  const seekProgressEl = document.getElementById("seek-progress");
  const seekProgressTrackEl = document.getElementById("seek-progress-track");
  const seekProgressBarEl = document.getElementById("seek-progress-bar");
  const seekProgressDetailEl = document.getElementById("seek-progress-detail");
  const compassButton = navControl._container.querySelector(
    ".maplibregl-ctrl-compass",
  );
  if (compassButton) {
    compassButton.addEventListener(
      "click",
      (e) => {
        e.preventDefault();
        e.stopImmediatePropagation();
        resetHomeView();
      },
      true,
    );
  }

  let mapReady = false;
  let restoring = true;
  let saveTimer = null;
  let terrainActive = false;
  let selectedSlug = null;
  let addPlacementMode = null;
  let createMode = false;
  let pendingCreateLat = null;
  let pendingCreateLon = null;
  let draftMarker = null;
  let siteLinksPayload = null;
  const viewshedVisible = new Map();
  if (
    savedMapState?.viewshedVisible &&
    typeof savedMapState.viewshedVisible === "object"
  ) {
    for (const [slug, visible] of Object.entries(
      savedMapState.viewshedVisible,
    )) {
      viewshedVisible.set(slug, visible !== false);
    }
  }
  const viewshedLoading = new Set();
  const siteViewshedReady = new Set();
  const siteOutboundLinksReady = new Set();
  const OUTBOUND_LINKS_PARALLEL = 3;
  const VIEWSHED_OVERLAY_BATCH = 3;
  const pinLoadMarkers = new Map();
  /** @type {Map<string, { raster?: number, target?: number, step?: number, total?: number, phase?: string }>} */
  const sitePinProgress = new Map();







  function setMarkerLngLatSafe(marker, lon, lat) {
    if (!marker || !coordsUsableForMarker(lon, lat)) return false;
    try {
      marker.setLngLat([lon, lat]);
      if (mapReady) marker.addTo(map);
      return true;
    } catch (_) {
      return false;
    }
  }



  let draftPlacementLat = null;
  let draftPlacementLon = null;
  let editMode = false;
  let editKind = null;
  let editSlug = null;
  let editSnapshot = null;
  let editCommittedCoords = null;
  let editCoordHistory = [];
  let editHistoryNextId = 0;
  const editHistoryMarkers = new Map();
  let editPrefetchTimer = null;
  let editPrefetchGen = 0;
  let editHiddenViewshedSlug = null;
  const siteBySlug = new Map(sites.map((s) => [s.slug, s]));

  const mapShell = document.querySelector(".map-shell");
  const sitePanel = document.getElementById("site-panel");
  const sitePanelView = document.getElementById("site-panel-view");
  const sitePanelCreate = document.getElementById("site-panel-create");
  const sitePanelClose = document.getElementById("site-panel-close");
  const sitePanelCreateClose = document.getElementById(
    "site-panel-create-close",
  );
  const sitePanelCreateName = document.getElementById("site-panel-create-name");
  const sitePanelCreateTagsEl = document.getElementById(
    "site-panel-create-tags",
  );
  const sitePanelCreateTagForm = document.getElementById(
    "site-panel-create-tag-form",
  );
  const sitePanelCreateTagInput = document.getElementById(
    "site-panel-create-tag-input",
  );
  const sitePanelCreateTagSuggestions = document.getElementById(
    "site-panel-create-tag-suggestions",
  );
  const sitePanelSlugPreview = document.getElementById(
    "site-panel-slug-preview",
  );
  const sitePanelCreateCoords = document.getElementById(
    "site-panel-create-coords",
  );
  const sitePanelCreateError = document.getElementById(
    "site-panel-create-error",
  );
  const sitePanelCreateSave = document.getElementById("site-panel-create-save");
  const sitePanelCreateCancel = document.getElementById(
    "site-panel-create-cancel",
  );
  const sitePanelViewshedToggle = document.getElementById(
    "site-panel-viewshed-toggle",
  );
  const sitePanelViewshedHint = document.getElementById(
    "site-panel-viewshed-hint",
  );
  const sitePanelCreateViewshed = document.getElementById(
    "site-panel-create-viewshed",
  );
  const sitePanelCreateViewshedSection = document.getElementById(
    "site-panel-create-viewshed-section",
  );
  const sitePanelCreateTitle = document.getElementById(
    "site-panel-create-title",
  );
  const sitePanelCreateBadge = document.getElementById(
    "site-panel-create-badge",
  );
  const sitePanelCreateLinksLabel = document.getElementById(
    "site-panel-create-links-label",
  );
  const sitePanelViewshedSection = document.getElementById("site-panel-footer");
  const sitePanelEdit = document.getElementById("site-panel-edit");
  const sitePanelEditOpen = document.getElementById("site-panel-edit-open");
  const sitePanelEditClose = document.getElementById("site-panel-edit-close");
  const sitePanelEditName = document.getElementById("site-panel-edit-name");
  const sitePanelEditSlug = document.getElementById("site-panel-edit-slug");
  const sitePanelEditLat = document.getElementById("site-panel-edit-lat");
  const sitePanelEditLon = document.getElementById("site-panel-edit-lon");
  const sitePanelEditHeight = document.getElementById("site-panel-edit-height");
  const sitePanelEditHeightHint = document.getElementById(
    "site-panel-edit-height-hint",
  );
  const sitePanelEditCopyCoords = document.getElementById(
    "site-panel-edit-copy-coords",
  );
  const sitePanelCopyCoords = document.getElementById("site-panel-copy-coords");
  const sitePanelCopyPlss = document.getElementById("site-panel-copy-plss");
  const sitePanelEditCopyPlss = document.getElementById(
    "site-panel-edit-copy-plss",
  );
  const sitePanelCreateCopyPlss = document.getElementById(
    "site-panel-create-copy-plss",
  );
  const sitePanelEditCoordHistory = document.getElementById(
    "site-panel-edit-coord-history",
  );
  const sitePanelEditViewshed = document.getElementById(
    "site-panel-edit-viewshed",
  );
  const sitePanelEditViewshedSection = document.getElementById(
    "site-panel-edit-viewshed-section",
  );
  const sitePanelEditError = document.getElementById("site-panel-edit-error");
  const sitePanelEditSave = document.getElementById("site-panel-edit-save");
  const sitePanelEditCancel = document.getElementById("site-panel-edit-cancel");
  const sitePanelEditLinksLabel = document.getElementById(
    "site-panel-edit-links-label",
  );
  const entityPanel = document.getElementById("entity-panel");
  const entityPanelToggle = document.getElementById("entity-panel-toggle");
  const entityPanelSitesList = document.getElementById(
    "entity-panel-sites-list",
  );
  const entityPanelTagFilters = document.getElementById(
    "entity-panel-tag-filters",
  );
  const entityPanelSitesCount = document.getElementById(
    "entity-panel-sites-count",
  );
  const entityPanelFilterVisible = document.getElementById(
    "entity-panel-filter-visible",
  );
  const entityPanelBulkTag = document.getElementById("entity-panel-bulk-tag");
  const entityPanelAddSite = document.getElementById("entity-panel-add-site");
  const entityPanelImportSites = document.getElementById(
    "entity-panel-import-sites",
  );
  const entityPanelSitesPane = document.getElementById(
    "entity-panel-sites-pane",
  );
  const entityPanelLandPane = document.getElementById("entity-panel-land-pane");
  const entityPanelLandList = document.getElementById("entity-panel-land-list");
  const entityPanelLandCount = document.getElementById(
    "entity-panel-land-count",
  );
  const entityPanelImportLand = document.getElementById(
    "entity-panel-import-land",
  );
  const entityPanelAddLandFolder = document.getElementById(
    "entity-panel-add-land-folder",
  );
  const entityPanelTabs = document.querySelectorAll("[data-entity-tab]");
  const importLandModal = document.getElementById("import-land-modal");
  const importLandGdb = document.getElementById("import-land-gdb");
  const importLandLabel = document.getElementById("import-land-label");
  const importLandStatus = document.getElementById("import-land-status");
  const importLandPreviewField = document.getElementById(
    "import-land-preview-field",
  );
  const importLandPreviewMapEl = document.getElementById(
    "import-land-preview-map",
  );
  const importLandError = document.getElementById("import-land-error");
  const importLandSave = document.getElementById("import-land-save");
  const importLandListCount = document.getElementById("import-land-list-count");
  const importLandSelectAll = document.getElementById("import-land-select-all");
  const importLandClearAll = document.getElementById("import-land-clear-all");
  const importLandLayerList = document.getElementById("import-land-layer-list");
  const editLandModal = document.getElementById("edit-land-modal");
  const editLandSourcePath = document.getElementById("edit-land-source-path");
  const editLandLabel = document.getElementById("edit-land-label");
  const editLandPreviewMapEl = document.getElementById("edit-land-preview-map");
  const editLandError = document.getElementById("edit-land-error");
  const editLandSave = document.getElementById("edit-land-save");
  const editLandListCount = document.getElementById("edit-land-list-count");
  const editLandSelectAll = document.getElementById("edit-land-select-all");
  const editLandClearAll = document.getElementById("edit-land-clear-all");
  const editLandLayerList = document.getElementById("edit-land-layer-list");
  const sitePanelTags = document.getElementById("site-panel-tags");
  const sitePanelTagsSection = document.getElementById(
    "site-panel-tags-section",
  );
  const addSiteModal = document.getElementById("add-site-modal");
  const addSiteName = document.getElementById("add-site-name");
  const addSiteCoords = document.getElementById("add-site-coords");
  const addSiteTagsEl = document.getElementById("add-site-tags");
  const addSiteTagForm = document.getElementById("add-site-tag-form");
  const addSiteTagInput = document.getElementById("add-site-tag-input");
  const addSiteTagSuggestions = document.getElementById(
    "add-site-tag-suggestions",
  );
  const addSiteError = document.getElementById("add-site-error");
  const addSiteSave = document.getElementById("add-site-save");
  const importSitesModal = document.getElementById("import-sites-modal");
  const importSitesFile = document.getElementById("import-sites-file");
  const importSitesStatus = document.getElementById("import-sites-status");
  const importSitesPreviewField = document.getElementById(
    "import-sites-preview-field",
  );
  const importSitesPreviewMapEl = document.getElementById(
    "import-sites-preview-map",
  );
  const importSitesTagsEl = document.getElementById("import-sites-tags");
  const importSitesTagForm = document.getElementById("import-sites-tag-form");
  const importSitesTagInput = document.getElementById("import-sites-tag-input");
  const importSitesTagSuggestions = document.getElementById(
    "import-sites-tag-suggestions",
  );
  const importSitesError = document.getElementById("import-sites-error");
  const importSitesSave = document.getElementById("import-sites-save");
  const importSitesListCount = document.getElementById(
    "import-sites-list-count",
  );
  const importSitesSelectAll = document.getElementById(
    "import-sites-select-all",
  );
  const importSitesClearAll = document.getElementById("import-sites-clear-all");
  const importSitesFilterVisible = document.getElementById(
    "import-sites-filter-visible",
  );
  const importSitesPointList = document.getElementById(
    "import-sites-point-list",
  );
  const bulkTagModal = document.getElementById("bulk-tag-modal");
  const bulkTagError = document.getElementById("bulk-tag-error");
  const bulkTagStatus = document.getElementById("bulk-tag-status");
  const bulkTagTagsEl = document.getElementById("bulk-tag-tags");
  const bulkTagAddForm = document.getElementById("bulk-tag-add-form");
  const bulkTagAddInput = document.getElementById("bulk-tag-add-input");
  const bulkTagAddSuggestions = document.getElementById(
    "bulk-tag-add-suggestions",
  );
  const bulkTagSave = document.getElementById("bulk-tag-save");
  const IMPORT_DEDUPE_METERS = 100;
  const IMPORT_PREVIEW_CIRCLE_PAINT = {
    "circle-radius": 6,
    "circle-color": ["case", ["get", "ignored"], "#94a3b8", "#4a6cf7"],
    "circle-stroke-width": 1.5,
    "circle-stroke-color": "#ffffff",
    "circle-opacity": ["case", ["get", "ignored"], 0.55, 1],
  };
  const IMPORT_PREVIEW_LABEL_PAINT = {
    "text-color": ["case", ["get", "ignored"], "#94a3b8", "#e8eaed"],
    "text-halo-color": "#1a1a1a",
    "text-halo-width": 2,
    "text-opacity": ["case", ["get", "ignored"], 0.7, 1],
  };
  const IMPORT_PREVIEW_SOURCE = "import-preview-points";
  const IMPORT_PREVIEW_LAYER = "import-preview-points-layer";
  const IMPORT_PREVIEW_LABEL_LAYER = "import-preview-points-labels";
  const siteHidden = new Set(savedMapState?.hiddenSites || []);
  const activeTagFilters = new Set(
    Array.isArray(savedMapState?.tagFilters)
      ? savedMapState.tagFilters.filter(
          (tag) => typeof tag === "string" && tag.trim(),
        )
      : [],
  );
  let tagFilterMode = savedMapState?.tagFilterMode === "or" ? "or" : "and";
  /** Sites that stay visible/listable despite active tag filters (e.g. just created). */
  const tagFilterBypassSlugs = new Set();
  let entityPanelOpen = savedMapState?.entityPanelOpen === true;
  let entityPanelTab =
    savedMapState?.entityPanelTab === "land" ? "land" : "sites";
  let entityPanelFilterByViewport = savedMapState?.filterByViewport === true;
  let tagAddOpen = false;
  let addSiteDraftTags = [];
  let createPanelDraftTags = [];
  let importDraftTags = [];
  let seekConvertDraftTags = [];
  let importPreviewPoints = [];
  let importPreviewPayload = null;
  let importPreviewFileName = "";
  let importPreviewSkipped = 0;
  let importPreviewMap = null;
  let importPreviewBusy = false;
  let importFilterByViewport = true;
  let importSelectedPointIndex = -1;
  let bulkTagModalOpen = false;
  let bulkTagTargetSlugs = [];
  let bulkTagInitialCounts = new Map();
  let bulkTagPending = new Map();
  const landVisible = new Map();
  if (
    savedMapState?.landVisible &&
    typeof savedMapState.landVisible === "object"
  ) {
    for (const [key, visible] of Object.entries(savedMapState.landVisible)) {
      landVisible.set(key, !!visible);
    }
  }
  const landLabelsVisible = new Map();
  if (
    savedMapState?.landLabelsVisible &&
    typeof savedMapState.landLabelsVisible === "object"
  ) {
    for (const [key, visible] of Object.entries(
      savedMapState.landLabelsVisible,
    )) {
      landLabelsVisible.set(key, !!visible);
    }
  }
  let landLayerOrder = Array.isArray(savedMapState?.landLayerOrder)
    ? savedMapState.landLayerOrder.filter((key) => typeof key === "string")
    : [];
  const landFoldersCollapsed = new Map();
  if (
    savedMapState?.landFoldersCollapsed &&
    typeof savedMapState.landFoldersCollapsed === "object"
  ) {
    for (const [key, collapsed] of Object.entries(
      savedMapState.landFoldersCollapsed,
    )) {
      landFoldersCollapsed.set(key, !!collapsed);
    }
  }
  let landDragKind = null;
  let landDragId = null;
  let landSidebarSaveTimer = null;
  let landSidebarMigrationPending = false;
  const LAND_SIDEBAR_SAVE_MS = 400;
  let importLandPreviewLayers = [];
  let importLandPreviewPath = "";
  let importLandPreviewMap = null;
  let importLandPreviewBusy = false;
  let importLandLayerStyles = new Map();
  let importLandLayerConfigs = new Map();
  let editLandLayerConfigs = new Map();
  let editLandSourceId = "";
  let editLandPreviewLayers = [];
  let editLandPreviewPath = "";
  let editLandPreviewMap = null;
  let editLandPreviewBusy = false;
  let editLandLayerStyles = new Map();
  let editLandPreviewRefresh = null;
  const IMPORT_LAND_PREVIEW_SOURCE = "import-land-preview";
  const EDIT_LAND_PREVIEW_SOURCE = "edit-land-preview";
  const landPreviewGeoJsonCache = new Map();
  const landPreviewGeoJsonInflight = new Map();
  const landLayerGeoJsonCache = new Map();
  const landLayerGeoJsonInflight = new Map();
  const landPreviewLoadingCounts = new Map();
  const landPreviewLoadingOverlays = new Map();










  const WARM_PRIORITY_INTERACTIVE = 0;
  const WARM_PRIORITY_VIEWPORT = 10;
  const WARM_VIEWPORT_SLUG_CAP = 48;
  let warmPrioritiesTimer = null;






  const WA_DIALOG_WAIT_MS = 5000;

  async function openWaDialog(dialog) {
    if (!dialog) return;
    if (!customElements.get("wa-dialog")) {
      await Promise.race([
        customElements.whenDefined("wa-dialog"),
        new Promise((resolve) => setTimeout(resolve, WA_DIALOG_WAIT_MS)),
      ]);
    }
    if (!customElements.get("wa-dialog")) {
      dialog.classList.add("wa-dialog-force-show");
    }
    dialog.open = true;
  }

  function sitesApiUrl() {
    return `/api/p/${projectSlug}/sites`;
  }

  function sitesImportPreviewApiUrl() {
    return `/api/p/${projectSlug}/sites/import/preview`;
  }

  function sitesImportApiUrl() {
    return `/api/p/${projectSlug}/sites/import`;
  }

  function sitesTagsBulkApiUrl() {
    return `/api/p/${projectSlug}/sites/tags/bulk`;
  }

  function entityPanelSites() {
    return sites
      .filter(
        (site) =>
          sitePassesTagFilter(site) || tagFilterBypassSlugs.has(site.slug),
      )
      .filter((site) => !entityPanelFilterByViewport || siteVisibleInMap(site));
  }

  function sidebarSiteSlugs() {
    if (!entityPanelSitesList) {
      return entityPanelSites().map((site) => site.slug);
    }
    return [
      ...entityPanelSitesList.querySelectorAll(
        ".entity-panel__row[data-site-slug]",
      ),
    ]
      .map((row) => row.dataset.siteSlug)
      .filter(Boolean);
  }

  function sidebarListedSites() {
    const listed = new Set(sidebarSiteSlugs());
    return entityPanelSites().filter((site) => listed.has(site.slug));
  }

  function renderTagToggleChips(container, draftTags, knownTags, onChange) {
    if (!container) return;
    container.innerHTML = "";
    const selected = new Set(draftTags);
    const shown = new Set([...knownTags, ...draftTags]);
    for (const tag of [...shown].sort((a, b) => a.localeCompare(b))) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = selected.has(tag)
        ? "site-tag site-tag--toggle is-selected"
        : "site-tag site-tag--toggle";
      chip.textContent = tag;
      chip.setAttribute("aria-pressed", selected.has(tag) ? "true" : "false");
      chip.title = selected.has(tag) ? `Remove tag ${tag}` : `Add tag ${tag}`;
      chip.addEventListener("click", () => {
        onChange(tag, selected.has(tag));
      });
      container.appendChild(chip);
    }
  }



  function previewSlugForName(name) {
    return previewSlugForNameFromGeo(name, new Set([...siteBySlug.keys()]))
  }














  setEntityPanelOpen(entityPanelOpen);











  /** Warm a viewshed that was skipped while the site was hidden/filtered. */





  /** Parallel cache probe only — one warm-priority bump for the batch. */
  function probeViewshedCacheForSite(site) {
    removeViewshedLayer(site.slug);
    scope.resetSiteProgress(site.slug);
    viewshedLoading.add(site.slug);
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch);
    void scope.tryLoadViewshedFromCache(site.slug);
  }


  function applyEntityVisibility() {
    applySiteLayerFilters();
    for (const site of sites) {
      scope.applyViewshedVisibilityForSite(site.slug);
    }
    refreshFilteredLinks();
    if (seekState?.running) scope.refreshSeekAncillaryLinksDisplay();
    renderEntityPanel();
  }

  function siteTags(site) {
    return Array.isArray(site?.tags) ? site.tags.filter(Boolean) : [];
  }

  function allProjectTags() {
    const found = new Set();
    for (const site of sites) {
      for (const tag of siteTags(site)) found.add(tag);
    }
    return [...found].sort((a, b) => a.localeCompare(b));
  }

  function sitePassesTagFilter(site) {
    if (activeTagFilters.size === 0) return true;
    const tags = siteTags(site);
    if (tagFilterMode === "or") {
      for (const tag of activeTagFilters) {
        if (tags.includes(tag)) return true;
      }
      return false;
    }
    for (const tag of activeTagFilters) {
      if (!tags.includes(tag)) return false;
    }
    return true;
  }

  function tagFilterScopePhrase() {
    if (activeTagFilters.size < 2) return "matching selected tags";
    return tagFilterMode === "or"
      ? "matching any selected tag"
      : "matching all selected tags";
  }

  function tagFilterEmptyMessage(inView) {
    const prefix = inView ? "No sites in view " : "No sites ";
    if (activeTagFilters.size < 2) return `${prefix}match the selected tags.`;
    if (tagFilterMode === "or") return `${prefix}match any selected tag.`;
    return `${prefix}have all selected tags.`;
  }

  function coordVisibleInMapViewport(mapInstance, lon, lat) {
    if (!mapInstance || lon == null || lat == null) return true;
    if (isMapTiltedView(mapInstance)) {
      const b = mapOverheadEquivalentBounds(mapInstance);
      return (
        lon >= b.getWest() &&
        lon <= b.getEast() &&
        lat >= b.getSouth() &&
        lat <= b.getNorth()
      );
    }
    const projected = mapInstance.project([lon, lat]);
    if (!Number.isFinite(projected.x) || !Number.isFinite(projected.y))
      return false;
    const canvas = mapInstance.getCanvas();
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    return (
      projected.x >= 0 &&
      projected.x <= w &&
      projected.y >= 0 &&
      projected.y <= h
    );
  }

  function siteVisibleInMap(site) {
    if (!mapReady || !site) return true;
    return coordVisibleInMapViewport(map, site.lon, site.lat);
  }

  function isEphemeralViewshedSlug(slug) {
    return (
      slug === C.DRAFT_VIEWSHED_SLUG ||
      String(slug).startsWith("_edit_hist_") ||
      String(slug).startsWith(C.SEEK_HOP_VIEWSHED_PREFIX)
    );
  }

  function isSiteMapHidden(slug) {
    if (isEphemeralViewshedSlug(slug)) return siteHidden.has(slug);
    if (siteHidden.has(slug)) return true;
    const site = siteBySlug.get(slug);
    if (!site) return true;
    if (scope.isSiteInSeekPlan(slug)) return false;
    if (tagFilterBypassSlugs.has(slug)) return false;
    return !sitePassesTagFilter(site);
  }

  function isSiteHidden(slug) {
    return siteHidden.has(slug);
  }

  function pruneActiveTagFilters() {
    const valid = new Set(allProjectTags());
    for (const tag of activeTagFilters) {
      if (!valid.has(tag)) activeTagFilters.delete(tag);
    }
  }

  function toggleTagFilter(tag) {
    const value = String(tag || "").trim();
    if (!value) return;
    if (activeTagFilters.has(value)) activeTagFilters.delete(value);
    else activeTagFilters.add(value);
    pruneActiveTagFilters();
    applyEntityVisibility();
    scope.ensureViewshedsForNewlyVisibleSites();
    scope.refreshSeekStartSelectIfOpen();
    scheduleSaveMapState();
  }

  function setTagFilterMode(mode) {
    const next = mode === "or" ? "or" : "and";
    if (tagFilterMode === next) return;
    tagFilterMode = next;
    applyEntityVisibility();
    renderEntityPanel();
    scope.refreshSeekStartSelectIfOpen();
    scheduleSaveMapState();
  }

  function setSiteHidden(slug, hidden) {
    if (hidden) siteHidden.add(slug);
    else siteHidden.delete(slug);
    applyEntityVisibility();
    if (!hidden) ensureViewshedLoadedForSlug(slug);
    scheduleSaveMapState();
  }

  function removeViewshedLayer(slug) {
    const layerId = scope.viewshedLayerId(slug);
    const sourceId = scope.viewshedSourceId(slug);
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    viewshedLoading.delete(slug);
    scope.updatePinOverlays();
  }

  function siteDeleteUrl(slug) {
    return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}`;
  }

  function purgeSiteLinksForSlug(slug) {
    if (!slug || !siteLinksPayload) return;
    const touches = (props) => props.a === slug || props.b === slug;
    const geojson = siteLinksPayload.geojson;
    if (!geojson || !Array.isArray(geojson.features)) {
      refreshFilteredLinks();
      return;
    }
    siteLinksPayload = {
      ...siteLinksPayload,
      geojson: {
        type: "FeatureCollection",
        features: geojson.features.filter((f) => !touches(f.properties || {})),
      },
      links: Array.isArray(siteLinksPayload.links)
        ? siteLinksPayload.links.filter((r) => !touches(r || {}))
        : siteLinksPayload.links,
    };
    addSiteLinksLayer(siteLinksPayload.geojson);
  }


  async function deleteSite(slug) {
    const site = siteBySlug.get(slug);
    if (!site) return;
    if (!confirm(`Delete site "${site.name}" permanently?`)) return;
    try {
      const resp = await fetch(siteDeleteUrl(slug), { method: "DELETE" });
      if (!resp.ok) return;
      unregisterSite(slug);
      await loadSiteLinks();
    } catch (_) {
      /* network error */
    }
  }

  function renderTagFilters() {
    if (!entityPanelTagFilters) return;
    entityPanelTagFilters.innerHTML = "";
    pruneActiveTagFilters();
    const tags = allProjectTags();
    if (!tags.length) {
      entityPanelTagFilters.hidden = true;
      return;
    }
    entityPanelTagFilters.hidden = false;

    const modeGroup = document.createElement("div");
    modeGroup.className = "entity-panel__tag-filter-mode";
    modeGroup.setAttribute("role", "group");
    modeGroup.setAttribute("aria-label", "Tag filter mode");
    for (const [mode, label] of [
      ["and", "intersect"],
      ["or", "union"],
    ]) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "entity-panel__tag-filter";
      const active = tagFilterMode === mode;
      if (active) btn.classList.add("entity-panel__tag-filter--active");
      btn.textContent = label;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.title =
        mode === "and"
          ? "Match all selected tags (intersect)"
          : "Match any selected tag (union)";
      btn.addEventListener("click", () => setTagFilterMode(mode));
      modeGroup.appendChild(btn);
    }
    entityPanelTagFilters.appendChild(modeGroup);

    for (const tag of tags) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "entity-panel__tag-filter";
      const active = activeTagFilters.has(tag);
      if (active) btn.classList.add("entity-panel__tag-filter--active");
      btn.textContent = tag;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.addEventListener("click", () => toggleTagFilter(tag));
      entityPanelTagFilters.appendChild(btn);
    }
  }

  function syncEntityPanelSitesCount(shown, total) {
    if (!entityPanelSitesCount) return;
    if (!total) {
      entityPanelSitesCount.textContent = "";
      return;
    }
    if (entityPanelFilterByViewport && shown < total) {
      entityPanelSitesCount.textContent = `${shown} of ${total} in view`;
    } else {
      entityPanelSitesCount.textContent = `${total} site${total === 1 ? "" : "s"}`;
    }
  }

  function entityPanelEmptyMessage(tagFilteredCount) {
    if (!sites.length) return "No sites yet.";
    if (entityPanelFilterByViewport && activeTagFilters.size) {
      return tagFilterEmptyMessage(tagFilteredCount > 0);
    }
    if (entityPanelFilterByViewport) {
      return "No sites in the current map view — pan or zoom out.";
    }
    return tagFilterEmptyMessage(false);
  }

  function renderEntityPanel() {
    renderTagFilters();
    syncBulkTagButton();
    if (entityPanelFilterVisible) {
      entityPanelFilterVisible.checked = entityPanelFilterByViewport;
    }
    if (entityPanelSitesList) {
      entityPanelSitesList.innerHTML = "";
      const tagFiltered = sites.filter((site) => sitePassesTagFilter(site));
      const sortedSites = entityPanelSites().sort((a, b) =>
        compareHuman(a.name, b.name),
      );
      syncEntityPanelSitesCount(sortedSites.length, tagFiltered.length);
      if (!sortedSites.length) {
        const empty = document.createElement("div");
        empty.className = "entity-panel__empty";
        empty.textContent = entityPanelEmptyMessage(tagFiltered.length);
        entityPanelSitesList.appendChild(empty);
      }
      for (const site of sortedSites) {
        entityPanelSitesList.appendChild(buildSiteEntityRow(site));
      }
    }
    if (bulkTagModalOpen) scope.syncBulkTagModalStatus();
  }

  function onMapMoveEndForEntityPanel() {
    if (entityPanelFilterByViewport) renderEntityPanel();
  }

  function syncBulkTagButton() {
    if (!entityPanelBulkTag) return;
    const count = sidebarSiteSlugs().length;
    entityPanelBulkTag.disabled = !mapReady || count === 0;
  }



  function toggleBulkTag(tag) {
    const state = scope.bulkTagVisualState(tag);
    if (state === "full") bulkTagPending.set(tag, "none");
    else bulkTagPending.set(tag, "all");
    scope.renderBulkTagTags();
    scope.syncBulkTagSaveButton();
  }

  function computeBulkTagOps() {
    const total = bulkTagTargetSlugs.length;
    const addTags = [];
    const removeTags = [];
    for (const [tag, pending] of bulkTagPending) {
      const count = bulkTagInitialCounts.get(tag) || 0;
      if (pending === "all" && count < total) addTags.push(tag);
      if (pending === "none" && count > 0) removeTags.push(tag);
    }
    return { addTags, removeTags };
  }


  function setBulkTagError(message) {
    if (!bulkTagError) return;
    if (message) {
      bulkTagError.textContent = message;
      bulkTagError.hidden = false;
    } else {
      bulkTagError.textContent = "";
      bulkTagError.hidden = true;
    }
  }

  function syncBulkTagModalStatus({ resetPending = false } = {}) {
    if (!bulkTagStatus) return;
    const listedSites = sidebarListedSites();
    bulkTagTargetSlugs = listedSites.map((site) => site.slug);
    const counts = new Map();
    for (const site of listedSites) {
      for (const tag of siteTags(site)) {
        counts.set(tag, (counts.get(tag) || 0) + 1);
      }
    }
    bulkTagInitialCounts = counts;
    if (resetPending) {
      bulkTagPending = new Map();
    } else {
      for (const tag of bulkTagPending.keys()) {
        if (!scope.bulkTagListTags().includes(tag)) bulkTagPending.delete(tag);
      }
    }
    const count = bulkTagTargetSlugs.length;
    const scopeParts = [];
    if (activeTagFilters.size) scopeParts.push(tagFilterScopePhrase());
    if (entityPanelFilterByViewport) scopeParts.push("in the current map view");
    const scope = scopeParts.length ? ` ${scopeParts.join(" and ")}` : "";
    bulkTagStatus.textContent =
      count === 0
        ? "No sites match the current sidebar filters."
        : count === 1
          ? `Apply to 1 site${scope}`
          : `Apply to ${count} sites${scope}`;
    scope.renderBulkTagTags();
    scope.syncBulkTagAddSuggestions();
    scope.syncBulkTagSaveButton();
  }

  function renderBulkTagTags() {
    if (!bulkTagTagsEl) return;
    bulkTagTagsEl.innerHTML = "";
    for (const tag of scope.bulkTagListTags()) {
      const state = scope.bulkTagVisualState(tag);
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "site-tag site-tag--toggle";
      if (state === "full") chip.classList.add("is-selected");
      if (state === "partial") chip.classList.add("is-partial");
      if (state === "partial") {
        const icon = document.createElement("span");
        icon.className = "site-tag__partial-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "◐";
        chip.appendChild(icon);
      }
      const label = document.createElement("span");
      label.textContent = tag;
      chip.appendChild(label);
      chip.setAttribute(
        "aria-pressed",
        state === "full" ? "true" : state === "partial" ? "mixed" : "false",
      );
      chip.title =
        state === "full"
          ? `Remove ${tag} from all sites in view`
          : `Add ${tag} to all sites in view`;
      chip.addEventListener("click", () => scope.toggleBulkTag(tag));
      bulkTagTagsEl.appendChild(chip);
    }
  }

  function syncBulkTagAddSuggestions() {
    if (!bulkTagAddSuggestions) return;
    bulkTagAddSuggestions.innerHTML = "";
    for (const tag of allProjectTags()) {
      const opt = document.createElement("option");
      opt.value = tag;
      bulkTagAddSuggestions.appendChild(opt);
    }
  }

  function syncBulkTagSaveButton() {
    if (!bulkTagSave) return;
    bulkTagSave.disabled = !bulkTagTargetSlugs.length || !scope.bulkTagHasChanges();
  }

  function addBulkTagFromInput() {
    if (!bulkTagAddInput) return;
    const tag = normalizeTagInput(bulkTagAddInput.value);
    bulkTagAddInput.value = "";
    if (!tag) return;
    if (!bulkTagInitialCounts.has(tag)) bulkTagInitialCounts.set(tag, 0);
    bulkTagPending.set(tag, "all");
    scope.renderBulkTagTags();
    scope.syncBulkTagSaveButton();
  }

  function closeBulkTagModal() {
    if (!bulkTagModal) return;
    bulkTagModal.open = false;
  }

  async function openBulkTagModal() {
    if (!bulkTagModal) return;
    scope.setBulkTagError("");
    await customElements.whenDefined("wa-dialog");
    scope.syncBulkTagModalStatus({ resetPending: true });
    bulkTagModal.open = true;
  }

  async function saveBulkTagModal() {
    scope.addBulkTagFromInput();
    scope.syncBulkTagModalStatus();
    const slugs = sidebarSiteSlugs();
    const { addTags, removeTags } = scope.computeBulkTagOps();
    if (!slugs.length || (!addTags.length && !removeTags.length)) {
      scope.setBulkTagError("Change at least one tag.");
      return;
    }
    scope.setBulkTagError("");
    if (bulkTagSave) bulkTagSave.disabled = true;
    try {
      const body = { slugs, add_tags: addTags, remove_tags: removeTags };
      const resp = await fetch(sitesTagsBulkApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setBulkTagError(payload.error || `Tag update failed (${resp.status})`);
        return;
      }
      const updated = Array.isArray(payload.sites) ? payload.sites : [];
      scope.closeBulkTagModal();
      for (const site of updated) {
        scope.applySiteRowUpdate(site, { refreshGeoJson: false });
        scope.syncTagFilterBypassForSite(site.slug);
      }
      applyEntityVisibility();
      renderTagFilters();
      scheduleSaveMapState();
    } catch (_) {
      scope.setBulkTagError("Could not reach server.");
    } finally {
      scope.syncBulkTagSaveButton();
    }
  }

  function makeEntityPanelActionBtn({
    icon,
    label,
    active,
    danger,
    disabled,
    extraClass,
    onClick,
  }) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "entity-panel__action";
    if (extraClass) btn.classList.add(extraClass);
    if (active) btn.classList.add("entity-panel__action--active");
    if (danger) btn.classList.add("entity-panel__action--danger");
    btn.disabled = !!disabled;
    btn.title = label;
    btn.setAttribute("aria-label", label);
    if (active !== undefined)
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.innerHTML = scope.mapToolIcon(icon, label);
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      onClick(ev);
    });
    return btn;
  }

  function buildSiteEntityRow(site) {
    const row = document.createElement("div");
    row.className = "entity-panel__row";
    row.dataset.siteSlug = site.slug;
    if (selectedSlug === site.slug)
      row.classList.add("entity-panel__row--selected");
    if (isSiteHidden(site.slug)) row.classList.add("entity-panel__row--hidden");

    const main = document.createElement("div");
    main.className = "entity-panel__main";
    const name = document.createElement("div");
    name.className = "entity-panel__name";
    name.textContent = site.name;
    main.appendChild(name);
    const tags = siteTags(site);
    if (tags.length) {
      const meta = document.createElement("div");
      meta.className = "entity-panel__meta";
      for (const tag of tags) {
        const pill = document.createElement("span");
        pill.className = "entity-panel__meta-tag";
        if (activeTagFilters.has(tag))
          pill.classList.add("entity-panel__meta-tag--active");
        pill.textContent = tag;
        meta.appendChild(pill);
      }
      main.appendChild(meta);
    }

    const controls = document.createElement("div");
    controls.className = "entity-panel__controls";

    const siteVisible = !isSiteHidden(site.slug);
    const eyeBtn = scope.makeEntityPanelActionBtn({
      icon: siteVisible ? "eye" : "eye-slash",
      label: siteVisible ? "Hide site on map" : "Show site on map",
      active: siteVisible,
      onClick: () => setSiteHidden(site.slug, siteVisible),
    });

    const vsBtn = scope.makeEntityPanelActionBtn({
      icon: "droplet",
      label: "Viewshed coverage",
      active: isViewshedVisible(site.slug),
      disabled: !siteVisible,
      extraClass: "entity-panel__viewshed-toggle",
      onClick: () => {
        setViewshedVisible(site.slug, !isViewshedVisible(site.slug));
        scheduleSaveMapState();
      },
    });

    const delBtn = scope.makeEntityPanelActionBtn({
      icon: "trash",
      label: "Delete site",
      danger: true,
      onClick: () => {
        void deleteSite(site.slug);
      },
    });

    controls.appendChild(eyeBtn);
    controls.appendChild(vsBtn);
    controls.appendChild(delBtn);

    row.appendChild(main);
    row.appendChild(controls);
    row.addEventListener("click", () => selectSite(site.slug));
    return row;
  }


  function syncCreateSlugPreview() {
    const name = sitePanelCreateName.value;
    sitePanelSlugPreview.textContent = previewSlugForName(name);
  }

  function renderCreatePanelTags() {
    renderTagToggleChips(
      sitePanelCreateTagsEl,
      createPanelDraftTags,
      allProjectTags(),
      (tag, wasSelected) => {
        if (wasSelected) {
          createPanelDraftTags = createPanelDraftTags.filter((t) => t !== tag);
        } else {
          createPanelDraftTags = [...createPanelDraftTags, tag];
        }
        renderCreatePanelTags();
        syncCreatePanelTagSuggestions();
      },
    );
  }

  function syncCreatePanelTagSuggestions() {
    if (!sitePanelCreateTagSuggestions) return;
    sitePanelCreateTagSuggestions.innerHTML = "";
    const selected = new Set(createPanelDraftTags);
    for (const tag of allProjectTags()) {
      if (selected.has(tag)) continue;
      const opt = document.createElement("option");
      opt.value = tag;
      sitePanelCreateTagSuggestions.appendChild(opt);
    }
  }

  function resetCreatePanelTags() {
    createPanelDraftTags = [];
    if (sitePanelCreateTagInput) sitePanelCreateTagInput.value = "";
    renderCreatePanelTags();
    syncCreatePanelTagSuggestions();
  }

  function addCreatePanelTagFromInput() {
    if (!sitePanelCreateTagInput) return;
    const tag = normalizeTagInput(sitePanelCreateTagInput.value);
    sitePanelCreateTagInput.value = "";
    if (!tag) return;
    if (!createPanelDraftTags.includes(tag)) {
      createPanelDraftTags = [...createPanelDraftTags, tag];
      renderCreatePanelTags();
      syncCreatePanelTagSuggestions();
    }
  }

  function syncCreatePanelForKind() {
    if (sitePanelCreateTitle) sitePanelCreateTitle.textContent = "New site";
    if (sitePanelCreateBadge) {
      sitePanelCreateBadge.textContent = "site";
      sitePanelCreateBadge.className =
        "site-panel__badge site-panel__badge--planned";
    }
    if (sitePanelCreateLinksLabel)
      sitePanelCreateLinksLabel.textContent = "Linked sites";
    if (sitePanelCreateViewshedSection)
      sitePanelCreateViewshedSection.hidden = false;
  }


  function resetCreatePrefetchUI() {
    setSectionVisible("site-panel-create-plss-section", false);
    setSectionVisible("site-panel-create-links-section", false);
    document.getElementById("site-panel-create-plss").textContent = "";
    const linksEl = document.getElementById("site-panel-create-links");
    if (linksEl) linksEl.innerHTML = "";
    removeDraftLinksLayer();
  }

  function removeDraftLinksLayer() {
    if (map.getLayer(C.DRAFT_LINKS_LABELS_LAYER))
      map.removeLayer(C.DRAFT_LINKS_LABELS_LAYER);
    if (map.getLayer(C.DRAFT_LINKS_LAYER)) map.removeLayer(C.DRAFT_LINKS_LAYER);
    if (map.getSource(C.DRAFT_LINKS_SOURCE)) map.removeSource(C.DRAFT_LINKS_SOURCE);
  }

  function removeEditHistoryLinksLayer() {
    if (map.getLayer(C.EDIT_HISTORY_LINKS_LABELS_LAYER))
      map.removeLayer(C.EDIT_HISTORY_LINKS_LABELS_LAYER);
    if (map.getLayer(C.EDIT_HISTORY_LINKS_LAYER))
      map.removeLayer(C.EDIT_HISTORY_LINKS_LAYER);
    if (map.getSource(C.EDIT_HISTORY_LINKS_SOURCE))
      map.removeSource(C.EDIT_HISTORY_LINKS_SOURCE);
  }

  function linksGeoJsonWithLabels(geojson) {
    if (!geojson || !geojson.features) return geojson;
    return {
      type: geojson.type || "FeatureCollection",
      features: geojson.features.map((feature) => {
        const props = feature.properties || {};
        const label = formatLinkDistanceKm(props.distance_km);
        return {
          ...feature,
          properties: { ...props, label: label || "" },
        };
      }),
    };
  }


  function createSeekSiteHopMarkerElement(siteName) {
    const wrap = document.createElement("div");
    wrap.className = "seek-hop-marker-wrap";
    if (siteName) {
      const label = document.createElement("div");
      label.className = "seek-hop-marker-label";
      label.textContent = siteName;
      wrap.appendChild(label);
    }
    const dot = document.createElement("div");
    dot.className = "seek-hop-marker seek-hop-marker--site";
    wrap.appendChild(dot);
    return wrap;
  }

  function linkLabelsLayerSpec(layerId, sourceId, visibility) {
    return {
      id: layerId,
      type: "symbol",
      source: sourceId,
      filter: ["!=", ["get", "label"], ""],
      layout: {
        "symbol-placement": "line-center",
        "text-field": ["get", "label"],
        "text-size": 11,
        "text-font": C.MAP_TEXT_FONT,
        "text-allow-overlap": true,
        "text-ignore-placement": true,
        visibility,
      },
      paint: {
        "text-color": "#e8eaed",
        "text-halo-color": "#1a1a1a",
        "text-halo-width": 2,
      },
    };
  }

  function addDraftLinksLayer(geojson) {
    if (
      !mapReady ||
      !geojson ||
      !geojson.features ||
      !geojson.features.length
    ) {
      removeDraftLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels(geojson);
    if (map.getSource(C.DRAFT_LINKS_SOURCE)) {
      map.getSource(C.DRAFT_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(C.DRAFT_LINKS_LAYER)) {
        map.setPaintProperty(C.DRAFT_LINKS_LAYER, "line-color", [
          "case",
          ["get", "manual"],
          "#0d9488",
          "#4a6cf7",
        ]);
      }
      raiseSiteLayers();
      return;
    }
    map.addSource(C.DRAFT_LINKS_SOURCE, { type: "geojson", data: labeled });
    map.addLayer(
      {
        id: C.DRAFT_LINKS_LAYER,
        type: "line",
        source: C.DRAFT_LINKS_SOURCE,
        paint: {
          "line-color": ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
          "line-width": 2.5,
          "line-opacity": 0.85,
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: "visible",
        },
      },
      C.SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(
        C.DRAFT_LINKS_LABELS_LAYER,
        C.DRAFT_LINKS_SOURCE,
        "visible",
      ),
      C.SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  function editHistoryLinksLineColor() {
    return C.DRAFT_MARKER_COLOR;
  }

  function addEditHistoryLinksLayer(geojson) {
    if (
      !mapReady ||
      !geojson ||
      !geojson.features ||
      !geojson.features.length
    ) {
      removeEditHistoryLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels(geojson);
    const lineColor = editHistoryLinksLineColor();
    if (map.getSource(C.EDIT_HISTORY_LINKS_SOURCE)) {
      map.getSource(C.EDIT_HISTORY_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(C.EDIT_HISTORY_LINKS_LAYER)) {
        map.setPaintProperty(C.EDIT_HISTORY_LINKS_LAYER, "line-color", lineColor);
      }
      raiseSiteLayers();
      return;
    }
    map.addSource(C.EDIT_HISTORY_LINKS_SOURCE, {
      type: "geojson",
      data: labeled,
    });
    map.addLayer(
      {
        id: C.EDIT_HISTORY_LINKS_LAYER,
        type: "line",
        source: C.EDIT_HISTORY_LINKS_SOURCE,
        paint: {
          "line-color": lineColor,
          "line-width": 2,
          "line-opacity": 0.7,
          "line-dasharray": [2, 2],
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: "visible",
        },
      },
      C.SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(
        C.EDIT_HISTORY_LINKS_LABELS_LAYER,
        C.EDIT_HISTORY_LINKS_SOURCE,
        "visible",
      ),
      C.SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  function refreshEditHistoryLinksLayer() {
    const features = [];
    for (const entry of editCoordHistory) {
      if (
        !entry.visible ||
        !entry.linksGeojson ||
        !Array.isArray(entry.linksGeojson.features)
      )
        continue;
      for (const feature of entry.linksGeojson.features) {
        features.push({
          ...feature,
          properties: {
            ...(feature.properties || {}),
            historyId: entry.id,
          },
        });
      }
    }
    if (!features.length) {
      removeEditHistoryLinksLayer();
      return;
    }
    addEditHistoryLinksLayer({ type: "FeatureCollection", features });
  }

  function formatAntennaHeightM(heightM) {
    if (heightM == null || !Number.isFinite(Number(heightM))) return null;
    return `${Number(heightM).toLocaleString(undefined, { maximumFractionDigits: 1 })} m`;
  }

  function resolvedSiteHeightM(site) {
    if (site?.height_m != null && Number.isFinite(Number(site.height_m))) {
      return Number(site.height_m);
    }
    return defaultTxHeightM;
  }

  function syncEditHeightHint() {
    if (!sitePanelEditHeightHint) return;
    sitePanelEditHeightHint.textContent = `Leave blank for project default (${defaultTxHeightM} m)`;
    if (sitePanelEditHeight) {
      sitePanelEditHeight.placeholder = String(defaultTxHeightM);
    }
  }

  function linkDistanceKmBetween(slugA, slugB) {
    const geojson = siteLinksPayload?.geojson;
    if (!geojson?.features) return null;
    const [a, b] = slugA <= slugB ? [slugA, slugB] : [slugB, slugA];
    for (const feature of geojson.features) {
      const props = feature.properties || {};
      if (props.a === a && props.b === b) return props.distance_km;
    }
    return null;
  }

  function linkedPeerDetailsForSite(slug) {
    return linkedPeersForSite(slug)
      .map((peerSlug) => {
        const peer = siteBySlug.get(peerSlug);
        return {
          slug: peerSlug,
          name: peer?.name || peerSlug,
          distanceKm: linkDistanceKmBetween(slug, peerSlug),
        };
      })
      .sort((left, right) => compareHuman(left.name, right.name));
  }

  function buildSitePanelLinkButton(peer, currentSlug) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "site-panel__link";
    btn.title = `Open ${peer.name}`;

    const main = document.createElement("span");
    main.className = "site-panel__link-name";
    const name = document.createElement("span");
    name.textContent = peer.name;
    main.appendChild(name);
    if (peer.name !== peer.slug) {
      const slug = document.createElement("span");
      slug.className = "site-panel__link-slug";
      slug.textContent = peer.slug;
      main.appendChild(slug);
    }
    btn.appendChild(main);

    const dist = formatLinkDistanceKm(peer.distanceKm);
    if (dist) {
      const distEl = document.createElement("span");
      distEl.className = "site-panel__link-dist";
      distEl.textContent = dist;
      btn.appendChild(distEl);
    }

    btn.addEventListener("click", () => {
      if (peer.slug !== currentSlug) selectSite(peer.slug);
    });
    return btn;
  }

  function formatLinkDistanceKm(distanceKm) {
    if (distanceKm == null || Number.isNaN(Number(distanceKm))) return null;
    return `${Number(distanceKm).toFixed(1)} km`;
  }

  function renderCreatePrefetch(payload) {
    const plss = payload.plss || "";
    setSectionVisible("site-panel-create-plss-section", !!plss);
    document.getElementById("site-panel-create-plss").textContent = plss || "—";
    const links = Array.isArray(payload.links) ? payload.links : [];
    const linked = links.filter((row) => row.linked !== false);
    setSectionVisible("site-panel-create-links-section", linked.length > 0);
    const linksEl = document.getElementById("site-panel-create-links");
    linksEl.innerHTML = "";
    for (const row of linked) {
      const slug = row.slug;
      const site = siteBySlug.get(slug);
      const label = site ? site.name : slug;
      const dist = formatLinkDistanceKm(row.distance_km);
      const li = document.createElement("li");
      li.textContent = dist ? `${label} — ${dist}` : label;
      linksEl.appendChild(li);
    }
    if (payload.links_geojson) addDraftLinksLayer(payload.links_geojson);
    else removeDraftLinksLayer();
  }


  async function saveNewPlacement() {
    const name = sitePanelCreateName.value.trim();
    if (!name) {
      setCreateError("Name is required.");
      return;
    }
    if (pendingCreateLat == null || pendingCreateLon == null) {
      setCreateError("Pick a location on the map first.");
      return;
    }
    const savedLat = pendingCreateLat;
    const savedLon = pendingCreateLon;
    setCreateError("");
    sitePanelCreateSave.disabled = true;
    try {
      const body = {
        name,
        lat: savedLat,
        lon: savedLon,
      };
      if (createPanelDraftTags.length) body.tags = [...createPanelDraftTags];
      const resp = await fetch(sitesApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setCreateError(payload.error || `Save failed (${resp.status})`);
        return;
      }
      const site = payload.site;
      if (!site || !site.slug) {
        setCreateError("Unexpected server response.");
        return;
      }
      removeDraftMarker();
      scope.removeDraftViewshed();
      pendingCreateLat = null;
      pendingCreateLon = null;
      createMode = false;
      clearAddPlacementMode();
      applySavedSiteToMap(site, savedLat, savedLon);
    } catch (_) {
      setCreateError("Could not reach server.");
    } finally {
      sitePanelCreateSave.disabled = false;
    }
  }











  function clearLandLayerGeoJsonCacheForLayer(sourceId, layerKey) {
    const prefix = `layer|${sourceId}|${layerKey}`;
    for (const key of [...landLayerGeoJsonCache.keys()]) {
      if (key === prefix || key.startsWith(`${prefix}|`)) {
        landLayerGeoJsonCache.delete(key);
        landLayerGeoJsonInflight.delete(key);
      }
    }
  }

  function clearAllLandLayerGeoJsonCache() {
    landLayerGeoJsonCache.clear();
    landLayerGeoJsonInflight.clear();
  }

  async function fetchCachedGeoJson(cache, inflight, key, fetchFn) {
    if (cache.has(key)) return cache.get(key);
    if (inflight.has(key)) return inflight.get(key);
    const promise = fetchFn()
      .then((data) => {
        cache.set(key, data);
        inflight.delete(key);
        return data;
      })
      .catch((err) => {
        inflight.delete(key);
        throw err;
      });
    inflight.set(key, promise);
    return promise;
  }




  function defaultLandLayerConfig() {
    return {
      labelField: "",
      role: "",
      include: [],
      exclude: [],
      attrsOpen: false,
      fields: null,
      fieldsLoading: false,
    };
  }

  function ensureLandLayerConfig(configs, layerName) {
    if (!configs.has(layerName))
      configs.set(layerName, scope.defaultLandLayerConfig());
    return configs.get(layerName);
  }

  function normalizeRegisteredLayer(layer) {
    if (typeof layer === "string") {
      const name = layer;
      return {
        name,
        key: scope.landLayerSlug(name),
        role: null,
        digest: null,
        style: null,
        styleField: null,
        labelField: null,
        include: [],
        exclude: [],
      };
    }
    const name = layer.name;
    return {
      name,
      key: layer.key || scope.landLayerSlug(name),
      role: layer.role || null,
      digest: layer.digest || null,
      style: layer.style || null,
      styleField: layer.styleField || layer.style_field || null,
      labelField: layer.labelField || layer.label_field || null,
      include: Array.isArray(layer.include) ? layer.include : [],
      exclude: Array.isArray(layer.exclude) ? layer.exclude : [],
    };
  }



  function humanizeLandText(text) {
    return (
      String(text)
        .replace(/[_-]+-?\d{10,}$/, "")
        .replace(/[_-]+$/g, "")
        .replace(/_/g, " ")
        .replace(/\s+/g, " ")
        .trim() || String(text)
    );
  }

  function friendlyLandLayerName(name) {
    return scope.humanizeLandText(String(name).replace(/^BLM[_\s-]+/i, ""));
  }

  function friendlyLandSourceTitle(source) {
    const explicit = String(source.label || "").trim();
    if (explicit && explicit !== source.id) return explicit;
    return scope.humanizeLandText(scope.landPathBasename(source.path));
  }



  function appendLandLayerRoleBadge(parent, role) {
    const spec = scope.landLayerRoleBadgeSpec(role);
    if (!spec) return null;
    const badge = document.createElement("span");
    badge.className = spec.className;
    badge.textContent = spec.label;
    badge.title = spec.title;
    parent.appendChild(badge);
    return badge;
  }

  function buildLandLayerControls(
    sourceId,
    layerKey,
    { labelField = null } = {},
  ) {
    const controls = document.createDocumentFragment();
    const spinnerSlot = document.createElement("span");
    spinnerSlot.className = "entity-panel__land-spinner-slot";
    spinnerSlot.setAttribute("aria-hidden", "true");
    const spinner = document.createElement("span");
    spinner.className = "entity-panel__land-row-spinner pin-load-spinner";
    spinner.hidden = true;
    spinnerSlot.appendChild(spinner);
    controls.appendChild(spinnerSlot);
    controls.appendChild(scope.buildLandLayerEyeBtn(sourceId, layerKey));
    if (labelField) {
      controls.appendChild(scope.buildLandLayerLabelBtn(sourceId, layerKey));
    }
    return controls;
  }

  function appendLandLayerMeta(parent, spec) {
    const meta = document.createElement("div");
    meta.className = "entity-panel__meta";
    let hasMeta = false;

    for (const filt of spec.include || []) {
      const field = filt.field || "";
      for (const value of filt.values || []) {
        if (!value) continue;
        const pill = document.createElement("span");
        pill.className =
          "entity-panel__meta-tag entity-panel__meta-tag--active";
        pill.textContent = field ? `${field}: ${value}` : String(value);
        meta.appendChild(pill);
        hasMeta = true;
      }
    }

    const styleMap = scope.styleMapFromLayerSpec(spec);
    if (styleMap && Object.keys(styleMap).length) {
      const legend = document.createElement("div");
      legend.className = "entity-panel__land-legend";
      for (const [key, rawStyle] of Object.entries(styleMap)) {
        const style = scope.normalizeLandLayerStyle(rawStyle);
        const chip = document.createElement("span");
        chip.className = "entity-panel__land-legend-chip";
        const swatch = document.createElement("span");
        swatch.className = "entity-panel__land-legend-swatch";
        swatch.style.backgroundColor = style.color;
        swatch.style.opacity = String(style.opacity);
        chip.appendChild(swatch);
        chip.append(String(key));
        legend.appendChild(chip);
      }
      meta.appendChild(legend);
      hasMeta = true;
    }

    if (hasMeta) parent.appendChild(meta);
  }

  function styleMapFromLayerSpec(spec) {
    if (!spec?.style) return null;
    if (
      spec.styleField &&
      typeof spec.style === "object" &&
      !("color" in spec.style)
    ) {
      return spec.style;
    }
    return null;
  }

  function flatStyleFromLayerSpec(spec) {
    if (!spec?.style) return scope.defaultLandLayerStyle();
    if (typeof spec.style === "object" && "color" in spec.style) {
      return scope.normalizeLandLayerStyle(spec.style);
    }
    if (spec.styleField && typeof spec.style === "object") {
      const first = Object.values(spec.style)[0];
      return scope.normalizeLandLayerStyle(first);
    }
    return scope.defaultLandLayerStyle();
  }

  function buildStyleMatchExpression(styleMap, prop, fallback) {
    const normalized = scope.normalizeLandLayerStyle(fallback);
    const expr = ["match", ["get", prop]];
    for (const [key, rawStyle] of Object.entries(styleMap || {})) {
      const style = scope.normalizeLandLayerStyle(rawStyle);
      expr.push(String(key), style.color);
    }
    expr.push(normalized.color);
    return expr;
  }

  function buildOpacityMatchExpression(styleMap, prop, fallback) {
    const normalized = scope.normalizeLandLayerStyle(fallback);
    const expr = ["match", ["get", prop]];
    for (const [key, rawStyle] of Object.entries(styleMap || {})) {
      const style = scope.normalizeLandLayerStyle(rawStyle);
      expr.push(String(key), style.opacity);
    }
    expr.push(normalized.opacity);
    return expr;
  }

  function layerConfigToPayload(name, layerStyles, layerConfigs) {
    const config = layerConfigs.get(name) || scope.defaultLandLayerConfig();
    const row = { name };
    if (config.role) row.role = config.role;
    if (config.labelField) row.labelField = config.labelField;
    if (config.include?.length) row.include = config.include;
    if (config.exclude?.length) row.exclude = config.exclude;
    if (layerStyles.has(name)) {
      row.style = scope.normalizeLandLayerStyle(layerStyles.get(name));
    }
    return row;
  }


  function excludedValuesForField(config, field) {
    if (!field) return new Set();
    const filt = (config.exclude || []).find((item) => item.field === field);
    return new Set(Array.isArray(filt?.values) ? filt.values : []);
  }

  function setExcludedValueForField(config, field, value, excluded) {
    if (!field || !value) return;
    if (!Array.isArray(config.exclude)) config.exclude = [];
    let filt = config.exclude.find((item) => item.field === field);
    if (!filt) {
      filt = { field, values: [] };
      config.exclude.push(filt);
    }
    const values = new Set(Array.isArray(filt.values) ? filt.values : []);
    if (excluded) values.add(value);
    else values.delete(value);
    filt.values = [...values];
    config.exclude = config.exclude.filter(
      (item) => Array.isArray(item.values) && item.values.length,
    );
  }

  function configFromRegisteredLayer(layer) {
    const spec = scope.normalizeRegisteredLayer(layer);
    return {
      labelField: spec.labelField || "",
      role: spec.role || "",
      include: spec.include || [],
      exclude: spec.exclude || [],
      attrsOpen: false,
      fields: null,
      fieldsLoading: false,
    };
  }

  function collectSelectedLayerPayloads(listEl, layerStyles, layerConfigs) {
    return scope.collectSelectedLandLayers(listEl).map((name) =>
      scope.layerConfigToPayload(name, layerStyles, layerConfigs),
    );
  }

  async function fetchLandLayerFields(path, layer) {
    const resp = await fetch(scope.landFieldsApiUrl(path, layer));
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok)
      throw new Error(payload.error || `Fields failed (${resp.status})`);
    return payload;
  }

  async function fetchLandFieldValues(path, layer, field) {
    const resp = await fetch(scope.landValuesApiUrl(path, layer, field));
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok)
      throw new Error(payload.error || `Values failed (${resp.status})`);
    return payload;
  }


  async function fetchLandPreviewGeoJson(path, layer, layerConfig) {
    const cacheKey = `${scope.landPreviewCacheKey(path, layer)}|${JSON.stringify(layerConfig || {})}`;
    return scope.fetchCachedGeoJson(
      landPreviewGeoJsonCache,
      landPreviewGeoJsonInflight,
      cacheKey,
      async () => {
        if (
          layerConfig &&
          (layerConfig.include?.length ||
            layerConfig.exclude?.length ||
            layerConfig.styleField ||
            layerConfig.labelField)
        ) {
          const body = {
            path,
            layer,
            include: layerConfig.include,
            exclude: layerConfig.exclude,
            labelField: layerConfig.labelField || undefined,
            styleField: layerConfig.styleField || undefined,
            role: layerConfig.role || undefined,
          };
          const resp = await fetch(scope.landPreviewGeoJsonPostUrl(), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const payload = await resp.json().catch(() => ({}));
          if (!resp.ok || !payload.geojson) {
            throw new Error(payload.error || `Preview failed (${resp.status})`);
          }
          return payload.geojson;
        }
        const resp = await fetch(scope.landPreviewGeoJsonUrl(path, layer));
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok || !payload.geojson) {
          throw new Error(payload.error || `Preview failed (${resp.status})`);
        }
        return payload.geojson;
      },
    );
  }

  async function fetchLandLayerGeoJson(sourceId, layerKey, digest) {
    return scope.fetchCachedGeoJson(
      landLayerGeoJsonCache,
      landLayerGeoJsonInflight,
      scope.landLayerCacheKey(sourceId, layerKey, digest),
      async () => {
        const resp = await fetch(scope.landLayerGeoJsonUrl(sourceId, layerKey));
        if (!resp.ok) throw new Error(`GeoJSON failed (${resp.status})`);
        const data = await resp.json();
        const respDigest = resp.headers.get("X-Peaky-Digest");
        if (respDigest && respDigest !== digest) {
          landLayerGeoJsonCache.set(
            scope.landLayerCacheKey(sourceId, layerKey, respDigest),
            data,
          );
        }
        return data;
      },
    );
  }


  function ensureLandPreviewLoadingOverlay(mapEl) {
    if (!mapEl) return null;
    if (landPreviewLoadingOverlays.has(mapEl))
      return landPreviewLoadingOverlays.get(mapEl);
    let wrap = mapEl.parentElement;
    if (!wrap?.classList.contains("import-land-preview-map-wrap")) {
      const parent = mapEl.parentElement;
      if (!parent) return null;
      wrap = document.createElement("div");
      wrap.className = "import-land-preview-map-wrap";
      parent.insertBefore(wrap, mapEl);
      wrap.appendChild(mapEl);
    }
    const overlay = document.createElement("div");
    overlay.className = "import-land-preview-loading";
    overlay.hidden = true;
    overlay.setAttribute("aria-hidden", "true");
    const spinner = document.createElement("div");
    spinner.className = "pin-load-spinner import-land-preview-spinner";
    overlay.appendChild(spinner);
    wrap.appendChild(overlay);
    landPreviewLoadingOverlays.set(mapEl, overlay);
    return overlay;
  }

  function setLandPreviewMapLoading(mapEl, loading) {
    const overlay = scope.ensureLandPreviewLoadingOverlay(mapEl);
    if (!overlay) return;
    overlay.hidden = !loading;
  }

  function beginLandPreviewMapFetch(mapEl) {
    if (!mapEl) return;
    const next = (landPreviewLoadingCounts.get(mapEl) || 0) + 1;
    landPreviewLoadingCounts.set(mapEl, next);
    scope.setLandPreviewMapLoading(mapEl, true);
  }

  function endLandPreviewMapFetch(mapEl) {
    if (!mapEl) return;
    const next = Math.max(0, (landPreviewLoadingCounts.get(mapEl) || 0) - 1);
    landPreviewLoadingCounts.set(mapEl, next);
    scope.setLandPreviewMapLoading(mapEl, next > 0);
  }

  function setLandLayerRowLoading(listEl, layerName, loading) {
    if (!listEl || !layerName) return;
    const escaped =
      typeof CSS !== "undefined" && CSS.escape
        ? CSS.escape(layerName)
        : layerName;
    const input = listEl.querySelector(
      `input[type="checkbox"][data-layer-name="${escaped}"]`,
    );
    const row = input?.closest(".import-land-layer-row");
    if (!row) return;
    row.classList.toggle("import-land-layer-row--loading", !!loading);
    if (input) input.disabled = !!loading;
  }

  function defaultLandLayerStyle() {
    return {
      color: C.LAND_DEFAULT_FILL_COLOR,
      opacity: C.LAND_DEFAULT_FILL_OPACITY,
    };
  }


  function normalizeLandLayerStyle(raw) {
    const defaults = scope.defaultLandLayerStyle();
    if (!raw || typeof raw !== "object") return { ...defaults };
    const color =
      typeof raw.color === "string" && /^#[0-9a-fA-F]{6}$/.test(raw.color)
        ? raw.color.toLowerCase()
        : defaults.color;
    const opacityRaw = Number(raw.opacity);
    const opacity = Number.isFinite(opacityRaw)
      ? Math.min(1, Math.max(0, opacityRaw))
      : defaults.opacity;
    return { color, opacity };
  }

  function resolveLandLayerStyle(sourceId, layerKey) {
    const source = landSources.find((s) => s.id === sourceId);
    if (!source || !Array.isArray(source.layers))
      return scope.defaultLandLayerStyle();
    const spec = source.layers
      .map((layer) => scope.normalizeRegisteredLayer(layer))
      .find((layer) => layer.key === layerKey);
    return scope.flatStyleFromLayerSpec(spec);
  }

  function resolveLandLayerSpec(sourceId, layerKey) {
    const source = landSources.find((s) => s.id === sourceId);
    if (!source || !Array.isArray(source.layers)) return null;
    return (
      source.layers
        .map((layer) => scope.normalizeRegisteredLayer(layer))
        .find((layer) => layer.key === layerKey) || null
    );
  }





  function isLandLayerVisible(sourceId, layer) {
    const key = scope.landLayerKey(sourceId, layer);
    if (!landVisible.has(key)) return false;
    return landVisible.get(key) === true;
  }

  function setLandLayerVisible(sourceId, layer, visible) {
    landVisible.set(scope.landLayerKey(sourceId, layer), !!visible);
    scheduleSaveMapState();
    scope.syncLandMapLayerVisibility(sourceId, layer);
    scope.syncLandMapLabelLayer(sourceId, layer);
  }


  function isLandLayerLabelsVisible(sourceId, layer) {
    if (!scope.landLayerHasLabels(sourceId, layer)) return false;
    const key = scope.landLayerKey(sourceId, layer);
    if (!landLabelsVisible.has(key)) return true;
    return landLabelsVisible.get(key) === true;
  }

  function setLandLayerLabelsVisible(sourceId, layer, visible) {
    landLabelsVisible.set(scope.landLayerKey(sourceId, layer), !!visible);
    scheduleSaveMapState();
    scope.syncLandMapLabelLayer(sourceId, layer);
  }


  function cloneLandSidebar(sidebar = landSidebar) {
    return {
      folders: sidebar.folders.map((folder) => ({
        id: folder.id,
        label: folder.label,
        sources: [...folder.sources],
      })),
      unfiledSources: [...sidebar.unfiledSources],
    };
  }


  function allLandSourceIds() {
    return landSources.map((source) => source.id);
  }

  function syncLandSidebarWithSources() {
    const valid = new Set(scope.allLandSourceIds());
    const assigned = new Set();
    const folders = [];
    for (const folder of landSidebar.folders) {
      const sources = folder.sources.filter(
        (sid) => valid.has(sid) && !assigned.has(sid),
      );
      sources.forEach((sid) => assigned.add(sid));
      folders.push({ id: folder.id, label: folder.label, sources });
    }
    const unfiledSources = landSidebar.unfiledSources.filter(
      (sid) => valid.has(sid) && !assigned.has(sid),
    );
    unfiledSources.forEach((sid) => assigned.add(sid));
    for (const sid of scope.allLandSourceIds()) {
      if (!assigned.has(sid)) unfiledSources.push(sid);
    }
    landSidebar = { folders, unfiledSources };
  }

  function orderedLandSourceIds() {
    scope.syncLandSidebarWithSources();
    const ids = [];
    for (const folder of landSidebar.folders) {
      for (const sid of folder.sources) ids.push(sid);
    }
    for (const sid of landSidebar.unfiledSources) ids.push(sid);
    return ids;
  }

  function slugifyLandFolderId(label, folders) {
    const base =
      String(label)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || "folder";
    const existing = new Set(folders.map((folder) => folder.id));
    if (!existing.has(base)) return base;
    let n = 2;
    while (existing.has(`${base}-${n}`)) n += 1;
    return `${base}-${n}`;
  }

  function removeSourceFromSidebar(sidebar, sourceId) {
    for (const folder of sidebar.folders) {
      folder.sources = folder.sources.filter((sid) => sid !== sourceId);
    }
    sidebar.unfiledSources = sidebar.unfiledSources.filter(
      (sid) => sid !== sourceId,
    );
  }

  function schedulePersistLandSidebar() {
    if (landSidebarSaveTimer) clearTimeout(landSidebarSaveTimer);
    landSidebarSaveTimer = setTimeout(() => {
      landSidebarSaveTimer = null;
      void scope.persistLandSidebar();
    }, LAND_SIDEBAR_SAVE_MS);
  }

  async function persistLandSidebar({ refreshMap = false } = {}) {
    scope.syncLandSidebarWithSources();
    try {
      const resp = await fetch(scope.landSidebarApiUrl(), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(landSidebar),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        window.alert(payload.error || `Save sidebar failed (${resp.status})`);
        return false;
      }
      if (payload.sidebar)
        landSidebar = normalizeLandSidebarInput(payload.sidebar);
      if (refreshMap) await scope.refreshLandMapLayers();
      return true;
    } catch (_) {
      window.alert("Could not reach server.");
      return false;
    }
  }

  async function maybeMigrateLandSidebarFromLayerOrder() {
    if (!landSidebarMigrationPending) return;
    landSidebarMigrationPending = false;
    await scope.persistLandSidebar();
  }

  function queueLandSidebarMigrationFromLayerOrder() {
    if (landSidebar.folders.length || landSidebar.unfiledSources.length) return;
    if (!landLayerOrder.length) return;
    const seen = new Set();
    const order = [];
    for (const key of landLayerOrder) {
      const sid = String(key).split("/")[0];
      if (!sid || seen.has(sid)) continue;
      seen.add(sid);
      order.push(sid);
    }
    for (const sid of scope.allLandSourceIds()) {
      if (!seen.has(sid)) order.push(sid);
    }
    landSidebar = { folders: [], unfiledSources: order };
    landSidebarMigrationPending = true;
  }

  function isLandFolderCollapsed(folderId) {
    return landFoldersCollapsed.get(folderId) === true;
  }

  function setLandFolderCollapsed(folderId, collapsed) {
    landFoldersCollapsed.set(folderId, !!collapsed);
    scheduleSaveMapState();
    scope.renderLandPanel();
  }


  function isLandFolderVisible(folderId) {
    const refs = scope.landFolderLayerRefs(folderId);
    if (!refs.length) return false;
    return refs.every((key) => landVisible.get(key) === true);
  }

  function setLandFolderVisible(folderId, visible) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder) return;
    for (const sourceId of folder.sources) {
      const source = scope.landSourceRecord(sourceId);
      if (!source || !Array.isArray(source.layers)) continue;
      for (const rawLayer of source.layers) {
        const spec = scope.normalizeRegisteredLayer(rawLayer);
        scope.setLandLayerVisible(sourceId, spec.key, visible);
      }
    }
    scope.renderLandPanel();
  }



  function applyLandSidebarMutation(mutator) {
    const next = scope.cloneLandSidebar();
    mutator(next);
    landSidebar = next;
    scope.syncLandSidebarWithSources();
    scope.renderLandPanel();
    scope.syncLandMapLayerOrder();
    scope.schedulePersistLandSidebar();
  }

  function reorderLandFolder(fromFolderId, beforeFolderId) {
    if (!fromFolderId || fromFolderId === beforeFolderId) return;
    scope.applyLandSidebarMutation((sidebar) => {
      const fromIdx = sidebar.folders.findIndex(
        (folder) => folder.id === fromFolderId,
      );
      const toIdx = sidebar.folders.findIndex(
        (folder) => folder.id === beforeFolderId,
      );
      if (fromIdx < 0 || toIdx < 0) return;
      const [folder] = sidebar.folders.splice(fromIdx, 1);
      sidebar.folders.splice(toIdx, 0, folder);
    });
  }

  function moveLandSource(
    sourceId,
    { folderId = null, beforeSourceId = null } = {},
  ) {
    if (!sourceId) return;
    scope.applyLandSidebarMutation((sidebar) => {
      scope.removeSourceFromSidebar(sidebar, sourceId);
      if (folderId) {
        const folder = sidebar.folders.find((item) => item.id === folderId);
        if (!folder) return;
        if (beforeSourceId) {
          const idx = folder.sources.indexOf(beforeSourceId);
          folder.sources.splice(
            idx >= 0 ? idx : folder.sources.length,
            0,
            sourceId,
          );
        } else {
          folder.sources.push(sourceId);
        }
        return;
      }
      if (beforeSourceId) {
        const idx = sidebar.unfiledSources.indexOf(beforeSourceId);
        sidebar.unfiledSources.splice(
          idx >= 0 ? idx : sidebar.unfiledSources.length,
          0,
          sourceId,
        );
      } else {
        sidebar.unfiledSources.push(sourceId);
      }
    });
  }

  function reorderLandSource(fromSourceId, beforeSourceId, folderId = null) {
    if (!fromSourceId || fromSourceId === beforeSourceId) return;
    scope.applyLandSidebarMutation((sidebar) => {
      scope.removeSourceFromSidebar(sidebar, fromSourceId);
      if (folderId) {
        const folder = sidebar.folders.find((item) => item.id === folderId);
        if (!folder) return;
        const idx = beforeSourceId
          ? folder.sources.indexOf(beforeSourceId)
          : folder.sources.length;
        folder.sources.splice(
          idx >= 0 ? idx : folder.sources.length,
          0,
          fromSourceId,
        );
        return;
      }
      const idx = beforeSourceId
        ? sidebar.unfiledSources.indexOf(beforeSourceId)
        : sidebar.unfiledSources.length;
      sidebar.unfiledSources.splice(
        idx >= 0 ? idx : sidebar.unfiledSources.length,
        0,
        fromSourceId,
      );
    });
  }

  function createLandFolder() {
    const label = window.prompt("Folder name");
    if (!label || !label.trim()) return;
    const trimmed = label.trim();
    const id = scope.slugifyLandFolderId(trimmed, landSidebar.folders);
    scope.applyLandSidebarMutation((sidebar) => {
      sidebar.folders.push({ id, label: trimmed, sources: [] });
    });
  }

  function renameLandFolder(folderId) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder) return;
    const label = window.prompt("Folder name", folder.label);
    if (!label || !label.trim() || label.trim() === folder.label) return;
    scope.applyLandSidebarMutation((sidebar) => {
      const target = sidebar.folders.find((item) => item.id === folderId);
      if (target) target.label = label.trim();
    });
  }

  function deleteLandFolder(folderId) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder) return;
    if (
      !window.confirm(
        `Delete folder "${folder.label}"? Sources will move to Unfiled.`,
      )
    )
      return;
    scope.applyLandSidebarMutation((sidebar) => {
      const idx = sidebar.folders.findIndex((item) => item.id === folderId);
      if (idx < 0) return;
      const [removed] = sidebar.folders.splice(idx, 1);
      sidebar.unfiledSources.push(...removed.sources);
    });
  }

  function syncLandMapLayerOrder() {
    if (!mapReady) return;
    const rows = scope.landLayerRows();
    const anchor = scope.viewshedLayerInsertBefore();
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const row = rows[i];
      const sourceMapId = scope.landMapSourceId(row.sourceId, row.layerKey);
      for (const suffix of ["-fill", "-line", "-labels"]) {
        const id = `${sourceMapId}${suffix}`;
        if (map.getLayer(id)) {
          try {
            map.moveLayer(id, anchor);
          } catch (_) {
            /* layer may be mid-remove */
          }
        }
      }
    }
    raiseSiteLayers();
  }

  function buildLandDragHandle({ kind, id, label }) {
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "entity-panel__land-drag-handle";
    handle.draggable = true;
    handle.title = "Drag to reorder";
    handle.setAttribute("aria-label", label || "Drag to reorder");
    handle.innerHTML = scope.mapToolIcon("grip-vertical", label || "Drag to reorder");
    handle.addEventListener("mousedown", (ev) => ev.stopPropagation());
    handle.addEventListener("click", (ev) => ev.stopPropagation());
    handle.addEventListener("dragstart", (ev) => {
      const row = handle.closest(".entity-panel__land-drop-row");
      if (!row) return;
      landDragKind = row.dataset.landDragKind || null;
      landDragId = row.dataset.landDragId || null;
      row.classList.add("entity-panel__row--land-dragging");
      ev.dataTransfer.effectAllowed = "move";
      try {
        ev.dataTransfer.setData("text/plain", `${landDragKind}:${landDragId}`);
      } catch (_) {
        /* Safari */
      }
    });
    return handle;
  }

  function clearLandDragState() {
    landDragKind = null;
    landDragId = null;
    if (!entityPanelLandList) return;
    for (const el of entityPanelLandList.querySelectorAll(
      ".entity-panel__land-drop-row.entity-panel__row--land-dragging, .entity-panel__land-drop-row.entity-panel__row--land-drop-target",
    )) {
      el.classList.remove(
        "entity-panel__row--land-dragging",
        "entity-panel__row--land-drop-target",
      );
    }
  }

  function initLandPanelDragDrop() {
    if (!entityPanelLandList || entityPanelLandList.dataset.landDragBound)
      return;
    entityPanelLandList.dataset.landDragBound = "1";

    entityPanelLandList.addEventListener("dragend", clearLandDragState);

    entityPanelLandList.addEventListener("dragover", (ev) => {
      const row = ev.target.closest(".entity-panel__land-drop-row");
      if (!row || !landDragKind || !landDragId) return;
      if (
        row.dataset.landDragKind === landDragKind &&
        row.dataset.landDragId === landDragId
      )
        return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      for (const el of entityPanelLandList.querySelectorAll(
        ".entity-panel__row--land-drop-target",
      )) {
        if (el !== row)
          el.classList.remove("entity-panel__row--land-drop-target");
      }
      row.classList.add("entity-panel__row--land-drop-target");
    });

    entityPanelLandList.addEventListener("dragleave", (ev) => {
      const row = ev.target.closest(".entity-panel__land-drop-row");
      if (!row) return;
      const related = ev.relatedTarget;
      if (related && row.contains(related)) return;
      row.classList.remove("entity-panel__row--land-drop-target");
    });

    entityPanelLandList.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const row = ev.target.closest(".entity-panel__land-drop-row");
      if (!row || !landDragKind || !landDragId) return;
      row.classList.remove("entity-panel__row--land-drop-target");
      const targetKind = row.dataset.landDragKind;
      const targetId = row.dataset.landDragId;
      const targetFolderId = row.dataset.landFolderId || null;
      if (landDragKind === "folder" && targetKind === "folder") {
        scope.reorderLandFolder(landDragId, targetId);
        return;
      }
      if (landDragKind === "source") {
        if (targetKind === "folder") {
          scope.moveLandSource(landDragId, { folderId: targetId });
          return;
        }
        if (targetKind === "unfiled") {
          scope.moveLandSource(landDragId, {});
          return;
        }
        if (targetKind === "source") {
          scope.reorderLandSource(landDragId, targetId, targetFolderId);
        }
      }
    });
  }

  function setEntityTab(tab) {
    const next = tab === "land" ? "land" : "sites";
    entityPanelTab = next;
    for (const btn of entityPanelTabs) {
      const active = btn.getAttribute("data-entity-tab") === next;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (entityPanelSitesPane) entityPanelSitesPane.hidden = next !== "sites";
    if (entityPanelLandPane) entityPanelLandPane.hidden = next !== "land";
    if (next === "land") void scope.maybeMigrateLandSidebarFromLayerOrder();
    if (entityPanelToggle) {
      entityPanelToggle.title = next === "land" ? "Land" : "Sites";
      entityPanelToggle.setAttribute(
        "aria-label",
        entityPanelOpen ? `Hide ${next}` : `Show ${next}`,
      );
    }
    scheduleSaveMapState();
  }

  function syncGeoJsonLabelLayer(
    mapInstance,
    sourceId,
    labelsId,
    showLabels,
    visibility,
  ) {
    if (!mapInstance) return;
    const vis = visibility === "none" ? "none" : "visible";
    if (!showLabels) {
      if (mapInstance.getLayer(labelsId)) mapInstance.removeLayer(labelsId);
      return;
    }
    if (!mapInstance.getSource(sourceId)) return;
    if (!mapInstance.getLayer(labelsId)) {
      mapInstance.addLayer({
        id: labelsId,
        type: "symbol",
        source: sourceId,
        filter: ["all", ["has", "label"], ["!=", ["get", "label"], ""]],
        layout: {
          "text-field": ["get", "label"],
          "text-size": 11,
          "text-font": C.MAP_LABEL_FONT,
          "text-allow-overlap": true,
          "text-max-width": 14,
          visibility: vis,
        },
        paint: {
          "text-color": "#f0f4ff",
          "text-halo-color": "#141820",
          "text-halo-width": 1.5,
        },
      });
    } else {
      mapInstance.setLayoutProperty(labelsId, "visibility", vis);
    }
  }

  function syncLandMapLabelLayer(sourceId, layerKey) {
    if (!mapReady) return;
    const sourceMapId = scope.landMapSourceId(sourceId, layerKey);
    const labelsId = `${sourceMapId}-labels`;
    const layerVisible = scope.isLandLayerVisible(sourceId, layerKey);
    const labelsVisible =
      layerVisible && scope.isLandLayerLabelsVisible(sourceId, layerKey);
    const vis = labelsVisible ? "visible" : "none";
    scope.syncGeoJsonLabelLayer(
      map,
      sourceMapId,
      labelsId,
      scope.landLayerHasLabels(sourceId, layerKey),
      vis,
    );
  }

  function syncLandMapLayerVisibility(sourceId, layer) {
    if (!mapReady) return;
    const sourceMapId = scope.landMapSourceId(sourceId, layer);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const vis = scope.isLandLayerVisible(sourceId, layer) ? "visible" : "none";
    if (map.getLayer(fillId)) map.setLayoutProperty(fillId, "visibility", vis);
    if (map.getLayer(lineId)) map.setLayoutProperty(lineId, "visibility", vis);
  }

  function applyLandMapLayerStyle(sourceId, layerKey) {
    if (!mapReady) return;
    const sourceMapId = scope.landMapSourceId(sourceId, layerKey);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const spec = scope.resolveLandLayerSpec(sourceId, layerKey);
    const styleMap = scope.styleMapFromLayerSpec(spec);
    const fallback = scope.flatStyleFromLayerSpec(spec);
    const lineFallback = scope.landLineColorFromFill(fallback.color);
    if (map.getLayer(fillId)) {
      if (spec?.styleField && styleMap) {
        map.setPaintProperty(
          fillId,
          "fill-color",
          scope.buildStyleMatchExpression(styleMap, "style_key", fallback),
        );
        map.setPaintProperty(
          fillId,
          "fill-opacity",
          scope.buildOpacityMatchExpression(styleMap, "style_key", fallback),
        );
      } else {
        map.setPaintProperty(fillId, "fill-color", fallback.color);
        map.setPaintProperty(fillId, "fill-opacity", fallback.opacity);
      }
      map.setPaintProperty(fillId, "fill-outline-color", lineFallback);
    }
    if (map.getLayer(lineId)) {
      map.setPaintProperty(lineId, "line-color", lineFallback);
      map.setPaintProperty(lineId, "line-width", C.LAND_LINE_WIDTH);
    }
  }

  async function ensureLandMapLayer(
    sourceId,
    layerKey,
    { force = false } = {},
  ) {
    if (!mapReady) return;
    const sourceMapId = scope.landMapSourceId(sourceId, layerKey);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const spec = scope.resolveLandLayerSpec(sourceId, layerKey);
    const styleMap = scope.styleMapFromLayerSpec(spec);
    const fallback = scope.flatStyleFromLayerSpec(spec);
    const lineColor = scope.landLineColorFromFill(fallback.color);
    const fillPaint =
      spec?.styleField && styleMap
        ? {
            "fill-color": scope.buildStyleMatchExpression(
              styleMap,
              "style_key",
              fallback,
            ),
            "fill-opacity": scope.buildOpacityMatchExpression(
              styleMap,
              "style_key",
              fallback,
            ),
            "fill-outline-color": lineColor,
          }
        : {
            "fill-color": fallback.color,
            "fill-opacity": fallback.opacity,
            "fill-outline-color": lineColor,
          };
    if (map.getSource(sourceMapId) && !force) {
      scope.applyLandMapLayerStyle(sourceId, layerKey);
      scope.syncLandMapLayerVisibility(sourceId, layerKey);
      scope.syncLandMapLabelLayer(sourceId, layerKey);
      return;
    }
    if (force && map.getSource(sourceMapId)) {
      scope.removeLandMapLayer(sourceId, layerKey);
    }
    try {
      const geojson = await scope.fetchLandLayerGeoJson(
        sourceId,
        layerKey,
        spec?.digest || "",
      );
      map.addSource(sourceMapId, { type: "geojson", data: geojson });
      map.addLayer(
        {
          id: fillId,
          type: "fill",
          source: sourceMapId,
          paint: fillPaint,
          layout: {
            visibility: scope.isLandLayerVisible(sourceId, layerKey)
              ? "visible"
              : "none",
          },
        },
        scope.viewshedLayerInsertBefore(),
      );
      map.addLayer(
        {
          id: lineId,
          type: "line",
          source: sourceMapId,
          paint: {
            "line-color": lineColor,
            "line-width": C.LAND_LINE_WIDTH,
          },
          layout: {
            visibility: scope.isLandLayerVisible(sourceId, layerKey)
              ? "visible"
              : "none",
          },
        },
        scope.viewshedLayerInsertBefore(),
      );
      scope.syncLandMapLabelLayer(sourceId, layerKey);
      raiseSiteLayers();
    } catch (_) {
      /* network */
    }
  }

  async function refreshLandMapLayer(sourceId, layerKey) {
    if (!mapReady) return;
    if (!scope.isLandLayerVisible(sourceId, layerKey)) return;
    scope.setLandSidebarRowLoading(sourceId, layerKey, true);
    try {
      scope.clearLandLayerGeoJsonCacheForLayer(sourceId, layerKey);
      await scope.ensureLandMapLayer(sourceId, layerKey, { force: true });
    } finally {
      scope.setLandSidebarRowLoading(sourceId, layerKey, false);
    }
  }

  async function reloadClippedLandLayers() {
    const rows = scope.landLayerRows().filter((row) => {
      if (row.spec.role === "aoi") return false;
      return scope.isLandLayerVisible(row.sourceId, row.layerKey);
    });
    await Promise.all(
      rows.map((row) => scope.refreshLandMapLayer(row.sourceId, row.layerKey)),
    );
  }

  function removeLandMapLayer(sourceId, layer) {
    if (!mapReady) return;
    const sourceMapId = scope.landMapSourceId(sourceId, layer);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const labelsId = `${sourceMapId}-labels`;
    if (map.getLayer(labelsId)) map.removeLayer(labelsId);
    if (map.getLayer(lineId)) map.removeLayer(lineId);
    if (map.getLayer(fillId)) map.removeLayer(fillId);
    if (map.getSource(sourceMapId)) map.removeSource(sourceMapId);
  }

  async function refreshLandMapLayers() {
    await Promise.all(
      scope.landLayerRows().map((row) =>
        scope.ensureLandMapLayer(row.sourceId, row.layerKey),
      ),
    );
    scope.syncLandMapLayerOrder();
  }

  function buildLandLayerEyeBtn(sourceId, layerKey) {
    const visible = scope.isLandLayerVisible(sourceId, layerKey);
    return scope.makeEntityPanelActionBtn({
      icon: visible ? "eye" : "eye-slash",
      label: visible ? "Hide layer on map" : "Show layer on map",
      active: visible,
      onClick: () => {
        const next = !scope.isLandLayerVisible(sourceId, layerKey);
        scope.setLandLayerVisible(sourceId, layerKey, next);
        scope.renderLandPanel();
        if (next) void scope.ensureLandMapLayer(sourceId, layerKey);
      },
    });
  }

  function buildLandLayerLabelBtn(sourceId, layerKey) {
    const layerVisible = scope.isLandLayerVisible(sourceId, layerKey);
    const labelsVisible = scope.isLandLayerLabelsVisible(sourceId, layerKey);
    return scope.makeEntityPanelActionBtn({
      icon: "font",
      label: labelsVisible ? "Hide labels" : "Show labels",
      active: labelsVisible,
      disabled: !layerVisible,
      extraClass: "entity-panel__land-label-toggle",
      onClick: () => {
        scope.setLandLayerLabelsVisible(
          sourceId,
          layerKey,
          !scope.isLandLayerLabelsVisible(sourceId, layerKey),
        );
        scope.renderLandPanel();
      },
    });
  }

  function buildLandSourceActionBtns(sourceId) {
    const editBtn = scope.makeEntityPanelActionBtn({
      icon: "pen",
      label: "Edit layers",
      onClick: () => {
        void scope.openEditLandModal(sourceId);
      },
    });
    const deleteBtn = scope.makeEntityPanelActionBtn({
      icon: "trash",
      label: "Remove source",
      danger: true,
      onClick: () => {
        void scope.deleteLandSource(sourceId);
      },
    });
    return [editBtn, deleteBtn];
  }

  function buildLandFolderEyeBtn(folderId) {
    const visible = scope.isLandFolderVisible(folderId);
    return scope.makeEntityPanelActionBtn({
      icon: visible ? "eye" : "eye-slash",
      label: visible
        ? "Hide all layers in folder"
        : "Show all layers in folder",
      active: visible,
      onClick: () => {
        scope.setLandFolderVisible(folderId, !visible);
      },
    });
  }

  function buildLandFolderActionBtns(folderId) {
    const renameBtn = scope.makeEntityPanelActionBtn({
      icon: "pen",
      label: "Rename folder",
      onClick: () => scope.renameLandFolder(folderId),
    });
    const deleteBtn = scope.makeEntityPanelActionBtn({
      icon: "trash",
      label: "Delete folder",
      danger: true,
      onClick: () => scope.deleteLandFolder(folderId),
    });
    return [renameBtn, deleteBtn];
  }

  function buildLandFolderRow(folder) {
    const collapsed = scope.isLandFolderCollapsed(folder.id);
    const el = document.createElement("div");
    el.className = "entity-panel__land-folder";

    const header = document.createElement("div");
    header.className =
      "entity-panel__land-folder-header entity-panel__land-drop-row";
    header.dataset.landDragKind = "folder";
    header.dataset.landDragId = folder.id;

    const collapseBtn = document.createElement("button");
    collapseBtn.type = "button";
    collapseBtn.className = "entity-panel__land-folder-collapse";
    collapseBtn.title = collapsed ? "Expand folder" : "Collapse folder";
    collapseBtn.setAttribute(
      "aria-label",
      collapsed ? "Expand folder" : "Collapse folder",
    );
    collapseBtn.innerHTML = scope.mapToolIcon(
      collapsed ? "chevron-right" : "chevron-down",
      "Toggle folder",
    );
    collapseBtn.addEventListener("click", () => {
      scope.setLandFolderCollapsed(folder.id, !collapsed);
    });

    header.appendChild(collapseBtn);
    header.appendChild(
      scope.buildLandDragHandle({
        kind: "folder",
        id: folder.id,
        label: "Drag to reorder folder",
      }),
    );

    const title = document.createElement("div");
    title.className = "entity-panel__land-folder-title";
    title.textContent = folder.label;
    title.title = folder.label;
    header.appendChild(title);

    const controls = document.createElement("div");
    controls.className = "entity-panel__land-source-actions";
    controls.appendChild(scope.buildLandFolderEyeBtn(folder.id));
    for (const btn of scope.buildLandFolderActionBtns(folder.id))
      controls.appendChild(btn);
    header.appendChild(controls);
    el.appendChild(header);

    const body = document.createElement("div");
    body.className = "entity-panel__land-folder-body";
    body.hidden = collapsed;
    for (const sourceId of folder.sources) {
      const group = scope.buildLandSourceGroup(sourceId, { folderId: folder.id });
      if (group) body.appendChild(group);
    }
    el.appendChild(body);
    return el;
  }

  function buildLandSourceGroup(sourceId, { folderId = null } = {}) {
    const source = scope.landSourceRecord(sourceId);
    if (!source) return null;
    const displayTitles = scope.landSourceDisplayTitles(landSources);
    const sourceTitle = displayTitles.get(sourceId) || source.label || sourceId;
    const layers = Array.isArray(source.layers) ? source.layers : [];
    const singleLayer = layers.length === 1;
    const singleSpec = singleLayer ? scope.normalizeRegisteredLayer(layers[0]) : null;

    const el = document.createElement("div");
    el.className = "entity-panel__land-source-group";

    const header = document.createElement("div");
    header.className =
      "entity-panel__land-source entity-panel__land-source-draggable entity-panel__land-drop-row";
    header.dataset.landDragKind = "source";
    header.dataset.landDragId = sourceId;
    if (folderId) header.dataset.landFolderId = folderId;
    if (singleSpec)
      header.dataset.landKey = scope.landLayerKey(sourceId, singleSpec.key);
    if (singleSpec && !scope.isLandLayerVisible(sourceId, singleSpec.key)) {
      header.classList.add("entity-panel__row--hidden");
    }

    header.appendChild(
      scope.buildLandDragHandle({
        kind: "source",
        id: sourceId,
        label: "Drag to move source",
      }),
    );

    const titleRow = document.createElement("div");
    titleRow.className = "entity-panel__land-source-title-row";
    if (singleSpec) scope.appendLandLayerRoleBadge(titleRow, singleSpec.role);
    const title = document.createElement("div");
    title.className = "entity-panel__land-source-title";
    title.textContent = sourceTitle;
    title.title = sourceTitle;
    titleRow.appendChild(title);
    header.appendChild(titleRow);

    const controls = document.createElement("div");
    controls.className = "entity-panel__land-source-actions";
    if (singleSpec) {
      controls.appendChild(
        scope.buildLandLayerControls(sourceId, singleSpec.key, {
          labelField: singleSpec.labelField,
        }),
      );
    }
    for (const btn of scope.buildLandSourceActionBtns(sourceId))
      controls.appendChild(btn);
    header.appendChild(controls);
    el.appendChild(header);

    if (singleSpec) {
      const metaHost = document.createElement("div");
      metaHost.className = "entity-panel__land-source-meta";
      scope.appendLandLayerMeta(metaHost, singleSpec);
      if (metaHost.childNodes.length) el.appendChild(metaHost);
    } else if (layers.length > 1) {
      const layerList = document.createElement("div");
      layerList.className = "entity-panel__land-source-layers";
      for (const rawLayer of layers) {
        const spec = scope.normalizeRegisteredLayer(rawLayer);
        layerList.appendChild(
          scope.buildLandEntityRow(
            {
              sourceId,
              layerKey: spec.key,
              spec,
            },
            { showName: true },
          ),
        );
      }
      el.appendChild(layerList);
    }
    return el;
  }

  function buildLandUnfiledSection({ showHeader = true } = {}) {
    scope.syncLandSidebarWithSources();
    if (!landSidebar.unfiledSources.length && !showHeader) return null;
    const el = document.createElement("div");
    el.className = "entity-panel__land-unfiled";

    if (showHeader) {
      const header = document.createElement("div");
      header.className =
        "entity-panel__land-unfiled-header entity-panel__land-drop-row";
      header.dataset.landDragKind = "unfiled";
      header.dataset.landDragId = "unfiled";
      header.textContent = "Unfiled";
      el.appendChild(header);
    }

    for (const sourceId of landSidebar.unfiledSources) {
      const group = scope.buildLandSourceGroup(sourceId);
      if (group) el.appendChild(group);
    }
    return el;
  }

  function renderLandPanel() {
    if (!entityPanelLandList) return;
    scope.syncLandSidebarWithSources();
    const rows = scope.landLayerRows();
    const sourceCount = scope.orderedLandSourceIds().length;
    if (entityPanelLandCount) {
      if (!sourceCount) {
        entityPanelLandCount.textContent = "No land sources";
      } else {
        entityPanelLandCount.textContent = `${sourceCount} source${sourceCount === 1 ? "" : "s"}, ${rows.length} layer${rows.length === 1 ? "" : "s"}`;
      }
    }
    entityPanelLandList.innerHTML = "";
    if (!sourceCount) {
      const empty = document.createElement("div");
      empty.className = "entity-panel__empty";
      empty.textContent = "Import GDB layers from data/.";
      entityPanelLandList.appendChild(empty);
      return;
    }
    const hasFolders = landSidebar.folders.length > 0;
    for (const folder of landSidebar.folders) {
      entityPanelLandList.appendChild(scope.buildLandFolderRow(folder));
    }
    if (hasFolders) {
      const unfiled = scope.buildLandUnfiledSection({ showHeader: true });
      if (unfiled) entityPanelLandList.appendChild(unfiled);
    } else {
      const unfiled = scope.buildLandUnfiledSection({ showHeader: false });
      if (unfiled) entityPanelLandList.appendChild(unfiled);
    }
  }

  function setLandSidebarRowLoading(sourceId, layerKey, loading) {
    if (!entityPanelLandList) return;
    const rowKey = scope.landLayerKey(sourceId, layerKey);
    const row =
      entityPanelLandList.querySelector(
        `.entity-panel__row--land[data-land-key="${rowKey}"]`,
      ) || entityPanelLandList.querySelector(`[data-land-key="${rowKey}"]`);
    if (!row) return;
    row.classList.toggle("entity-panel__row--land-loading", !!loading);
    const spinner = row.querySelector(".entity-panel__land-row-spinner");
    if (spinner) spinner.hidden = !loading;
  }

  function buildLandEntityRow(row, { showName = false } = {}) {
    const rowKey = scope.landLayerKey(row.sourceId, row.layerKey);
    const el = document.createElement("div");
    el.className =
      "entity-panel__row entity-panel__row--land entity-panel__row--land-nested";
    el.dataset.landKey = rowKey;
    const visible = scope.isLandLayerVisible(row.sourceId, row.layerKey);
    if (!visible) el.classList.add("entity-panel__row--hidden");

    const main = document.createElement("div");
    main.className = "entity-panel__main";
    if (showName) {
      const titleRow = document.createElement("div");
      titleRow.className = "entity-panel__land-title-row";
      scope.appendLandLayerRoleBadge(titleRow, row.spec.role);
      const name = document.createElement("div");
      name.className = "entity-panel__name";
      const nameText = scope.friendlyLandLayerName(row.spec.name);
      name.textContent = nameText;
      name.title = nameText;
      titleRow.appendChild(name);
      main.appendChild(titleRow);
    }
    scope.appendLandLayerMeta(main, row.spec);

    const controls = document.createElement("div");
    controls.className = "entity-panel__controls";
    controls.appendChild(
      scope.buildLandLayerControls(row.sourceId, row.layerKey, {
        labelField: row.spec.labelField,
      }),
    );

    el.appendChild(main);
    el.appendChild(controls);
    return el;
  }

  async function reloadLandSources({ refreshMap = true } = {}) {
    const prevAoiDigest = landAoiDigest;
    try {
      const resp = await fetch(scope.landApiUrl());
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) return;
      landSources = Array.isArray(payload.sources) ? payload.sources : [];
      if (payload.sidebar)
        landSidebar = normalizeLandSidebarInput(payload.sidebar);
      scope.syncLandSidebarWithSources();
      const nextAoiDigest =
        typeof payload.aoiDigest === "string" ? payload.aoiDigest : "none";
      const aoiChanged = nextAoiDigest !== prevAoiDigest;
      landAoiDigest = nextAoiDigest;
      scope.renderLandPanel();
      if (!refreshMap) return;
      if (aoiChanged) {
        scope.clearAllLandLayerGeoJsonCache();
        await scope.reloadClippedLandLayers();
      } else {
        await scope.refreshLandMapLayers();
      }
    } catch (_) {
      /* network */
    }
  }

  async function deleteLandSource(sourceId) {
    if (!window.confirm(`Remove land source "${sourceId}"?`)) return;
    const prevAoiDigest = landAoiDigest;
    try {
      const resp = await fetch(scope.landSourceApiUrl(sourceId), {
        method: "DELETE",
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        window.alert(payload.error || `Delete failed (${resp.status})`);
        return;
      }
      const source = landSources.find((s) => s.id === sourceId);
      if (source && Array.isArray(source.layers)) {
        for (const rawLayer of source.layers) {
          const spec = scope.normalizeRegisteredLayer(rawLayer);
          scope.removeLandMapLayer(sourceId, spec.key);
          landVisible.delete(scope.landLayerKey(sourceId, spec.key));
          landLabelsVisible.delete(scope.landLayerKey(sourceId, spec.key));
          scope.clearLandLayerGeoJsonCacheForLayer(sourceId, spec.key);
        }
      }
      await scope.reloadLandSources({ refreshMap: false });
      if (landAoiDigest !== prevAoiDigest) {
        scope.clearAllLandLayerGeoJsonCache();
        await scope.reloadClippedLandLayers();
      }
      scheduleSaveMapState();
    } catch (_) {
      window.alert("Could not reach server.");
    }
  }

  function setImportLandError(message) {
    if (!importLandError) return;
    if (message) {
      importLandError.textContent = message;
      importLandError.hidden = false;
    } else {
      importLandError.textContent = "";
      importLandError.hidden = true;
    }
  }

  function setEditLandError(message) {
    if (!editLandError) return;
    if (message) {
      editLandError.textContent = message;
      editLandError.hidden = false;
    } else {
      editLandError.textContent = "";
      editLandError.hidden = true;
    }
  }

  function destroyImportLandPreviewMap() {
    if (importLandPreviewMap) {
      importLandPreviewMap.remove();
      importLandPreviewMap = null;
    }
  }

  function destroyEditLandPreviewMap() {
    if (editLandPreviewMap) {
      editLandPreviewMap.remove();
      editLandPreviewMap = null;
    }
  }

  function ensureLandPreviewMap(el, mapRef) {
    if (!el) return null;
    if (mapRef) {
      mapRef.resize();
      return mapRef;
    }
    const preview = new maplibregl.Map({
      container: el,
      style: scope.basemapStyle(currentBasemapKey),
      center: map.getCenter(),
      zoom: map.getZoom(),
      bearing: 0,
      pitch: 0,
      attributionControl: false,
    });
    return preview;
  }

  function whenPreviewMapReady(previewMap) {
    if (!previewMap) return Promise.resolve();
    if (previewMap.isStyleLoaded()) return Promise.resolve();
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      previewMap.once("load", finish);
      previewMap.once("error", finish);
      window.setTimeout(finish, 8000);
    });
  }

  function resetLandPreviewMapLoading(mapEl) {
    if (!mapEl) return;
    landPreviewLoadingCounts.set(mapEl, 0);
    scope.setLandPreviewMapLoading(mapEl, false);
  }

  function fitPreviewMapToBboxes(previewMap, bboxes) {
    if (!previewMap || !bboxes.length) return;
    const fit = () => {
      const bounds = new maplibregl.LngLatBounds();
      for (const bbox of bboxes) {
        if (!Array.isArray(bbox) || bbox.length !== 4) continue;
        const [minx, miny, maxx, maxy] = bbox;
        bounds.extend([minx, miny]);
        bounds.extend([maxx, maxy]);
      }
      if (!bounds.isEmpty()) {
        previewMap.fitBounds(bounds, { padding: 36, maxZoom: 12, duration: 0 });
      }
    };
    if (previewMap.loaded()) fit();
    else previewMap.once("load", fit);
  }

  function ensureLandLayerStyleState(layerStyles, layerName, seed) {
    if (!layerStyles.has(layerName)) {
      layerStyles.set(layerName, scope.normalizeLandLayerStyle(seed));
    }
    return layerStyles.get(layerName);
  }

  function buildLandLayerAttrPanel(
    layerName,
    gdbPath,
    config,
    onLabelFieldChange,
  ) {
    const panel = document.createElement("div");
    panel.className = "import-land-layer-attrs";
    panel.hidden = !config.attrsOpen;

    const labelTitle = document.createElement("div");
    labelTitle.className = "import-land-attrs-section-title";
    labelTitle.textContent = "Label field";

    const labelSelect = document.createElement("select");
    labelSelect.className = "import-land-attrs-select";
    labelSelect.innerHTML = '<option value="">None</option>';

    const valuesEl = document.createElement("div");
    valuesEl.className = "import-land-attrs-values";

    const valuesTitle = document.createElement("div");
    valuesTitle.className = "import-land-attrs-section-title";
    valuesTitle.textContent = "Categories";
    valuesTitle.hidden = true;

    const valuesHint = document.createElement("div");
    valuesHint.className = "wa-caption pf-muted import-land-attrs-values-hint";
    valuesHint.textContent = "Check to exclude from import";
    valuesHint.hidden = true;

    const roleTitle = document.createElement("div");
    roleTitle.className = "import-land-attrs-section-title";
    roleTitle.textContent = "Role";

    const roleSelect = document.createElement("select");
    roleSelect.className = "import-land-attrs-select";
    for (const [value, label] of [
      ["", "Default"],
      ["aoi", "AOI (clip boundary)"],
      ["include", "Include"],
      ["exclude", "Exclude"],
    ]) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      roleSelect.appendChild(opt);
    }
    roleSelect.value = config.role || "";
    roleSelect.addEventListener("change", () => {
      config.role = roleSelect.value;
    });

    function populateLabelSelect() {
      const fields = config.fields || [];
      const prev = labelSelect.value || config.labelField;
      labelSelect.innerHTML = '<option value="">None</option>';
      for (const field of fields
        .slice()
        .sort((a, b) =>
          scope.landColumnSortKey(a.name).localeCompare(scope.landColumnSortKey(b.name)),
        )) {
        const opt = document.createElement("option");
        opt.value = field.name;
        opt.textContent = field.name;
        labelSelect.appendChild(opt);
      }
      if (prev && [...labelSelect.options].some((opt) => opt.value === prev)) {
        labelSelect.value = prev;
      }
    }

    async function loadLabelValues() {
      if (!config.labelField) {
        valuesEl.innerHTML = "";
        valuesTitle.hidden = true;
        valuesHint.hidden = true;
        return;
      }
      valuesTitle.hidden = false;
      valuesHint.hidden = false;
      valuesEl.innerHTML = '<span class="wa-caption pf-muted">Loading…</span>';
      try {
        const payload = await scope.fetchLandFieldValues(
          gdbPath,
          layerName,
          config.labelField,
        );
        const rows = Array.isArray(payload.values) ? payload.values : [];
        valuesEl.innerHTML = "";
        if (!rows.length) {
          valuesEl.innerHTML =
            '<span class="wa-caption pf-muted">No values</span>';
          return;
        }
        const excluded = scope.excludedValuesForField(config, config.labelField);
        for (const row of rows) {
          const item = document.createElement("label");
          item.className = "import-land-attrs-value-row";
          if (excluded.has(row.value)) {
            item.classList.add("import-land-attrs-value-row--excluded");
          }

          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = excluded.has(row.value);
          checkbox.title = "Exclude this category";

          const text = document.createElement("span");
          text.textContent = `${row.value} (${row.count})`;

          checkbox.addEventListener("change", () => {
            scope.setExcludedValueForField(
              config,
              config.labelField,
              row.value,
              checkbox.checked,
            );
            item.classList.toggle(
              "import-land-attrs-value-row--excluded",
              checkbox.checked,
            );
            if (onLabelFieldChange) onLabelFieldChange(layerName);
          });

          item.appendChild(checkbox);
          item.appendChild(text);
          valuesEl.appendChild(item);
        }
      } catch (_) {
        valuesEl.innerHTML =
          '<span class="wa-caption pf-muted">Could not load values</span>';
      }
    }

    async function ensureFields() {
      if (config.fields) {
        scope.populateLabelSelect();
        return;
      }
      if (config.fieldsLoading) return;
      config.fieldsLoading = true;
      labelSelect.disabled = true;
      try {
        const payload = await scope.fetchLandLayerFields(gdbPath, layerName);
        config.fields = Array.isArray(payload.fields) ? payload.fields : [];
        scope.populateLabelSelect();
      } catch (_) {
        labelSelect.innerHTML =
          '<option value="">Could not load fields</option>';
      } finally {
        config.fieldsLoading = false;
        labelSelect.disabled = false;
      }
    }

    labelSelect.addEventListener("change", () => {
      config.labelField = labelSelect.value;
      void scope.loadLabelValues();
      if (onLabelFieldChange) onLabelFieldChange(layerName);
    });

    panel.appendChild(roleTitle);
    panel.appendChild(roleSelect);
    panel.appendChild(labelTitle);
    panel.appendChild(labelSelect);
    panel.appendChild(valuesTitle);
    panel.appendChild(valuesHint);
    panel.appendChild(valuesEl);

    void scope.ensureFields().then(() => {
      if (config.labelField) void scope.loadLabelValues();
    });

    return panel;
  }

  function renderLandLayerChecklist(
    listEl,
    layers,
    {
      selected,
      layerStyles,
      layerConfigs,
      gdbPath,
      onToggle,
      onStyleChange,
      onLabelFieldChange,
      countEl,
    },
  ) {
    if (!listEl) return;
    listEl.innerHTML = "";
    const selectedSet =
      selected instanceof Set ? selected : new Set(selected || []);
    for (const layer of layers) {
      scope.ensureLandLayerStyleState(layerStyles, layer.name);
      scope.ensureLandLayerConfig(layerConfigs, layer.name);
      const config = layerConfigs.get(layer.name);
      const row = document.createElement("div");
      row.className = "import-sites-point-row import-land-layer-row";
      if (selectedSet.has(layer.name))
        row.classList.add("import-land-layer-row--selected");

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selectedSet.has(layer.name);
      checkbox.dataset.layerName = layer.name;
      checkbox.addEventListener("change", () => {
        onToggle(layer.name, checkbox.checked);
        row.classList.toggle(
          "import-land-layer-row--selected",
          checkbox.checked,
        );
        attrsToggle.hidden = !checkbox.checked;
        if (!checkbox.checked) {
          config.attrsOpen = false;
          attrsPanel.hidden = true;
        }
      });

      const text = document.createElement("span");
      text.className = "import-sites-point-label import-land-layer-label";
      text.textContent = `${layer.name} (${layer.geometry || "layer"}, ${layer.count || 0})`;

      const styleWrap = document.createElement("div");
      styleWrap.className = "import-land-layer-style";

      const colorInput = document.createElement("input");
      colorInput.type = "color";
      colorInput.className = "import-land-layer-color";
      colorInput.value = layerStyles.get(layer.name).color;
      colorInput.title = "Layer color";
      colorInput.addEventListener("input", () => {
        const style = layerStyles.get(layer.name);
        style.color = colorInput.value.toLowerCase();
        onStyleChange(layer.name, style);
      });

      const opacityInput = document.createElement("input");
      opacityInput.type = "range";
      opacityInput.className = "import-land-layer-opacity";
      opacityInput.min = "0";
      opacityInput.max = "100";
      opacityInput.step = "1";
      opacityInput.value = String(
        Math.round(layerStyles.get(layer.name).opacity * 100),
      );
      opacityInput.title = "Layer opacity";

      const opacityLabel = document.createElement("span");
      opacityLabel.className = "import-land-layer-opacity-label";
      opacityLabel.textContent = `${opacityInput.value}%`;

      opacityInput.addEventListener("input", () => {
        const style = layerStyles.get(layer.name);
        style.opacity = Number(opacityInput.value) / 100;
        opacityLabel.textContent = `${opacityInput.value}%`;
        onStyleChange(layer.name, style);
      });

      styleWrap.appendChild(colorInput);
      styleWrap.appendChild(opacityInput);
      styleWrap.appendChild(opacityLabel);

      const attrsToggle = document.createElement("button");
      attrsToggle.type = "button";
      attrsToggle.className = "import-land-attrs-toggle";
      attrsToggle.textContent = "Attributes";
      attrsToggle.hidden = !checkbox.checked;

      const attrsPanel = scope.buildLandLayerAttrPanel(
        layer.name,
        gdbPath,
        config,
        onLabelFieldChange,
      );

      attrsToggle.addEventListener("click", () => {
        config.attrsOpen = !config.attrsOpen;
        attrsPanel.hidden = !config.attrsOpen;
      });

      row.appendChild(checkbox);
      row.appendChild(text);
      row.appendChild(styleWrap);
      row.appendChild(attrsToggle);
      row.appendChild(attrsPanel);
      listEl.appendChild(row);
    }
    if (countEl) {
      const n = selectedSet.size;
      countEl.textContent = `${n} of ${layers.length} selected`;
    }
  }


  function applyLandPreviewLayerStyle(
    previewMap,
    sourceName,
    fillId,
    lineId,
    style,
  ) {
    if (!previewMap) return;
    const normalized = scope.normalizeLandLayerStyle(style);
    const lineColor = scope.landLineColorFromFill(normalized.color);
    if (previewMap.getLayer(fillId)) {
      previewMap.setPaintProperty(fillId, "fill-color", normalized.color);
      previewMap.setPaintProperty(fillId, "fill-opacity", normalized.opacity);
      previewMap.setPaintProperty(fillId, "fill-outline-color", lineColor);
    }
    if (previewMap.getLayer(lineId)) {
      previewMap.setPaintProperty(lineId, "line-color", lineColor);
      previewMap.setPaintProperty(
        lineId,
        "line-width",
        C.LAND_PREVIEW_LINE_WIDTH,
      );
    }
  }


  function syncLandPreviewLabelLayer(previewMap, sourceName, showLabels) {
    scope.syncGeoJsonLabelLayer(
      previewMap,
      sourceName,
      scope.landPreviewLabelsLayerId(sourceName),
      showLabels,
      "visible",
    );
  }

  function applyLandPreviewMapData(
    previewMap,
    sourceName,
    fillId,
    lineId,
    data,
    style,
    config,
  ) {
    if (!previewMap) return Promise.resolve();
    const apply = () => {
      try {
        if (!previewMap.getSource(sourceName)) {
          previewMap.addSource(sourceName, { type: "geojson", data });
          for (const spec of scope.landPreviewLayerSpecs(
            sourceName,
            fillId,
            lineId,
            style,
          )) {
            if (!previewMap.getLayer(spec.id)) previewMap.addLayer(spec);
          }
        } else {
          previewMap.getSource(sourceName).setData(data);
        }
        scope.applyLandPreviewLayerStyle(
          previewMap,
          sourceName,
          fillId,
          lineId,
          style,
        );
        scope.syncLandPreviewLabelLayer(previewMap, sourceName, !!config?.labelField);
      } catch (_) {
        /* style/source race */
      }
    };
    if (previewMap.isStyleLoaded()) {
      apply();
      return Promise.resolve();
    }
    return scope.whenPreviewMapReady(previewMap).then(apply);
  }

  function removeLandPreviewLayer(previewMap, prefix, layerName) {
    if (!previewMap) return;
    const sourceName = scope.landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const labelsId = scope.landPreviewLabelsLayerId(sourceName);
    if (previewMap.getLayer(labelsId)) previewMap.removeLayer(labelsId);
    if (previewMap.getLayer(lineId)) previewMap.removeLayer(lineId);
    if (previewMap.getLayer(fillId)) previewMap.removeLayer(fillId);
    if (previewMap.getSource(sourceName)) previewMap.removeSource(sourceName);
  }

  function clearLandPreviewMapLayers(
    previewMap,
    prefix,
    allLayerNames,
    keepLayerNames,
  ) {
    if (!previewMap) return;
    const keep = new Set(keepLayerNames);
    for (const layerName of allLayerNames) {
      if (!keep.has(layerName))
        scope.removeLandPreviewLayer(previewMap, prefix, layerName);
    }
  }

  async function refreshLandPreviewLayerConfig(
    previewMap,
    path,
    layerName,
    sourceIdPrefix,
    layerStyles,
    layerConfigs,
  ) {
    if (!previewMap || !path || !layerName) return;
    const config = layerConfigs?.get(layerName) || scope.defaultLandLayerConfig();
    const prefix = scope.landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = scope.landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const style = scope.normalizeLandLayerStyle(layerStyles.get(layerName));
    await scope.whenPreviewMapReady(previewMap);
    try {
      const geojson = await scope.fetchLandPreviewGeoJson(
        path,
        layerName,
        scope.landPreviewLayerConfig(config),
      );
      if (previewMap.getSource(sourceName)) {
        previewMap.getSource(sourceName).setData(geojson);
        scope.applyLandPreviewLayerStyle(
          previewMap,
          sourceName,
          fillId,
          lineId,
          style,
        );
        scope.syncLandPreviewLabelLayer(previewMap, sourceName, !!config.labelField);
      } else {
        await scope.applyLandPreviewMapData(
          previewMap,
          sourceName,
          fillId,
          lineId,
          geojson,
          style,
          config,
        );
      }
    } catch (_) {
      /* skip */
    }
  }

  async function showLandPreviewLayer(
    previewMap,
    mapEl,
    listEl,
    path,
    layerName,
    sourceIdPrefix,
    layerStyles,
    layerConfigs,
    layerMetaList,
  ) {
    if (!previewMap || !path || !layerName) return;
    const config = layerConfigs?.get(layerName) || scope.defaultLandLayerConfig();
    const prefix = scope.landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = scope.landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const style = scope.normalizeLandLayerStyle(layerStyles.get(layerName));
    await scope.whenPreviewMapReady(previewMap);
    const hasSource = !!previewMap.getSource(sourceName);
    if (hasSource && !config.labelField) {
      scope.applyLandPreviewLayerStyle(previewMap, sourceName, fillId, lineId, style);
      scope.syncLandPreviewLabelLayer(previewMap, sourceName, false);
      return;
    }
    if (!hasSource) scope.setLandLayerRowLoading(listEl, layerName, true);
    try {
      const geojson = await scope.fetchLandPreviewGeoJson(
        path,
        layerName,
        scope.landPreviewLayerConfig(config),
      );
      await scope.applyLandPreviewMapData(
        previewMap,
        sourceName,
        fillId,
        lineId,
        geojson,
        style,
        config,
      );
      const layerMeta = layerMetaList.find((layer) => layer.name === layerName);
      if (layerMeta?.bbox) scope.fitPreviewMapToBboxes(previewMap, [layerMeta.bbox]);
    } catch (_) {
      /* skip layer */
    } finally {
      if (!hasSource) scope.setLandLayerRowLoading(listEl, layerName, false);
      scope.resetLandPreviewMapLoading(mapEl);
    }
  }

  function hideLandPreviewLayer(
    previewMap,
    sourceIdPrefix,
    layerName,
    mapEl,
    listEl,
  ) {
    if (!previewMap || !layerName) return;
    scope.removeLandPreviewLayer(
      previewMap,
      scope.landPreviewMapPrefix(sourceIdPrefix),
      layerName,
    );
    scope.setLandLayerRowLoading(listEl, layerName, false);
    scope.resetLandPreviewMapLoading(mapEl);
  }

  async function updateLandPreviewLayerStyle(
    previewMap,
    layerName,
    sourceIdPrefix,
    layerStyles,
  ) {
    if (!previewMap || !layerName) return;
    const prefix = scope.landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = scope.landPreviewSourceId(prefix, layerName);
    if (!previewMap.getSource(sourceName)) return;
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    scope.applyLandPreviewLayerStyle(
      previewMap,
      sourceName,
      fillId,
      lineId,
      layerStyles.get(layerName),
    );
  }

  async function syncLandPreviewMap(
    previewMap,
    mapEl,
    listEl,
    path,
    selectedNames,
    sourceIdPrefix,
    layerStyles,
    layerConfigs,
    allLayerNames,
    layerMetaList,
  ) {
    if (!previewMap) return;
    const selected = [...selectedNames];
    const prefix = scope.landPreviewMapPrefix(sourceIdPrefix);
    scope.clearLandPreviewMapLayers(previewMap, prefix, allLayerNames, selected);
    await scope.whenPreviewMapReady(previewMap);
    const bboxes = [];
    await Promise.all(
      selected.map(async (layerName) => {
        await scope.showLandPreviewLayer(
          previewMap,
          mapEl,
          listEl,
          path,
          layerName,
          sourceIdPrefix,
          layerStyles,
          layerConfigs,
          layerMetaList,
        );
        const layerMeta = layerMetaList.find(
          (layer) => layer.name === layerName,
        );
        if (layerMeta?.bbox) bboxes.push(layerMeta.bbox);
      }),
    );
    scope.fitPreviewMapToBboxes(previewMap, bboxes);
  }

  function resetImportLandModal() {
    scope.setImportLandError("");
    importLandPreviewLayers = [];
    importLandPreviewPath = "";
    importLandPreviewBusy = false;
    importLandLayerStyles = new Map();
    importLandLayerConfigs = new Map();
    if (importLandPreviewMapEl) {
      scope.resetLandPreviewMapLoading(importLandPreviewMapEl);
    }
    if (importLandGdb) importLandGdb.value = "";
    if (importLandLabel) importLandLabel.value = "";
    if (importLandStatus)
      importLandStatus.textContent =
        "Choose a FileGDB under the project data folder.";
    if (importLandPreviewField) importLandPreviewField.hidden = true;
    if (importLandLayerList) importLandLayerList.innerHTML = "";
    if (importLandSave) importLandSave.disabled = true;
    scope.destroyImportLandPreviewMap();
  }

  function fillImportLandGdbSelect(paths) {
    if (!importLandGdb) return;
    importLandGdb.innerHTML = '<option value="">Select a GDB…</option>';
    for (const path of paths) {
      const opt = document.createElement("option");
      opt.value = path;
      opt.textContent = path;
      importLandGdb.appendChild(opt);
    }
  }

  async function populateImportLandGdbSelect() {
    if (!importLandGdb) return;
    if (landDataGdbPaths.length) {
      scope.fillImportLandGdbSelect(landDataGdbPaths);
      return;
    }
    importLandGdb.innerHTML = '<option value="">Loading GDB list…</option>';
    importLandGdb.disabled = true;
    try {
      const resp = await fetch(scope.landDataGdbsUrl());
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.fillImportLandGdbSelect([]);
        return;
      }
      landDataGdbPaths = Array.isArray(payload.paths) ? payload.paths : [];
      scope.fillImportLandGdbSelect(landDataGdbPaths);
    } catch (_) {
      scope.fillImportLandGdbSelect([]);
    } finally {
      importLandGdb.disabled = false;
    }
  }

  async function previewImportLandPath(path) {
    if (!path) {
      scope.resetImportLandModal();
      return;
    }
    scope.setImportLandError("");
    importLandPreviewBusy = true;
    if (importLandSave) importLandSave.disabled = true;
    if (importLandStatus) importLandStatus.textContent = "Loading layers…";
    try {
      const resp = await fetch(scope.landImportPreviewApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setImportLandError(payload.error || `Preview failed (${resp.status})`);
        if (importLandPreviewField) importLandPreviewField.hidden = true;
        return;
      }
      importLandPreviewPath = path;
      importLandPreviewLayers = Array.isArray(payload.layers)
        ? payload.layers
        : [];
      importLandLayerStyles = new Map();
      importLandLayerConfigs = new Map();
      if (importLandPreviewField) importLandPreviewField.hidden = false;
      if (importLandStatus) {
        importLandStatus.textContent = `${importLandPreviewLayers.length} layer(s) in ${path}`;
      }
      const selected = new Set();
      if (importLandPreviewLayers.length === 1) {
        selected.add(importLandPreviewLayers[0].name);
      }
      importLandPreviewMap = scope.ensureLandPreviewMap(
        importLandPreviewMapEl,
        importLandPreviewMap,
      );
      scope.renderLandLayerChecklist(importLandLayerList, importLandPreviewLayers, {
        selected,
        layerStyles: importLandLayerStyles,
        layerConfigs: importLandLayerConfigs,
        gdbPath: importLandPreviewPath,
        countEl: importLandListCount,
        onToggle: (name, checked) => {
          if (checked) selected.add(name);
          else selected.delete(name);
          if (importLandListCount) {
            importLandListCount.textContent = `${selected.size} of ${importLandPreviewLayers.length} selected`;
          }
          if (importLandSave) importLandSave.disabled = selected.size === 0;
          if (checked) {
            void scope.showLandPreviewLayer(
              importLandPreviewMap,
              importLandPreviewMapEl,
              importLandLayerList,
              importLandPreviewPath,
              name,
              "import",
              importLandLayerStyles,
              importLandLayerConfigs,
              importLandPreviewLayers,
            );
          } else {
            scope.hideLandPreviewLayer(
              importLandPreviewMap,
              "import",
              name,
              importLandPreviewMapEl,
              importLandLayerList,
            );
          }
        },
        onStyleChange: (name) => {
          if (selected.has(name)) {
            void scope.updateLandPreviewLayerStyle(
              importLandPreviewMap,
              name,
              "import",
              importLandLayerStyles,
            );
          }
        },
        onLabelFieldChange: (name) => {
          if (!selected.has(name)) return;
          void scope.refreshLandPreviewLayerConfig(
            importLandPreviewMap,
            importLandPreviewPath,
            name,
            "import",
            importLandLayerStyles,
            importLandLayerConfigs,
          );
        },
      });
      if (importLandSave) importLandSave.disabled = selected.size === 0;
      if (importLandSelectAll)
        importLandSelectAll.disabled = importLandPreviewLayers.length === 0;
      if (importLandClearAll)
        importLandClearAll.disabled = importLandPreviewLayers.length === 0;
      await scope.whenPreviewMapReady(importLandPreviewMap);
      requestAnimationFrame(() => importLandPreviewMap?.resize());
      for (const name of selected) {
        await scope.showLandPreviewLayer(
          importLandPreviewMap,
          importLandPreviewMapEl,
          importLandLayerList,
          importLandPreviewPath,
          name,
          "import",
          importLandLayerStyles,
          importLandLayerConfigs,
          importLandPreviewLayers,
        );
      }
      scope.fitPreviewMapToBboxes(
        importLandPreviewMap,
        importLandPreviewLayers.map((layer) => layer.bbox).filter(Boolean),
      );
    } catch (_) {
      scope.setImportLandError("Could not reach server.");
    } finally {
      importLandPreviewBusy = false;
    }
  }

  async function openImportLandModal() {
    if (!importLandModal) return;
    setAddPlacementMode(null);
    setEntityPanelOpen(true);
    scope.setEntityTab("land");
    scope.resetImportLandModal();
    await customElements.whenDefined("wa-dialog");
    importLandModal.open = true;
    void scope.populateImportLandGdbSelect();
  }

  function collectSelectedLandLayers(listEl) {
    const selected = [];
    if (!listEl) return selected;
    for (const input of listEl.querySelectorAll(
      'input[type="checkbox"][data-layer-name]',
    )) {
      if (input.checked && input.dataset.layerName)
        selected.push(input.dataset.layerName);
    }
    return selected;
  }

  async function saveImportLandModal() {
    const selected = scope.collectSelectedLandLayers(importLandLayerList);
    if (!importLandPreviewPath || !selected.length) {
      scope.setImportLandError("Select a GDB and at least one layer.");
      return;
    }
    scope.setImportLandError("");
    if (importLandSave) importLandSave.disabled = true;
    const prevAoiDigest = landAoiDigest;
    try {
      const body = {
        path: importLandPreviewPath,
        layers: scope.collectSelectedLayerPayloads(
          importLandLayerList,
          importLandLayerStyles,
          importLandLayerConfigs,
        ),
      };
      if (importLandLabel?.value.trim())
        body.label = importLandLabel.value.trim();
      const resp = await fetch(scope.landImportApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setImportLandError(payload.error || `Import failed (${resp.status})`);
        return;
      }
      if (importLandModal) importLandModal.open = false;
      await scope.reloadLandSources({ refreshMap: false });
      const source = payload.source;
      if (source?.id && Array.isArray(source.layers)) {
        for (const rawLayer of source.layers) {
          const spec = scope.normalizeRegisteredLayer(rawLayer);
          scope.setLandLayerVisible(source.id, spec.key, true);
        }
        scope.renderLandPanel();
        if (landAoiDigest !== prevAoiDigest) {
          scope.clearAllLandLayerGeoJsonCache();
          await scope.reloadClippedLandLayers();
        } else {
          await Promise.all(
            source.layers.map((rawLayer) => {
              const spec = scope.normalizeRegisteredLayer(rawLayer);
              return scope.refreshLandMapLayer(source.id, spec.key);
            }),
          );
        }
      } else {
        await scope.refreshLandMapLayers();
      }
    } catch (_) {
      scope.setImportLandError("Could not reach server.");
    } finally {
      if (importLandSave) importLandSave.disabled = false;
    }
  }

  async function openEditLandModal(sourceId) {
    const source = landSources.find((s) => s.id === sourceId);
    if (!source || !editLandModal) return;
    editLandSourceId = sourceId;
    editLandPreviewPath = source.path;
    scope.setEditLandError("");
    if (editLandSourcePath) editLandSourcePath.textContent = source.path;
    if (editLandLabel) editLandLabel.value = source.label || source.id;
    if (editLandSave) editLandSave.disabled = true;
    try {
      const resp = await fetch(scope.landImportPreviewApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: source.path }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setEditLandError(payload.error || `Preview failed (${resp.status})`);
        return;
      }
      editLandPreviewLayers = Array.isArray(payload.layers)
        ? payload.layers
        : [];
      editLandLayerStyles = new Map();
      editLandLayerConfigs = new Map();
      const registeredByName = new Map();
      for (const rawLayer of source.layers || []) {
        const spec = scope.normalizeRegisteredLayer(rawLayer);
        registeredByName.set(spec.name, rawLayer);
        editLandLayerConfigs.set(
          spec.name,
          scope.configFromRegisteredLayer(rawLayer),
        );
        scope.ensureLandLayerStyleState(
          editLandLayerStyles,
          spec.name,
          scope.flatStyleFromLayerSpec(spec),
        );
      }
      const allLayerNames = editLandPreviewLayers.map((layer) => layer.name);
      const selected = new Set([...registeredByName.keys()]);
      const refreshEditPreview = () => {
        void scope.syncLandPreviewMap(
          editLandPreviewMap,
          editLandPreviewMapEl,
          editLandLayerList,
          editLandPreviewPath,
          selected,
          "edit",
          editLandLayerStyles,
          editLandLayerConfigs,
          allLayerNames,
          editLandPreviewLayers,
        );
      };
      scope.renderLandLayerChecklist(editLandLayerList, editLandPreviewLayers, {
        selected,
        layerStyles: editLandLayerStyles,
        layerConfigs: editLandLayerConfigs,
        gdbPath: editLandPreviewPath,
        countEl: editLandListCount,
        onToggle: (name, checked) => {
          if (checked) selected.add(name);
          else selected.delete(name);
          if (editLandListCount) {
            editLandListCount.textContent = `${selected.size} of ${editLandPreviewLayers.length} selected`;
          }
          if (editLandSave) editLandSave.disabled = selected.size === 0;
          if (checked) {
            void scope.showLandPreviewLayer(
              editLandPreviewMap,
              editLandPreviewMapEl,
              editLandLayerList,
              editLandPreviewPath,
              name,
              "edit",
              editLandLayerStyles,
              editLandLayerConfigs,
              editLandPreviewLayers,
            );
          } else {
            scope.hideLandPreviewLayer(
              editLandPreviewMap,
              "edit",
              name,
              editLandPreviewMapEl,
              editLandLayerList,
            );
          }
        },
        onStyleChange: (name) => {
          if (selected.has(name)) {
            void scope.updateLandPreviewLayerStyle(
              editLandPreviewMap,
              name,
              "edit",
              editLandLayerStyles,
            );
          }
        },
        onLabelFieldChange: (name) => {
          if (!selected.has(name)) return;
          void scope.refreshLandPreviewLayerConfig(
            editLandPreviewMap,
            editLandPreviewPath,
            name,
            "edit",
            editLandLayerStyles,
            editLandLayerConfigs,
          );
        },
      });
      editLandPreviewMap = scope.ensureLandPreviewMap(
        editLandPreviewMapEl,
        editLandPreviewMap,
      );
      if (editLandSave) editLandSave.disabled = selected.size === 0;
      editLandPreviewRefresh = refreshEditPreview;
      if (selected.size) refreshEditPreview();
      await customElements.whenDefined("wa-dialog");
      editLandModal.open = true;
    } catch (_) {
      scope.setEditLandError("Could not reach server.");
    }
  }

  async function saveEditLandModal() {
    if (!editLandSourceId) return;
    const selected = scope.collectSelectedLandLayers(editLandLayerList);
    if (!selected.length) {
      scope.setEditLandError("Select at least one layer.");
      return;
    }
    scope.setEditLandError("");
    if (editLandSave) editLandSave.disabled = true;
    const prevAoiDigest = landAoiDigest;
    const prevSource = landSources.find((s) => s.id === editLandSourceId);
    const prevKeys = new Set(
      (prevSource?.layers || []).map(
        (layer) => scope.normalizeRegisteredLayer(layer).key,
      ),
    );
    try {
      const body = {
        layers: scope.collectSelectedLayerPayloads(
          editLandLayerList,
          editLandLayerStyles,
          editLandLayerConfigs,
        ),
        label: editLandLabel?.value.trim() || editLandSourceId,
      };
      const resp = await fetch(scope.landSourceApiUrl(editLandSourceId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setEditLandError(payload.error || `Save failed (${resp.status})`);
        return;
      }
      if (editLandModal) editLandModal.open = false;
      const updated =
        payload.source || landSources.find((s) => s.id === editLandSourceId);
      const newKeys = new Set(
        (updated?.layers || []).map(
          (layer) => scope.normalizeRegisteredLayer(layer).key,
        ),
      );
      for (const key of prevKeys) {
        if (!newKeys.has(key)) {
          scope.removeLandMapLayer(editLandSourceId, key);
          landVisible.delete(scope.landLayerKey(editLandSourceId, key));
          landLabelsVisible.delete(scope.landLayerKey(editLandSourceId, key));
          scope.clearLandLayerGeoJsonCacheForLayer(editLandSourceId, key);
        }
      }
      for (const key of newKeys) {
        if (!prevKeys.has(key))
          scope.setLandLayerVisible(editLandSourceId, key, true);
      }
      await scope.reloadLandSources({ refreshMap: false });
      if (landAoiDigest !== prevAoiDigest) {
        scope.clearAllLandLayerGeoJsonCache();
        await scope.reloadClippedLandLayers();
      } else {
        await Promise.all(
          [...newKeys].map((key) => scope.refreshLandMapLayer(editLandSourceId, key)),
        );
      }
    } catch (_) {
      scope.setEditLandError("Could not reach server.");
    } finally {
      if (editLandSave) editLandSave.disabled = false;
    }
  }




  function siteLinksLinePaint() {
    return {
      "line-color": [
        "case",
        ["get", "manual"],
        "#0d9488",
        ["==", ["get", "strength"], "weak"],
        "#ef4444",
        "#4a6cf7",
      ],
      "line-width": 2.5,
      "line-opacity": 0.85,
      "line-dasharray": [
        "case",
        ["==", ["get", "strength"], "weak"],
        ["literal", [4, 3]],
        ["literal", [1, 0]],
      ],
    };
  }

  function addSiteLinksLayer(geojson) {
    const filtered = scope.filterSiteLinksGeoJson(geojson);
    if (!filtered || !filtered.features || !filtered.features.length) {
      if (map.getLayer(C.LINKS_LABELS_LAYER)) map.removeLayer(C.LINKS_LABELS_LAYER);
      if (map.getLayer(C.LINKS_LAYER)) map.removeLayer(C.LINKS_LAYER);
      if (map.getSource(C.LINKS_SOURCE)) map.removeSource(C.LINKS_SOURCE);
      raiseSiteLayers();
      return;
    }
    const labeled = linksGeoJsonWithLabels(filtered);
    const linkVisibility = showSiteLinks ? "visible" : "none";
    const linePaint = siteLinksLinePaint();
    if (map.getSource(C.LINKS_SOURCE)) {
      map.getSource(C.LINKS_SOURCE).setData(labeled);
      if (map.getLayer(C.LINKS_LAYER)) {
        for (const [key, val] of Object.entries(linePaint)) {
          map.setPaintProperty(C.LINKS_LAYER, key, val);
        }
      }
      setSiteLinksVisible(showSiteLinks);
      raiseSiteLayers();
      return;
    }
    map.addSource(C.LINKS_SOURCE, { type: "geojson", data: labeled });
    map.addLayer(
      {
        id: C.LINKS_LAYER,
        type: "line",
        source: C.LINKS_SOURCE,
        paint: linePaint,
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: linkVisibility,
        },
      },
      C.SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(C.LINKS_LABELS_LAYER, C.LINKS_SOURCE, linkVisibility),
      C.SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  async function loadSiteLinks() {
    try {
      const resp = await fetch(linksApiUrl());
      if (!resp.ok) {
        syncWarmPriorities();
        return;
      }
      const payload = await resp.json();
      applySiteLinksPayload(payload);
      syncWarmPriorities();
    } catch (_) {
      syncWarmPriorities();
    }
  }

  const singleSiteLinksInflight = new Set();

  /** Fetch one site's links (existing footprints only) and merge into the mesh. */

  function scheduleSingleSiteLinksRetry(slug) {
    if (!slug || siteOutboundLinksReady.has(slug)) return;
    window.setTimeout(() => {
      if (scope.sitePinSpinning(slug) && !siteOutboundLinksReady.has(slug)) {
        void loadSingleSiteLinks(slug);
      }
    }, 5000);
  }

  function mergeSingleSiteLinks(slug, payload) {
    if (!payload) return;
    const features = payload.geojson?.features;
    if (!Array.isArray(features)) {
      if (payload.outbound_ready !== false) {
        markSiteOutboundLinksReady(slug);
      }
      return;
    }
    const base =
      siteLinksPayload &&
      siteLinksPayload.geojson &&
      Array.isArray(siteLinksPayload.geojson.features)
        ? siteLinksPayload
        : {
            status: "pending",
            links: [],
            geojson: { type: "FeatureCollection", features: [] },
          };
    const untouched = (props) => props.a !== slug && props.b !== slug;
    const mergedFeatures = base.geojson.features
      .filter((f) => untouched(f.properties || {}))
      .concat(features);
    const links = (Array.isArray(base.links) ? base.links : [])
      .filter((r) => untouched(r || {}))
      .concat(Array.isArray(payload.links) ? payload.links : []);
    siteLinksPayload = {
      ...base,
      links,
      geojson: { type: "FeatureCollection", features: mergedFeatures },
    };
    addSiteLinksLayer(siteLinksPayload.geojson);
    if (payload.outbound_ready !== false) {
      markSiteOutboundLinksReady(slug);
    }
    if (selectedSlug) scope.renderPanel(siteBySlug.get(selectedSlug));
  }


  function viewshedOverlaySlugs() {
    return [
      ...sites.map((site) => site.slug),
      C.DRAFT_VIEWSHED_SLUG,
      ...editCoordHistory.map((entry) => scope.editHistorySlug(entry.id)),
      ...seekHopCoordViewshedSlugs,
    ];
  }

  function viewshedLayerInsertBefore() {
    return map.getLayer(C.SITES_CIRCLE) ? C.SITES_CIRCLE : undefined;
  }

  /** Land layer reorder moves fills to the top; keep RF overlays above land, below sites. */
  function raiseViewshedLayers() {
    if (!mapReady) return;
    const beforeId = scope.viewshedLayerInsertBefore();
    if (!beforeId) return;
    for (const slug of scope.viewshedOverlaySlugs()) {
      const layerId = scope.viewshedLayerId(slug);
      if (!map.getLayer(layerId)) continue;
      try {
        map.moveLayer(layerId, beforeId);
      } catch (_) {
        /* layer may be mid-remove */
      }
    }
  }


  function basemapRasterOpacityForTerrain() {
    if (scope.usesSkadiAnalysisDem()) return 0;
    return 0.9;
  }

  function showTerrainOverlays() {
    ensureTerrainSource();
    ensureHillshadeLayer();
    map.setTerrain({ source: C.TERRAIN_SOURCE, exaggeration: 1.35 });
    if (map.getLayer("basemap")) {
      map.setPaintProperty(
        "basemap",
        "raster-opacity",
        scope.basemapRasterOpacityForTerrain(),
      );
    }
    raiseSiteLayers();
  }

  function hideTerrainOverlays() {
    map.setTerrain(null);
    removeTerrainSource();
    if (map.getLayer("basemap")) {
      map.setPaintProperty("basemap", "raster-opacity", 1);
    }
  }

  function syncTerrainFromPitch() {
    if (!mapReady) return;
    const pitch = map.getPitch();
    if (!terrainActive && pitch >= C.PITCH_TERRAIN_ON) {
      terrainActive = true;
      scope.showTerrainOverlays();
    } else if (terrainActive && pitch <= C.PITCH_TERRAIN_OFF) {
      terrainActive = false;
      scope.hideTerrainOverlays();
    }
  }

  function ensureBasemapReference(bm) {
    if (!bm.referenceTiles) return;
    if (!map.getSource(C.BASEMAP_REFERENCE_SOURCE)) {
      map.addSource(C.BASEMAP_REFERENCE_SOURCE, {
        type: "raster",
        tiles: bm.referenceTiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      });
      map.addLayer(
        {
          id: C.BASEMAP_REFERENCE_LAYER,
          type: "raster",
          source: C.BASEMAP_REFERENCE_SOURCE,
        },
        map.getLayer(C.SITES_CIRCLE) ? C.SITES_CIRCLE : undefined,
      );
    } else {
      map.getSource(C.BASEMAP_REFERENCE_SOURCE).setTiles(bm.referenceTiles);
    }
    raiseSiteLayers();
  }

  function removeBasemapReference() {
    if (map.getLayer(C.BASEMAP_REFERENCE_LAYER))
      map.removeLayer(C.BASEMAP_REFERENCE_LAYER);
    if (map.getSource(C.BASEMAP_REFERENCE_SOURCE))
      map.removeSource(C.BASEMAP_REFERENCE_SOURCE);
  }

  function viewshedSourceId(slug) {
    return `viewshed-${slug}`;
  }

  function viewshedLayerId(slug) {
    return `viewshed-${slug}-raster`;
  }


  function setViewshedOpacity(opacity) {
    viewshedOpacity = Math.max(0, Math.min(1, opacity));
    scope.syncOpacitySlider();
    scope.applyViewshedOpacityToAllLayers();
  }

  function viewshedSimQueryParams() {
    const params = new URLSearchParams();
    params.set("radius_km", String(viewshedRadiusKm));
    params.set("quality", String(viewshedQuality));
    return params;
  }

  function viewshedPreviewSimQueryParams() {
    const params = new URLSearchParams();
    params.set("radius_km", String(viewshedRadiusKm));
    params.set("quality", String(C.VIEWSHED_PREVIEW_QUALITY));
    return params;
  }

  function projectEventsUrl() {
    return `/api/p/${projectSlug}/events`;
  }

  function viewshedWarmUrl(siteSlug) {
    const params = scope.viewshedSimQueryParams();
    return `/api/p/${projectSlug}/viewsheds/${siteSlug}/warm?${params}`;
  }

  function viewshedPrefetchWarmUrl(lat, lon, { preview = true } = {}) {
    const params = preview
      ? scope.viewshedPreviewSimQueryParams()
      : scope.viewshedSimQueryParams();
    params.set("lat", String(lat));
    params.set("lon", String(lon));
    return `/api/p/${projectSlug}/viewsheds/prefetch/warm?${params}`;
  }

  let serveEventsSource = null;
  const viewshedPendingEpoch = new Map();

  function reconcilePendingViewsheds() {
    syncWarmPriorities();
  }

  function connectProjectEvents() {
    if (serveEventsSource) {
      serveEventsSource.close();
      serveEventsSource = null;
    }
    serveEventsSource = new EventSource(scope.projectEventsUrl());
    serveEventsSource.addEventListener("hello", () => {
      scope.reconcilePendingViewsheds();
    });
    serveEventsSource.addEventListener("viewshed", (ev) => {
      try {
        scope.handleViewshedEvent(JSON.parse(ev.data));
      } catch (_) {
        /* ignore malformed SSE payload */
      }
    });
    serveEventsSource.addEventListener("links", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (!data) return;
        applySiteLinksPayload(data);
      } catch (_) {
        /* ignore malformed SSE payload */
      }
    });
  }

  function viewshedAtTarget(vs) {
    if (vs?.at_target === false) return false;
    const raster = Number(vs?.raster_dimension);
    const target = Number(vs?.raster_target);
    if (Number.isFinite(raster) && Number.isFinite(target)) {
      return raster >= target;
    }
    return true;
  }

  function finalizeViewshedReady(vs) {
    if (vs?.slug) {
      setSitePinProgress(vs.slug, {
        raster: Number(vs.raster_dimension) || 0,
        target: Number(vs.raster_target) || 0,
        step: Number(vs.ladder_step) || 0,
        total: Number(vs.ladder_total) || 0,
      });
    }
    if (!scope.viewshedAtTarget(vs)) {
      scope.updatePinOverlays();
      return;
    }
    markSiteViewshedReady(vs.slug);
    if (vs.slug === C.DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      scope.syncCreateViewshedCheckbox();
      scope.syncEditViewshedCheckbox();
    }
  }

  function handleViewshedReady(vs, epoch) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) {
      if (vs?.slug) scope.clearViewshedLoadingState(vs.slug);
      return;
    }
    const pendingEpoch = viewshedPendingEpoch.get(vs.slug);
    if (epoch != null && pendingEpoch != null && pendingEpoch !== epoch) return;
    if (String(vs.slug).startsWith("_edit_hist_")) {
      scope.renderEditCoordHistory();
    }
    scope.addViewshedLayer(vs);
    scope.finalizeViewshedReady(vs);
    if (!isEphemeralViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug);
    } else {
      markSiteOutboundLinksReady(vs.slug);
    }
  }

  function acceptViewshedOverlay(vs) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) return;
    if (isSiteMapHidden(vs.slug) || !isViewshedVisible(vs.slug)) return;
    scope.addViewshedLayer(vs);
    scope.finalizeViewshedReady(vs);
    if (!isEphemeralViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug);
    } else {
      markSiteOutboundLinksReady(vs.slug);
    }
  }

  function routeDraftViewshedToSeekHops(data) {
    if (data.slug !== C.DRAFT_VIEWSHED_SLUG || data.status !== "ready")
      return false;
    if (data.lat == null || data.lon == null) return false;
    let routed = false;
    for (const [slug, coords] of seekHopCoordViewshedCoords) {
      if (!viewshedPendingEpoch.has(slug)) continue;
      if (
        Math.abs(coords.lat - data.lat) > 1e-5 ||
        Math.abs(coords.lon - data.lon) > 1e-5
      )
        continue;
      const epoch = viewshedPendingEpoch.get(slug);
      scope.handleViewshedReady({ ...data, slug }, epoch);
      routed = true;
    }
    return routed;
  }

  function handleViewshedEvent(data) {
    if (!data || !data.slug) return;
    if (data.status === "ready") {
      if (data.raster_dimension != null || data.ladder_step != null) {
        setSitePinProgress(data.slug, {
          raster: Number(data.raster_dimension) || 0,
          target: Number(data.raster_target) || 0,
          step: Number(data.ladder_step) || 0,
          total: Number(data.ladder_total) || 0,
        });
        scope.updatePinOverlays();
      }
      if (scope.routeDraftViewshedToSeekHops(data)) return;
      const epoch = viewshedPendingEpoch.get(data.slug);
      if (epoch != null) {
        scope.handleViewshedReady(data, epoch);
      }
      if (!siteViewshedReady.has(data.slug)) {
        scope.acceptViewshedOverlay(data);
      }
      return;
    }
    const epoch = viewshedPendingEpoch.get(data.slug);
    if (epoch == null) return;
    if (data.status === "error") {
      viewshedPendingEpoch.delete(data.slug);
      viewshedLoading.delete(data.slug);
      clearSitePinProgress(data.slug);
      if (data.slug === C.DRAFT_VIEWSHED_SLUG) {
        draftViewshedLoading = false;
        scope.syncCreateViewshedCheckbox();
        scope.syncEditViewshedCheckbox();
      }
      scope.updatePinOverlays();
      if (data.slug === selectedSlug) scope.syncViewshedCheckbox();
    }
  }

  function updatePinOverlays() {
    if (!mapReady) return;
    try {
      const active = new Set();
      for (const site of sites) {
        if (!scope.sitePinSpinning(site.slug)) continue;
        if (!coordsUsableForMarker(site.lon, site.lat)) continue;
        active.add(site.slug);
        renderPinLoadOverlay(site.slug, site.lon, site.lat);
      }
      if (
        viewshedLoading.has(C.DRAFT_VIEWSHED_SLUG) &&
        draftPlacementLat != null &&
        draftPlacementLon != null
      ) {
        active.add(C.DRAFT_VIEWSHED_SLUG);
        renderPinLoadOverlay(
          C.DRAFT_VIEWSHED_SLUG,
          draftPlacementLon,
          draftPlacementLat,
        );
      }
      for (const entry of editCoordHistory) {
        const slug = scope.editHistorySlug(entry.id);
        if (!viewshedLoading.has(slug) || !entry.visible) continue;
        if (!coordsUsableForMarker(entry.lon, entry.lat)) continue;
        active.add(slug);
        renderPinLoadOverlay(slug, entry.lon, entry.lat);
      }
      for (const slug of seekHopCoordViewshedSlugs) {
        if (!viewshedLoading.has(slug)) continue;
        const coords = seekHopCoordViewshedCoords.get(slug);
        if (!coords || !coordsUsableForMarker(coords.lon, coords.lat)) continue;
        active.add(slug);
        renderPinLoadOverlay(slug, coords.lon, coords.lat);
      }
      if (seekScanning && seekSessionActive()) {
        const from = seekCurrentFrom();
        if (from && coordsUsableForMarker(from.lon, from.lat)) {
          active.add(C.SEEK_SCAN_PIN);
          const marker = ensurePinLoadMarker(C.SEEK_SCAN_PIN, "spinner");
          if (setMarkerLngLatSafe(marker, from.lon, from.lat)) {
            marker.getElement().hidden = false;
          }
        }
      }
      for (const [slug] of pinLoadMarkers) {
        if (!active.has(slug)) hidePinLoadMarker(slug);
      }
    } catch (_) {
      /* MapLibre can throw during resize; never block site save/UI */
    }
  }

  function waitForMapIdle(maxMs = 120) {
    return new Promise((resolve) => {
      if (!mapReady) {
        window.setTimeout(resolve, 0);
        return;
      }
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      map.once("idle", finish);
      window.setTimeout(finish, maxMs);
    });
  }


  let viewshedLoadEpoch = 0;

  function bumpViewshedLoadEpoch() {
    viewshedLoadEpoch += 1;
  }


  function viewshedPrefetchMetaUrl(lat, lon) {
    const params = scope.viewshedPreviewSimQueryParams();
    params.set("lat", String(lat));
    params.set("lon", String(lon));
    return `/api/p/${projectSlug}/viewsheds/prefetch?${params}`;
  }

  function clearViewshedLoadingState(slug) {
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
    clearSitePinProgress(slug);
    if (slug === C.DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      scope.syncCreateViewshedCheckbox();
      scope.syncEditViewshedCheckbox();
    }
    scope.updatePinOverlays();
    if (slug === selectedSlug) scope.syncViewshedCheckbox();
  }

  async function tryLoadViewshedFromCache(slug, coords) {
    try {
      const resp = await fetch(scope.viewshedMetaUrl(slug, coords || {}));
      if (!resp.ok) return false;
      const overlay = await resp.json();
      if (overlay.url && overlay.coordinates) {
        scope.acceptViewshedOverlay({ ...overlay, slug, status: "ready" });
        return true;
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false;
  }

  function viewshedIndexUrl() {
    const params = scope.viewshedSimQueryParams();
    return `/api/p/${projectSlug}/viewsheds/index?${params}`;
  }

  async function fetchOutboundLinksParallel(slugs) {
    if (!slugs.length) return;
    let cursor = 0;
    async function worker() {
      while (cursor < slugs.length) {
        const slug = slugs[cursor];
        cursor += 1;
        await loadSingleSiteLinks(slug);
      }
    }
    const workers = Math.min(OUTBOUND_LINKS_PARALLEL, slugs.length);
    await Promise.all(Array.from({ length: workers }, () => scope.worker()));
  }


  function scheduleViewshedLoad(site) {
    removeViewshedLayer(site.slug);
    scope.resetSiteProgress(site.slug);
    viewshedLoading.add(site.slug);
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch);
    scope.updatePinOverlays();
    if (site.slug === selectedSlug) scope.syncViewshedCheckbox();
    const priority =
      site.slug === selectedSlug
        ? WARM_PRIORITY_INTERACTIVE
        : WARM_PRIORITY_VIEWPORT;
    void bumpWarmPriorities([site.slug], priority);
    void scope.tryLoadViewshedFromCache(site.slug);
  }


  function sitesPrefetchUrl(lat, lon, excludeSite) {
    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    if (excludeSite) params.set("exclude_site", excludeSite);
    return `/api/p/${projectSlug}/sites/prefetch?${params}`;
  }

  function filterEditSitePrefetchPayload(payload) {
    if (!payload || !editSlug) return payload;
    const links = Array.isArray(payload.links)
      ? payload.links.filter((row) => row.slug !== editSlug)
      : payload.links;
    let linksGeojson = payload.links_geojson;
    if (linksGeojson && Array.isArray(linksGeojson.features)) {
      linksGeojson = {
        ...linksGeojson,
        features: linksGeojson.features.filter(
          (feature) => (feature.properties || {}).slug !== editSlug,
        ),
      };
    }
    return { ...payload, links, links_geojson: linksGeojson };
  }

  function editSiteCopyCoords(excludeLat, excludeLon) {
    const out = [];
    if (editSnapshot) {
      out.push({
        lat: Number(editSnapshot.lat),
        lon: Number(editSnapshot.lon),
      });
    }
    const current = scope.readEditCoords();
    if (current) out.push({ lat: current.lat, lon: current.lon });
    for (const entry of editCoordHistory) {
      out.push({ lat: entry.lat, lon: entry.lon });
    }
    if (excludeLat == null || excludeLon == null) return out;
    return out.filter(
      (c) => !scope.coordsMatchPair(c.lat, c.lon, excludeLat, excludeLon),
    );
  }

  function filterHistoryEntryLinksGeojson(geojson, entryLat, entryLon) {
    if (!geojson || !Array.isArray(geojson.features)) {
      return { type: "FeatureCollection", features: [] };
    }
    const copyCoords = scope.editSiteCopyCoords(entryLat, entryLon);
    const features = geojson.features.filter((feature) => {
      const props = feature.properties || {};
      if (editSlug && props.slug === editSlug) return false;
      const geom = feature.geometry;
      if (
        !geom ||
        geom.type !== "LineString" ||
        !Array.isArray(geom.coordinates)
      )
        return false;
      for (const pt of geom.coordinates) {
        if (!Array.isArray(pt) || pt.length < 2) continue;
        const lon = Number(pt[0]);
        const lat = Number(pt[1]);
        if (scope.coordsMatchPair(lat, lon, entryLat, entryLon)) continue;
        for (const c of copyCoords) {
          if (scope.coordsMatchPair(lat, lon, c.lat, c.lon)) return false;
        }
      }
      return true;
    });
    return { type: "FeatureCollection", features };
  }

  let draftViewshedLoading = false;
  let placementPrefetchGen = 0;

  function syncCreateViewshedCheckbox() {
    if (!sitePanelCreateViewshed || !createMode) return;
    sitePanelCreateViewshed.checked = isViewshedVisible(C.DRAFT_VIEWSHED_SLUG);
    const hint = document.getElementById("site-panel-create-viewshed-hint");
    if (hint) hint.textContent = draftViewshedLoading ? "Loading…" : "";
  }

  function removeDraftViewshed() {
    try {
      const sourceId = scope.viewshedSourceId(C.DRAFT_VIEWSHED_SLUG);
      const layerId = scope.viewshedLayerId(C.DRAFT_VIEWSHED_SLUG);
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
    } catch (_) {
      /* map may be mid-resize */
    }
    viewshedPendingEpoch.delete(C.DRAFT_VIEWSHED_SLUG);
    viewshedLoading.delete(C.DRAFT_VIEWSHED_SLUG);
    draftViewshedLoading = false;
    draftPlacementLat = null;
    draftPlacementLon = null;
    scope.updatePinOverlays();
  }



  function clearDraftViewshedLoading() {
    viewshedPendingEpoch.delete(C.DRAFT_VIEWSHED_SLUG);
    viewshedLoading.delete(C.DRAFT_VIEWSHED_SLUG);
    draftViewshedLoading = false;
    scope.updatePinOverlays();
    scope.syncCreateViewshedCheckbox();
    scope.syncEditViewshedCheckbox();
  }

  async function tryLoadCoordViewshedFromCache(slug, lat, lon) {
    try {
      const resp = await fetch(scope.viewshedPrefetchMetaUrl(lat, lon));
      if (!resp.ok) return false;
      const overlay = await resp.json();
      if (overlay.url && overlay.coordinates) {
        scope.acceptViewshedOverlay({ ...overlay, slug, status: "ready" });
        return true;
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false;
  }

  async function tryLoadDraftViewshedFromCache(lat, lon) {
    return scope.tryLoadCoordViewshedFromCache(C.DRAFT_VIEWSHED_SLUG, lat, lon);
  }



  function syncEntityPanelViewshedToggle(slug) {
    if (!entityPanelSitesList) return;
    const row = entityPanelSitesList.querySelector(
      `[data-site-slug="${CSS.escape(slug)}"]`,
    );
    if (!row) return;
    const btn = row.querySelector(".entity-panel__viewshed-toggle");
    if (!btn) return;
    const visible = isViewshedVisible(slug);
    const hidden = isSiteHidden(slug);
    btn.disabled = hidden;
    btn.setAttribute("aria-pressed", visible ? "true" : "false");
    btn.classList.toggle("entity-panel__action--active", visible);
  }

  function syncSitePanelViewshedToggle(slug) {
    if (!sitePanelViewshedToggle || slug !== selectedSlug) return;
    if (sitePanelView && sitePanelView.hidden) return;
    const visible = isViewshedVisible(slug);
    sitePanelViewshedToggle.setAttribute(
      "aria-pressed",
      visible ? "true" : "false",
    );
    sitePanelViewshedToggle.classList.toggle(
      "site-panel__action--active",
      visible,
    );
    if (sitePanelViewshedHint) {
      sitePanelViewshedHint.textContent = scope.sitePinSpinning(slug)
        ? sitePinProgressLabel(slug)
        : "";
    }
  }

  /** Mirror viewshedVisible state into every checkbox bound to this site slug. */
  function syncViewshedUiForSlug(slug) {
    if (!slug) return;
    scope.syncEntityPanelViewshedToggle(slug);
    scope.syncSitePanelViewshedToggle(slug);
    if (editMode && editSlug === slug) scope.syncEditViewshedCheckbox();
    scope.updatePinOverlays();
  }


  function syncViewshedCheckbox() {
    if (!selectedSlug) return;
    scope.syncViewshedUiForSlug(selectedSlug);
  }

  function addViewshedLayer(vs) {
    const sourceId = scope.viewshedSourceId(vs.slug);
    const layerId = scope.viewshedLayerId(vs.slug);
    const existingSource = map.getSource(sourceId);
    if (existingSource) {
      if (typeof existingSource.updateImage === "function") {
        existingSource.updateImage({
          url: vs.url,
          coordinates: vs.coordinates,
        });
      } else {
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        map.removeSource(sourceId);
        map.addSource(sourceId, {
          type: "image",
          url: vs.url,
          coordinates: vs.coordinates,
        });
        map.addLayer(
          {
            id: layerId,
            type: "raster",
            source: sourceId,
            paint: {
              "raster-opacity": viewshedOpacity,
              "raster-fade-duration": 0,
            },
          },
          scope.viewshedLayerInsertBefore(),
        );
      }
    } else {
      map.addSource(sourceId, {
        type: "image",
        url: vs.url,
        coordinates: vs.coordinates,
      });
      map.addLayer(
        {
          id: layerId,
          type: "raster",
          source: sourceId,
          paint: {
            "raster-opacity": viewshedOpacity,
            "raster-fade-duration": 0,
          },
        },
        scope.viewshedLayerInsertBefore(),
      );
    }
    if (!isViewshedVisible(vs.slug) || isSiteMapHidden(vs.slug)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    } else {
      map.setLayoutProperty(layerId, "visibility", "visible");
    }
    viewshedLoading.delete(vs.slug);
    scope.updatePinOverlays();
    if (vs.slug === selectedSlug) scope.syncViewshedCheckbox();
    if (vs.slug === C.DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      scope.syncCreateViewshedCheckbox();
      scope.syncEditViewshedCheckbox();
      if (draftPlacementLat != null && draftPlacementLon != null) {
        void scope.loadPlacementPrefetchAt(draftPlacementLat, draftPlacementLon);
      }
    }
    raiseSiteLayers();
  }


  function updateSelectedLayer() {
    if (!map.getLayer(C.SITES_SELECTED)) return;
    if (editMode && selectedSlug === editSlug) {
      map.setFilter(C.SITES_SELECTED, ["==", ["get", "slug"], ""]);
      return;
    }
    const filter = combineLayerFilters(
      ["==", ["get", "slug"], selectedSlug || ""],
      siteVisibilityFilter(),
    );
    map.setFilter(C.SITES_SELECTED, filter);
  }


  function setSectionVisible(sectionId, visible) {
    const el = document.getElementById(sectionId);
    if (el) el.hidden = !visible;
  }


  function normalizeTagInput(raw) {
    return String(raw || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }




















  function syncSeekGoalLine() {
    if (!mapReady) return;
    if (!seekSessionActive()) {
      removeSeekGoalLineLayer();
      removeSeekWedgeLayers();
      return;
    }
    const from = seekCurrentFrom();
    const goal = seekGoalCoords();
    if (!from || !goal) {
      removeSeekGoalLineLayer();
      removeSeekWedgeLayers();
      return;
    }
    const data = {
      type: "FeatureCollection",
      features: [buildSeekGoalLineFeature(from, goal)],
    };
    if (map.getSource(C.SEEK_GOAL_LINE_SOURCE)) {
      map.getSource(C.SEEK_GOAL_LINE_SOURCE).setData(data);
    } else {
      map.addSource(C.SEEK_GOAL_LINE_SOURCE, { type: "geojson", data });
      map.addLayer(
        {
          id: C.SEEK_GOAL_LINE_LAYER,
          type: "line",
          source: C.SEEK_GOAL_LINE_SOURCE,
          paint: {
            "line-color": "#22c55e",
            "line-width": 2,
            "line-opacity": 0.75,
            "line-dasharray": [4, 3],
          },
          layout: { "line-cap": "round", "line-join": "round" },
        },
        C.SITES_CIRCLE,
      );
    }
    syncSeekWedge();
    raiseSiteLayers();
  }





























  function syncTagFilterBypassForSite(slug) {
    tagFilterBypassSlugs.delete(slug);
  }

  function applySiteTagChange(slug) {
    if (!siteBySlug.get(slug)) {
      tagFilterBypassSlugs.delete(slug);
      return;
    }
    scope.syncTagFilterBypassForSite(slug);
    applyEntityVisibility();
    renderTagFilters();
  }

  const siteTagSaveQueue = new Map();


  async function flushSiteTagSave(slug) {
    const state = siteTagSaveQueue.get(slug);
    if (!state || state.inflight) return;
    const tags = state.pendingTags;
    if (tags == null) return;
    state.pendingTags = null;
    state.inflight = true;
    const waiters = state.waiters.splice(0);
    try {
      const resp = await fetch(siteDeleteUrl(slug), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(payload.error || `Tag update failed (${resp.status})`);
      }
      const site = payload.site;
      if (!site) throw new Error("Tag update returned no site");
      scope.applySiteRowUpdate(site);
      scope.applySiteTagChange(site.slug);
      for (const w of waiters) w.resolve(site);
    } catch (err) {
      for (const w of waiters) w.reject(err);
    } finally {
      state.inflight = false;
      if (state.pendingTags != null) void scope.flushSiteTagSave(slug);
    }
  }


  function buildTagAddForm(site, currentTags) {
    const form = document.createElement("form");
    form.className = "site-tag-add-form";
    const input = document.createElement("input");
    input.type = "text";
    input.setAttribute("list", "site-tag-suggestions");
    input.placeholder = "tag";
    input.autocomplete = "off";
    input.maxLength = 32;
    const list = document.createElement("datalist");
    list.id = "site-tag-suggestions";
    for (const tag of allProjectTags()) {
      if (currentTags.includes(tag)) continue;
      const opt = document.createElement("option");
      opt.value = tag;
      list.appendChild(opt);
    }
    form.appendChild(input);
    form.appendChild(list);
    const finish = () => {
      tagAddOpen = false;
      if (selectedSlug === site.slug)
        scope.renderSiteTags(siteBySlug.get(site.slug) || site);
    };
    const commitPendingTag = async () => {
      if (!tagAddOpen) return;
      const tag = normalizeTagInput(input.value);
      tagAddOpen = false;
      const current = siteTags(siteBySlug.get(site.slug) || site);
      if (!tag || current.includes(tag)) {
        if (selectedSlug === site.slug)
          scope.renderSiteTags(siteBySlug.get(site.slug) || site);
        return;
      }
      try {
        const updated = await scope.patchSiteTags(site.slug, [...current, tag], {
          immediate: true,
        });
        if (selectedSlug === site.slug) scope.renderSiteTags(updated);
      } catch (_) {
        if (selectedSlug === site.slug)
          scope.renderSiteTags(siteBySlug.get(site.slug) || site);
      }
    };
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      void commitPendingTag();
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        finish();
      }
    });
    input.addEventListener("blur", () => {
      setTimeout(() => {
        void commitPendingTag();
      }, 150);
    });
    queueMicrotask(() => input.focus());
    return form;
  }

  function renderPanel(site) {
    if (!site) return;
    if (sitePanelViewshedSection) sitePanelViewshedSection.hidden = false;
    if (sitePanelTagsSection) sitePanelTagsSection.hidden = false;
    document.getElementById("site-panel-name").textContent = site.name;
    scope.renderSiteTags(site);
    document.getElementById("site-panel-coords").textContent =
      `${formatCoord(site.lat)}, ${formatCoord(site.lon)}`;
    const heightEl = document.getElementById("site-panel-height");
    if (heightEl) {
      const explicit = formatAntennaHeightM(site.height_m);
      if (explicit) {
        heightEl.textContent = explicit;
        heightEl.classList.remove("text-muted");
      } else {
        heightEl.textContent = `default (${defaultTxHeightM} m)`;
        heightEl.classList.add("text-muted");
      }
    }
    const plss = site.plss || "";
    setSectionVisible("site-panel-plss-section", !!plss);
    document.getElementById("site-panel-plss").textContent = plss;
    const desc = site.description || "";
    setSectionVisible("site-panel-desc-section", !!desc);
    document.getElementById("site-panel-desc").textContent = desc;
    const peers = linkedPeerDetailsForSite(site.slug);
    setSectionVisible("site-panel-links-section", peers.length > 0);
    const linksLabel = document.getElementById("site-panel-links-label");
    if (linksLabel) {
      linksLabel.textContent =
        peers.length === 1 ? "1 link" : `${peers.length} links`;
    }
    const linksEl = document.getElementById("site-panel-links");
    linksEl.innerHTML = "";
    for (const peer of peers) {
      linksEl.appendChild(buildSitePanelLinkButton(peer, site.slug));
    }
    scope.syncViewshedCheckbox();
  }

  function setEditError(message) {
    if (!sitePanelEditError) return;
    if (!message) {
      sitePanelEditError.hidden = true;
      sitePanelEditError.textContent = "";
      return;
    }
    sitePanelEditError.textContent = message;
    sitePanelEditError.hidden = false;
  }

  function resetEditPrefetchPanelUI() {
    setSectionVisible("site-panel-edit-plss-section", false);
    setSectionVisible("site-panel-edit-links-section", false);
    const plssEl = document.getElementById("site-panel-edit-plss");
    const linksEl = document.getElementById("site-panel-edit-links");
    if (plssEl) plssEl.textContent = "";
    if (linksEl) linksEl.innerHTML = "";
  }

  function resetEditPrefetchUI() {
    scope.resetEditPrefetchPanelUI();
    removeDraftLinksLayer();
    removeEditHistoryLinksLayer();
  }

  function renderEditPrefetch(payload) {
    const plss = payload.plss || "";
    setSectionVisible("site-panel-edit-plss-section", !!plss);
    document.getElementById("site-panel-edit-plss").textContent = plss || "—";
    const links = Array.isArray(payload.links) ? payload.links : [];
    const linked = links.filter((row) => {
      if (row.linked === false) return false;
      if (editMode && editSlug && row.slug === editSlug) return false;
      return true;
    });
    setSectionVisible("site-panel-edit-links-section", linked.length > 0);
    if (sitePanelEditLinksLabel) {
      sitePanelEditLinksLabel.textContent = "Linked sites";
    }
    const linksEl = document.getElementById("site-panel-edit-links");
    linksEl.innerHTML = "";
    for (const row of linked) {
      const slug = row.slug;
      const site = siteBySlug.get(slug);
      const label = site ? site.name : slug;
      const dist = formatLinkDistanceKm(row.distance_km);
      const li = document.createElement("li");
      li.textContent = dist ? `${label} — ${dist}` : label;
      linksEl.appendChild(li);
    }
    if (scope.editShowsSitePreview() && payload.links_geojson) {
      addDraftLinksLayer(payload.links_geojson);
    } else {
      removeDraftLinksLayer();
    }
  }












  const EDIT_COORD_HISTORY_MIN_M = 25;

  function editHistorySlug(id) {
    return `_edit_hist_${id}`;
  }

  function removeEditHistoryMarker(id) {
    const marker = editHistoryMarkers.get(id);
    if (marker) {
      marker.remove();
      editHistoryMarkers.delete(id);
    }
  }

  function cancelEditHistoryViewshedLoad(id) {
    const entry = editCoordHistory.find((row) => row.id === id);
    const slug = scope.editHistorySlug(id);
    if (entry) entry.viewshedGen = (entry.viewshedGen || 0) + 1;
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
  }

  function hideEditHistoryViewshed(id) {
    scope.cancelEditHistoryViewshedLoad(id);
    const slug = scope.editHistorySlug(id);
    viewshedVisible.set(slug, false);
    const layerId = scope.viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    }
    scope.updatePinOverlays();
  }

  function showEditHistoryViewshed(entry) {
    if (!scope.editShowsSitePreview()) return;
    const slug = scope.editHistorySlug(entry.id);
    viewshedVisible.set(slug, true);
    const layerId = scope.viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "visible");
      scope.updatePinOverlays();
      return;
    }
    void scope.loadEditHistoryViewshed(entry.id, entry.lat, entry.lon);
  }

  function removeEditHistoryMapArtifacts(id) {
    const entry = editCoordHistory.find((row) => row.id === id);
    scope.removeEditHistoryMarker(id);
    scope.cancelEditHistoryViewshedLoad(id);
    const slug = scope.editHistorySlug(id);
    removeViewshedLayer(slug);
    viewshedVisible.delete(slug);
    if (entry) {
      entry.linksLoading = false;
      entry.linksGen = (entry.linksGen || 0) + 1;
    }
    refreshEditHistoryLinksLayer();
  }


  function historyHasCoords(lat, lon) {
    return editCoordHistory.some((entry) =>
      scope.coordsMatchPair(entry.lat, entry.lon, lat, lon),
    );
  }


  function commitEditCoordMove() {
    const coords = scope.readEditCoords();
    if (!coords) return;
    if (!editCommittedCoords) {
      editCommittedCoords = { lat: coords.lat, lon: coords.lon };
      return;
    }
    if (
      scope.coordsMatchPair(
        coords.lat,
        coords.lon,
        editCommittedCoords.lat,
        editCommittedCoords.lon,
      )
    ) {
      return;
    }
    const movedM = scope.coordSeparationM(
      editCommittedCoords.lat,
      editCommittedCoords.lon,
      coords.lat,
      coords.lon,
    );
    if (movedM >= EDIT_COORD_HISTORY_MIN_M) {
      scope.pushEditCoordHistory(editCommittedCoords.lat, editCommittedCoords.lon);
    }
    editCommittedCoords = { lat: coords.lat, lon: coords.lon };
  }

  function updateEditHistoryMarker(entry) {
    if (!entry.visible) return;
    const marker = editHistoryMarkers.get(entry.id);
    if (marker) {
      marker.remove();
      editHistoryMarkers.delete(entry.id);
    }
    const markerColor = C.DRAFT_MARKER_COLOR;
    editHistoryMarkers.set(
      entry.id,
      new maplibregl.Marker({ color: markerColor })
        .setLngLat([entry.lon, entry.lat])
        .addTo(map),
    );
  }

  function invalidateEditCoordHistoryPrefetch() {
    for (const entry of editCoordHistory) {
      entry.linksGeojson = null;
      entry.linksLoading = false;
      entry.linksGen = (entry.linksGen || 0) + 1;
    }
  }

  function clearEditHistoryViewshed(id) {
    scope.cancelEditHistoryViewshedLoad(id);
    const slug = scope.editHistorySlug(id);
    removeViewshedLayer(slug);
    viewshedVisible.delete(slug);
  }

  async function loadEditHistoryViewshed(id, lat, lon) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry || !entry.visible || !scope.editShowsSitePreview()) return;
    const slug = scope.editHistorySlug(id);
    const gen = (entry.viewshedGen = (entry.viewshedGen || 0) + 1);
    removeViewshedLayer(slug);
    viewshedLoading.add(slug);
    const epoch = viewshedLoadEpoch;
    viewshedPendingEpoch.set(slug, epoch);
    scope.updatePinOverlays();
    scope.renderEditCoordHistory();
    try {
      const resp = await fetch(scope.viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
      });
      if (!entry.visible || entry.viewshedGen !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (!resp.ok) {
        viewshedPendingEpoch.delete(slug);
        viewshedLoading.delete(slug);
        scope.updatePinOverlays();
        scope.renderEditCoordHistory();
        return;
      }
      const vs = await resp.json();
      if (!entry.visible || entry.viewshedGen !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (vs && vs.status === "ready") {
        viewshedVisible.set(slug, true);
        scope.handleViewshedReady({ ...vs, slug }, epoch);
      }
    } catch (_) {
      if (entry.viewshedGen === gen) {
        viewshedPendingEpoch.delete(slug);
        viewshedLoading.delete(slug);
        scope.updatePinOverlays();
        scope.renderEditCoordHistory();
      }
    }
  }

  async function loadEditHistoryLinks(id, lat, lon) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry) return;
    const gen = (entry.linksGen = (entry.linksGen || 0) + 1);
    entry.linksLoading = true;
    scope.renderEditCoordHistory();
    try {
      const url = scope.sitesPrefetchUrl(lat, lon, editSlug);
      const resp = await fetch(url);
      if (!entry.visible || entry.linksGen !== gen) return;
      if (!resp.ok) {
        entry.linksGeojson = null;
        return;
      }
      const payload = await resp.json();
      if (!entry.visible || entry.linksGen !== gen) return;
      let linksGeojson = payload.links_geojson;
      const filtered = scope.filterEditSitePrefetchPayload({
        links_geojson: linksGeojson,
      });
      entry.linksGeojson = scope.filterHistoryEntryLinksGeojson(
        filtered.links_geojson,
        lat,
        lon,
      );
      refreshEditHistoryLinksLayer();
    } catch (_) {
      if (entry.linksGen === gen) entry.linksGeojson = null;
    } finally {
      if (entry.linksGen === gen) {
        entry.linksLoading = false;
        scope.renderEditCoordHistory();
        refreshEditHistoryLinksLayer();
      }
    }
  }

  async function loadEditHistoryMapArtifacts(entry) {
    if (!entry.visible) return;
    scope.showEditHistoryViewshed(entry);
    if (!scope.editShowsSitePreview()) {
      if (entry.linksGeojson) entry.linksGeojson = null;
      refreshEditHistoryLinksLayer();
      return;
    }
    if (
      entry.linksGeojson &&
      entry.linksGeojson.features &&
      entry.linksGeojson.features.length
    ) {
      refreshEditHistoryLinksLayer();
      return;
    }
    await scope.loadEditHistoryLinks(entry.id, entry.lat, entry.lon);
  }

  function setEditHistoryEntryVisible(id, visible) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry) return;
    entry.visible = visible;
    if (visible) {
      scope.updateEditHistoryMarker(entry);
      void scope.loadEditHistoryMapArtifacts(entry);
    } else {
      scope.removeEditHistoryMarker(id);
      scope.hideEditHistoryViewshed(id);
      entry.linksGen = (entry.linksGen || 0) + 1;
      refreshEditHistoryLinksLayer();
    }
    scope.renderEditCoordHistory();
  }

  function deleteEditHistoryEntry(id) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (entry) {
      entry.linksGeojson = null;
    }
    scope.removeEditHistoryMapArtifacts(id);
    editCoordHistory = editCoordHistory.filter((row) => row.id !== id);
    scope.renderEditCoordHistory();
  }

  async function copyCoordPair(lat, lon) {
    const text = `${formatCoord(lat)}, ${formatCoord(lon)}`;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      /* clipboard optional */
    }
  }



  async function runEditPrefetchAt(lat, lon) {
    const gen = ++editPrefetchGen;
    const showSitePreview = scope.editShowsSitePreview();
    const atOriginal = scope.coordsMatchEditSnapshot(lat, lon);
    scope.resetEditPrefetchPanelUI();
    scope.updateEditDraftMarker(lat, lon);
    applySiteLayerFilters();
    refreshFilteredLinks();
    if (sitePanelEditViewshedSection) {
      sitePanelEditViewshedSection.hidden = !showSitePreview;
    }
    if (showSitePreview) {
      if (atOriginal) {
        scope.removeDraftViewshed();
        scope.restoreEditHiddenViewshed();
      } else {
        scope.loadEditDraftViewshedAt(lat, lon);
      }
    } else {
      scope.removeDraftViewshed();
      removeDraftLinksLayer();
    }
    try {
      const url = scope.sitesPrefetchUrl(lat, lon, editSlug);
      const resp = await fetch(url);
      if (gen !== editPrefetchGen) return;
      if (!resp.ok) return;
      let payload = await resp.json();
      if (gen !== editPrefetchGen) return;
      payload = scope.filterEditSitePrefetchPayload(payload);
      scope.renderEditPrefetch(payload);
    } catch (_) {
      /* edit prefetch optional */
    }
  }


  function scheduleEditPrefetch() {
    if (!editMode) return;
    if (editPrefetchTimer) clearTimeout(editPrefetchTimer);
    editPrefetchTimer = setTimeout(() => {
      editPrefetchTimer = null;
      const coords = scope.readEditCoords();
      if (!coords) return;
      scope.commitEditCoordMove();
      void scope.runEditPrefetchAt(coords.lat, coords.lon);
    }, C.COORD_PREFETCH_MS);
  }

  function restoreEditHiddenViewshed() {
    if (editHiddenViewshedSlug) {
      scope.applyViewshedVisibilityForSite(editHiddenViewshedSlug);
      editHiddenViewshedSlug = null;
    }
  }





  function applySiteRowUpdate(site, { refreshGeoJson = true } = {}) {
    const row = normalizeSiteFromApi(site);
    if (!row) return;
    const ix = sites.findIndex((s) => s.slug === row.slug);
    if (ix >= 0) sites[ix] = row;
    else sites.push(row);
    siteBySlug.set(row.slug, row);
    if (refreshGeoJson && map.getSource(C.SITES_SOURCE)) {
      map.getSource(C.SITES_SOURCE).setData(sitesGeoJson());
    }
    if (refreshGeoJson) {
      applySiteLayerFilters();
      updateSelectedLayer();
      raiseSiteLayers();
    }
  }

  function finishEditSaveUi() {
    applySiteLayerFilters();
    refreshFilteredLinks();
    syncEditMapShell();
    updateSelectedLayer();
    raiseSiteLayers();
  }

  function cleanupEditSaveArtifacts() {
    removeDraftMarker();
    scope.removeDraftViewshed();
    removeDraftLinksLayer();
    editHiddenViewshedSlug = null;
    scope.clearEditCoordHistory();
    editPrefetchGen += 1;
    if (editPrefetchTimer) {
      clearTimeout(editPrefetchTimer);
      editPrefetchTimer = null;
    }
    editMode = false;
    editKind = null;
    editSlug = null;
    editSnapshot = null;
  }

  async function copyEditCoords() {
    const coords = scope.readEditCoords();
    if (!coords) return;
    await scope.copyCoordPair(coords.lat, coords.lon);
  }

  async function copyPlssFromElement(el) {
    if (!el) return;
    const text = (el.textContent || "").trim();
    if (!text || text === "—") return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      /* clipboard optional */
    }
  }



  const LONG_PRESS_MS = 500;
  const LONG_PRESS_MOVE_PX = 12;
  let longPressTimer = null;
  let longPressStart = null;

  function clearLongPressTimer() {
    if (longPressTimer) {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }
    longPressStart = null;
  }

  function lngLatFromClientPoint(clientX, clientY) {
    const rect = map.getCanvas().getBoundingClientRect();
    return map.unproject([clientX - rect.left, clientY - rect.top]);
  }



  let seekPanelOpen = false;
  let seekRunning = false;
  let seekFetchTimer = null;
  let seekFetchEpoch = 0;
  let seekGoalPlacementMode = false;
  let seekGoalMarker = null;
  let seekPendingGoalLat = null;
  let seekPendingGoalLon = null;
  let seekSiteCandidateSlugs = new Set();
  let seekActiveFetchKey = null;
  let seekHopCoordViewshedSlugs = new Set();
  const seekHopCoordViewshedCoords = new Map();
  const seekHopViewshedGen = new Map();
  let seekAncillaryLinksGen = 0;
  let seekAncillaryLinksRawFeatures = [];
  let seekAncillaryLinksTimer = null;
  let seekAncillaryLinksAbort = null;

  function abortSeekInFlight() {
    const ac = seekFetchAbort;
    seekFetchAbort = null;
    if (ac) ac.abort();
  }

  function invalidateSeekFetch() {
    scope.abortSeekInFlight();
    seekFetchEpoch += 1;
    if (seekFetchTimer) window.clearTimeout(seekFetchTimer);
    seekFetchTimer = null;
  }

  function cancelSeekScanUi() {
    scope.invalidateSeekFetch();
    scope.setSeekScanning(false);
  }

  function beginSeekFetch() {
    scope.abortSeekInFlight();
    seekFetchEpoch += 1;
    const epoch = seekFetchEpoch;
    const ac = new AbortController();
    seekFetchAbort = ac;
    return { epoch, signal: ac.signal };
  }



  function migrateSeekStateGoal(parsed) {
    if (!parsed || typeof parsed !== "object") return parsed;
    if (parsed.goalLat != null && parsed.goalLon != null) return parsed;
    if (parsed.goalSlug) {
      const site = sites.find((s) => s.slug === parsed.goalSlug);
      if (site) {
        parsed.goalLat = site.lat;
        parsed.goalLon = site.lon;
      }
      delete parsed.goalSlug;
    }
    return parsed;
  }


  function updateSeekGoalCoordsDisplay() {
    const goal = seekGoalCoords();
    if (seekGoalCoordsEl) {
      seekGoalCoordsEl.textContent = goal
        ? `${formatCoord(goal.lat)}, ${formatCoord(goal.lon)}`
        : "Not set";
    }
  }

  function removeSeekGoalMarker() {
    if (seekGoalMarker) {
      seekGoalMarker.remove();
      seekGoalMarker = null;
    }
  }

  function syncSeekGoalMarker() {
    if (!mapReady) return;
    const goal = seekGoalCoords();
    if (!goal) {
      scope.removeSeekGoalMarker();
      return;
    }
    if (!seekGoalMarker) {
      const el = document.createElement("div");
      el.className = "seek-goal-marker";
      el.setAttribute("aria-hidden", "true");
      seekGoalMarker = new maplibregl.Marker({ element: el, anchor: "center" });
    }
    seekGoalMarker.setLngLat([goal.lon, goal.lat]).addTo(map);
  }

  function setSeekGoalPlacementMode(active) {
    seekGoalPlacementMode = active;
    if (mapShell) mapShell.classList.toggle("seek-goal-placement-mode", active);
    if (seekSetGoalBtn) {
      seekSetGoalBtn.setAttribute("aria-pressed", active ? "true" : "false");
      seekSetGoalBtn.classList.toggle("active", active);
    }
    syncMapCursor();
    if (active) {
      scope.setSeekStatus("Click the map to set goal");
    } else if (seekPanelOpen && !seekSessionActive()) {
      const goal = seekGoalCoords();
      if (!goal) scope.setSeekStatus("Pick a start site and set goal on the map");
      else scope.setSeekStatus("Pick a start site to begin");
    }
  }

  function setSeekGoalAt(lat, lon, { refresh = true } = {}) {
    const prev = seekGoalCoords();
    const moved =
      !prev ||
      Math.abs(prev.lat - lat) > 1e-7 ||
      Math.abs(prev.lon - lon) > 1e-7;
    seekPendingGoalLat = lat;
    seekPendingGoalLon = lon;
    if (seekState?.running) {
      seekState.goalLat = lat;
      seekState.goalLon = lon;
      if (moved) {
        scope.clearSeekRedoStack();
        seekState.complete = false;
        scope.saveSeekState({ immediatePlan: true });
      }
    }
    scope.setSeekGoalPlacementMode(false);
    scope.updateSeekGoalCoordsDisplay();
    scope.syncSeekGoalMarker();
    if (refresh && seekSessionActive()) {
      if (moved) {
        scope.applySeekLayers({
          candidates: { type: "FeatureCollection", features: [] },
          lines: { type: "FeatureCollection", features: [] },
        });
      }
      scope.promptSeekManualRecalc();
    } else scope.maybeAutoStartSeekFromSelects();
  }

  function syncSeekGoalUi() {
    scope.updateSeekGoalCoordsDisplay();
    scope.syncSeekGoalMarker();
    scope.syncSeekGoalLine();
    if (seekSetGoalBtn) {
      seekSetGoalBtn.disabled = !seekPanelOpen || seekScanning;
    }
  }

  function loadSeekRedoStack() {
    try {
      const raw = localStorage.getItem(SEEK_REDO_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function saveSeekRedoStack() {
    if (!seekState?.redoStack?.length) {
      localStorage.removeItem(SEEK_REDO_KEY);
      return;
    }
    localStorage.setItem(SEEK_REDO_KEY, JSON.stringify(seekState.redoStack));
  }



  let seekPlanSaveTimer = null;
  let seekPlanSaveSeq = 0;

  async function flushSeekPlanToYaml({ immediate = false } = {}) {
    if (seekPlanSaveTimer) {
      window.clearTimeout(seekPlanSaveTimer);
      seekPlanSaveTimer = null;
    }
    const seq = ++seekPlanSaveSeq;
    if (!seekState) {
      try {
        const resp = await fetch(`/api/p/${projectSlug}/seek/plan`, {
          method: "DELETE",
        });
        if (seq !== seekPlanSaveSeq) return;
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          scope.setSeekStatus(
            body.error || `Failed to clear seek plan (${resp.status})`,
          );
          return;
        }
        if (config.seek && typeof config.seek === "object") {
          config.seek.plan = null;
        }
      } catch (err) {
        if (seq !== seekPlanSaveSeq) return;
        scope.setSeekStatus(String(err));
      }
      return;
    }
    const plan = scope.seekStateToYamlPlan(seekState);
    if (!plan) return;
    try {
      const resp = await fetch(`/api/p/${projectSlug}/seek/plan`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan),
      });
      if (seq !== seekPlanSaveSeq) return;
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        scope.setSeekStatus(
          body.error || `Failed to save seek plan (${resp.status})`,
        );
        return;
      }
      if (config.seek && typeof config.seek === "object") {
        config.seek.plan = plan;
      }
    } catch (err) {
      if (seq !== seekPlanSaveSeq) return;
      scope.setSeekStatus(String(err));
    }
  }

  function persistSeekPlanToYaml({ immediate = false } = {}) {
    if (seekPlanSaveTimer) window.clearTimeout(seekPlanSaveTimer);
    if (immediate) {
      void scope.flushSeekPlanToYaml({ immediate: true });
      return;
    }
    seekPlanSaveTimer = window.setTimeout(() => {
      seekPlanSaveTimer = null;
      void scope.flushSeekPlanToYaml();
    }, C.SEEK_PLAN_SAVE_MS);
  }

  function loadSeekStateFromLocalStorage() {
    try {
      const raw = localStorage.getItem(SEEK_STATE_KEY);
      if (!raw) return null;
      const parsed = scope.migrateSeekStateGoal(JSON.parse(raw));
      if (!parsed || typeof parsed !== "object") return null;
      if (!Array.isArray(parsed.redoStack))
        parsed.redoStack = scope.loadSeekRedoStack();
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function rehydrateSeekStateFromConfig() {
    if (seekState?.running) return seekState;
    const fromYaml = config.seek?.plan
      ? scope.seekStateFromYamlPlan(config.seek.plan)
      : null;
    if (!fromYaml) return null;
    seekState = fromYaml;
    if (seekState.goalLat != null && seekState.goalLon != null) {
      seekPendingGoalLat = seekState.goalLat;
      seekPendingGoalLon = seekState.goalLon;
    }
    return seekState;
  }

  function initSeekState() {
    const yamlPlan = config.seek?.plan;
    const fromYaml = yamlPlan ? scope.seekStateFromYamlPlan(yamlPlan) : null;
    if (fromYaml) {
      localStorage.removeItem(SEEK_STATE_KEY);
      return fromYaml;
    }
    return scope.loadSeekStateFromLocalStorage();
  }

  function loadSeekState() {
    return scope.initSeekState();
  }

  let seekState = scope.loadSeekState();
  if (seekState?.goalLat != null && seekState?.goalLon != null) {
    seekPendingGoalLat = seekState.goalLat;
    seekPendingGoalLon = seekState.goalLon;
  }
  let seekMarkers = [];
  let seekGoalInRange = false;
  let seekScanning = false;
  let seekProgressPollTimer = null;
  let seekProgressTickTimer = null;
  let lastSeekProgress = null;
  let seekViewshedRetryCount = 0;
  let seekScanStartedAt = 0;
  let seekFetchAbort = null;
  let seekSelectsHydrating = false;

  function clearSeekRedoStack() {
    if (
      seekState &&
      Array.isArray(seekState.redoStack) &&
      seekState.redoStack.length
    ) {
      seekState.redoStack = [];
    }
  }

  function saveSeekState({ immediatePlan = false } = {}) {
    scope.saveSeekRedoStack();
    if (!seekState) {
      localStorage.removeItem(SEEK_STATE_KEY);
      scope.persistSeekPlanToYaml({ immediate: immediatePlan });
      return;
    }
    scope.persistSeekPlanToYaml({ immediate: immediatePlan });
  }

  function setSeekStatus(text) {
    if (seekStatusEl) seekStatusEl.textContent = text || "";
  }

  const SEEK_PROGRESS_FALLBACK = {
    eligible_land: "Building eligible land…",
    trim: "Trimming to hop range and view…",
    dem: "Loading Skadi DEM…",
    peak_scan: "Scanning linkable peaks…",
    peak_links: "Scanning linkable peaks…",
    rf: "Checking goal and site links…",
    viewshed: "Warming viewshed…",
    starting: "Starting peak scan…",
  };

  function updateSeekProgressUi(prog) {
    if (!seekProgressEl) return;
    lastSeekProgress = prog || null;
    const phase = prog?.phase || "starting";
    const done = Number(prog?.done) || 0;
    const total = Number(prog?.total) || 0;
    const detail = prog?.detail || SEEK_PROGRESS_FALLBACK[phase] || "Working…";
    const elapsed = seekScanStartedAt
      ? Math.floor((Date.now() - seekScanStartedAt) / 1000)
      : 0;
    const detailWithElapsed = elapsed > 0 ? `${detail} (${elapsed}s)` : detail;
    if (seekProgressDetailEl) {
      seekProgressDetailEl.textContent = detailWithElapsed;
    }
    if (seekProgressTrackEl) {
      seekProgressTrackEl.setAttribute("aria-valuetext", detailWithElapsed);
    }
    if (seekProgressBarEl && seekProgressTrackEl) {
      if (total > 0) {
        seekProgressBarEl.classList.remove(
          "seek-panel__progress-bar--indeterminate",
        );
        const pct = Math.min(100, Math.round((done / total) * 100));
        seekProgressBarEl.style.width = `${pct}%`;
        seekProgressTrackEl.setAttribute("aria-valuenow", String(pct));
      } else {
        seekProgressBarEl.classList.add(
          "seek-panel__progress-bar--indeterminate",
        );
        seekProgressBarEl.style.width = "";
        seekProgressTrackEl.setAttribute("aria-valuenow", "0");
      }
    }
  }

  function startSeekProgressTick() {
    scope.stopSeekProgressTick();
    seekProgressTickTimer = window.setInterval(() => {
      if (!seekScanning) return;
      scope.updateSeekProgressUi(lastSeekProgress || { phase: "starting" });
    }, 1000);
  }

  function stopSeekProgressTick() {
    if (seekProgressTickTimer) window.clearInterval(seekProgressTickTimer);
    seekProgressTickTimer = null;
  }

  function stopSeekProgressPoll() {
    if (seekProgressPollTimer) window.clearInterval(seekProgressPollTimer);
    seekProgressPollTimer = null;
  }

  function stopSeekProgressUi() {
    scope.stopSeekProgressPoll();
    scope.stopSeekProgressTick();
    lastSeekProgress = null;
  }

  function sleepMs(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function pollSeekUntilDone(expectedGen, signal, epoch) {
    for (;;) {
      if (signal?.aborted) return { cancelled: true };
      if (epoch !== seekFetchEpoch) return { cancelled: true };
      let resp;
      try {
        resp = await fetch(`/api/p/${projectSlug}/seek/scan-progress`, {
          signal,
        });
      } catch (err) {
        if (err?.name === "AbortError") return { cancelled: true };
        await scope.sleepMs(C.SEEK_PROGRESS_POLL_MS);
        continue;
      }
      if (!resp.ok) {
        await scope.sleepMs(C.SEEK_PROGRESS_POLL_MS);
        continue;
      }
      const body = await resp.json().catch(() => ({}));
      if (body?.gen != null && body.gen !== expectedGen)
        return { cancelled: true };
      if (body?.progress) scope.updateSeekProgressUi(body.progress);
      if (body?.status === "done" && body?.result) {
        return { payload: { project: body.project, ...body.result } };
      }
      if (body?.status === "cancelled") return { cancelled: true };
      if (body?.status === "error") {
        return {
          error: body.error || "Seek failed",
          errorStatus: body.error_status || 422,
          notReady: body.error_status === 503,
        };
      }
      await scope.sleepMs(C.SEEK_PROGRESS_POLL_MS);
    }
  }

  function setSeekScanning(active) {
    seekScanning = active;
    if (seekStatusEl) seekStatusEl.hidden = active;
    if (seekProgressEl) seekProgressEl.hidden = !active;
    if (active) {
      scope.setSeekStatus("");
      seekScanStartedAt = Date.now();
      scope.updateSeekProgressUi({ phase: "starting" });
      scope.startSeekProgressTick();
    } else {
      scope.stopSeekProgressUi();
      seekScanStartedAt = 0;
      if (seekProgressDetailEl) seekProgressDetailEl.textContent = "";
    }
    scope.updatePinOverlays();
    scope.syncSeekPanelUi();
  }

  function syncSeekPanelUi() {
    if (mapToolSeek) {
      mapToolSeek.classList.toggle("map-toolbar-tool--active", seekPanelOpen);
      mapToolSeek.setAttribute(
        "aria-pressed",
        seekPanelOpen ? "true" : "false",
      );
    }
    if (seekPanel) seekPanel.hidden = !seekPanelOpen;
    if (seekUndoBtn) {
      const canUndo = Boolean(
        seekState?.running &&
        Array.isArray(seekState.hops) &&
        seekState.hops.length > 1,
      );
      seekUndoBtn.disabled = !canUndo;
    }
    if (seekRedoBtn) {
      const canRedo = Boolean(
        seekState?.running &&
        Array.isArray(seekState.redoStack) &&
        seekState.redoStack.length,
      );
      seekRedoBtn.disabled = !canRedo;
    }
    const seekStartLocked = Boolean(seekState?.running) || seekScanning;
    if (seekStartSelect) seekStartSelect.disabled = seekStartLocked;
    scope.syncSeekGoalUi();
    scope.syncSeekRefreshUi();
    scope.syncSeekConvertSitesBtn();
  }

  function countSeekLocHops() {
    const plan = scope.seekStateToYamlPlan(seekState) || config.seek?.plan;
    if (!plan || !Array.isArray(plan.hops)) return 0;
    return plan.hops.filter(
      (hop) => hop && typeof hop === "object" && hop.loc && !hop.site,
    ).length;
  }

  function countSeekUniqueLocHops() {
    const plan = scope.seekStateToYamlPlan(seekState) || config.seek?.plan;
    if (!plan || !Array.isArray(plan.hops)) return 0;
    const seen = new Set();
    let n = 0;
    for (const hop of plan.hops) {
      if (
        !hop ||
        typeof hop !== "object" ||
        hop.site ||
        !Array.isArray(hop.loc) ||
        hop.loc.length !== 2
      )
        continue;
      const lat = Number(hop.loc[0]);
      const lon = Number(hop.loc[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const hm = hop.height_m != null ? Number(hop.height_m) : null;
      const key = `${lat.toFixed(6)},${lon.toFixed(6)},${hm != null && Number.isFinite(hm) ? hm.toFixed(1) : ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      n += 1;
    }
    return n;
  }

  function syncSeekConvertSitesBtn() {
    if (!seekConvertSitesBtn) return;
    const locHops = scope.countSeekLocHops();
    seekConvertSitesBtn.disabled = locHops === 0 || seekScanning;
    seekConvertSitesBtn.title =
      locHops > 0
        ? `Create preset sites from ${locHops} coordinate hop(s) in the saved path`
        : "No coordinate hops in the saved path";
  }

  function setSeekConvertError(text) {
    if (!seekConvertError) return;
    if (text) {
      seekConvertError.textContent = text;
      seekConvertError.hidden = false;
    } else {
      seekConvertError.textContent = "";
      seekConvertError.hidden = true;
    }
  }

  function effectiveSeekConvertDraftTags() {
    const tags = [...seekConvertDraftTags];
    const pending = seekConvertTagInput
      ? normalizeTagInput(seekConvertTagInput.value)
      : "";
    if (pending && !tags.includes(pending)) tags.push(pending);
    return tags;
  }

  function syncSeekConvertSaveButton() {
    if (!seekConvertSave) return;
    const uniqueSites = scope.countSeekUniqueLocHops();
    const ready =
      uniqueSites > 0 &&
      scope.effectiveSeekConvertDraftTags().length > 0 &&
      String(seekConvertNamePrefix?.value || "").trim().length > 0;
    seekConvertSave.disabled = !ready;
  }

  function renderSeekConvertTags() {
    if (!seekConvertTagsEl) return;
    seekConvertTagsEl.innerHTML = "";
    const known = allProjectTags();
    const selected = new Set(seekConvertDraftTags);
    const shown = new Set([...known, ...seekConvertDraftTags]);
    for (const tag of [...shown].sort((a, b) => a.localeCompare(b))) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = selected.has(tag)
        ? "site-tag site-tag--toggle is-selected"
        : "site-tag site-tag--toggle";
      chip.textContent = tag;
      chip.setAttribute("aria-pressed", selected.has(tag) ? "true" : "false");
      chip.title = selected.has(tag) ? `Remove tag ${tag}` : `Add tag ${tag}`;
      chip.addEventListener("click", () => {
        if (selected.has(tag)) {
          seekConvertDraftTags = seekConvertDraftTags.filter((t) => t !== tag);
        } else {
          seekConvertDraftTags = [...seekConvertDraftTags, tag];
        }
        scope.renderSeekConvertTags();
        scope.syncSeekConvertTagSuggestions();
        scope.syncSeekConvertSaveButton();
      });
      seekConvertTagsEl.appendChild(chip);
    }
  }

  function syncSeekConvertTagSuggestions() {
    if (!seekConvertTagSuggestions) return;
    seekConvertTagSuggestions.innerHTML = "";
    const selected = new Set(seekConvertDraftTags);
    for (const tag of allProjectTags()) {
      if (selected.has(tag)) continue;
      const opt = document.createElement("option");
      opt.value = tag;
      seekConvertTagSuggestions.appendChild(opt);
    }
  }

  function maxExistingPrefixedSiteNumber(prefix) {
    const p = String(prefix || "").trim();
    if (!p) return 0;
    const slugBase = slugifyName(p);
    let max = 0;
    for (const site of sites) {
      const name = String(site.name || "").trim();
      if (
        name.length >= p.length &&
        name.slice(0, p.length).toLowerCase() === p.toLowerCase()
      ) {
        const rest = name.slice(p.length).trim();
        const n = Number.parseInt(rest, 10);
        if (rest && String(n) === rest && n > max) max = n;
      }
      const slug = String(site.slug || "");
      if (slug.startsWith(slugBase)) {
        let rest = slug.slice(slugBase.length);
        if (rest.startsWith("-")) rest = rest.slice(1);
        const m = rest.match(/^(\d+)/);
        if (m) max = Math.max(max, Number.parseInt(m[1], 10));
      }
    }
    return max;
  }


  function syncSeekConvertHopCountText() {
    if (!seekConvertHopCount) return;
    const locHops = scope.countSeekLocHops();
    const uniqueSites = scope.countSeekUniqueLocHops();
    const prefix = String(seekConvertNamePrefix?.value || "").trim();
    if (locHops === 0) {
      seekConvertHopCount.textContent = "No coordinate hops in the saved path.";
      return;
    }
    const names = scope.seekConvertNamePreview(prefix, uniqueSites);
    if (uniqueSites === locHops) {
      seekConvertHopCount.textContent = `${locHops} coordinate hop(s) will become ${uniqueSites} site(s). Names: ${names}.`;
    } else {
      seekConvertHopCount.textContent = `${locHops} coordinate hop(s) will become ${uniqueSites} site(s) (duplicate coordinates reuse one site). Names: ${names}.`;
    }
  }

  function resetSeekConvertModal() {
    scope.setSeekConvertError("");
    seekConvertDraftTags = [];
    if (seekConvertTagInput) seekConvertTagInput.value = "";
    if (seekConvertNamePrefix) seekConvertNamePrefix.value = "Relay";
    scope.syncSeekConvertHopCountText();
    scope.renderSeekConvertTags();
    scope.syncSeekConvertTagSuggestions();
    scope.syncSeekConvertSaveButton();
  }

  async function openSeekConvertModal() {
    if (!seekConvertSitesModal) return;
    if (scope.countSeekLocHops() === 0) return;
    scope.resetSeekConvertModal();
    await customElements.whenDefined("wa-dialog");
    seekConvertSitesModal.open = true;
    requestAnimationFrame(() => {
      seekConvertNamePrefix?.focus();
      seekConvertNamePrefix?.select();
    });
  }

  function closeSeekConvertModal() {
    if (!seekConvertSitesModal) return;
    seekConvertSitesModal.open = false;
  }

  function addSeekConvertTagFromInput() {
    if (!seekConvertTagInput) return;
    const tag = normalizeTagInput(seekConvertTagInput.value);
    seekConvertTagInput.value = "";
    if (!tag) return;
    if (!seekConvertDraftTags.includes(tag)) {
      seekConvertDraftTags = [...seekConvertDraftTags, tag];
      scope.renderSeekConvertTags();
      scope.syncSeekConvertTagSuggestions();
      scope.syncSeekConvertSaveButton();
    }
  }

  async function saveSeekConvertModal() {
    scope.addSeekConvertTagFromInput();
    const namePrefix = String(seekConvertNamePrefix?.value || "").trim();
    const tags = scope.effectiveSeekConvertDraftTags();
    if (!namePrefix) {
      scope.setSeekConvertError("Name prefix is required.");
      return;
    }
    if (!tags.length) {
      scope.setSeekConvertError("Choose at least one tag.");
      return;
    }
    if (scope.countSeekLocHops() === 0) {
      scope.setSeekConvertError("No coordinate hops to convert.");
      return;
    }
    scope.setSeekConvertError("");
    if (seekConvertSave) seekConvertSave.disabled = true;
    try {
      const resp = await fetch(
        `/api/p/${projectSlug}/seek/plan/convert-to-sites`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name_prefix: namePrefix, tags }),
        },
      );
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        scope.setSeekConvertError(payload.error || `Convert failed (${resp.status})`);
        return;
      }
      const imported = Array.isArray(payload.sites) ? payload.sites : [];
      const plan = payload.plan;
      scope.closeSeekConvertModal();
      for (const site of imported) {
        registerSite(site);
      }
      if (plan && config.seek && typeof config.seek === "object") {
        config.seek.plan = plan;
      }
      const fromYaml = plan ? scope.seekStateFromYamlPlan(plan) : null;
      if (fromYaml) {
        seekState = fromYaml;
        if (seekState.goalLat != null && seekState.goalLon != null) {
          seekPendingGoalLat = seekState.goalLat;
          seekPendingGoalLon = seekState.goalLon;
        }
      }
      scope.saveSeekState({ immediatePlan: true });
      const firstTag = tags[0];
      if (firstTag) {
        activeTagFilters.clear();
        activeTagFilters.add(firstTag);
        pruneActiveTagFilters();
        renderEntityPanel();
      }
      scope.syncSeekPanelUi();
      scope.updateSeekPathOverlay();
      scope.populateSeekStartSelect();
      const converted = payload.converted ?? 0;
      const tagged = payload.tagged ?? 0;
      let status = `Converted ${converted} hop(s) to sites`;
      if (tagged > 0) status += `; tagged ${tagged} existing site(s)`;
      scope.setSeekStatus(status);
      void loadSiteLinks();
      if (imported[0]?.slug) selectSite(imported[0].slug);
    } catch (err) {
      scope.setSeekConvertError(String(err));
    } finally {
      scope.syncSeekConvertSaveButton();
    }
  }

  function maybeAutoStartSeekFromSelects() {
    if (seekSelectsHydrating) return;
    if (seekState?.running) return;
    const startSlug = seekStartSelect?.value;
    if (!startSlug) {
      scope.setSeekStatus("Pick a start site and set goal on the map");
      return;
    }
    const startSite = siteBySlug.get(startSlug);
    const goal = seekGoalCoords();
    if (!startSite || !goal) {
      if (!goal) scope.setSeekStatus("Set goal on the map, then pick start site");
      return;
    }
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      C.SEEK_GOAL_SAME_AS_START_M
    ) {
      scope.setSeekStatus("Goal overlaps start site — pick a different point");
      return;
    }
    scope.startSeekRun();
  }

  function populateSeekStartSelect() {
    if (!seekStartSelect) return;
    seekSelectsHydrating = true;
    // Match entity-panel visibility: tag filter OR post-add bypass.
    const eligible = sites
      .filter(
        (site) =>
          sitePassesTagFilter(site) || tagFilterBypassSlugs.has(site.slug),
      )
      .slice()
      .sort((a, b) => compareHuman(a.name, b.name));
    if (!eligible.length) {
      seekStartSelect.innerHTML =
        '<option value="">No sites match tags</option>';
      seekSelectsHydrating = false;
      return;
    }
    const opts = eligible
      .map((site) => `<option value="${site.slug}">${site.name}</option>`)
      .join("");
    seekStartSelect.innerHTML = opts;
    const eligibleSlugs = new Set(eligible.map((site) => site.slug));
    if (seekState?.startSlug && eligibleSlugs.has(seekState.startSlug)) {
      seekStartSelect.value = seekState.startSlug;
    } else if (eligibleSlugs.has(seekStartSelect.value)) {
      /* keep current pick */
    } else {
      seekStartSelect.selectedIndex = 0;
    }
    seekSelectsHydrating = false;
  }

  function refreshSeekStartSelectIfOpen() {
    if (seekPanelOpen) scope.populateSeekStartSelect();
  }


  function promptSeekManualRecalc() {
    if (!seekSessionActive() || seekState?.complete) return;
    scope.setSeekStatus(scope.seekPanHintText());
  }

  function toggleSeekPanel(force) {
    const nextOpen = typeof force === "boolean" ? force : !seekPanelOpen;
    if (seekPanelOpen && !nextOpen) {
      scope.setSeekGoalPlacementMode(false);
      if (seekScanning) {
        scope.cancelSeekScanUi();
        if (seekSessionActive()) {
          scope.setSeekStatus(scope.seekPanHintText());
        }
      }
    }
    seekPanelOpen = nextOpen;
    if (seekPanelOpen) {
      scope.populateSeekStartSelect();
      if (seekState?.goalLat != null && seekState?.goalLon != null) {
        seekPendingGoalLat = seekState.goalLat;
        seekPendingGoalLon = seekState.goalLon;
      }
      scope.syncSeekGoalUi();
      if (seekState?.running && !seekState?.complete) {
        seekRunning = true;
        if (!seekScanning) {
          scope.promptSeekManualRecalc();
        }
      } else if (!seekState?.running) {
        scope.setSeekStatus("Pick a start site and set goal on the map");
      }
    } else {
      scope.syncSeekGoalUi();
    }
    scope.syncSeekPanelUi();
  }

  function clearSeekMarkers() {
    for (const marker of seekMarkers) marker.remove();
    seekMarkers = [];
  }

  function removeSeekCandidateLayers() {
    const layerIds = [
      C.SEEK_LINES_LABELS_LAYER,
      C.SEEK_LINES_LAYER,
      C.SEEK_CANDIDATES_LABELS_LAYER,
      C.SEEK_CANDIDATES_LAYER,
      C.SEEK_GOAL_LINE_LAYER,
    ];
    for (const id of layerIds) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    for (const src of [
      C.SEEK_LINES_SOURCE,
      C.SEEK_CANDIDATES_SOURCE,
      C.SEEK_GOAL_LINE_SOURCE,
    ]) {
      if (map.getSource(src)) map.removeSource(src);
    }
  }

  function removeSeekLayers() {
    scope.removeSeekCandidateLayers();
    if (map.getLayer(C.SEEK_PATH_LAYER)) map.removeLayer(C.SEEK_PATH_LAYER);
    if (map.getSource(C.SEEK_PATH_SOURCE)) map.removeSource(C.SEEK_PATH_SOURCE);
    scope.clearSeekMarkers();
  }





  function applySeekLayers(payload) {
    if (!mapReady || !payload) return;
    scope.removeSeekCandidateLayers();
    seekSiteCandidateSlugs = scope.seekSiteCandidateSlugsFromPayload(payload);

    const lines = payload.lines;
    if (lines && lines.features && lines.features.length) {
      const filteredLines = scope.filterSeekLineFeatures(lines.features);
      if (filteredLines.length) {
        const labeled = scope.seekLinesGeoJsonWithLabels({
          ...lines,
          features: filteredLines,
        });
        map.addSource(C.SEEK_LINES_SOURCE, { type: "geojson", data: labeled });
        map.addLayer(
          {
            id: C.SEEK_LINES_LAYER,
            type: "line",
            source: C.SEEK_LINES_SOURCE,
            paint: {
              "line-color": [
                "case",
                ["boolean", ["get", "is_goal"], false],
                "#22c55e",
                ["boolean", ["get", "is_site"], false],
                C.DRAFT_MARKER_COLOR,
                ["case", ["get", "rf_viable"], "#4a6cf7", "#94a3b8"],
              ],
              "line-width": [
                "case",
                ["boolean", ["get", "is_goal"], false],
                3.5,
                ["boolean", ["get", "is_site"], false],
                3,
                2.5,
              ],
              "line-opacity": 0.9,
              "line-dasharray": [
                "case",
                ["boolean", ["get", "is_goal"], false],
                [
                  "case",
                  ["get", "rf_viable"],
                  ["literal", [1, 0]],
                  ["literal", [2, 2]],
                ],
                [
                  "case",
                  ["get", "rf_viable"],
                  ["literal", [1, 0]],
                  ["literal", [2, 2]],
                ],
              ],
            },
            layout: { "line-cap": "round", "line-join": "round" },
          },
          C.SITES_CIRCLE,
        );
        map.addLayer(
          linkLabelsLayerSpec(
            C.SEEK_LINES_LABELS_LAYER,
            C.SEEK_LINES_SOURCE,
            "visible",
          ),
          C.SITES_CIRCLE,
        );
      }
    }

    const candidates = scope.seekCandidatesGeoJsonForDisplay({
      ...payload.candidates,
      features: scope.filterSeekCandidateFeatures(payload.candidates?.features),
    });
    if (candidates && candidates.features && candidates.features.length) {
      map.addSource(C.SEEK_CANDIDATES_SOURCE, {
        type: "geojson",
        data: candidates,
      });
      map.addLayer(
        {
          id: C.SEEK_CANDIDATES_LAYER,
          type: "circle",
          source: C.SEEK_CANDIDATES_SOURCE,
          paint: {
            "circle-radius": [
              "case",
              ["boolean", ["get", "is_goal"], false],
              10,
              ["boolean", ["get", "is_site"], false],
              7,
              7,
            ],
            "circle-color": [
              "case",
              ["boolean", ["get", "is_goal"], false],
              "#22c55e",
              ["boolean", ["get", "is_site"], false],
              "#4a6cf7",
              "#fb923c",
            ],
            "circle-stroke-color": [
              "case",
              ["boolean", ["get", "is_site"], false],
              "#ffffff",
              "#1a1a1a",
            ],
            "circle-stroke-width": [
              "case",
              ["boolean", ["get", "is_site"], false],
              2,
              1.5,
            ],
          },
        },
        C.SITES_CIRCLE,
      );
      map.addLayer(
        seekSiteCandidateLabelsLayerSpec(
          C.SEEK_CANDIDATES_LABELS_LAYER,
          C.SEEK_CANDIDATES_SOURCE,
        ),
        C.SITES_CIRCLE,
      );
    }

    if (seekState?.running && Array.isArray(seekState.hops)) {
      scope.updateSeekPathOverlay();
    }

    scope.syncSeekGoalLine();
    if (seekSessionActive() && siteLinksPayload?.geojson) {
      refreshFilteredLinks();
    }
    raiseSiteLayers();
  }


  /** Peak bin size (m) from map zoom: ~20 bins across viewport width, clamped 500–1500 m. */





  /** Site mesh pairs that duplicate the committed seek chain (both endpoints are consecutive hops). */





  function filterSeekLineFeatures(features) {
    return (features || []).filter((f) => !scope.seekLineFeatureIsRedundant(f));
  }

  function filterSeekCandidateFeatures(features) {
    return (features || []).filter((feature) => {
      const props = feature?.properties || {};
      if (props.is_goal) return true;
      if (props.is_site && props.site_slug && scope.isSiteInSeekPlan(props.site_slug)) {
        return false;
      }
      const coords = feature?.geometry?.coordinates;
      if (coords?.length >= 2 && scope.seekCoordsNearGoal(coords[1], coords[0])) {
        return false;
      }
      return true;
    });
  }

  function isSiteInSeekPlan(slug) {
    return Boolean(seekState?.running && scope.seekPathSiteSlugs().has(slug));
  }


  function cancelSeekHopViewshedLoad(slug) {
    seekHopViewshedGen.set(slug, (seekHopViewshedGen.get(slug) || 0) + 1);
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
  }

  function clearSeekHopViewshed(slug) {
    scope.cancelSeekHopViewshedLoad(slug);
    removeViewshedLayer(slug);
    seekHopCoordViewshedSlugs.delete(slug);
    seekHopCoordViewshedCoords.delete(slug);
    viewshedVisible.delete(slug);
  }

  function clearAllSeekHopViewsheds() {
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      scope.clearSeekHopViewshed(slug);
    }
    seekHopCoordViewshedSlugs.clear();
    seekHopCoordViewshedCoords.clear();
  }

  async function loadSeekHopCoordViewshed(slug, lat, lon) {
    if (!String(slug).startsWith(C.SEEK_HOP_VIEWSHED_PREFIX)) return;
    const gen = (seekHopViewshedGen.get(slug) || 0) + 1;
    seekHopViewshedGen.set(slug, gen);
    viewshedVisible.set(slug, true);
    seekHopCoordViewshedCoords.set(slug, { lat, lon });
    if (await scope.tryLoadCoordViewshedFromCache(slug, lat, lon)) return;
    removeViewshedLayer(slug);
    viewshedLoading.add(slug);
    const epoch = viewshedLoadEpoch;
    viewshedPendingEpoch.set(slug, epoch);
    scope.updatePinOverlays();
    try {
      const resp = await fetch(scope.viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
      });
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (!resp.ok) {
        scope.cancelSeekHopViewshedLoad(slug);
        scope.updatePinOverlays();
        return;
      }
      const vs = await resp.json();
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (vs && vs.status === "ready") {
        scope.handleViewshedReady({ ...vs, slug }, epoch);
      }
    } catch (_) {
      if ((seekHopViewshedGen.get(slug) || 0) === gen) {
        scope.cancelSeekHopViewshedLoad(slug);
        scope.updatePinOverlays();
      }
    }
  }

  function syncSeekHopViewsheds() {
    if (!mapReady || !seekState?.running || !Array.isArray(seekState.hops)) {
      scope.clearAllSeekHopViewsheds();
      scope.cancelSeekAncillaryLinksFetch();
      seekAncillaryLinksGen += 1;
      scope.removeSeekAncillaryLinksLayer();
      return;
    }
    const wantedCoordSlugs = new Set();
    for (const hop of seekState.hops) {
      const siteSlug = hop.site_slug || scope.seekSiteSlugNear(hop.lat, hop.lon);
      if (siteSlug) {
        const coordSlug = scope.seekHopCoordViewshedSlug(hop.lat, hop.lon);
        if (seekHopCoordViewshedSlugs.has(coordSlug)) {
          scope.clearSeekHopViewshed(coordSlug);
        }
        viewshedVisible.set(siteSlug, true);
        ensureViewshedLoadedForSlug(siteSlug);
        if (map.getLayer(scope.viewshedLayerId(siteSlug))) {
          scope.applyViewshedVisibilityForSite(siteSlug);
        }
        continue;
      }
      const slug = scope.seekHopCoordViewshedSlug(hop.lat, hop.lon);
      wantedCoordSlugs.add(slug);
      seekHopCoordViewshedSlugs.add(slug);
      viewshedVisible.set(slug, true);
      if (!map.getLayer(scope.viewshedLayerId(slug)) && !viewshedLoading.has(slug)) {
        void scope.loadSeekHopCoordViewshed(slug, hop.lat, hop.lon);
      } else if (map.getLayer(scope.viewshedLayerId(slug))) {
        map.setLayoutProperty(scope.viewshedLayerId(slug), "visibility", "visible");
      }
    }
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      if (!wantedCoordSlugs.has(slug)) {
        scope.clearSeekHopViewshed(slug);
      }
    }
    scope.raiseViewshedLayers();
    scope.scheduleSeekAncillaryLinks();
  }



  function canonicalSitePairKey(slugA, slugB) {
    return slugA <= slugB ? `${slugA}|${slugB}` : `${slugB}|${slugA}`;
  }

  function findSiteLinkFeature(slugA, slugB) {
    const features = siteLinksPayload?.geojson?.features;
    if (!features) return null;
    for (const feature of features) {
      const props = feature.properties || {};
      if (
        (props.a === slugA && props.b === slugB) ||
        (props.a === slugB && props.b === slugA)
      ) {
        return feature;
      }
    }
    return null;
  }

  async function ensureSiteLinksForSlug(slug) {
    if (!slug) return;
    const hasLinks = (siteLinksPayload?.links || []).some(
      (row) => row.linked && (row.a === slug || row.b === slug),
    );
    if (hasLinks) return;
    await loadSingleSiteLinks(slug);
  }

  function removeSeekAncillaryLinksLayer() {
    if (map.getLayer(C.SEEK_ANCILLARY_LINES_LABELS_LAYER)) {
      map.removeLayer(C.SEEK_ANCILLARY_LINES_LABELS_LAYER);
    }
    if (map.getLayer(C.SEEK_ANCILLARY_LINES_LAYER))
      map.removeLayer(C.SEEK_ANCILLARY_LINES_LAYER);
    if (map.getSource(C.SEEK_ANCILLARY_LINES_SOURCE))
      map.removeSource(C.SEEK_ANCILLARY_LINES_SOURCE);
  }


  function refreshSeekAncillaryLinksDisplay() {
    if (!seekAncillaryLinksRawFeatures.length) {
      scope.removeSeekAncillaryLinksLayer();
      return;
    }
    scope.addSeekAncillaryLinksLayer({
      type: "FeatureCollection",
      features: seekAncillaryLinksRawFeatures,
    });
  }

  function addSeekAncillaryLinksLayer(geojson) {
    const features = (geojson?.features || []).filter(
      seekAncillaryLinkFeatureVisible,
    );
    if (!mapReady || !features.length) {
      scope.removeSeekAncillaryLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels({
      type: "FeatureCollection",
      features,
    });
    if (map.getSource(C.SEEK_ANCILLARY_LINES_SOURCE)) {
      map.getSource(C.SEEK_ANCILLARY_LINES_SOURCE).setData(labeled);
      raiseSiteLayers();
      return;
    }
    map.addSource(C.SEEK_ANCILLARY_LINES_SOURCE, {
      type: "geojson",
      data: labeled,
    });
    map.addLayer(
      {
        id: C.SEEK_ANCILLARY_LINES_LAYER,
        type: "line",
        source: C.SEEK_ANCILLARY_LINES_SOURCE,
        paint: {
          "line-color": ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
          "line-width": 2.5,
          "line-opacity": 0.75,
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: "visible",
        },
      },
      C.SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(
        C.SEEK_ANCILLARY_LINES_LABELS_LAYER,
        C.SEEK_ANCILLARY_LINES_SOURCE,
        "visible",
      ),
      C.SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  function cancelSeekAncillaryLinksFetch() {
    if (seekAncillaryLinksAbort) {
      seekAncillaryLinksAbort.abort();
      seekAncillaryLinksAbort = null;
    }
  }

  async function collectSeekAncillaryLinkFeatures(signal, gen) {
    const hops = seekState?.hops;
    if (!hops?.length) return [];
    const seen = new Set();
    const features = [];

    const addFeature = (feature, dedupeKey) => {
      if (!feature || seen.has(dedupeKey)) return;
      seen.add(dedupeKey);
      features.push(feature);
    };

    for (let hopIndex = 0; hopIndex < hops.length; hopIndex += 1) {
      if (signal?.aborted || gen !== seekAncillaryLinksGen) return null;
      if (seekSessionActive() && hopIndex === hops.length - 1) continue;

      const hop = hops[hopIndex];
      const neighbors = scope.seekChainNeighborKeys(hopIndex);

      if (hop.site_slug) {
        await scope.ensureSiteLinksForSlug(hop.site_slug);
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null;
        for (const peerSlug of linkedPeersForSite(hop.site_slug)) {
          if (neighbors.has(`site:${peerSlug}`)) continue;
          if (isSiteMapHidden(peerSlug)) continue;
          const linkFeature = scope.findSiteLinkFeature(hop.site_slug, peerSlug);
          if (!linkFeature) continue;
          if (!scope.seekAncillaryLinkFeatureVisible(linkFeature)) continue;
          const props = linkFeature.properties || {};
          addFeature(
            linkFeature,
            scope.canonicalSitePairKey(String(props.a), String(props.b)),
          );
        }
        continue;
      }

      try {
        const resp = await fetch(scope.sitesPrefetchUrl(hop.lat, hop.lon), {
          signal,
        });
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null;
        if (!resp.ok) continue;
        const payload = await resp.json();
        const geojson = payload?.links_geojson;
        if (!geojson?.features?.length) continue;
        const fromKey = scope.seekHopEndpointKey(hop);
        for (const feature of geojson.features) {
          const slug = feature.properties?.slug;
          if (!slug) continue;
          if (neighbors.has(`site:${slug}`)) continue;
          if (!scope.seekAncillaryLinkFeatureVisible(feature)) continue;
          addFeature(feature, `${fromKey}|site:${slug}`);
        }
      } catch (err) {
        if (err?.name === "AbortError") return null;
      }
    }
    return features;
  }

  async function flushSeekAncillaryLinks() {
    if (seekAncillaryLinksTimer) {
      window.clearTimeout(seekAncillaryLinksTimer);
      seekAncillaryLinksTimer = null;
    }
    scope.cancelSeekAncillaryLinksFetch();
    if (
      !mapReady ||
      !seekState?.running ||
      !Array.isArray(seekState.hops) ||
      !seekState.hops.length
    ) {
      seekAncillaryLinksRawFeatures = [];
      scope.removeSeekAncillaryLinksLayer();
      return;
    }
    const gen = ++seekAncillaryLinksGen;
    const ac = new AbortController();
    seekAncillaryLinksAbort = ac;
    const features = await scope.collectSeekAncillaryLinkFeatures(ac.signal, gen);
    if (gen !== seekAncillaryLinksGen) return;
    seekAncillaryLinksAbort = null;
    if (features == null) return;
    seekAncillaryLinksRawFeatures = features;
    scope.addSeekAncillaryLinksLayer({
      type: "FeatureCollection",
      features: seekAncillaryLinksRawFeatures,
    });
  }

  function scheduleSeekAncillaryLinks() {
    if (seekAncillaryLinksTimer) window.clearTimeout(seekAncillaryLinksTimer);
    seekAncillaryLinksTimer = window.setTimeout(() => {
      seekAncillaryLinksTimer = null;
      void scope.flushSeekAncillaryLinks();
    }, C.SEEK_ANCILLARY_LINKS_DEBOUNCE_MS);
  }

  function appendSeekHopMarkers() {
    if (!seekState || !Array.isArray(seekState.hops)) return;
    seekState.hops.forEach((hop, idx) => {
      if (idx === 0) return;
      if (hop.site_slug) {
        if (isSiteMapHidden(hop.site_slug)) {
          const siteName =
            hop.site_name ||
            siteBySlug.get(hop.site_slug)?.name ||
            hop.site_slug;
          const el = createSeekSiteHopMarkerElement(siteName);
          seekMarkers.push(
            new maplibregl.Marker({ element: el, anchor: "bottom" })
              .setLngLat([hop.lon, hop.lat])
              .addTo(map),
          );
        }
        return;
      }
      let peakNum = 0;
      for (let i = 1; i <= idx; i += 1) {
        if (!seekState.hops[i].site_slug) peakNum += 1;
      }
      const el = document.createElement("div");
      el.className = "seek-hop-marker";
      el.textContent = String(peakNum);
      seekMarkers.push(
        new maplibregl.Marker({ element: el })
          .setLngLat([hop.lon, hop.lat])
          .addTo(map),
      );
    });
  }

  function updateSeekPathOverlay() {
    scope.syncSeekHopViewsheds();
    if (
      !mapReady ||
      !seekState ||
      !Array.isArray(seekState.hops) ||
      seekState.hops.length < 2
    ) {
      if (map.getLayer(C.SEEK_PATH_LAYER)) map.removeLayer(C.SEEK_PATH_LAYER);
      if (map.getSource(C.SEEK_PATH_SOURCE)) map.removeSource(C.SEEK_PATH_SOURCE);
      scope.clearSeekMarkers();
      return;
    }
    const coords = seekState.hops.map((hop) => [hop.lon, hop.lat]);
    const pathGeoJson = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {},
    };
    if (map.getSource(C.SEEK_PATH_SOURCE)) {
      map.getSource(C.SEEK_PATH_SOURCE).setData(pathGeoJson);
    } else {
      map.addSource(C.SEEK_PATH_SOURCE, { type: "geojson", data: pathGeoJson });
      map.addLayer(
        {
          id: C.SEEK_PATH_LAYER,
          type: "line",
          source: C.SEEK_PATH_SOURCE,
          paint: {
            "line-color": "#fbbf24",
            "line-width": 3,
            "line-opacity": 0.85,
          },
          layout: { "line-cap": "round", "line-join": "round" },
        },
        C.SITES_CIRCLE,
      );
    }
    scope.clearSeekMarkers();
    scope.appendSeekHopMarkers();
    applySiteLayerFilters();
    scope.syncSeekGoalLine();
    if (seekSessionActive() && siteLinksPayload?.geojson) {
      refreshFilteredLinks();
    }
    raiseSiteLayers();
  }



  function applySeekCandidatePayload(payload) {
    seekGoalInRange = Boolean(
      payload.meta?.goal_in_viewshed ?? payload.meta?.goal_reachable,
    );
    scope.applySeekLayers(payload);
    scope.syncSeekPanelUi();
    const n =
      payload.meta?.n_candidates ?? payload.candidates?.features?.length ?? 0;
    const nSites = payload.meta?.n_site_candidates ?? 0;
    let statusText;
    if (payload.meta?.goal_rf_viable) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ""} — direct RF link to goal`;
    } else if (payload.meta?.goal_finish_eligible) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ""} — no RF link to goal (${payload.meta.goal_distance_km ?? "?"} km)`;
    } else if (payload.meta?.goal_in_hop_range === false) {
      const maxKm = payload.meta?.hop_range_km ?? "?";
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ""} — goal out of hop range (${payload.meta.goal_distance_km ?? "?"} km, max ${maxKm} km)`;
    } else if (payload.meta?.goal_hop_eligible) {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ""} — goal visible but not a valid hop target`;
    } else {
      statusText = `${n} peak(s)${nSites ? `, ${nSites} site(s)` : ""} in view — click peak or site to commit hop`;
    }
    if (payload.meta?.peaks_cache === "build" && payload.meta?.n_peaks_total) {
      statusText += ` · cached ${payload.meta.n_peaks_total} peaks`;
    }
    const wedgePeaks = payload.meta?.n_peaks_in_wedge;
    const rfViable = payload.meta?.n_peaks_linkable;
    if (wedgePeaks != null && rfViable != null) {
      statusText += ` · ${rfViable} RF-viable of ${wedgePeaks} in wedge`;
    }
    if (isMapTiltedView()) {
      statusText += " · scanning hop range in goal wedge";
    }
    scope.setSeekStatus(statusText);
    scope.resetSeekViewshedRetries();
  }

  async function warmDraftViewshedForSeek(lat, lon, signal) {
    const siteSlug = scope.seekSiteSlugForFrom({ lat, lon });
    if (siteSlug) {
      setViewshedVisible(C.DRAFT_VIEWSHED_SLUG, false);
      removeViewshedLayer(C.DRAFT_VIEWSHED_SLUG);
      viewshedVisible.set(siteSlug, true);
      ensureViewshedLoadedForSlug(siteSlug);
      return Boolean(map.getLayer(scope.viewshedLayerId(siteSlug)));
    }
    viewshedVisible.set(C.DRAFT_VIEWSHED_SLUG, true);
    if (await scope.tryLoadDraftViewshedFromCache(lat, lon)) return true;
    try {
      const resp = await fetch(scope.viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
        signal,
      });
      if (!resp.ok) return false;
      const vs = await resp.json().catch(() => null);
      if (vs?.status === "ready" && vs.url && vs.coordinates) {
        scope.handleViewshedReady(
          { ...vs, slug: C.DRAFT_VIEWSHED_SLUG },
          viewshedLoadEpoch,
        );
        return true;
      }
    } catch (err) {
      if (err?.name === "AbortError") throw err;
    }
    return false;
  }

  function resetSeekViewshedRetries() {
    seekViewshedRetryCount = 0;
  }

  function scheduleSeekViewshedRetry(from, errorText, epoch) {
    scope.resetSeekViewshedRetries();
    const hint =
      errorText && /tile|dem|skadi|mirror|waiting/i.test(errorText)
        ? "Skadi DEM tiles still loading"
        : errorText || "Seek scan not ready";
    scope.setSeekStatus(`${hint} — click Recalculate`);
    return false;
  }

  async function refreshSeekCandidates() {
    if (!seekSessionActive() || !mapReady) return;
    const from = seekCurrentFrom();
    const goal = seekGoalCoords();
    if (!from || !goal) return;
    const fetchKey = scope.seekFetchParamsKey();
    if (!fetchKey) return;
    seekActiveFetchKey = fetchKey;
    const { epoch, signal } = scope.beginSeekFetch();
    let seekRetryScheduled = false;
    scope.setSeekScanning(true);
    scope.updateSeekProgressUi({ phase: "viewshed", detail: "Warming viewshed…" });
    try {
      await scope.warmDraftViewshedForSeek(from.lat, from.lon, signal);
    } catch (err) {
      if (err?.name === "AbortError") return;
    }
    if (epoch !== seekFetchEpoch) return;
    scope.updateSeekProgressUi({ phase: "starting" });
    const params = new URLSearchParams({
      from_lat: String(from.lat),
      from_lon: String(from.lon),
      goal_lat: String(goal.lat),
      goal_lon: String(goal.lon),
      bbox: scope.seekViewportBbox(),
      peak_bin_size_m: String(scope.seekPeakBinSizeM()),
    });
    const exclude = scope.seekExcludeParam();
    if (exclude) params.set("exclude", exclude);
    const excludeSlugs = scope.seekExcludeSlugsParam();
    if (excludeSlugs) params.set("exclude_slugs", excludeSlugs);
    try {
      const resp = await fetch(
        `/api/p/${projectSlug}/seek/candidates?${params}`,
        { signal },
      );
      const kickoff = await resp.json().catch(() => ({}));
      if (epoch !== seekFetchEpoch) return;
      if (!resp.ok) {
        seekGoalInRange = false;
        scope.syncSeekPanelUi();
        scope.setSeekStatus(kickoff.error || `Seek failed (${resp.status})`);
        return;
      }
      if (resp.status !== 202 || kickoff.gen == null) {
        scope.setSeekStatus("Unexpected seek response");
        return;
      }
      const outcome = await scope.pollSeekUntilDone(kickoff.gen, signal, epoch);
      if (epoch !== seekFetchEpoch) return;
      if (outcome.cancelled) return;
      if (outcome.error) {
        seekGoalInRange = false;
        scope.syncSeekPanelUi();
        if (outcome.notReady) {
          seekRetryScheduled = scope.scheduleSeekViewshedRetry(
            from,
            outcome.error,
            epoch,
          );
        } else {
          scope.resetSeekViewshedRetries();
          scope.setSeekStatus(outcome.error);
        }
        return;
      }
      scope.resetSeekViewshedRetries();
      scope.applySeekCandidatePayload(outcome.payload);
    } catch (err) {
      if (err?.name === "AbortError") return;
      if (epoch !== seekFetchEpoch) return;
      seekGoalInRange = false;
      scope.syncSeekPanelUi();
      scope.setSeekStatus(String(err));
    } finally {
      if (epoch === seekFetchEpoch) seekFetchAbort = null;
      if (epoch === seekFetchEpoch && !seekRetryScheduled)
        scope.setSeekScanning(false);
    }
  }

  function onMapMoveEndForSeek() {
    scope.syncSeekRefreshUi();
  }

  function syncSeekRefreshUi() {
    const showRefresh = Boolean(seekSessionActive() && !seekState?.complete);
    if (seekRefreshBtn) {
      seekRefreshBtn.hidden = !showRefresh;
      seekRefreshBtn.disabled = seekScanning || !seekSessionActive();
    }
  }

  function startSeekRun() {
    const startSlug = seekStartSelect?.value;
    const goal = seekGoalCoords();
    if (!startSlug || !goal) {
      scope.setSeekStatus("Pick a start site and set goal on the map");
      return;
    }
    const startSite = siteBySlug.get(startSlug);
    if (!startSite) return;
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      C.SEEK_GOAL_SAME_AS_START_M
    ) {
      scope.setSeekStatus("Goal overlaps start site — pick a different point");
      return;
    }
    seekRunning = true;
    seekState = {
      running: true,
      startSlug,
      goalLat: goal.lat,
      goalLon: goal.lon,
      hops: [
        {
          lat: startSite.lat,
          lon: startSite.lon,
          elev_m: startSite.height_m ?? null,
          site_slug: startSlug,
        },
      ],
      currentFrom: { lat: startSite.lat, lon: startSite.lon },
      complete: false,
      redoStack: [],
    };
    scope.saveSeekState({ immediatePlan: true });
    scope.syncSeekPanelUi();
    scope.updateSeekPathOverlay();
    scope.promptSeekManualRecalc();
  }

  function commitSeekCandidate(feature) {
    if (!seekSessionActive() || !feature?.geometry?.coordinates) return;
    const props = feature.properties || {};
    if (props.is_goal) return;
    if (props.site_slug) {
      if (!seekSiteCandidateSlugs.has(String(props.site_slug))) return;
    } else if (!scope.seekRfViable(props)) {
      return;
    }
    scope.abortSeekInFlight();
    scope.clearSeekRedoStack();
    const [lon, lat] = feature.geometry.coordinates;
    const hop = {
      lat,
      lon,
      elev_m: props.elev_m ?? null,
    };
    if (props.site_slug) {
      hop.site_slug = props.site_slug;
      hop.site_name = props.site_name || null;
    }
    seekState.hops.push(hop);
    seekState.currentFrom = { lat, lon };
    scope.saveSeekState({ immediatePlan: true });
    scope.syncSeekPanelUi();
    scope.updateSeekPathOverlay();
    scope.applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    scope.promptSeekManualRecalc();
  }

  function undoSeekHop() {
    if (
      !seekState ||
      !Array.isArray(seekState.hops) ||
      seekState.hops.length <= 1
    )
      return;
    if (seekScanning) scope.cancelSeekScanUi();
    seekState.complete = false;
    seekRunning = true;
    if (!Array.isArray(seekState.redoStack)) seekState.redoStack = [];
    const removed = seekState.hops.pop();
    seekState.redoStack.push(removed);
    const last = seekState.hops[seekState.hops.length - 1];
    seekState.currentFrom = { lat: last.lat, lon: last.lon };
    scope.saveSeekState({ immediatePlan: true });
    scope.syncSeekPanelUi();
    scope.applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    scope.promptSeekManualRecalc();
  }

  function redoSeekHop() {
    if (!seekState?.redoStack?.length) return;
    if (seekScanning) scope.cancelSeekScanUi();
    const hop = seekState.redoStack.pop();
    seekState.hops.push(hop);
    seekState.currentFrom = { lat: hop.lat, lon: hop.lon };
    seekState.complete = false;
    seekRunning = true;
    scope.saveSeekState({ immediatePlan: true });
    scope.syncSeekPanelUi();
    scope.applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    scope.promptSeekManualRecalc();
  }

  function resetSeekRun() {
    seekRunning = false;
    seekGoalInRange = false;
    seekSiteCandidateSlugs = new Set();
    seekActiveFetchKey = null;
    seekPendingGoalLat = null;
    seekPendingGoalLon = null;
    scope.setSeekGoalPlacementMode(false);
    scope.cancelSeekScanUi();
    scope.cancelSeekAncillaryLinksFetch();
    seekAncillaryLinksGen += 1;
    if (seekAncillaryLinksTimer) {
      window.clearTimeout(seekAncillaryLinksTimer);
      seekAncillaryLinksTimer = null;
    }
    seekState = null;
    scope.saveSeekState({ immediatePlan: true });
    scope.removeSeekLayers();
    scope.clearAllSeekHopViewsheds();
    scope.removeSeekAncillaryLinksLayer();
    scope.removeSeekGoalMarker();
    setViewshedVisible(C.DRAFT_VIEWSHED_SLUG, false);
    scope.setSeekStatus("");
    scope.syncSeekPanelUi();
    if (siteLinksPayload?.geojson) refreshFilteredLinks();
  }

  function restoreSeekSessionIfAny() {
    scope.rehydrateSeekStateFromConfig();
    if (!seekState?.running) return;
    if (!config.seek?.plan) {
      const plan = scope.seekStateToYamlPlan(seekState);
      if (plan) scope.persistSeekPlanToYaml({ immediate: true });
    }
    applySiteLayerFilters();
    seekRunning = !seekState.complete;
    seekPanelOpen = true;
    if (seekState.goalLat != null && seekState.goalLon != null) {
      seekPendingGoalLat = seekState.goalLat;
      seekPendingGoalLon = seekState.goalLon;
    }
    scope.populateSeekStartSelect();
    scope.syncSeekGoalUi();
    scope.syncSeekPanelUi();
    scope.updateSeekPathOverlay();
    if (!seekState.complete) {
      scope.promptSeekManualRecalc();
    } else {
      scope.applySeekLayers({
        candidates: { type: "FeatureCollection", features: [] },
        lines: { type: "FeatureCollection", features: [] },
      });
    }
  }


  function fitSites() {
    const points = [...sites];
    if (!points.length) return;
    const lons = points.map((p) => p.lon);
    const lats = points.map((p) => p.lat);
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const { latDelta, lonDelta } = kmToDegreeDeltas(
      centerLat,
      C.SITE_FIT_BUFFER_KM,
    );
    map.fitBounds(
      [
        [Math.min(...lons) - lonDelta, Math.min(...lats) - latDelta],
        [Math.max(...lons) + lonDelta, Math.max(...lats) + latDelta],
      ],
      { padding: 48, bearing: 0, pitch: 0, maxZoom: 15 },
    );
  }

  function resetHomeView() {
    if (terrainActive) {
      terrainActive = false;
      scope.hideTerrainOverlays();
    }
    map.resetNorthPitch();
  }

  function setBasemapKey(key) {
    if (!BASEMAPS[key]) return;
    currentBasemapKey = key;
    syncBasemapMenu();
    setBasemap(key);
    refreshTerrainSourceIfNeeded();
    scheduleSaveMapState();
  }


  map.on("load", () => {
    mapReady = true;
    syncMapViewport();
    map.resize();
    addSiteLayers();
    scope.wireMapInteractions();
    scope.connectProjectEvents();
    renderEntityPanel();
    scope.renderLandPanel();
    scope.setEntityTab(entityPanelTab);
    applyEntityVisibility();
    syncBasemapMenu();
    setBasemap(currentBasemapKey);
    if (savedMapState) {
      setSiteLinksVisible(showSiteLinks);
      scope.syncTerrainFromPitch();
    } else {
      fitSites();
    }
    void scope.refreshLandMapLayers();
    scope.restoreSeekSessionIfAny();
    restoring = false;
    map.once("idle", () => {
      void scope.loadViewshedIndex();
      void loadSiteLinks();
    });
  });

  map.on("pitch", () => {
    scope.syncTerrainFromPitch();
    scheduleSaveMapState();
    scope.syncSeekRefreshUi();
  });
  map.on("moveend", scheduleSaveMapState);
  map.on("moveend", onMapMoveEndForEntityPanel);
  map.on("moveend", onMapMoveEndForWarmPriorities);
  map.on("moveend", onMapMoveEndForSeek);
  map.on("rotateend", scheduleSaveMapState);
  if (mapBasemapMenu) {
    mapBasemapMenu.addEventListener("wa-select", (ev) => {
      const item = ev.detail.item;
      if (!item) return;
      setBasemapKey(
        item.value || item.getAttribute("data-basemap") || "street",
      );
    });
  }
  if (viewshedOpacityInput) {
    viewshedOpacityInput.addEventListener("input", (ev) => {
      scope.setViewshedOpacity(Number(ev.target.value) / 100);
      scheduleSaveMapState();
    });
  }
  sitePanelClose.addEventListener("click", deselectSite);
  if (sitePanelViewshedToggle) {
    sitePanelViewshedToggle.innerHTML = scope.mapToolIcon(
      "droplet",
      "Viewshed coverage",
    );
    sitePanelViewshedToggle.addEventListener("click", () => {
      if (!selectedSlug) return;
      setViewshedVisible(selectedSlug, !isViewshedVisible(selectedSlug));
      scheduleSaveMapState();
    });
  }
  if (sitePanelEditOpen) {
    sitePanelEditOpen.addEventListener("click", () => scope.openEditPanel());
  }
  if (sitePanelEditClose) {
    sitePanelEditClose.addEventListener("click", cancelEdit);
  }
  if (sitePanelEditCancel) {
    sitePanelEditCancel.addEventListener("click", cancelEdit);
  }
  if (sitePanelEditSave) {
    sitePanelEditSave.addEventListener("click", () => {
      void scope.saveEdit();
    });
  }
  if (sitePanelEditCopyCoords) {
    sitePanelEditCopyCoords.addEventListener("click", () => {
      void scope.copyEditCoords();
    });
  }
  if (sitePanelCopyCoords) {
    sitePanelCopyCoords.addEventListener("click", () => {
      const site = selectedSlug ? siteBySlug.get(selectedSlug) : null;
      if (!site) return;
      void scope.copyCoordPair(site.lat, site.lon);
    });
  }
  if (sitePanelCopyPlss) {
    sitePanelCopyPlss.addEventListener("click", () => {
      void scope.copyPlssFromElement(document.getElementById("site-panel-plss"));
    });
  }
  if (sitePanelEditCopyPlss) {
    sitePanelEditCopyPlss.addEventListener("click", () => {
      void scope.copyPlssFromElement(document.getElementById("site-panel-edit-plss"));
    });
  }
  if (sitePanelCreateCopyPlss) {
    sitePanelCreateCopyPlss.addEventListener("click", () => {
      void scope.copyPlssFromElement(
        document.getElementById("site-panel-create-plss"),
      );
    });
  }
  if (sitePanelEditLat) {
    sitePanelEditLat.addEventListener("input", onEditCoordsChanged);
    sitePanelEditLat.addEventListener("paste", (ev) => {
      const text = ev.clipboardData && ev.clipboardData.getData("text");
      if (text && scope.applyCoordPaste(text, "lat")) ev.preventDefault();
    });
  }
  if (sitePanelEditLon) {
    sitePanelEditLon.addEventListener("input", onEditCoordsChanged);
    sitePanelEditLon.addEventListener("paste", (ev) => {
      const text = ev.clipboardData && ev.clipboardData.getData("text");
      if (text && scope.applyCoordPaste(text, "lon")) ev.preventDefault();
    });
  }
  if (sitePanelEditViewshed) {
    sitePanelEditViewshed.addEventListener("change", (ev) => {
      if (!editMode) return;
      const coords = scope.readEditCoords();
      const atOriginal =
        coords && scope.coordsMatchEditSnapshot(coords.lat, coords.lon);
      const visible = ev.target.checked;
      if (atOriginal && editSlug) {
        setViewshedVisible(editSlug, visible);
        if (!visible) scope.removeDraftViewshed();
        return;
      }
      setViewshedVisible(C.DRAFT_VIEWSHED_SLUG, visible);
      if (visible && coords) {
        hideViewshedLayerForEdit(editSlug);
        void loadDraftViewshedAt(coords.lat, coords.lon);
      } else {
        scope.removeDraftViewshed();
      }
    });
  }
  sitePanelCreateClose.addEventListener("click", cancelCreate);
  sitePanelCreateCancel.addEventListener("click", cancelCreate);
  sitePanelCreateSave.addEventListener("click", () => {
    void saveNewPlacement();
  });
  sitePanelCreateName.addEventListener("input", syncCreateSlugPreview);
  sitePanelCreateName.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      void saveNewPlacement();
    }
  });
  if (sitePanelCreateTagForm) {
    sitePanelCreateTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      addCreatePanelTagFromInput();
    });
  }
  if (entityPanelToggle) {
    entityPanelToggle.addEventListener("click", () => {
      if (entityPanelOpen) {
        setEntityPanelOpen(false);
      } else {
        setEntityPanelOpen(true);
      }
    });
  }
  if (entityPanelFilterVisible) {
    entityPanelFilterVisible.addEventListener("change", () => {
      entityPanelFilterByViewport = !!entityPanelFilterVisible.checked;
      renderEntityPanel();
      scheduleSaveMapState();
    });
  }
  if (mapToolSeek) {
    mapToolSeek.addEventListener("click", () => scope.toggleSeekPanel());
  }
  if (seekPanelClose) {
    seekPanelClose.addEventListener("click", () => scope.toggleSeekPanel(false));
  }
  if (seekStartSelect) {
    seekStartSelect.addEventListener("change", () =>
      scope.maybeAutoStartSeekFromSelects(),
    );
  }
  if (seekSetGoalBtn) {
    seekSetGoalBtn.addEventListener("click", () => {
      if (!seekPanelOpen || seekScanning) return;
      scope.setSeekGoalPlacementMode(!seekGoalPlacementMode);
    });
  }
  if (seekUndoBtn) {
    seekUndoBtn.addEventListener("click", () => scope.undoSeekHop());
  }
  if (seekRedoBtn) {
    seekRedoBtn.addEventListener("click", () => scope.redoSeekHop());
  }
  if (seekResetBtn) {
    seekResetBtn.addEventListener("click", () => scope.resetSeekRun());
  }
  if (seekConvertSitesBtn) {
    seekConvertSitesBtn.addEventListener("click", () => {
      void scope.openSeekConvertModal();
    });
  }
  if (seekConvertNamePrefix) {
    seekConvertNamePrefix.addEventListener("input", () => {
      scope.syncSeekConvertHopCountText();
      scope.syncSeekConvertSaveButton();
    });
  }
  if (seekConvertTagForm) {
    seekConvertTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      scope.addSeekConvertTagFromInput();
    });
  }
  if (seekConvertSave) {
    seekConvertSave.addEventListener("click", () => {
      void scope.saveSeekConvertModal();
    });
  }
  if (seekRefreshBtn) {
    seekRefreshBtn.addEventListener("click", () => {
      if (!seekSessionActive() || seekScanning) return;
      void scope.refreshSeekCandidates();
    });
  }
  if (mapToolSites) {
    mapToolSites.addEventListener("click", () => {
      toggleEntityPanel();
    });
  }
  if (entityPanelAddSite) {
    entityPanelAddSite.addEventListener("click", () => {
      void openAddSiteModal();
    });
  }
  if (entityPanelImportSites) {
    entityPanelImportSites.addEventListener("click", () => {
      void scope.openImportSitesModal();
    });
  }
  if (entityPanelImportLand) {
    entityPanelImportLand.addEventListener("click", () => {
      void scope.openImportLandModal();
    });
  }
  if (entityPanelAddLandFolder) {
    entityPanelAddLandFolder.addEventListener("click", () => {
      scope.createLandFolder();
    });
  }
  scope.queueLandSidebarMigrationFromLayerOrder();
  for (const tabBtn of entityPanelTabs) {
    tabBtn.addEventListener("click", () => {
      scope.setEntityTab(tabBtn.getAttribute("data-entity-tab") || "sites");
    });
  }
  scope.initLandPanelDragDrop();
  if (importLandGdb) {
    importLandGdb.addEventListener("change", () => {
      void scope.previewImportLandPath(importLandGdb.value);
    });
  }
  if (importLandSave) {
    importLandSave.addEventListener("click", () => {
      void scope.saveImportLandModal();
    });
  }
  if (importLandSelectAll) {
    importLandSelectAll.addEventListener("click", () => {
      if (!importLandLayerList) return;
      for (const input of importLandLayerList.querySelectorAll(
        'input[type="checkbox"][data-layer-name]',
      )) {
        input.checked = true;
        input.dispatchEvent(new Event("change"));
      }
    });
  }
  if (importLandClearAll) {
    importLandClearAll.addEventListener("click", () => {
      if (!importLandLayerList) return;
      for (const input of importLandLayerList.querySelectorAll(
        'input[type="checkbox"][data-layer-name]',
      )) {
        input.checked = false;
        input.dispatchEvent(new Event("change"));
      }
    });
  }
  if (importLandModal) {
    importLandModal.addEventListener("wa-after-show", () => {
      if (importLandPreviewMap)
        requestAnimationFrame(() => importLandPreviewMap.resize());
    });
    importLandModal.addEventListener("wa-after-hide", () => {
      scope.resetImportLandModal();
    });
  }
  if (editLandSave) {
    editLandSave.addEventListener("click", () => {
      void scope.saveEditLandModal();
    });
  }
  if (editLandSelectAll) {
    editLandSelectAll.addEventListener("click", () => {
      if (!editLandLayerList) return;
      for (const input of editLandLayerList.querySelectorAll(
        'input[type="checkbox"][data-layer-name]',
      )) {
        input.checked = true;
        input.dispatchEvent(new Event("change"));
      }
    });
  }
  if (editLandClearAll) {
    editLandClearAll.addEventListener("click", () => {
      if (!editLandLayerList) return;
      for (const input of editLandLayerList.querySelectorAll(
        'input[type="checkbox"][data-layer-name]',
      )) {
        input.checked = false;
        input.dispatchEvent(new Event("change"));
      }
    });
  }
  if (editLandModal) {
    editLandModal.addEventListener("wa-after-show", () => {
      if (editLandPreviewMap) {
        requestAnimationFrame(() => {
          editLandPreviewMap.resize();
          if (editLandPreviewRefresh) void editLandPreviewRefresh();
        });
      }
    });
    editLandModal.addEventListener("wa-after-hide", () => {
      scope.destroyEditLandPreviewMap();
      editLandSourceId = "";
      editLandPreviewRefresh = null;
      editLandPreviewLayers = [];
      editLandPreviewPath = "";
      editLandLayerStyles = new Map();
      editLandLayerConfigs = new Map();
      if (editLandLayerList) editLandLayerList.innerHTML = "";
      scope.setEditLandError("");
    });
  }
  if (entityPanelBulkTag) {
    entityPanelBulkTag.addEventListener("click", () => {
      void scope.openBulkTagModal();
    });
  }
  if (bulkTagSave) {
    bulkTagSave.addEventListener("click", () => {
      void scope.saveBulkTagModal();
    });
  }
  if (bulkTagAddForm) {
    bulkTagAddForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      scope.addBulkTagFromInput();
    });
  }
  if (bulkTagModal) {
    bulkTagModal.addEventListener("wa-after-show", () => {
      bulkTagModalOpen = true;
    });
    bulkTagModal.addEventListener("wa-after-hide", () => {
      bulkTagModalOpen = false;
      scope.setBulkTagError("");
    });
  }
  if (bulkTagAddInput) {
    bulkTagAddInput.addEventListener("input", () => {
      scope.syncBulkTagSaveButton();
    });
  }
  if (importSitesFile) {
    importSitesFile.addEventListener("change", () => {
      const file = importSitesFile.files && importSitesFile.files[0];
      if (file) void scope.previewImportFile(file);
    });
  }
  if (importSitesSave) {
    importSitesSave.addEventListener("click", () => {
      void scope.saveImportSitesModal();
    });
  }
  if (importSitesTagForm) {
    importSitesTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      scope.addImportDraftTagFromInput();
    });
  }
  if (importSitesTagInput) {
    importSitesTagInput.addEventListener("input", () => {
      scope.syncImportSaveButton();
    });
  }
  if (importSitesFilterVisible) {
    importSitesFilterVisible.addEventListener("change", () => {
      importFilterByViewport = !!importSitesFilterVisible.checked;
      scope.renderImportPointList();
    });
  }
  if (importSitesSelectAll) {
    importSitesSelectAll.addEventListener("click", () => {
      scope.setAllImportPointsIgnored(false);
    });
  }
  if (importSitesClearAll) {
    importSitesClearAll.addEventListener("click", () => {
      scope.setAllImportPointsIgnored(true);
    });
  }
  if (importSitesModal) {
    importSitesModal.addEventListener("wa-after-show", () => {
      if (importPreviewMap) {
        requestAnimationFrame(() => {
          importPreviewMap.resize();
        });
      }
    });
    importSitesModal.addEventListener("wa-after-hide", () => {
      scope.resetImportSitesModal();
    });
  }
  if (addSiteSave) {
    addSiteSave.addEventListener("click", () => {
      void saveAddSiteModal();
    });
  }
  if (addSiteTagForm) {
    addSiteTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      addDraftTagFromInput();
    });
  }
  if (addSiteCoords) {
    addSiteCoords.addEventListener("paste", (ev) => {
      const text = ev.clipboardData?.getData("text") || "";
      const pair = parseCoordPairFromText(text);
      if (!pair) return;
      ev.preventDefault();
      addSiteCoords.value = `${formatCoord(pair.lat)}, ${formatCoord(pair.lon)}`;
      setAddSiteError("");
    });
    addSiteCoords.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        void saveAddSiteModal();
      }
    });
  }
  if (addSiteName) {
    addSiteName.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        addSiteCoords?.focus();
      }
    });
  }
  if (sitePanelCreateViewshed) {
    sitePanelCreateViewshed.addEventListener("change", (ev) => {
      if (!createMode) return;
      const visible = ev.target.checked;
      setViewshedVisible(C.DRAFT_VIEWSHED_SLUG, visible);
      if (!visible) scope.removeDraftViewshed();
    });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (createMode) {
      cancelCreate();
      return;
    }
    if (seekPanelOpen && seekRunning) {
      scope.resetSeekRun();
      scope.toggleSeekPanel(false);
      return;
    }
    if (seekPanelOpen) {
      scope.toggleSeekPanel(false);
      return;
    }
    if (editMode) {
      scope.cancelEdit();
      return;
    }
    if (selectedSlug) deselectSite();
  });


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


  return {
    reloadViewshedsForSimChange,
    setViewshedSimulation,
  }

}
