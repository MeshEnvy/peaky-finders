(function () {
  const config = window.PEAKY_PROJECT || {};
  const projectSlug = config.slug;
  let sites = config.sites || [];

  const TERRAIN_SOURCE = "terrain-dem";
  const TERRAIN_HILLSHADE = "terrain-hillshade";
  const BASEMAP_REFERENCE_SOURCE = "basemap-reference";
  const BASEMAP_REFERENCE_LAYER = "basemap-reference";
  const SITES_SOURCE = "sites";
  const SITES_CIRCLE = "sites-circle";
  const SITES_LABELS = "sites-labels";
  const SITES_SELECTED = "sites-selected";
  const LINKS_SOURCE = "site-links";
  const LINKS_LAYER = "site-links-line";
  const LINKS_LABELS_LAYER = "site-links-label";
  const DRAFT_LINKS_SOURCE = "draft-site-links";
  const DRAFT_LINKS_LAYER = "draft-site-links-line";
  const DRAFT_LINKS_LABELS_LAYER = "draft-site-links-label";
  const EDIT_HISTORY_LINKS_SOURCE = "edit-history-links";
  const EDIT_HISTORY_LINKS_LAYER = "edit-history-links-line";
  const EDIT_HISTORY_LINKS_LABELS_LAYER = "edit-history-links-label";
  const VIEWSHED_OPACITY_DEFAULT = 0.75;
  const DRAFT_VIEWSHED_SLUG = "_draft";
  const VIEWSHED_PREVIEW_RASTER_DIMENSION = 256;
  const COORD_PREFETCH_MS = 350;
  const DRAFT_MARKER_COLOR = "#fbbf24";
  const simDefaults = config.simulation || {};
  const VIEWSHED_RADIUS_KM_MIN = Number(simDefaults.radius_km_min) || 1;
  const VIEWSHED_RADIUS_KM_MAX = Number(simDefaults.radius_km_max) || 100;
  const VIEWSHED_RASTER_MIN = Number(simDefaults.raster_dimension_min) || 128;
  const VIEWSHED_RASTER_MAX = Number(simDefaults.raster_dimension_max) || 4096;
  const PITCH_TERRAIN_ON = 12;
  const PITCH_TERRAIN_OFF = 6;
  const SITE_FIT_BUFFER_KM = 30;
  const MAP_STATE_KEY = `peaky.map.v1.${projectSlug}`;
  const MAP_STATE_SAVE_MS = 400;
  const MAP_GLYPHS_URL = "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf";
  const MAP_TEXT_FONT = ["Noto Sans Regular"];
  const MAP_LABEL_FONT = ["Noto Sans Medium"];

  function syncOpacitySlider() {
    const opacityEl = document.getElementById("viewshed-opacity");
    if (opacityEl) opacityEl.value = String(Math.round(viewshedOpacity * 100));
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
      ["satellite", "Satellite"],
    ]) {
      const item = document.createElement("wa-dropdown-item");
      item.setAttribute("data-basemap", key);
      item.value = key;
      item.textContent = label;
      basemapDropdown.appendChild(item);
    }
    basemapDropdown.insertBefore(basemapBtn, basemapDropdown.firstChild);

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
    opacitySlider.value = String(Math.round(viewshedOpacity * 100));
    opacitySlider.title = "Viewshed opacity";

    opacityMenu.appendChild(opacityLabel);
    opacityMenu.appendChild(opacitySlider);
    opacityDropdown.appendChild(opacityBtn);
    opacityDropdown.appendChild(opacityMenu);

    for (const el of [basemapDropdown, sitesBtn, opacityDropdown, settingsBtn]) {
      navGroup.appendChild(el);
    }

    syncOpacitySlider();

    return {
      mapBasemapMenu: basemapDropdown,
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
    if (!saved || !Array.isArray(saved.center) || saved.center.length !== 2) return false;
    if (!BASEMAPS[saved.basemap]) return false;
    if (typeof saved.zoom !== "number") return false;
    return true;
  }

  function loadMapState() {
    try {
      const raw = localStorage.getItem(MAP_STATE_KEY);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      return isValidSavedState(saved) ? saved : null;
    } catch (_) {
      return null;
    }
  }

  function persistMapState(state) {
    try {
      localStorage.setItem(MAP_STATE_KEY, JSON.stringify(state));
    } catch (_) {
      /* private mode or quota */
    }
  }

  const savedMapState = loadMapState();
  let currentBasemapKey =
    savedMapState && BASEMAPS[savedMapState.basemap] ? savedMapState.basemap : "street";
  let showSiteLinks = savedMapState?.showLinks ?? true;
  let viewshedOpacity = savedMapState?.viewshedOpacity ?? VIEWSHED_OPACITY_DEFAULT;
  const defaultRadiusKm = Number(simDefaults.radius_km) || 60;
  const defaultRasterDimension = Number(simDefaults.raster_dimension) || 500;
  let viewshedRadiusKm = defaultRadiusKm;
  let viewshedRasterDimension = defaultRasterDimension;
  viewshedRadiusKm = Math.max(
    VIEWSHED_RADIUS_KM_MIN,
    Math.min(VIEWSHED_RADIUS_KM_MAX, viewshedRadiusKm),
  );
  viewshedRasterDimension = Math.max(
    VIEWSHED_RASTER_MIN,
    Math.min(VIEWSHED_RASTER_MAX, Math.round(viewshedRasterDimension)),
  );

  function syncToolbarFromSaved(saved) {
    if (!saved) return;
    if (BASEMAPS[saved.basemap]) currentBasemapKey = saved.basemap;
    if (typeof saved.showLinks === "boolean") showSiteLinks = saved.showLinks;
    if (typeof saved.viewshedOpacity === "number") {
      viewshedOpacity = saved.viewshedOpacity;
      syncOpacitySlider();
    }
  }

  function clampRadiusKm(km) {
    return Math.max(VIEWSHED_RADIUS_KM_MIN, Math.min(VIEWSHED_RADIUS_KM_MAX, Number(km)));
  }

  function clampRasterDimension(px) {
    return Math.max(
      VIEWSHED_RASTER_MIN,
      Math.min(VIEWSHED_RASTER_MAX, Math.round(Number(px))),
    );
  }

  function setViewshedSimulation(radiusKm, rasterDimension) {
    const nextRadius = clampRadiusKm(radiusKm);
    const nextRaster = clampRasterDimension(rasterDimension);
    const changed =
      nextRadius !== viewshedRadiusKm || nextRaster !== viewshedRasterDimension;
    viewshedRadiusKm = nextRadius;
    viewshedRasterDimension = nextRaster;
    if (window.PEAKY_HOME_SETTINGS && typeof window.PEAKY_HOME_SETTINGS.updateGearSummary === "function") {
      window.PEAKY_HOME_SETTINGS.updateGearSummary();
    }
    return changed;
  }

  syncToolbarFromSaved(savedMapState);
  setViewshedSimulation(defaultRadiusKm, defaultRasterDimension);

  function basemapStyle(key) {
    const bm = BASEMAPS[key] || BASEMAPS.street;
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
      layers: [{ id: "basemap", type: "raster", source: "basemap" }],
    };
  }

  function terrainDemSourceSpec() {
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

  function kmToDegreeDeltas(latDeg, km) {
    const m = km * 1000;
    const latDelta = m / 111_320;
    const lonDelta = m / (111_320 * Math.cos((latDeg * Math.PI) / 180));
    return { latDelta, lonDelta };
  }


  function sitesGeoJson() {
    return {
      type: "FeatureCollection",
      features: sites.map((site) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [site.lon, site.lat] },
        properties: { name: site.name, slug: site.slug },
      })),
    };
  }

  const map = new maplibregl.Map({
    container: "map",
    style: basemapStyle(currentBasemapKey),
    center: savedMapState ? savedMapState.center : [-98.35, 39.5],
    zoom: savedMapState ? savedMapState.zoom : 4,
    maxPitch: 85,
    bearing: savedMapState ? savedMapState.bearing || 0 : 0,
    pitch: savedMapState ? savedMapState.pitch || 0 : 0,
    attributionControl: { compact: true },
  });
  const navControl = new maplibregl.NavigationControl({ visualizePitch: true });
  map.addControl(navControl, "top-right");
  const mapToolbarRefs = installMapToolbar(navControl._container);
  const mapBasemapMenu = mapToolbarRefs.mapBasemapMenu;
  const mapToolSites = mapToolbarRefs.mapToolSites;
  const viewshedOpacityInput = mapToolbarRefs.viewshedOpacityInput;
  const compassButton = navControl._container.querySelector(".maplibregl-ctrl-compass");
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
  if (savedMapState?.viewshedVisible && typeof savedMapState.viewshedVisible === "object") {
    for (const [slug, visible] of Object.entries(savedMapState.viewshedVisible)) {
      viewshedVisible.set(slug, visible !== false);
    }
  }
  const viewshedLoading = new Set();
  const pinLoadOverlays = document.getElementById("pin-load-overlays");
  const pinSpinners = new Map();
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
  const sitePanelCreateClose = document.getElementById("site-panel-create-close");
  const sitePanelCreateName = document.getElementById("site-panel-create-name");
  const sitePanelSlugPreview = document.getElementById("site-panel-slug-preview");
  const sitePanelCreateCoords = document.getElementById("site-panel-create-coords");
  const sitePanelCreateError = document.getElementById("site-panel-create-error");
  const sitePanelCreateSave = document.getElementById("site-panel-create-save");
  const sitePanelCreateCancel = document.getElementById("site-panel-create-cancel");
  const sitePanelViewshed = document.getElementById("site-panel-viewshed");
  const sitePanelViewshedHint = document.getElementById("site-panel-viewshed-hint");
  const sitePanelCreateViewshed = document.getElementById("site-panel-create-viewshed");
  const sitePanelCreateViewshedSection = document.getElementById("site-panel-create-viewshed-section");
  const sitePanelCreateTitle = document.getElementById("site-panel-create-title");
  const sitePanelCreateBadge = document.getElementById("site-panel-create-badge");
  const sitePanelCreateLinksLabel = document.getElementById("site-panel-create-links-label");
  const sitePanelViewshedSection = document.getElementById("site-panel-viewshed-section");
  const sitePanelEdit = document.getElementById("site-panel-edit");
  const sitePanelEditOpen = document.getElementById("site-panel-edit-open");
  const sitePanelEditClose = document.getElementById("site-panel-edit-close");
  const sitePanelEditName = document.getElementById("site-panel-edit-name");
  const sitePanelEditSlug = document.getElementById("site-panel-edit-slug");
  const sitePanelEditLat = document.getElementById("site-panel-edit-lat");
  const sitePanelEditLon = document.getElementById("site-panel-edit-lon");
  const sitePanelEditCopyCoords = document.getElementById("site-panel-edit-copy-coords");
  const sitePanelCopyPlss = document.getElementById("site-panel-copy-plss");
  const sitePanelEditCopyPlss = document.getElementById("site-panel-edit-copy-plss");
  const sitePanelCreateCopyPlss = document.getElementById("site-panel-create-copy-plss");
  const sitePanelEditCoordHistory = document.getElementById("site-panel-edit-coord-history");
  const sitePanelEditViewshed = document.getElementById("site-panel-edit-viewshed");
  const sitePanelEditViewshedSection = document.getElementById("site-panel-edit-viewshed-section");
  const sitePanelEditError = document.getElementById("site-panel-edit-error");
  const sitePanelEditSave = document.getElementById("site-panel-edit-save");
  const sitePanelEditCancel = document.getElementById("site-panel-edit-cancel");
  const sitePanelEditLinksLabel = document.getElementById("site-panel-edit-links-label");
  const entityPanel = document.getElementById("entity-panel");
  const entityPanelToggle = document.getElementById("entity-panel-toggle");
  const entityPanelSitesList = document.getElementById("entity-panel-sites-list");
  const entityPanelTagFilters = document.getElementById("entity-panel-tag-filters");
  const entityPanelAddSite = document.getElementById("entity-panel-add-site");
  const sitePanelTags = document.getElementById("site-panel-tags");
  const sitePanelTagsSection = document.getElementById("site-panel-tags-section");
  const siteHidden = new Set(savedMapState?.hiddenSites || []);
  const activeTagFilters = new Set(
    Array.isArray(savedMapState?.tagFilters)
      ? savedMapState.tagFilters.filter((tag) => typeof tag === "string" && tag.trim())
      : [],
  );
  let entityPanelOpen = false;
  let tagAddOpen = false;

  function ensureTerrainSource() {
    if (map.getSource(TERRAIN_SOURCE)) return;
    map.addSource(TERRAIN_SOURCE, terrainDemSourceSpec());
  }

  function ensureHillshadeLayer() {
    if (map.getLayer(TERRAIN_HILLSHADE)) return;
    ensureTerrainSource();
    map.addLayer(
      {
        id: TERRAIN_HILLSHADE,
        type: "hillshade",
        source: TERRAIN_SOURCE,
        paint: {
          "hillshade-exaggeration": 0.35,
          "hillshade-shadow-color": "#0a0e14",
          "hillshade-highlight-color": "#ffffff",
          "hillshade-accent-color": "#64748b",
        },
      },
      "basemap",
    );
  }

  function removeHillshadeLayer() {
    if (map.getLayer(TERRAIN_HILLSHADE)) map.removeLayer(TERRAIN_HILLSHADE);
  }

  function removeTerrainSource() {
    removeHillshadeLayer();
    if (map.getSource(TERRAIN_SOURCE)) map.removeSource(TERRAIN_SOURCE);
  }

  function linksApiUrl() {
    return `/api/p/${projectSlug}/links`;
  }

  function linksWarmApiUrl() {
    return `/api/p/${projectSlug}/links/warm`;
  }




  function sitesApiUrl() {
    return `/api/p/${projectSlug}/sites`;
  }

  function slugifyName(name) {
    let base = String(name || "")
      .replace(/[^\w\s-]/g, "")
      .trim()
      .replace(/[\s_]+/g, "-")
      .toLowerCase()
      .replace(/^-+|-+$/g, "");
    return base || "site";
  }

  function previewSlugForName(name) {
    const base = slugifyName(name);
    const taken = new Set([...siteBySlug.keys()]);
    if (!taken.has(base)) return base;
    let n = 2;
    while (taken.has(`${base}-${n}`)) n += 1;
    return `${base}-${n}`;
  }

  function showPanelView() {
    createMode = false;
    editMode = false;
    sitePanelView.hidden = false;
    sitePanelCreate.hidden = true;
    if (sitePanelEdit) sitePanelEdit.hidden = true;
    syncEditMapShell();
  }

  function showPanelEdit() {
    createMode = false;
    editMode = true;
    sitePanelView.hidden = true;
    sitePanelCreate.hidden = true;
    if (sitePanelEdit) sitePanelEdit.hidden = false;
    syncEditMapShell();
  }

  function showPanelCreate() {
    createMode = true;
    editMode = false;
    selectedSlug = null;
    updateSelectedLayer();
    sitePanelView.hidden = true;
    sitePanelCreate.hidden = false;
    if (sitePanelEdit) sitePanelEdit.hidden = true;
    syncCreatePanelForKind();
    syncEditMapShell();
  }

  function setCreateError(message) {
    if (!message) {
      sitePanelCreateError.hidden = true;
      sitePanelCreateError.textContent = "";
      return;
    }
    sitePanelCreateError.textContent = message;
    sitePanelCreateError.hidden = false;
  }

  function removeDraftMarker() {
    if (draftMarker) {
      draftMarker.remove();
      draftMarker = null;
    }
  }

  function syncEditMapShell() {
    if (mapShell) mapShell.classList.toggle("edit-mode", editMode);
    syncMapCursor();
  }

  function syncMapCursor() {
    if (!mapReady) return;
    map.getCanvas().style.cursor = addPlacementMode || editMode ? "crosshair" : "";
  }

  function setAddPlacementMode(kind) {
    addPlacementMode = kind;
    if (entityPanelAddSite) {
      entityPanelAddSite.classList.toggle("active", kind === "site");
    }
    if (mapShell) {
      mapShell.classList.toggle("add-placement-mode", !!kind);
      mapShell.classList.toggle("add-site-mode", !!kind);
    }
    syncMapCursor();
    if (!kind) cancelCreate();
  }

  function syncBasemapMenu() {
    if (!mapBasemapMenu) return;
    for (const btn of mapBasemapMenu.querySelectorAll("[data-basemap]")) {
      const active = btn.getAttribute("data-basemap") === currentBasemapKey;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-current", active ? "true" : "false");
    }
  }

  function syncEntityPanelToggles() {
    if (mapToolSites) {
      const active = entityPanelOpen;
      mapToolSites.classList.toggle("active", active);
      mapToolSites.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function setEntityPanelOpen(open) {
    entityPanelOpen = !!open;
    if (entityPanel) entityPanel.hidden = !entityPanelOpen;
    if (mapShell) mapShell.classList.toggle("entity-panel-open", entityPanelOpen);
    if (entityPanelToggle) {
      entityPanelToggle.setAttribute("aria-expanded", entityPanelOpen ? "true" : "false");
      entityPanelToggle.setAttribute(
        "aria-label",
        entityPanelOpen ? "Hide sites" : "Show sites",
      );
    }
    syncEntityPanelToggles();
  }

  function toggleEntityPanel() {
    setEntityPanelOpen(!entityPanelOpen);
  }

  function combineLayerFilters(...parts) {
    const filters = parts.filter(Boolean);
    if (!filters.length) return true;
    if (filters.length === 1) return filters[0];
    return ["all", ...filters];
  }

  function editSiteLayerFilter() {
    if (editMode && editSlug) {
      return ["!=", ["get", "slug"], editSlug];
    }
    return null;
  }


  function siteVisibilityFilter() {
    const hidden = [];
    for (const site of sites) {
      if (isSiteMapHidden(site.slug)) hidden.push(site.slug);
    }
    if (!hidden.length) return null;
    return ["!", ["in", ["get", "slug"], ["literal", hidden]]];
  }


  function applySiteLayerFilters() {
    if (!mapReady) return;
    const filter = combineLayerFilters(siteVisibilityFilter(), editSiteLayerFilter());
    for (const layerId of [SITES_CIRCLE, SITES_LABELS]) {
      if (!map.getLayer(layerId)) continue;
      map.setFilter(layerId, filter);
    }
    updateSelectedLayer();
  }


  function editCoordsMovedFromSnapshot() {
    const coords = readEditCoords();
    if (!coords || !editSnapshot) return false;
    return !coordsMatchEditSnapshot(coords.lat, coords.lon);
  }

  function linkFeatureTouchesSnapshotCoords(feature) {
    if (!editMode || !editSnapshot || !editCoordsMovedFromSnapshot()) return false;
    const geom = feature.geometry;
    if (!geom || geom.type !== "LineString" || !Array.isArray(geom.coordinates)) return false;
    const snapLon = Number(editSnapshot.lon);
    const snapLat = Number(editSnapshot.lat);
    for (const pt of geom.coordinates) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const lon = Number(pt[0]);
      const lat = Number(pt[1]);
      if (Math.abs(lon - snapLon) < 1e-5 && Math.abs(lat - snapLat) < 1e-5) return true;
    }
    return false;
  }

  function filterSiteLinksGeoJson(geojson) {
    if (!geojson || !geojson.features) return geojson;
    const features = geojson.features.filter((feature) => {
      const props = feature.properties || {};
      if (isSiteMapHidden(props.a) || isSiteMapHidden(props.b)) return false;
      if (editMode && editSlug) {
        if (props.a === editSlug || props.b === editSlug) return false;
      }
      if (linkFeatureTouchesSnapshotCoords(feature)) return false;
      return true;
    });
    return { type: geojson.type || "FeatureCollection", features };
  }


  function refreshFilteredLinks() {
    if (siteLinksPayload && siteLinksPayload.geojson) {
      addSiteLinksLayer(siteLinksPayload.geojson);
    }
  }

  function applyViewshedVisibilityForSite(slug) {
    const layerId = viewshedLayerId(slug);
    if (!map.getLayer(layerId)) return;
    const visible = !isSiteMapHidden(slug) && isViewshedVisible(slug);
    map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }

  function applyEntityVisibility() {
    applySiteLayerFilters();
    for (const site of sites) {
      applyViewshedVisibilityForSite(site.slug);
    }
    refreshFilteredLinks();
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
    for (const tag of activeTagFilters) {
      if (tags.includes(tag)) return true;
    }
    return false;
  }

  function isSiteMapHidden(slug) {
    if (siteHidden.has(slug)) return true;
    const site = siteBySlug.get(slug);
    if (!site) return true;
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
    scheduleSaveMapState();
  }

  function setSiteHidden(slug, hidden) {
    if (hidden) siteHidden.add(slug);
    else siteHidden.delete(slug);
    applyEntityVisibility();
    scheduleSaveMapState();
  }


  function removeViewshedLayer(slug) {
    const layerId = viewshedLayerId(slug);
    const sourceId = viewshedSourceId(slug);
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    viewshedLoading.delete(slug);
    updatePinOverlays();
  }

  function siteDeleteUrl(slug) {
    return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}`;
  }


  function unregisterSite(slug) {
    const idx = sites.findIndex((s) => s.slug === slug);
    if (idx >= 0) sites.splice(idx, 1);
    siteBySlug.delete(slug);
    siteHidden.delete(slug);
    viewshedVisible.delete(slug);
    removeViewshedLayer(slug);
    if (selectedSlug === slug) deselectSite();
    addSiteLayers();
    renderEntityPanel();
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

  function renderEntityPanel() {
    renderTagFilters();
    if (entityPanelSitesList) {
      entityPanelSitesList.innerHTML = "";
      const sortedSites = [...sites]
        .filter((site) => sitePassesTagFilter(site))
        .sort((a, b) => a.name.localeCompare(b.name));
      if (!sortedSites.length) {
        const empty = document.createElement("div");
        empty.className = "entity-panel__empty";
        empty.textContent = activeTagFilters.size
          ? "No sites match the selected tags."
          : "No sites yet.";
        entityPanelSitesList.appendChild(empty);
      }
      for (const site of sortedSites) {
        entityPanelSitesList.appendChild(buildSiteEntityRow(site));
      }
    }
  }

  function buildSiteEntityRow(site) {
    const row = document.createElement("div");
    row.className = "entity-panel__row";
    row.dataset.siteSlug = site.slug;
    if (selectedSlug === site.slug) row.classList.add("entity-panel__row--selected");
    if (isSiteHidden(site.slug)) row.classList.add("entity-panel__row--hidden");

    const main = document.createElement("div");
    main.className = "entity-panel__main";
    const name = document.createElement("div");
    name.className = "entity-panel__name";
    name.textContent = site.name;
    const meta = document.createElement("div");
    meta.className = "entity-panel__meta";
    const tags = siteTags(site);
    meta.textContent = tags.length ? tags.join(", ") : "";
    main.appendChild(name);
    main.appendChild(meta);

    const controls = document.createElement("div");
    controls.className = "entity-panel__controls";

    const eyeBtn = document.createElement("wa-button");
    eyeBtn.size = "s";
    eyeBtn.appearance = "outlined";
    eyeBtn.title = isSiteHidden(site.slug) ? "Show site" : "Hide site";
    eyeBtn.textContent = isSiteHidden(site.slug) ? "Show" : "Hide";
    eyeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setSiteHidden(site.slug, !isSiteHidden(site.slug));
    });

    const vsCheck = document.createElement("input");
    vsCheck.type = "checkbox";
    vsCheck.className = "entity-panel__viewshed-check";
    vsCheck.title = "Show viewshed";
    vsCheck.checked = isViewshedVisible(site.slug);
    vsCheck.disabled = isSiteHidden(site.slug);
    vsCheck.addEventListener("click", (ev) => ev.stopPropagation());
    vsCheck.addEventListener("change", (ev) => {
      ev.stopPropagation();
      setViewshedVisible(site.slug, ev.target.checked);
      scheduleSaveMapState();
    });

    const delBtn = document.createElement("wa-button");
    delBtn.size = "s";
    delBtn.appearance = "outlined";
    delBtn.variant = "danger";
    delBtn.title = "Delete site";
    delBtn.textContent = "Del";
    delBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      void deleteSite(site.slug);
    });

    controls.appendChild(eyeBtn);
    controls.appendChild(vsCheck);
    controls.appendChild(delBtn);

    row.appendChild(main);
    row.appendChild(controls);
    row.addEventListener("click", () => selectSite(site.slug));
    return row;
  }


  function registerSite(site) {
    sites.push(site);
    siteBySlug.set(site.slug, site);
    addSiteLayers();
    renderEntityPanel();
  }


  function syncCreateSlugPreview() {
    const name = sitePanelCreateName.value;
    sitePanelSlugPreview.textContent = previewSlugForName(name);
  }

  function syncCreatePanelForKind() {
    if (sitePanelCreateTitle) sitePanelCreateTitle.textContent = "New site";
    if (sitePanelCreateBadge) {
      sitePanelCreateBadge.textContent = "site";
      sitePanelCreateBadge.className = "site-panel__badge site-panel__badge--planned";
    }
    if (sitePanelCreateLinksLabel) sitePanelCreateLinksLabel.textContent = "Linked sites";
    if (sitePanelCreateViewshedSection) sitePanelCreateViewshedSection.hidden = false;
  }

  function openCreatePanel(lat, lon) {
    pendingCreateLat = lat;
    pendingCreateLon = lon;
    setCreateError("");
    sitePanelCreateName.value = "";
    sitePanelCreateCoords.textContent = `${formatCoord(lat)}, ${formatCoord(lon)}`;
    syncCreateSlugPreview();
    syncCreatePanelForKind();
    resetCreatePrefetchUI();
    removeDraftMarker();
    const markerColor = DRAFT_MARKER_COLOR;
    draftMarker = new maplibregl.Marker({ color: markerColor })
      .setLngLat([lon, lat])
      .addTo(map);
    sitePanel.hidden = false;
    showPanelCreate();
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
    syncCreateViewshedCheckbox();
    void loadDraftViewshedAt(lat, lon);
    void loadPlacementPrefetchAt(lat, lon);
    sitePanelCreateName.focus();
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
    if (map.getLayer(DRAFT_LINKS_LABELS_LAYER)) map.removeLayer(DRAFT_LINKS_LABELS_LAYER);
    if (map.getLayer(DRAFT_LINKS_LAYER)) map.removeLayer(DRAFT_LINKS_LAYER);
    if (map.getSource(DRAFT_LINKS_SOURCE)) map.removeSource(DRAFT_LINKS_SOURCE);
  }

  function removeEditHistoryLinksLayer() {
    if (map.getLayer(EDIT_HISTORY_LINKS_LABELS_LAYER)) map.removeLayer(EDIT_HISTORY_LINKS_LABELS_LAYER);
    if (map.getLayer(EDIT_HISTORY_LINKS_LAYER)) map.removeLayer(EDIT_HISTORY_LINKS_LAYER);
    if (map.getSource(EDIT_HISTORY_LINKS_SOURCE)) map.removeSource(EDIT_HISTORY_LINKS_SOURCE);
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
        "text-font": MAP_TEXT_FONT,
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
    if (!mapReady || !geojson || !geojson.features || !geojson.features.length) {
      removeDraftLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels(geojson);
    if (map.getSource(DRAFT_LINKS_SOURCE)) {
      map.getSource(DRAFT_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(DRAFT_LINKS_LAYER)) {
        map.setPaintProperty(
          DRAFT_LINKS_LAYER,
          "line-color",
          ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
        );
      }
      raiseSiteLayers();
      return;
    }
    map.addSource(DRAFT_LINKS_SOURCE, { type: "geojson", data: labeled });
    map.addLayer(
      {
        id: DRAFT_LINKS_LAYER,
        type: "line",
        source: DRAFT_LINKS_SOURCE,
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
      SITES_CIRCLE,
    );
    map.addLayer(linkLabelsLayerSpec(DRAFT_LINKS_LABELS_LAYER, DRAFT_LINKS_SOURCE, "visible"), SITES_CIRCLE);
    raiseSiteLayers();
  }

  function editHistoryLinksLineColor() {
    return DRAFT_MARKER_COLOR;
  }

  function addEditHistoryLinksLayer(geojson) {
    if (!mapReady || !geojson || !geojson.features || !geojson.features.length) {
      removeEditHistoryLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels(geojson);
    const lineColor = editHistoryLinksLineColor();
    if (map.getSource(EDIT_HISTORY_LINKS_SOURCE)) {
      map.getSource(EDIT_HISTORY_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(EDIT_HISTORY_LINKS_LAYER)) {
        map.setPaintProperty(EDIT_HISTORY_LINKS_LAYER, "line-color", lineColor);
      }
      raiseSiteLayers();
      return;
    }
    map.addSource(EDIT_HISTORY_LINKS_SOURCE, { type: "geojson", data: labeled });
    map.addLayer(
      {
        id: EDIT_HISTORY_LINKS_LAYER,
        type: "line",
        source: EDIT_HISTORY_LINKS_SOURCE,
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
      SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(EDIT_HISTORY_LINKS_LABELS_LAYER, EDIT_HISTORY_LINKS_SOURCE, "visible"),
      SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  function refreshEditHistoryLinksLayer() {
    const features = [];
    for (const entry of editCoordHistory) {
      if (!entry.visible || !entry.linksGeojson || !Array.isArray(entry.linksGeojson.features)) continue;
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

  function cancelCreate() {
    if (!createMode && !draftMarker) return;
    createMode = false;
    pendingCreateLat = null;
    pendingCreateLon = null;
    removeDraftMarker();
    removeDraftViewshed();
    resetCreatePrefetchUI();
    setCreateError("");
    if (selectedSlug) {
      sitePanel.hidden = false;
      showPanelView();
      renderPanel(siteBySlug.get(selectedSlug));
    } else {
      sitePanel.hidden = true;
      sitePanelView.hidden = false;
      sitePanelCreate.hidden = true;
    }
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
    setCreateError("");
    sitePanelCreateSave.disabled = true;
    try {
      const resp = await fetch(sitesApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          lat: pendingCreateLat,
          lon: pendingCreateLon,
        }),
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
      removeDraftViewshed();
      pendingCreateLat = null;
      pendingCreateLon = null;
      createMode = false;
      setAddPlacementMode(null);
      registerSite(site);
      viewshedVisible.set(site.slug, true);
      scheduleViewshedLoad(site);
      void loadSiteLinks();
      selectSite(site.slug);
    } catch (_) {
      setCreateError("Could not reach server.");
    } finally {
      sitePanelCreateSave.disabled = false;
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
      showLinks: showSiteLinks,
      viewshedOpacity,
      hiddenSites: [...siteHidden],
      tagFilters: [...activeTagFilters].sort((a, b) => a.localeCompare(b)),
      viewshedVisible: Object.fromEntries(viewshedVisible),
    };
  }

  function scheduleSaveMapState() {
    if (!mapReady || restoring) return;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      persistMapState(captureMapState());
    }, MAP_STATE_SAVE_MS);
  }




  function setSiteLinksVisible(visible) {
    if (!mapReady) return;
    const vis = visible ? "visible" : "none";
    if (map.getLayer(LINKS_LAYER)) map.setLayoutProperty(LINKS_LAYER, "visibility", vis);
    if (map.getLayer(LINKS_LABELS_LAYER)) map.setLayoutProperty(LINKS_LABELS_LAYER, "visibility", vis);
  }

  function addSiteLinksLayer(geojson) {
    const filtered = filterSiteLinksGeoJson(geojson);
    if (!filtered || !filtered.features || !filtered.features.length) {
      if (map.getLayer(LINKS_LABELS_LAYER)) map.removeLayer(LINKS_LABELS_LAYER);
      if (map.getLayer(LINKS_LAYER)) map.removeLayer(LINKS_LAYER);
      if (map.getSource(LINKS_SOURCE)) map.removeSource(LINKS_SOURCE);
      raiseSiteLayers();
      return;
    }
    const labeled = linksGeoJsonWithLabels(filtered);
    const linkVisibility = showSiteLinks ? "visible" : "none";
    if (map.getSource(LINKS_SOURCE)) {
      map.getSource(LINKS_SOURCE).setData(labeled);
      setSiteLinksVisible(showSiteLinks);
      raiseSiteLayers();
      return;
    }
    map.addSource(LINKS_SOURCE, { type: "geojson", data: labeled });
    map.addLayer(
      {
        id: LINKS_LAYER,
        type: "line",
        source: LINKS_SOURCE,
        paint: {
          "line-color": ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
          "line-width": 2.5,
          "line-opacity": 0.85,
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: linkVisibility,
        },
      },
      SITES_CIRCLE,
    );
    map.addLayer(linkLabelsLayerSpec(LINKS_LABELS_LAYER, LINKS_SOURCE, linkVisibility), SITES_CIRCLE);
    raiseSiteLayers();
  }

  async function loadSiteLinks() {
    try {
      const resp = await fetch(linksApiUrl());
      if (!resp.ok) return;
      const payload = await resp.json();
      applySiteLinksPayload(payload);
      if (payload && payload.status === "pending") {
        void fetch(linksWarmApiUrl(), { method: "POST" });
      }
    } catch (_) {
      /* links optional */
    }
  }

  function applySiteLinksPayload(payload) {
    if (!payload) return;
    siteLinksPayload = payload;
    if (payload.geojson) addSiteLinksLayer(payload.geojson);
    if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
  }


  function raiseSiteLayers() {
    for (const id of [
      EDIT_HISTORY_LINKS_LAYER,
      EDIT_HISTORY_LINKS_LABELS_LAYER,
      DRAFT_LINKS_LAYER,
      DRAFT_LINKS_LABELS_LAYER,
      LINKS_LAYER,
      LINKS_LABELS_LAYER,
      SITES_CIRCLE,
      SITES_LABELS,
      SITES_SELECTED,
    ]) {
      if (map.getLayer(id)) {
        try {
          map.moveLayer(id);
        } catch (_) {
          /* layer may be mid-remove */
        }
      }
    }
  }

  function showTerrainOverlays() {
    ensureTerrainSource();
    ensureHillshadeLayer();
    map.setTerrain({ source: TERRAIN_SOURCE, exaggeration: 1.35 });
    if (map.getLayer("basemap")) {
      map.setPaintProperty("basemap", "raster-opacity", 0.9);
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
    if (!terrainActive && pitch >= PITCH_TERRAIN_ON) {
      terrainActive = true;
      showTerrainOverlays();
    } else if (terrainActive && pitch <= PITCH_TERRAIN_OFF) {
      terrainActive = false;
      hideTerrainOverlays();
    }
  }

  function ensureBasemapReference(bm) {
    if (!bm.referenceTiles) return;
    if (!map.getSource(BASEMAP_REFERENCE_SOURCE)) {
      map.addSource(BASEMAP_REFERENCE_SOURCE, {
        type: "raster",
        tiles: bm.referenceTiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      });
      map.addLayer(
        {
          id: BASEMAP_REFERENCE_LAYER,
          type: "raster",
          source: BASEMAP_REFERENCE_SOURCE,
        },
        map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined,
      );
    } else {
      map.getSource(BASEMAP_REFERENCE_SOURCE).setTiles(bm.referenceTiles);
    }
    raiseSiteLayers();
  }

  function removeBasemapReference() {
    if (map.getLayer(BASEMAP_REFERENCE_LAYER)) map.removeLayer(BASEMAP_REFERENCE_LAYER);
    if (map.getSource(BASEMAP_REFERENCE_SOURCE)) map.removeSource(BASEMAP_REFERENCE_SOURCE);
  }

  function viewshedSourceId(slug) {
    return `viewshed-${slug}`;
  }

  function viewshedLayerId(slug) {
    return `viewshed-${slug}-raster`;
  }

  function applyViewshedOpacityToAllLayers() {
    if (!mapReady) return;
    const slugs = [
      ...sites.map((site) => site.slug),
      DRAFT_VIEWSHED_SLUG,
      ...editCoordHistory.map((entry) => editHistorySlug(entry.id)),
    ];
    for (const slug of slugs) {
      const layerId = viewshedLayerId(slug);
      if (map.getLayer(layerId)) {
        map.setPaintProperty(layerId, "raster-opacity", viewshedOpacity);
      }
    }
  }

  function setViewshedOpacity(opacity) {
    viewshedOpacity = Math.max(0, Math.min(1, opacity));
    syncOpacitySlider();
    applyViewshedOpacityToAllLayers();
  }

  function viewshedSimQueryParams() {
    const params = new URLSearchParams();
    params.set("radius_km", String(viewshedRadiusKm));
    params.set("raster_dimension", String(viewshedRasterDimension));
    return params;
  }

  function viewshedPreviewSimQueryParams() {
    const params = new URLSearchParams();
    params.set("radius_km", String(viewshedRadiusKm));
    params.set("raster_dimension", String(VIEWSHED_PREVIEW_RASTER_DIMENSION));
    return params;
  }

  function projectEventsUrl() {
    return `/api/p/${projectSlug}/events`;
  }

  function viewshedWarmUrl(siteSlug) {
    const params = viewshedSimQueryParams();
    return `/api/p/${projectSlug}/viewsheds/${siteSlug}/warm?${params}`;
  }

  function viewshedPrefetchWarmUrl(lat, lon, { preview = true } = {}) {
    const params = preview ? viewshedPreviewSimQueryParams() : viewshedSimQueryParams();
    params.set("lat", String(lat));
    params.set("lon", String(lon));
    return `/api/p/${projectSlug}/viewsheds/prefetch/warm?${params}`;
  }

  let serveEventsSource = null;
  const viewshedPendingEpoch = new Map();

  function reconcilePendingViewsheds() {
    for (const [slug, epoch] of viewshedPendingEpoch) {
      if (slug === DRAFT_VIEWSHED_SLUG) {
        if (draftPlacementLat != null && draftPlacementLon != null) {
          void loadDraftViewshedAt(draftPlacementLat, draftPlacementLon, { refreshOnly: true });
        }
        continue;
      }
      const site = siteBySlug.get(slug);
      if (site) enqueueViewshedLoad(site, epoch);
    }
  }

  function connectProjectEvents() {
    if (serveEventsSource) {
      serveEventsSource.close();
      serveEventsSource = null;
    }
    serveEventsSource = new EventSource(projectEventsUrl());
    serveEventsSource.addEventListener("hello", () => {
      reconcilePendingViewsheds();
    });
    serveEventsSource.addEventListener("viewshed", (ev) => {
      try {
        handleViewshedEvent(JSON.parse(ev.data));
      } catch (_) {
        /* ignore malformed SSE payload */
      }
    });
    serveEventsSource.addEventListener("links", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data && data.status === "ready") applySiteLinksPayload(data);
      } catch (_) {
        /* ignore malformed SSE payload */
      }
    });
  }

  function handleViewshedReady(vs, epoch) {
    if (vs?.slug && epoch != null && viewshedPendingEpoch.get(vs.slug) !== epoch) return;
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) {
      if (vs?.slug) clearViewshedLoadingState(vs.slug);
      return;
    }
    if (viewshedPendingEpoch.get(vs.slug) !== epoch) return;
    viewshedPendingEpoch.delete(vs.slug);
    if (vs.slug === DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
    }
    if (String(vs.slug).startsWith("_edit_hist_")) {
      renderEditCoordHistory();
    }
    addViewshedLayer(vs);
  }

  function handleViewshedEvent(data) {
    if (!data || !data.slug) return;
    const epoch = viewshedPendingEpoch.get(data.slug);
    if (epoch == null) return;
    if (data.status === "ready") {
      handleViewshedReady(data, epoch);
      return;
    }
    if (data.status === "error") {
      viewshedPendingEpoch.delete(data.slug);
      viewshedLoading.delete(data.slug);
      if (data.slug === DRAFT_VIEWSHED_SLUG) {
        draftViewshedLoading = false;
        syncCreateViewshedCheckbox();
        syncEditViewshedCheckbox();
      }
      updatePinOverlays();
      if (data.slug === selectedSlug) syncViewshedCheckbox();
    }
  }

  function updatePinOverlays() {
    if (!pinLoadOverlays || !mapReady) return;
    const active = new Set();
    for (const site of sites) {
      if (!viewshedLoading.has(site.slug) || isSiteMapHidden(site.slug)) continue;
      active.add(site.slug);
      let el = pinSpinners.get(site.slug);
      if (!el) {
        el = document.createElement("div");
        el.className = "pin-load-spinner";
        el.setAttribute("data-slug", site.slug);
        pinLoadOverlays.appendChild(el);
        pinSpinners.set(site.slug, el);
      }
      const pt = map.project([site.lon, site.lat]);
      el.style.left = `${pt.x}px`;
      el.style.top = `${pt.y}px`;
      el.hidden = false;
    }
    if (
      viewshedLoading.has(DRAFT_VIEWSHED_SLUG) &&
      draftPlacementLat != null &&
      draftPlacementLon != null
    ) {
      active.add(DRAFT_VIEWSHED_SLUG);
      let el = pinSpinners.get(DRAFT_VIEWSHED_SLUG);
      if (!el) {
        el = document.createElement("div");
        el.className = "pin-load-spinner";
        el.setAttribute("data-slug", DRAFT_VIEWSHED_SLUG);
        pinLoadOverlays.appendChild(el);
        pinSpinners.set(DRAFT_VIEWSHED_SLUG, el);
      }
      const pt = map.project([draftPlacementLon, draftPlacementLat]);
      el.style.left = `${pt.x}px`;
      el.style.top = `${pt.y}px`;
      el.hidden = false;
    }
    for (const entry of editCoordHistory) {
      const slug = editHistorySlug(entry.id);
      if (!viewshedLoading.has(slug) || !entry.visible) continue;
      active.add(slug);
      let el = pinSpinners.get(slug);
      if (!el) {
        el = document.createElement("div");
        el.className = "pin-load-spinner";
        el.setAttribute("data-slug", slug);
        pinLoadOverlays.appendChild(el);
        pinSpinners.set(slug, el);
      }
      const pt = map.project([entry.lon, entry.lat]);
      el.style.left = `${pt.x}px`;
      el.style.top = `${pt.y}px`;
      el.hidden = false;
    }
    for (const [slug, el] of pinSpinners) {
      if (!active.has(slug)) el.hidden = true;
    }
  }

  let viewshedLoadEpoch = 0;
  const VIEWSHED_WARM_MAX_CONCURRENT = 6;
  const VIEWSHED_WARM_POLL_MS = 1000;
  const VIEWSHED_WARM_POLL_MAX = 180;
  const viewshedLoadQueue = [];
  let viewshedLoadActive = 0;

  function bumpViewshedLoadEpoch() {
    viewshedLoadEpoch += 1;
  }

  function viewshedMetaUrl(siteSlug, { lat, lon } = {}) {
    const params = viewshedSimQueryParams();
    if (lat != null && lon != null) {
      params.set("lat", String(lat));
      params.set("lon", String(lon));
      return `/api/p/${projectSlug}/viewsheds/prefetch?${params}`;
    }
    return `/api/p/${projectSlug}/viewsheds/${siteSlug}?${params}`;
  }

  function clearViewshedLoadingState(slug) {
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
    if (slug === DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
    }
    updatePinOverlays();
    if (slug === selectedSlug) syncViewshedCheckbox();
  }

  async function pollViewshedUntilReady(slug, epoch, coords) {
    for (let attempt = 0; attempt < VIEWSHED_WARM_POLL_MAX; attempt += 1) {
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      await new Promise((resolve) => setTimeout(resolve, VIEWSHED_WARM_POLL_MS));
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      try {
        const resp = await fetch(viewshedMetaUrl(slug, coords || {}));
        if (!resp.ok) continue;
        const overlay = await resp.json();
        if (overlay.url && overlay.coordinates) {
          handleViewshedReady({ ...overlay, slug, status: "ready" }, epoch);
          return;
        }
      } catch (_) {
        /* retry */
      }
    }
    if (viewshedPendingEpoch.get(slug) === epoch) {
      clearViewshedLoadingState(slug);
    }
  }

  function drainViewshedLoadQueue() {
    while (viewshedLoadActive < VIEWSHED_WARM_MAX_CONCURRENT && viewshedLoadQueue.length > 0) {
      const job = viewshedLoadQueue.shift();
      if (!job || viewshedPendingEpoch.get(job.site.slug) !== job.epoch) continue;
      viewshedLoadActive += 1;
      void loadViewshedForSite(job.site, job.epoch).finally(() => {
        viewshedLoadActive -= 1;
        drainViewshedLoadQueue();
      });
    }
  }

  function enqueueViewshedLoad(site, epoch) {
    viewshedLoadQueue.push({ site, epoch });
    drainViewshedLoadQueue();
  }

  function scheduleViewshedLoad(site) {
    const epoch = viewshedLoadEpoch;
    removeViewshedLayer(site.slug);
    viewshedLoading.add(site.slug);
    viewshedPendingEpoch.set(site.slug, epoch);
    updatePinOverlays();
    if (site.slug === selectedSlug) syncViewshedCheckbox();
    enqueueViewshedLoad(site, epoch);
  }

  function reloadViewshedsForSimChange() {
    bumpViewshedLoadEpoch();
    viewshedLoadQueue.length = 0;
    for (const site of sites) {
      if (!isSiteMapHidden(site.slug) && isViewshedVisible(site.slug)) {
        scheduleViewshedLoad(site);
      } else {
        removeViewshedLayer(site.slug);
        viewshedLoading.delete(site.slug);
        viewshedPendingEpoch.delete(site.slug);
      }
    }
    if (
      createMode &&
      draftPlacementLat != null &&
      draftPlacementLon != null &&
      isViewshedVisible(DRAFT_VIEWSHED_SLUG)
    ) {
      void loadDraftViewshedAt(draftPlacementLat, draftPlacementLon, { refreshOnly: true });
    }
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
      out.push({ lat: Number(editSnapshot.lat), lon: Number(editSnapshot.lon) });
    }
    const current = readEditCoords();
    if (current) out.push({ lat: current.lat, lon: current.lon });
    for (const entry of editCoordHistory) {
      out.push({ lat: entry.lat, lon: entry.lon });
    }
    if (excludeLat == null || excludeLon == null) return out;
    return out.filter((c) => !coordsMatchPair(c.lat, c.lon, excludeLat, excludeLon));
  }

  function filterHistoryEntryLinksGeojson(geojson, entryLat, entryLon) {
    if (!geojson || !Array.isArray(geojson.features)) {
      return { type: "FeatureCollection", features: [] };
    }
    const copyCoords = editSiteCopyCoords(entryLat, entryLon);
    const features = geojson.features.filter((feature) => {
      const props = feature.properties || {};
      if (editSlug && props.slug === editSlug) return false;
      const geom = feature.geometry;
      if (!geom || geom.type !== "LineString" || !Array.isArray(geom.coordinates)) return false;
      for (const pt of geom.coordinates) {
        if (!Array.isArray(pt) || pt.length < 2) continue;
        const lon = Number(pt[0]);
        const lat = Number(pt[1]);
        if (coordsMatchPair(lat, lon, entryLat, entryLon)) continue;
        for (const c of copyCoords) {
          if (coordsMatchPair(lat, lon, c.lat, c.lon)) return false;
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
    sitePanelCreateViewshed.checked = isViewshedVisible(DRAFT_VIEWSHED_SLUG);
    const hint = document.getElementById("site-panel-create-viewshed-hint");
    if (hint) hint.textContent = draftViewshedLoading ? "Loading…" : "";
  }

  function removeDraftViewshed() {
    const sourceId = viewshedSourceId(DRAFT_VIEWSHED_SLUG);
    const layerId = viewshedLayerId(DRAFT_VIEWSHED_SLUG);
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    viewshedLoading.delete(DRAFT_VIEWSHED_SLUG);
    draftPlacementLat = null;
    draftPlacementLon = null;
    updatePinOverlays();
  }

  async function loadPlacementPrefetchAt(lat, lon) {
    const gen = ++placementPrefetchGen;
    resetCreatePrefetchUI();
    try {
      const resp = await fetch(sitesPrefetchUrl(lat, lon));
      if (gen !== placementPrefetchGen) return;
      if (!resp.ok) return;
      const payload = await resp.json();
      if (gen !== placementPrefetchGen) return;
      if (payload && (payload.plss || payload.links || payload.links_geojson)) {
        renderCreatePrefetch(payload);
      }
    } catch (_) {
      /* placement prefetch optional */
    }
  }

  function clearDraftViewshedLoading() {
    viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG);
    viewshedLoading.delete(DRAFT_VIEWSHED_SLUG);
    draftViewshedLoading = false;
    updatePinOverlays();
    syncCreateViewshedCheckbox();
    syncEditViewshedCheckbox();
  }

  async function loadDraftViewshedAt(lat, lon, options) {
    const refreshOnly = Boolean(options && options.refreshOnly);
    if (refreshOnly) {
      removeViewshedLayer(DRAFT_VIEWSHED_SLUG);
    } else {
      removeDraftViewshed();
      draftPlacementLat = lat;
      draftPlacementLon = lon;
    }
    draftViewshedLoading = true;
    viewshedLoading.add(DRAFT_VIEWSHED_SLUG);
    const epoch = viewshedLoadEpoch;
    viewshedPendingEpoch.set(DRAFT_VIEWSHED_SLUG, epoch);
    updatePinOverlays();
    syncCreateViewshedCheckbox();
    syncEditViewshedCheckbox();
    try {
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), { method: "POST" });
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return;
      if (!resp.ok) {
        clearDraftViewshedLoading();
        return;
      }
      const vs = await resp.json();
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return;
      if (vs && vs.status === "ready") {
        handleViewshedReady({ ...vs, slug: DRAFT_VIEWSHED_SLUG }, epoch);
      } else if (vs && vs.status === "queued") {
        void pollViewshedUntilReady(DRAFT_VIEWSHED_SLUG, epoch, { lat, lon });
      }
    } catch (_) {
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) === epoch) {
        clearDraftViewshedLoading();
      }
    } finally {
      if (!viewshedPendingEpoch.has(DRAFT_VIEWSHED_SLUG)) {
        draftViewshedLoading = false;
        updatePinOverlays();
        syncCreateViewshedCheckbox();
        syncEditViewshedCheckbox();
      }
    }
  }

  function isViewshedVisible(slug) {
    return viewshedVisible.get(slug) !== false;
  }

  function syncEntityPanelViewshedCheckbox(slug) {
    if (!entityPanelSitesList) return;
    const row = entityPanelSitesList.querySelector(`[data-site-slug="${CSS.escape(slug)}"]`);
    if (!row) return;
    const check = row.querySelector(".entity-panel__viewshed-check");
    if (check) check.checked = isViewshedVisible(slug);
  }

  function syncSitePanelViewshedCheckbox(slug) {
    if (!sitePanelViewshed || slug !== selectedSlug) return;
    if (sitePanelView && sitePanelView.hidden) return;
    sitePanelViewshed.checked = isViewshedVisible(slug);
    if (sitePanelViewshedHint) {
      sitePanelViewshedHint.textContent = viewshedLoading.has(slug) ? "Loading…" : "";
    }
  }

  /** Mirror viewshedVisible state into every checkbox bound to this site slug. */
  function syncViewshedUiForSlug(slug) {
    if (!slug) return;
    syncEntityPanelViewshedCheckbox(slug);
    syncSitePanelViewshedCheckbox(slug);
    if (editMode && editSlug === slug) syncEditViewshedCheckbox();
    updatePinOverlays();
  }

  function setViewshedVisible(slug, visible) {
    viewshedVisible.set(slug, visible);
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      applyViewshedVisibilityForSite(slug);
    } else if (visible && !isSiteMapHidden(slug)) {
      const site = siteBySlug.get(slug);
      if (site) scheduleViewshedLoad(site);
    }
    syncViewshedUiForSlug(slug);
    if (slug === DRAFT_VIEWSHED_SLUG) {
      syncCreateViewshedCheckbox();
      if (editMode) syncEditViewshedCheckbox();
    }
  }

  function syncViewshedCheckbox() {
    if (!selectedSlug) return;
    syncViewshedUiForSlug(selectedSlug);
  }

  function addViewshedLayer(vs) {
    const sourceId = viewshedSourceId(vs.slug);
    const layerId = viewshedLayerId(vs.slug);
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
        map.addLayer({
          id: layerId,
          type: "raster",
          source: sourceId,
          paint: {
            "raster-opacity": viewshedOpacity,
            "raster-fade-duration": 0,
          },
        });
      }
    } else {
      map.addSource(sourceId, {
        type: "image",
        url: vs.url,
        coordinates: vs.coordinates,
      });
      map.addLayer({
        id: layerId,
        type: "raster",
        source: sourceId,
        paint: {
          "raster-opacity": viewshedOpacity,
          "raster-fade-duration": 0,
        },
      });
    }
    if (!isViewshedVisible(vs.slug) || isSiteMapHidden(vs.slug)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    } else {
      map.setLayoutProperty(layerId, "visibility", "visible");
    }
    viewshedLoading.delete(vs.slug);
    updatePinOverlays();
    if (vs.slug === selectedSlug) syncViewshedCheckbox();
    if (vs.slug === DRAFT_VIEWSHED_SLUG) {
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
    }
    raiseSiteLayers();
  }

  async function loadViewshedForSite(site, epoch) {
    if (epoch != null && viewshedPendingEpoch.get(site.slug) !== epoch) return;
    try {
      const resp = await fetch(viewshedWarmUrl(site.slug), { method: "POST" });
      if (epoch != null && viewshedPendingEpoch.get(site.slug) !== epoch) return;
      if (!resp.ok) {
        clearViewshedLoadingState(site.slug);
        return;
      }
      const vs = await resp.json();
      if (epoch != null && viewshedPendingEpoch.get(site.slug) !== epoch) return;
      if (vs && vs.status === "ready") {
        handleViewshedReady(vs, epoch);
      } else if (vs && vs.status === "queued") {
        void pollViewshedUntilReady(site.slug, epoch);
      } else {
        clearViewshedLoadingState(site.slug);
      }
    } catch (_) {
      if (viewshedPendingEpoch.get(site.slug) === epoch) {
        clearViewshedLoadingState(site.slug);
      }
    }
  }

  function loadAllViewsheds() {
    bumpViewshedLoadEpoch();
    for (const site of sites) {
      if (!isSiteMapHidden(site.slug) && isViewshedVisible(site.slug)) {
        scheduleViewshedLoad(site);
      }
    }
  }


  function addSiteLayers() {
    if (map.getSource(SITES_SOURCE)) {
      map.getSource(SITES_SOURCE).setData(sitesGeoJson());
      applySiteLayerFilters();
      return;
    }
    map.addSource(SITES_SOURCE, { type: "geojson", data: sitesGeoJson() });
    map.addLayer({
      id: SITES_CIRCLE,
      type: "circle",
      source: SITES_SOURCE,
      paint: {
        "circle-radius": 7,
        "circle-color": "#4a6cf7",
        "circle-stroke-width": 2,
        "circle-stroke-color": "#fff",
      },
    });
    map.addLayer({
      id: SITES_LABELS,
      type: "symbol",
      source: SITES_SOURCE,
      layout: {
        "text-field": ["get", "name"],
        "text-size": 12,
        "text-offset": [0, -1.4],
        "text-anchor": "bottom",
        "text-font": MAP_LABEL_FONT,
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#e8eaed",
        "text-halo-color": "#1a1a1a",
        "text-halo-width": 2,
      },
    });
    map.addLayer({
      id: SITES_SELECTED,
      type: "circle",
      source: SITES_SOURCE,
      filter: ["==", ["get", "slug"], ""],
      paint: {
        "circle-radius": 11,
        "circle-color": "#4a6cf7",
        "circle-stroke-width": 3,
        "circle-stroke-color": "#fbbf24",
        "circle-opacity": 0.35,
      },
    });
    applySiteLayerFilters();
  }


  function updateSelectedLayer() {
    if (!map.getLayer(SITES_SELECTED)) return;
    if (editMode && selectedSlug === editSlug) {
      map.setFilter(SITES_SELECTED, ["==", ["get", "slug"], ""]);
      return;
    }
    const filter = combineLayerFilters(
      ["==", ["get", "slug"], selectedSlug || ""],
      siteVisibilityFilter(),
    );
    map.setFilter(SITES_SELECTED, filter);
  }

  function formatCoord(n) {
    return Number(n).toFixed(6);
  }

  function setSectionVisible(sectionId, visible) {
    const el = document.getElementById(sectionId);
    if (el) el.hidden = !visible;
  }

  function linkedPeersForSite(slug) {
    if (!siteLinksPayload || !Array.isArray(siteLinksPayload.links)) return [];
    const peers = [];
    for (const row of siteLinksPayload.links) {
      if (!row.linked) continue;
      if (row.a === slug) peers.push(row.b);
      else if (row.b === slug) peers.push(row.a);
    }
    return peers.sort();
  }



  function normalizeTagInput(raw) {
    return String(raw || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  async function patchSiteTags(slug, tags) {
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
    applySiteRowUpdate(site);
    return site;
  }

  function renderSiteTags(site) {
    if (!sitePanelTags) return;
    sitePanelTags.innerHTML = "";
    tagAddOpen = false;
    const tags = siteTags(site);
    for (const tag of tags) {
      const chip = document.createElement("span");
      chip.className = "site-tag";
      const label = document.createElement("span");
      label.textContent = tag;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "site-tag__remove";
      remove.title = `Remove ${tag}`;
      remove.setAttribute("aria-label", `Remove tag ${tag}`);
      remove.textContent = "×";
      remove.addEventListener("click", (ev) => {
        ev.stopPropagation();
        void (async () => {
          try {
            const next = tags.filter((t) => t !== tag);
            const updated = await patchSiteTags(site.slug, next);
            if (selectedSlug === site.slug) renderSiteTags(updated);
            applyEntityVisibility();
          } catch (_) {
            /* network / validation */
          }
        })();
      });
      chip.appendChild(label);
      chip.appendChild(remove);
      sitePanelTags.appendChild(chip);
    }

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "site-tag-add";
    addBtn.title = "Add tag";
    addBtn.setAttribute("aria-label", "Add tag");
    addBtn.textContent = "+";
    addBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (tagAddOpen) return;
      tagAddOpen = true;
      addBtn.replaceWith(buildTagAddForm(site, tags));
    });
    sitePanelTags.appendChild(addBtn);
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
      if (selectedSlug === site.slug) renderSiteTags(siteBySlug.get(site.slug) || site);
    };
    form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const tag = normalizeTagInput(input.value);
      if (!tag || currentTags.includes(tag)) {
        finish();
        return;
      }
      void (async () => {
        try {
          const updated = await patchSiteTags(site.slug, [...currentTags, tag]);
          if (selectedSlug === site.slug) renderSiteTags(updated);
          applyEntityVisibility();
        } catch (_) {
          finish();
        }
      })();
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        finish();
      }
    });
    input.addEventListener("blur", () => {
      setTimeout(() => {
        if (tagAddOpen) finish();
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
    renderSiteTags(site);
    document.getElementById("site-panel-coords").textContent =
      `${formatCoord(site.lat)}, ${formatCoord(site.lon)}`;
    const elevEl = document.getElementById("site-panel-elevation");
    if (site.elevation_m != null) {
      elevEl.textContent = `${site.elevation_m} m`;
      elevEl.classList.remove("text-muted");
    } else {
      elevEl.textContent = "—";
      elevEl.classList.add("text-muted");
    }
    const plss = site.plss || "";
    setSectionVisible("site-panel-plss-section", !!plss);
    document.getElementById("site-panel-plss").textContent = plss;
    const desc = site.description || "";
    setSectionVisible("site-panel-desc-section", !!desc);
    document.getElementById("site-panel-desc").textContent = desc;
    const peers = linkedPeersForSite(site.slug);
    setSectionVisible("site-panel-links-section", peers.length > 0);
    const linksEl = document.getElementById("site-panel-links");
    linksEl.innerHTML = "";
    for (const peer of peers) {
      const li = document.createElement("li");
      li.textContent = peer;
      linksEl.appendChild(li);
    }
    syncViewshedCheckbox();
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
    resetEditPrefetchPanelUI();
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
    if (editShowsSitePreview() && payload.links_geojson) {
      addDraftLinksLayer(payload.links_geojson);
    } else {
      removeDraftLinksLayer();
    }
  }

  function readEditCoords() {
    const lat = Number.parseFloat(sitePanelEditLat.value);
    const lon = Number.parseFloat(sitePanelEditLon.value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return { lat, lon };
  }

  function parseCoordPairFromText(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed) return null;
    const parts = trimmed.split(/[,\s]+/).filter(Boolean);
    if (parts.length < 2) return null;
    const lat = Number.parseFloat(parts[0]);
    const lon = Number.parseFloat(parts[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    return { lat, lon };
  }

  function applyCoordPaste(text, targetField) {
    const pair = parseCoordPairFromText(text);
    if (!pair) return false;
    sitePanelEditLat.value = formatCoord(pair.lat);
    sitePanelEditLon.value = formatCoord(pair.lon);
    onEditCoordsChanged();
    return true;
  }




  function editShowsSitePreview() {
    return editMode;
  }

  function editWantsDraftViewshed() {
    if (!editMode || !editShowsSitePreview()) return false;
    return !sitePanelEditViewshed || sitePanelEditViewshed.checked;
  }

  function ensureEditDraftViewshedEnabled() {
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
    if (sitePanelEditViewshed) sitePanelEditViewshed.checked = true;
  }

  function loadEditDraftViewshedAt(lat, lon) {
    if (!editWantsDraftViewshed()) return;
    ensureEditDraftViewshedEnabled();
    hideViewshedLayerForEdit(editSlug);
    void loadDraftViewshedAt(lat, lon);
  }

  function updateEditDraftMarker(lat, lon) {
    removeDraftMarker();
    const markerColor = DRAFT_MARKER_COLOR;
    draftMarker = new maplibregl.Marker({ color: markerColor })
      .setLngLat([lon, lat])
      .addTo(map);
  }


  function syncEditViewshedCheckbox() {
    if (!sitePanelEditViewshed || !editMode) return;
    const coords = readEditCoords();
    const atOriginal = coords && coordsMatchEditSnapshot(coords.lat, coords.lon);
    const hint = document.getElementById("site-panel-edit-viewshed-hint");
    if (atOriginal && editSlug) {
      sitePanelEditViewshed.checked = isViewshedVisible(editSlug);
      if (hint) hint.textContent = viewshedLoading.has(editSlug) ? "Loading…" : "";
      return;
    }
    sitePanelEditViewshed.checked = isViewshedVisible(DRAFT_VIEWSHED_SLUG);
    if (hint) hint.textContent = draftViewshedLoading ? "Loading…" : "";
  }

  function coordsMatchPair(lat1, lon1, lat2, lon2) {
    return Math.abs(lat1 - lat2) < 1e-5 && Math.abs(lon1 - lon2) < 1e-5;
  }

  function coordSeparationM(lat1, lon1, lat2, lon2) {
    const r = 6371000;
    const phi1 = (lat1 * Math.PI) / 180;
    const phi2 = (lat2 * Math.PI) / 180;
    const dphi = ((lat2 - lat1) * Math.PI) / 180;
    const dlambda = ((lon2 - lon1) * Math.PI) / 180;
    const a =
      Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlambda / 2) ** 2;
    return 2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
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
    const slug = editHistorySlug(id);
    if (entry) entry.viewshedGen = (entry.viewshedGen || 0) + 1;
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
  }

  function hideEditHistoryViewshed(id) {
    cancelEditHistoryViewshedLoad(id);
    const slug = editHistorySlug(id);
    viewshedVisible.set(slug, false);
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    }
    updatePinOverlays();
  }

  function showEditHistoryViewshed(entry) {
    if (!editShowsSitePreview()) return;
    const slug = editHistorySlug(entry.id);
    viewshedVisible.set(slug, true);
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "visible");
      updatePinOverlays();
      return;
    }
    void loadEditHistoryViewshed(entry.id, entry.lat, entry.lon);
  }

  function removeEditHistoryMapArtifacts(id) {
    const entry = editCoordHistory.find((row) => row.id === id);
    removeEditHistoryMarker(id);
    cancelEditHistoryViewshedLoad(id);
    const slug = editHistorySlug(id);
    removeViewshedLayer(slug);
    viewshedVisible.delete(slug);
    if (entry) {
      entry.linksLoading = false;
      entry.linksGen = (entry.linksGen || 0) + 1;
    }
    refreshEditHistoryLinksLayer();
  }

  function clearEditCoordHistory() {
    for (const entry of editCoordHistory) {
      removeEditHistoryMapArtifacts(entry.id);
    }
    editCoordHistory = [];
    editHistoryNextId = 0;
    editCommittedCoords = null;
    removeEditHistoryLinksLayer();
    renderEditCoordHistory();
  }

  function historyHasCoords(lat, lon) {
    return editCoordHistory.some((entry) => coordsMatchPair(entry.lat, entry.lon, lat, lon));
  }

  function pushEditCoordHistory(lat, lon) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    if (historyHasCoords(lat, lon)) return;
    const coords = readEditCoords();
    if (coords && coordsMatchPair(coords.lat, coords.lon, lat, lon)) return;
    editCoordHistory.unshift({
      id: ++editHistoryNextId,
      lat,
      lon,
      visible: false,
    });
    renderEditCoordHistory();
  }

  function commitEditCoordMove() {
    const coords = readEditCoords();
    if (!coords) return;
    if (!editCommittedCoords) {
      editCommittedCoords = { lat: coords.lat, lon: coords.lon };
      return;
    }
    if (coordsMatchPair(coords.lat, coords.lon, editCommittedCoords.lat, editCommittedCoords.lon)) {
      return;
    }
    const movedM = coordSeparationM(
      editCommittedCoords.lat,
      editCommittedCoords.lon,
      coords.lat,
      coords.lon,
    );
    if (movedM >= EDIT_COORD_HISTORY_MIN_M) {
      pushEditCoordHistory(editCommittedCoords.lat, editCommittedCoords.lon);
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
    const markerColor = DRAFT_MARKER_COLOR;
    editHistoryMarkers.set(
      entry.id,
      new maplibregl.Marker({ color: markerColor }).setLngLat([entry.lon, entry.lat]).addTo(map),
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
    cancelEditHistoryViewshedLoad(id);
    const slug = editHistorySlug(id);
    removeViewshedLayer(slug);
    viewshedVisible.delete(slug);
  }


  async function loadEditHistoryViewshed(id, lat, lon) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry || !entry.visible || !editShowsSitePreview()) return;
    const slug = editHistorySlug(id);
    const gen = (entry.viewshedGen = (entry.viewshedGen || 0) + 1);
    removeViewshedLayer(slug);
    viewshedLoading.add(slug);
    const epoch = viewshedLoadEpoch;
    viewshedPendingEpoch.set(slug, epoch);
    updatePinOverlays();
    renderEditCoordHistory();
    try {
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), { method: "POST" });
      if (!entry.visible || entry.viewshedGen !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (!resp.ok) {
        viewshedPendingEpoch.delete(slug);
        viewshedLoading.delete(slug);
        updatePinOverlays();
        renderEditCoordHistory();
        return;
      }
      const vs = await resp.json();
      if (!entry.visible || entry.viewshedGen !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (vs && vs.status === "ready") {
        viewshedVisible.set(slug, true);
        handleViewshedReady({ ...vs, slug }, epoch);
      }
    } catch (_) {
      if (entry.viewshedGen === gen) {
        viewshedPendingEpoch.delete(slug);
        viewshedLoading.delete(slug);
        updatePinOverlays();
        renderEditCoordHistory();
      }
    }
  }

  async function loadEditHistoryLinks(id, lat, lon) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry) return;
    const gen = (entry.linksGen = (entry.linksGen || 0) + 1);
    entry.linksLoading = true;
    renderEditCoordHistory();
    try {
      const url = sitesPrefetchUrl(lat, lon, editSlug);
      const resp = await fetch(url);
      if (!entry.visible || entry.linksGen !== gen) return;
      if (!resp.ok) {
        entry.linksGeojson = null;
        return;
      }
      const payload = await resp.json();
      if (!entry.visible || entry.linksGen !== gen) return;
      let linksGeojson = payload.links_geojson;
      const filtered = filterEditSitePrefetchPayload({ links_geojson: linksGeojson });
      entry.linksGeojson = filterHistoryEntryLinksGeojson(filtered.links_geojson, lat, lon);
      refreshEditHistoryLinksLayer();
    } catch (_) {
      if (entry.linksGen === gen) entry.linksGeojson = null;
    } finally {
      if (entry.linksGen === gen) {
        entry.linksLoading = false;
        renderEditCoordHistory();
        refreshEditHistoryLinksLayer();
      }
    }
  }

  async function loadEditHistoryMapArtifacts(entry) {
    if (!entry.visible) return;
    showEditHistoryViewshed(entry);
    if (!editShowsSitePreview()) {
      if (entry.linksGeojson) entry.linksGeojson = null;
      refreshEditHistoryLinksLayer();
      return;
    }
    if (entry.linksGeojson && entry.linksGeojson.features && entry.linksGeojson.features.length) {
      refreshEditHistoryLinksLayer();
      return;
    }
    await loadEditHistoryLinks(entry.id, entry.lat, entry.lon);
  }

  function setEditHistoryEntryVisible(id, visible) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (!entry) return;
    entry.visible = visible;
    if (visible) {
      updateEditHistoryMarker(entry);
      void loadEditHistoryMapArtifacts(entry);
    } else {
      removeEditHistoryMarker(id);
      hideEditHistoryViewshed(id);
      entry.linksGen = (entry.linksGen || 0) + 1;
      refreshEditHistoryLinksLayer();
    }
    renderEditCoordHistory();
  }

  function deleteEditHistoryEntry(id) {
    const entry = editCoordHistory.find((row) => row.id === id);
    if (entry) {
      entry.linksGeojson = null;
    }
    removeEditHistoryMapArtifacts(id);
    editCoordHistory = editCoordHistory.filter((row) => row.id !== id);
    renderEditCoordHistory();
  }

  async function copyCoordPair(lat, lon) {
    const text = `${formatCoord(lat)}, ${formatCoord(lon)}`;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      /* clipboard optional */
    }
  }

  function renderEditCoordHistory() {
    if (!sitePanelEditCoordHistory) return;
    sitePanelEditCoordHistory.innerHTML = "";
    sitePanelEditCoordHistory.hidden = editCoordHistory.length === 0;
    for (const entry of editCoordHistory) {
      const row = document.createElement("li");
      row.className = "edit-coord-history__row";
      if (entry.visible) row.classList.add("edit-coord-history__row--visible");

      const viewBtn = document.createElement("wa-button");
      viewBtn.appearance = "outlined";
      viewBtn.size = "s";
      viewBtn.className = `coord-action-btn${entry.visible ? " active" : ""}`;
      viewBtn.title = entry.visible ? "Hide on map" : "Show on map";
      viewBtn.textContent = entry.visible ? "◉" : "○";
      viewBtn.addEventListener("click", () => {
        setEditHistoryEntryVisible(entry.id, !entry.visible);
      });

      const coordsEl = document.createElement("span");
      coordsEl.className = "edit-coord-history__coords";
      coordsEl.textContent = `${formatCoord(entry.lat)}, ${formatCoord(entry.lon)}`;
      const slug = editHistorySlug(entry.id);
      const loadingParts = [];
      if (editShowsSitePreview() && viewshedLoading.has(slug)) loadingParts.push("viewshed");
      if (entry.linksLoading) loadingParts.push("links");
      if (loadingParts.length) coordsEl.textContent += ` (${loadingParts.join(", ")}…)`;

      const actions = document.createElement("div");
      actions.className = "edit-coord-history__actions";

      const copyBtn = document.createElement("wa-button");
      copyBtn.appearance = "outlined";
      copyBtn.size = "s";
      copyBtn.className = "coord-action-btn";
      copyBtn.title = "Copy lat, lon";
      copyBtn.innerHTML = '<wa-icon name="copy" label="Copy coordinates"></wa-icon>';
      copyBtn.addEventListener("click", () => {
        void copyCoordPair(entry.lat, entry.lon);
      });

      const deleteBtn = document.createElement("wa-button");
      deleteBtn.appearance = "outlined";
      deleteBtn.size = "s";
      deleteBtn.className = "coord-action-btn";
      deleteBtn.title = "Remove from history";
      deleteBtn.innerHTML = '<wa-icon name="xmark" label="Remove from history"></wa-icon>';
      deleteBtn.addEventListener("click", () => {
        deleteEditHistoryEntry(entry.id);
      });

      actions.appendChild(copyBtn);
      actions.appendChild(deleteBtn);

      row.appendChild(viewBtn);
      row.appendChild(coordsEl);
      row.appendChild(actions);
      sitePanelEditCoordHistory.appendChild(row);
    }
  }

  function coordsMatchEditSnapshot(lat, lon) {
    if (!editSnapshot) return false;
    return (
      Math.abs(lat - Number(editSnapshot.lat)) < 1e-5 &&
      Math.abs(lon - Number(editSnapshot.lon)) < 1e-5
    );
  }

  async function runEditPrefetchAt(lat, lon) {
    const gen = ++editPrefetchGen;
    const showSitePreview = editShowsSitePreview();
    const atOriginal = coordsMatchEditSnapshot(lat, lon);
    resetEditPrefetchPanelUI();
    updateEditDraftMarker(lat, lon);
    applySiteLayerFilters();
    refreshFilteredLinks();
    if (sitePanelEditViewshedSection) {
      sitePanelEditViewshedSection.hidden = !showSitePreview;
    }
    if (showSitePreview) {
      if (atOriginal) {
        removeDraftViewshed();
        restoreEditHiddenViewshed();
      } else {
        loadEditDraftViewshedAt(lat, lon);
      }
    } else {
      removeDraftViewshed();
      removeDraftLinksLayer();
    }
    try {
      const url = sitesPrefetchUrl(lat, lon, editSlug);
      const resp = await fetch(url);
      if (gen !== editPrefetchGen) return;
      if (!resp.ok) return;
      let payload = await resp.json();
      if (gen !== editPrefetchGen) return;
      payload = filterEditSitePrefetchPayload(payload);
      renderEditPrefetch(payload);
    } catch (_) {
      /* edit prefetch optional */
    }
  }

  function onEditCoordsChanged() {
    if (!editMode) return;
    const coords = readEditCoords();
    if (!coords) return;
    const atOriginal = coordsMatchEditSnapshot(coords.lat, coords.lon);
    updateEditDraftMarker(coords.lat, coords.lon);
    if (!atOriginal) {
      removeDraftLinksLayer();
    }
    refreshFilteredLinks();
    scheduleEditPrefetch();
  }

  function scheduleEditPrefetch() {
    if (!editMode) return;
    if (editPrefetchTimer) clearTimeout(editPrefetchTimer);
    editPrefetchTimer = setTimeout(() => {
      editPrefetchTimer = null;
      const coords = readEditCoords();
      if (!coords) return;
      commitEditCoordMove();
      void runEditPrefetchAt(coords.lat, coords.lon);
    }, COORD_PREFETCH_MS);
  }

  function restoreEditHiddenViewshed() {
    if (editHiddenViewshedSlug) {
      applyViewshedVisibilityForSite(editHiddenViewshedSlug);
      editHiddenViewshedSlug = null;
    }
  }

  function hideViewshedLayerForEdit(slug) {
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setLayoutProperty(layerId, "visibility", "none");
      editHiddenViewshedSlug = slug;
    }
  }

  function openEditPanel() {
    const slug = selectedSlug;
    const entity = siteBySlug.get(slug);
    if (!entity || !slug) return;
    editKind = "site";
    editSlug = slug;
    editSnapshot = { ...entity };
    clearEditCoordHistory();
    editCommittedCoords = { lat: Number(entity.lat), lon: Number(entity.lon) };
    setEditError("");
    document.getElementById("site-panel-edit-title").textContent = entity.name;
    sitePanelEditSlug.textContent = slug;
    sitePanelEditName.value = entity.name;
    sitePanelEditLat.value = formatCoord(entity.lat);
    sitePanelEditLon.value = formatCoord(entity.lon);
    if (sitePanelEditViewshedSection) {
      sitePanelEditViewshedSection.hidden = !editShowsSitePreview();
    }
    showPanelEdit();
    applySiteLayerFilters();
    refreshFilteredLinks();
    syncEditViewshedCheckbox();
    void runEditPrefetchAt(entity.lat, entity.lon);
  }

  function cancelEdit() {
    if (!editMode) return;
    editMode = false;
    editKind = null;
    editSlug = null;
    editSnapshot = null;
    clearEditCoordHistory();
    editPrefetchGen += 1;
    if (editPrefetchTimer) {
      clearTimeout(editPrefetchTimer);
      editPrefetchTimer = null;
    }
    setEditError("");
    removeDraftMarker();
    removeDraftViewshed();
    removeDraftLinksLayer();
    restoreEditHiddenViewshed();
    resetEditPrefetchUI();
    applySiteLayerFilters();
    refreshFilteredLinks();
    syncEditMapShell();
    sitePanel.hidden = false;
    showPanelView();
    if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
  }

  async function saveEdit() {
    if (!editMode || !editSlug) return;
    const name = sitePanelEditName.value.trim();
    if (!name) {
      setEditError("Name is required.");
      return;
    }
    const coords = readEditCoords();
    if (!coords) {
      setEditError("Valid latitude and longitude are required.");
      return;
    }
    setEditError("");
    sitePanelEditSave.disabled = true;
    const savedEditSlug = editSlug;
    const apiUrl = `/api/p/${projectSlug}/sites/${savedEditSlug}`;
    const body = {
      name,
      lat: coords.lat,
      lon: coords.lon,
    };
    try {
      const resp = await fetch(apiUrl, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setEditError(payload.error || `Save failed (${resp.status})`);
        return;
      }
      cleanupEditSaveArtifacts();
      if (payload.promoted && payload.site) {
        const site = payload.site;
        registerSite(site);
        selectedSlug = site.slug;
        viewshedVisible.set(site.slug, true);
        scheduleViewshedLoad(site);
        showPanelView();
        renderPanel(site);
        renderEntityPanel();
        finishEditSaveUi();
        void loadSiteLinks();
        return;
      }
      if (payload.site) {
        const site = payload.site;
        applySiteRowUpdate(site);
        selectedSlug = site.slug;
        viewshedVisible.set(site.slug, true);
        scheduleViewshedLoad(site);
        showPanelView();
        renderPanel(site);
        renderEntityPanel();
        finishEditSaveUi();
        void loadSiteLinks();
      }
    } catch (_) {
      setEditError("Could not reach server.");
    } finally {
      sitePanelEditSave.disabled = false;
    }
  }

  function applySiteRowUpdate(site) {
    const ix = sites.findIndex((s) => s.slug === site.slug);
    if (ix >= 0) sites[ix] = site;
    else sites.push(site);
    siteBySlug.set(site.slug, site);
    if (map.getSource(SITES_SOURCE)) {
      map.getSource(SITES_SOURCE).setData(sitesGeoJson());
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
    removeDraftViewshed();
    removeDraftLinksLayer();
    editHiddenViewshedSlug = null;
    clearEditCoordHistory();
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
    const coords = readEditCoords();
    if (!coords) return;
    await copyCoordPair(coords.lat, coords.lon);
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


  function selectSite(slug) {
    const site = siteBySlug.get(slug);
    if (!site) return;
    if (createMode) cancelCreate();
    if (editMode) cancelEdit();
    selectedSlug = slug;
    sitePanel.hidden = false;
    showPanelView();
    renderPanel(site);
    updateSelectedLayer();
    raiseSiteLayers();
    renderEntityPanel();
  }

  function deselectSite() {
    if (createMode) {
      cancelCreate();
      return;
    }
    if (editMode) {
      cancelEdit();
      return;
    }
    selectedSlug = null;
    sitePanel.hidden = true;
    updateSelectedLayer();
  }

  function wireMapInteractions() {
    const siteLayerIds = [SITES_CIRCLE, SITES_LABELS];
    map.on("mousemove", () => {
      if (addPlacementMode || editMode) map.getCanvas().style.cursor = "crosshair";
    });
    for (const layerId of siteLayerIds) {
      map.on("mouseenter", layerId, () => {
        if (addPlacementMode || editMode) {
          map.getCanvas().style.cursor = "crosshair";
          return;
        }
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layerId, () => {
        syncMapCursor();
      });
    }
    map.on("click", (ev) => {
      if (editMode) {
        sitePanelEditLat.value = formatCoord(ev.lngLat.lat);
        sitePanelEditLon.value = formatCoord(ev.lngLat.lng);
        onEditCoordsChanged();
        return;
      }
      const feats = map.queryRenderedFeatures(ev.point, { layers: siteLayerIds });
      if (feats.length) {
        const slug = feats[0].properties && feats[0].properties.slug;
        if (slug) {
          ev.preventDefault();
          if (addPlacementMode) setAddPlacementMode(null);
          selectSite(slug);
        }
        return;
      }
      if (addPlacementMode) {
        openCreatePanel(ev.lngLat.lat, ev.lngLat.lng);
        return;
      }
      deselectSite();
    });
  }

  function fitSites() {
    const points = [...sites];
    if (!points.length) return;
    const lons = points.map((p) => p.lon);
    const lats = points.map((p) => p.lat);
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const { latDelta, lonDelta } = kmToDegreeDeltas(centerLat, SITE_FIT_BUFFER_KM);
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
      hideTerrainOverlays();
    }
    fitSites();
  }

  function setBasemapKey(key) {
    if (!BASEMAPS[key]) return;
    currentBasemapKey = key;
    syncBasemapMenu();
    setBasemap(key);
    scheduleSaveMapState();
  }

  function setBasemap(key) {
    const bm = BASEMAPS[key];
    if (!bm || !mapReady) return;
    const src = map.getSource("basemap");
    if (!src || typeof src.setTiles !== "function") return;
    src.setTiles(bm.tiles);
    map.setMaxZoom(bm.maxzoom);
    if (bm.referenceTiles) ensureBasemapReference(bm);
    else removeBasemapReference();
    raiseSiteLayers();
  }

  map.on("load", () => {
    mapReady = true;
    addSiteLayers();
    wireMapInteractions();
    connectProjectEvents();
    void loadSiteLinks();
    loadAllViewsheds();
    renderEntityPanel();
    applyEntityVisibility();
    syncBasemapMenu();
    setBasemap(currentBasemapKey);
    if (savedMapState) {
      setSiteLinksVisible(showSiteLinks);
      syncTerrainFromPitch();
    } else {
      fitSites();
    }
    restoring = false;
  });

  map.on("pitch", () => {
    syncTerrainFromPitch();
    scheduleSaveMapState();
  });
  map.on("moveend", scheduleSaveMapState);
  map.on("rotateend", scheduleSaveMapState);
  map.on("move", updatePinOverlays);
  map.on("resize", updatePinOverlays);
  if (mapBasemapMenu) {
    mapBasemapMenu.addEventListener("wa-select", (ev) => {
      const item = ev.detail.item;
      if (!item) return;
      setBasemapKey(item.value || item.getAttribute("data-basemap") || "street");
    });
  }
  if (viewshedOpacityInput) {
    viewshedOpacityInput.addEventListener("input", (ev) => {
      setViewshedOpacity(Number(ev.target.value) / 100);
      scheduleSaveMapState();
    });
  }
  sitePanelClose.addEventListener("click", deselectSite);
  if (sitePanelEditOpen) {
    sitePanelEditOpen.addEventListener("click", () => openEditPanel());
  }
  if (sitePanelEditClose) {
    sitePanelEditClose.addEventListener("click", cancelEdit);
  }
  if (sitePanelEditCancel) {
    sitePanelEditCancel.addEventListener("click", cancelEdit);
  }
  if (sitePanelEditSave) {
    sitePanelEditSave.addEventListener("click", () => {
      void saveEdit();
    });
  }
  if (sitePanelEditCopyCoords) {
    sitePanelEditCopyCoords.addEventListener("click", () => {
      void copyEditCoords();
    });
  }
  if (sitePanelCopyPlss) {
    sitePanelCopyPlss.addEventListener("click", () => {
      void copyPlssFromElement(document.getElementById("site-panel-plss"));
    });
  }
  if (sitePanelEditCopyPlss) {
    sitePanelEditCopyPlss.addEventListener("click", () => {
      void copyPlssFromElement(document.getElementById("site-panel-edit-plss"));
    });
  }
  if (sitePanelCreateCopyPlss) {
    sitePanelCreateCopyPlss.addEventListener("click", () => {
      void copyPlssFromElement(document.getElementById("site-panel-create-plss"));
    });
  }
  if (sitePanelEditLat) {
    sitePanelEditLat.addEventListener("input", onEditCoordsChanged);
    sitePanelEditLat.addEventListener("paste", (ev) => {
      const text = ev.clipboardData && ev.clipboardData.getData("text");
      if (text && applyCoordPaste(text, "lat")) ev.preventDefault();
    });
  }
  if (sitePanelEditLon) {
    sitePanelEditLon.addEventListener("input", onEditCoordsChanged);
    sitePanelEditLon.addEventListener("paste", (ev) => {
      const text = ev.clipboardData && ev.clipboardData.getData("text");
      if (text && applyCoordPaste(text, "lon")) ev.preventDefault();
    });
  }
  if (sitePanelEditViewshed) {
    sitePanelEditViewshed.addEventListener("change", (ev) => {
      if (!editMode) return;
      const coords = readEditCoords();
      const atOriginal = coords && coordsMatchEditSnapshot(coords.lat, coords.lon);
      const visible = ev.target.checked;
      if (atOriginal && editSlug) {
        setViewshedVisible(editSlug, visible);
        if (!visible) removeDraftViewshed();
        return;
      }
      setViewshedVisible(DRAFT_VIEWSHED_SLUG, visible);
      if (visible && coords) {
        hideViewshedLayerForEdit(editSlug);
        void loadDraftViewshedAt(coords.lat, coords.lon);
      } else {
        removeDraftViewshed();
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
  if (entityPanelToggle) {
    entityPanelToggle.addEventListener("click", () => {
      if (entityPanelOpen) {
        setEntityPanelOpen(false);
      } else {
        setEntityPanelOpen(true);
      }
    });
  }
  if (mapToolSites) {
    mapToolSites.addEventListener("click", () => {
      toggleEntityPanel();
    });
  }
  if (entityPanelAddSite) {
    entityPanelAddSite.addEventListener("click", () => {
      setEntityPanelOpen(true);
      setAddPlacementMode(addPlacementMode === "site" ? null : "site");
    });
  }
  sitePanelViewshed.addEventListener("change", (ev) => {
    if (selectedSlug) {
      setViewshedVisible(selectedSlug, ev.target.checked);
      scheduleSaveMapState();
    }
  });
  if (sitePanelCreateViewshed) {
    sitePanelCreateViewshed.addEventListener("change", (ev) => {
      if (createMode) setViewshedVisible(DRAFT_VIEWSHED_SLUG, ev.target.checked);
    });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (createMode) {
      cancelCreate();
      return;
    }
    if (editMode) {
      cancelEdit();
      return;
    }
    if (selectedSlug) deselectSite();
  });

  window.PEAKY_MAP = {
    reloadViewshedsForSimChange,
    setViewshedSimulation,
  };
})();
