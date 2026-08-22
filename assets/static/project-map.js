(function () {
  const config = window.PEAKY_PROJECT || {};
  const projectSlug = config.slug;
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
  const SEEK_CANDIDATES_SOURCE = "seek-candidates";
  const SEEK_CANDIDATES_LAYER = "seek-candidates-circle";
  const SEEK_CANDIDATES_LABELS_LAYER = "seek-candidates-label";
  const SEEK_LINES_SOURCE = "seek-candidate-lines";
  const SEEK_LINES_LAYER = "seek-candidate-lines-line";
  const SEEK_LINES_LABELS_LAYER = "seek-candidate-lines-label";
  const SEEK_PATH_SOURCE = "seek-path";
  const SEEK_PATH_LAYER = "seek-path-line";
  const SEEK_GOAL_LINE_SOURCE = "seek-goal-line";
  const SEEK_GOAL_LINE_LAYER = "seek-goal-line";
  const SEEK_WEDGE_SOURCE = "seek-goal-wedge";
  const SEEK_WEDGE_FILL_LAYER = "seek-goal-wedge-fill";
  const SEEK_WEDGE_OUTLINE_LAYER = "seek-goal-wedge-outline";
  const SEEK_WEDGE_NEAR_DEG = 10;
  const SEEK_WEDGE_FAR_DEG = 50;
  const SEEK_ANCILLARY_LINES_SOURCE = "seek-ancillary-lines";
  const SEEK_ANCILLARY_LINES_LAYER = "seek-ancillary-lines-line";
  const SEEK_ANCILLARY_LINES_LABELS_LAYER = "seek-ancillary-lines-label";
  const SEEK_ANCILLARY_LINKS_DEBOUNCE_MS = 450;
  const SEEK_STATE_KEY = `peaky.seek.v1.${projectSlug}`;
  const SEEK_REDO_KEY = `peaky.seek.redo.v1.${projectSlug}`;
  const SEEK_PLAN_SAVE_MS = 400;
  const SEEK_PEAK_BIN_MIN_M = 500;
  const SEEK_PEAK_BIN_MAX_M = 1500;
  const SEEK_PEAK_BINS_ACROSS_VIEWPORT = 20;
  const SEEK_SCAN_PIN = "__seek_scan__";
  const SEEK_PROGRESS_POLL_MS = 400;
  const SEEK_GOAL_SAME_AS_START_M = 50;
  const SEEK_HOP_VIEWSHED_PREFIX = "_seek_hop_";
  const LAND_DEFAULT_FILL_COLOR = "#4a6cf7";
  const LAND_DEFAULT_FILL_OPACITY = 0.48;
  const LAND_DEFAULT_LINE_COLOR = "#1e40af";
  const LAND_LINE_WIDTH = 1.25;
  const LAND_PREVIEW_LINE_WIDTH = 2.5;
  const VIEWSHED_OPACITY_DEFAULT = 0.75;
  const DRAFT_VIEWSHED_SLUG = "_draft";
  const VIEWSHED_PREVIEW_QUALITY = 1;
  const SKADI_DEM_SPACING_M = 30;
  const VIEWSHED_QUALITY_MIN = 1;
  const VIEWSHED_QUALITY_MAX = 5;
  const VIEWSHED_RASTER_MIN = 128;
  const VIEWSHED_RASTER_MAX = 4096;
  const COORD_PREFETCH_MS = 350;
  const DRAFT_MARKER_COLOR = "#fbbf24";
  const simDefaults = config.simulation || {};
  const VIEWSHED_RADIUS_KM_MIN = Number(simDefaults.radius_km_min) || 1;
  const VIEWSHED_RADIUS_KM_MAX = Number(simDefaults.radius_km_max) || 100;
  const PITCH_TERRAIN_ON = 12;
  const PITCH_TERRAIN_OFF = 6;
  const SITE_FIT_BUFFER_KM = 30;
  const MAP_STATE_KEY = `peaky.map.v1.${projectSlug}`;
  const MAP_STATE_SAVE_MS = 400;
  const MAP_GLYPHS_URL =
    "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf";
  const MAP_TEXT_FONT = ["Noto Sans Regular"];
  const MAP_LABEL_FONT = ["Noto Sans Medium"];

  function demNativeRasterDimension(radiusKm) {
    const px = Math.ceil((radiusKm * 1000) / SKADI_DEM_SPACING_M);
    return Math.max(VIEWSHED_RASTER_MIN, Math.min(VIEWSHED_RASTER_MAX, px));
  }

  function rasterUpgradeLadder(minPx, targetPx) {
    const min = Math.max(VIEWSHED_RASTER_MIN, Math.min(VIEWSHED_RASTER_MAX, minPx));
    const target = Math.max(min, Math.min(VIEWSHED_RASTER_MAX, targetPx));
    const ladder = [min];
    let cur = min;
    while (cur < target) {
      const next = Math.min(cur * 2, target);
      if (next <= cur) break;
      ladder.push(next);
      cur = next;
    }
    return ladder;
  }

  function computeViewshedRaster(quality, radiusKm) {
    const q = Math.max(
      VIEWSHED_QUALITY_MIN,
      Math.min(VIEWSHED_QUALITY_MAX, Math.round(Number(quality) || VIEWSHED_QUALITY_MIN)),
    );
    if (q === 1) return VIEWSHED_RASTER_MIN;
    const full = demNativeRasterDimension(radiusKm);
    if (q === 5 || full <= VIEWSHED_RASTER_MIN) return full;
    const ladder = rasterUpgradeLadder(VIEWSHED_RASTER_MIN, full);
    const idx = Math.round(((q - 1) / (VIEWSHED_QUALITY_MAX - 1)) * (ladder.length - 1));
    return ladder[Math.min(idx, ladder.length - 1)];
  }

  function compareHuman(left, right) {
    return String(left).localeCompare(String(right), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  }

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
    opacitySlider.value = String(Math.round(viewshedOpacity * 100));
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
    savedMapState && BASEMAPS[savedMapState.basemap]
      ? savedMapState.basemap
      : "street";
  let showSiteLinks = savedMapState?.showLinks ?? true;
  let viewshedOpacity =
    savedMapState?.viewshedOpacity ?? VIEWSHED_OPACITY_DEFAULT;
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
    VIEWSHED_QUALITY_MIN,
    Math.min(VIEWSHED_QUALITY_MAX, Math.round(viewshedQuality)),
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
    return Math.max(
      VIEWSHED_RADIUS_KM_MIN,
      Math.min(VIEWSHED_RADIUS_KM_MAX, Number(km)),
    );
  }

  function clampViewshedQuality(quality) {
    return Math.max(
      VIEWSHED_QUALITY_MIN,
      Math.min(VIEWSHED_QUALITY_MAX, Math.round(Number(quality))),
    );
  }

  function setViewshedSimulation(radiusKm, quality) {
    const nextRadius = clampRadiusKm(radiusKm);
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

  syncToolbarFromSaved(savedMapState);
  setViewshedSimulation(defaultRadiusKm, defaultViewshedQuality);

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

  function kmToDegreeDeltas(latDeg, km) {
    const m = km * 1000;
    const latDelta = m / 111_320;
    const lonDelta = m / (111_320 * Math.cos((latDeg * Math.PI) / 180));
    return { latDelta, lonDelta };
  }

  /** True when the map is pitched into 3D terrain view. */
  function isMapTiltedView(mapInstance = map) {
    return Boolean(
      mapInstance && mapReady && mapInstance.getPitch() >= PITCH_TERRAIN_ON,
    );
  }

  /** Geographic bounds for the same center/zoom as overhead view (ignores pitch/bearing). */
  function mapOverheadEquivalentBounds(mapInstance) {
    const center = mapInstance.getCenter();
    const zoom = mapInstance.getZoom();
    const canvas = mapInstance.getCanvas();
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    const latRad = (center.lat * Math.PI) / 180;
    const worldSize = 512 * 2 ** zoom;
    const metersPerPixel = (40_075_016.686 * Math.cos(latRad)) / worldSize;
    const halfWidthM = (w / 2) * metersPerPixel;
    const halfHeightM = (h / 2) * metersPerPixel;
    const latDelta = halfHeightM / 111_320;
    const lonDelta = halfWidthM / (111_320 * Math.max(1e-6, Math.cos(latRad)));
    const west = center.lng - lonDelta;
    const east = center.lng + lonDelta;
    const south = center.lat - latDelta;
    const north = center.lat + latDelta;
    return {
      getWest: () => west,
      getEast: () => east,
      getSouth: () => south,
      getNorth: () => north,
    };
  }

  /** Bounds used for warm priorities and sidebar "in view" when the map is flat. */
  function mapDataViewportBounds(mapInstance = map) {
    if (isMapTiltedView(mapInstance))
      return mapOverheadEquivalentBounds(mapInstance);
    return mapInstance.getBounds();
  }

  function lngLatBoundsFromPoints(points) {
    let west = Infinity;
    let east = -Infinity;
    let south = Infinity;
    let north = -Infinity;
    for (const ll of points) {
      if (!ll || !Number.isFinite(ll.lng) || !Number.isFinite(ll.lat)) continue;
      if (Math.abs(ll.lat) > 90 || Math.abs(ll.lng) > 180) continue;
      west = Math.min(west, ll.lng);
      east = Math.max(east, ll.lng);
      south = Math.min(south, ll.lat);
      north = Math.max(north, ll.lat);
    }
    if (!Number.isFinite(west)) return null;
    return {
      getWest: () => west,
      getEast: () => east,
      getSouth: () => south,
      getNorth: () => north,
    };
  }

  function padMapBounds(bounds, minSpanM) {
    if (!bounds || minSpanM <= 0) return bounds;
    const centerLat = (bounds.getNorth() + bounds.getSouth()) / 2;
    const latRad = (centerLat * Math.PI) / 180;
    const minLatDelta = minSpanM / 111320;
    const minLonDelta = minSpanM / (111320 * Math.max(1e-6, Math.cos(latRad)));
    let west = bounds.getWest();
    let east = bounds.getEast();
    let south = bounds.getSouth();
    let north = bounds.getNorth();
    if (east - west < minLonDelta) {
      const cx = (east + west) / 2;
      west = cx - minLonDelta / 2;
      east = cx + minLonDelta / 2;
    }
    if (north - south < minLatDelta) {
      const cy = (north + south) / 2;
      south = cy - minLatDelta / 2;
      north = cy + minLatDelta / 2;
    }
    return {
      getWest: () => west,
      getEast: () => east,
      getSouth: () => south,
      getNorth: () => north,
    };
  }

  /** Geographic bounds of terrain visible on screen (pitch-aware). */
  function mapSeekScanBounds(mapInstance = map) {
    if (!mapInstance || !mapReady) return mapDataViewportBounds(mapInstance);
    if (!isMapTiltedView(mapInstance)) return mapInstance.getBounds();
    const canvas = mapInstance.getCanvas();
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    const yMin = h * 0.1;
    const cols = 7;
    const rows = 7;
    const points = [];
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        const x = ((col + 0.5) / cols) * w;
        const y = yMin + ((row + 0.5) / rows) * (h - yMin);
        points.push(mapInstance.unproject([x, y]));
      }
    }
    const bounds = lngLatBoundsFromPoints(points);
    if (bounds) return bounds;
    return mapInstance.getBounds();
  }

  function seekPeakBinSizeMForBounds(bounds) {
    const centerLat = (bounds.getNorth() + bounds.getSouth()) / 2;
    const lngSpan = Math.abs(bounds.getEast() - bounds.getWest());
    const metersPerDegLng = 111320 * Math.cos((centerLat * Math.PI) / 180);
    const viewportWidthM = lngSpan * metersPerDegLng;
    const raw = viewportWidthM / SEEK_PEAK_BINS_ACROSS_VIEWPORT;
    return Math.round(
      Math.max(SEEK_PEAK_BIN_MIN_M, Math.min(SEEK_PEAK_BIN_MAX_M, raw)),
    );
  }

  function seekScanBoundsForRequest() {
    const raw = mapSeekScanBounds();
    const binM = seekPeakBinSizeMForBounds(raw);
    return padMapBounds(raw, Math.max(binM * 4, SEEK_PEAK_BIN_MIN_M * 2));
  }

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
    style: basemapStyle(currentBasemapKey),
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
        updatePinOverlays();
      });
    }).observe(mapContainer);
  }
  const mapToolbarRefs = installMapToolbar(navControl._container);
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

  function setSitePinProgress(slug, partial) {
    if (!slug) return;
    sitePinProgress.set(slug, { ...(sitePinProgress.get(slug) || {}), ...partial });
  }

  function clearSitePinProgress(slug) {
    sitePinProgress.delete(slug);
  }

  function sitePinProgressLabel(slug) {
    if (siteViewshedReady.has(slug) && !siteOutboundLinksReady.has(slug)) {
      return "links…";
    }
    const p = sitePinProgress.get(slug);
    if (p?.step > 0 && p?.total > 0 && p?.raster > 0) {
      return `${p.step}/${p.total} · ${p.raster}px`;
    }
    if (p?.step > 0 && p?.total > 0) {
      return `${p.step}/${p.total}`;
    }
    if (p?.raster > 0 && p?.target > 0) {
      return `${p.raster}/${p.target}px`;
    }
    return "warm…";
  }

  function sitePinProgressFraction(slug) {
    if (siteViewshedReady.has(slug) && siteOutboundLinksReady.has(slug)) return 1;
    if (siteViewshedReady.has(slug) && !siteOutboundLinksReady.has(slug)) return 0.92;
    const p = sitePinProgress.get(slug);
    // Prefer ladder steps (equal weight) over raw px ratio.
    if (p?.total > 0 && p.step > 0) {
      return Math.min(0.88, 0.06 + 0.82 * (p.step / p.total));
    }
    if (p?.target > 0 && p.raster > 0) {
      return Math.min(0.88, 0.06 + 0.82 * (p.raster / p.target));
    }
    return 0.06;
  }

  const PIN_LOAD_MARKER_OFFSET = [0, 10];

  function coordsUsableForMarker(lon, lat) {
    return (
      Number.isFinite(lon) &&
      Number.isFinite(lat) &&
      Math.abs(lat) <= 90 &&
      Math.abs(lon) <= 180
    );
  }

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

  function ensurePinLoadMarker(slug, kind = "overlay") {
    let marker = pinLoadMarkers.get(slug);
    if (!marker) {
      const el = document.createElement("div");
      el.setAttribute("data-slug", slug);
      if (kind === "spinner") {
        el.className = "pin-load-spinner";
      } else {
        el.className = "pin-load-overlay";
        el.innerHTML =
          '<div class="pin-load-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100">' +
          '<div class="pin-load-progress__fill"></div></div>' +
          '<div class="pin-load-progress__label"></div>';
      }
      // Never addTo(map) until setLngLat — MapLibre smart_wrap crashes on undefined lng.
      marker = new maplibregl.Marker({
        element: el,
        anchor: kind === "spinner" ? "center" : "top",
        offset: kind === "spinner" ? [0, 0] : PIN_LOAD_MARKER_OFFSET,
      });
      pinLoadMarkers.set(slug, marker);
    }
    return marker;
  }

  function hidePinLoadMarker(slug) {
    const marker = pinLoadMarkers.get(slug);
    if (!marker) return;
    marker.remove();
    pinLoadMarkers.delete(slug);
  }

  function renderPinLoadOverlay(slug, lon, lat) {
    if (!mapReady || !coordsUsableForMarker(lon, lat)) return;
    const marker = ensurePinLoadMarker(slug);
    if (!setMarkerLngLatSafe(marker, lon, lat)) return;
    const el = marker.getElement();
    const frac = sitePinProgressFraction(slug);
    const pct = Math.round(frac * 100);
    const fill = el.querySelector(".pin-load-progress__fill");
    const label = el.querySelector(".pin-load-progress__label");
    const bar = el.querySelector(".pin-load-progress");
    if (fill) fill.style.width = `${pct}%`;
    if (label) label.textContent = sitePinProgressLabel(slug);
    if (bar) {
      bar.setAttribute("aria-valuenow", String(pct));
      bar.setAttribute("aria-label", `${slug} ${sitePinProgressLabel(slug)}`);
    }
    el.hidden = false;
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
  const landFolderModal = document.getElementById("land-folder-modal");
  const landFolderName = document.getElementById("land-folder-name");
  const landFolderError = document.getElementById("land-folder-error");
  const landFolderSave = document.getElementById("land-folder-save");
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
  const landSourceBatchVisible = new Map();
  if (
    savedMapState?.landSourceBatchVisible &&
    typeof savedMapState.landSourceBatchVisible === "object"
  ) {
    for (const [key, visible] of Object.entries(
      savedMapState.landSourceBatchVisible,
    )) {
      landSourceBatchVisible.set(key, !!visible);
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

  function ensureTerrainSource() {
    if (map.getSource(TERRAIN_SOURCE)) {
      const spec = terrainDemSourceSpec();
      const src = map.getSource(TERRAIN_SOURCE);
      if (src && typeof src.setTiles === "function") {
        src.setTiles(spec.tiles);
      }
      return;
    }
    map.addSource(TERRAIN_SOURCE, terrainDemSourceSpec());
  }

  function refreshTerrainSourceIfNeeded() {
    if (!mapReady) return;
    const wasActive = terrainActive;
    if (wasActive) {
      terrainActive = false;
      hideTerrainOverlays();
    }
    if (wasActive) syncTerrainFromPitch();
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
          "hillshade-exaggeration": 0.45,
          "hillshade-shadow-color": "#3d4654",
          "hillshade-highlight-color": "#f8fafc",
          "hillshade-accent-color": "#94a3b8",
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

  function siteLinksApiUrl(slug) {
    return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}/links`;
  }

  function linksWarmApiUrl() {
    return `/api/p/${projectSlug}/links/warm`;
  }

  function warmPrioritiesApiUrl() {
    return `/api/p/${projectSlug}/warm/priorities`;
  }

  const WARM_PRIORITY_INTERACTIVE = 0;
  const WARM_PRIORITY_VIEWPORT = 10;
  const WARM_VIEWPORT_SLUG_CAP = 48;
  let warmPrioritiesTimer = null;

  function warmPrioritySlugsInViewport() {
    const slugs = [];
    for (const site of sites) {
      if (isSiteMapHidden(site.slug) || !isViewshedVisible(site.slug)) continue;
      if (siteVisibleInMap(site)) slugs.push(site.slug);
      if (slugs.length >= WARM_VIEWPORT_SLUG_CAP) break;
    }
    return slugs;
  }

  function bumpWarmPriorities(slugs, priority) {
    const list = Array.isArray(slugs) ? slugs.filter(Boolean) : [];
    if (!list.length) return;
    fetch(warmPrioritiesApiUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slugs: list, priority }),
    }).catch(() => {
      /* background warm is best-effort */
    });
  }

  function syncWarmPriorities() {
    if (!mapReady) return;
    const viewport = warmPrioritySlugsInViewport();
    const slugs = new Set(viewport);
    if (selectedSlug && isViewshedVisible(selectedSlug))
      slugs.add(selectedSlug);
    if (!slugs.size) return;
    if (selectedSlug && slugs.has(selectedSlug)) {
      void bumpWarmPriorities([selectedSlug], WARM_PRIORITY_INTERACTIVE);
      slugs.delete(selectedSlug);
    }
    if (slugs.size) {
      void bumpWarmPriorities([...slugs], WARM_PRIORITY_VIEWPORT);
    }
  }

  function scheduleWarmPrioritiesSync() {
    if (warmPrioritiesTimer) clearTimeout(warmPrioritiesTimer);
    warmPrioritiesTimer = setTimeout(() => {
      warmPrioritiesTimer = null;
      syncWarmPriorities();
    }, 300);
  }

  function onMapMoveEndForWarmPriorities() {
    if (isMapTiltedView()) return;
    scheduleWarmPrioritiesSync();
  }

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

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
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
    map.getCanvas().style.cursor =
      addPlacementMode || editMode || seekGoalPlacementMode ? "crosshair" : "";
  }

  function clearAddPlacementMode() {
    addPlacementMode = null;
    if (entityPanelAddSite) {
      entityPanelAddSite.classList.remove("active");
    }
    if (mapShell) {
      mapShell.classList.remove("add-placement-mode", "add-site-mode");
    }
    syncMapCursor();
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

  function syncMapViewport() {
    if (mapShell) {
      mapShell.classList.toggle(
        "site-panel-open",
        sitePanel && !sitePanel.hidden,
      );
    }
  }

  function setEntityPanelOpen(open) {
    entityPanelOpen = !!open;
    if (entityPanel) entityPanel.hidden = !entityPanelOpen;
    if (mapShell)
      mapShell.classList.toggle("entity-panel-open", entityPanelOpen);
    if (entityPanelToggle) {
      entityPanelToggle.setAttribute(
        "aria-expanded",
        entityPanelOpen ? "true" : "false",
      );
      entityPanelToggle.setAttribute(
        "aria-label",
        entityPanelOpen ? "Hide sites" : "Show sites",
      );
    }
    syncEntityPanelToggles();
    scheduleSaveMapState();
    if (mapReady) {
      map.resize();
      requestAnimationFrame(() => updatePinOverlays());
    }
  }

  setEntityPanelOpen(entityPanelOpen);

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
    const filter = combineLayerFilters(
      siteVisibilityFilter(),
      editSiteLayerFilter(),
    );
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
    if (!editMode || !editSnapshot || !editCoordsMovedFromSnapshot())
      return false;
    const geom = feature.geometry;
    if (!geom || geom.type !== "LineString" || !Array.isArray(geom.coordinates))
      return false;
    const snapLon = Number(editSnapshot.lon);
    const snapLat = Number(editSnapshot.lat);
    for (const pt of geom.coordinates) {
      if (!Array.isArray(pt) || pt.length < 2) continue;
      const lon = Number(pt[0]);
      const lat = Number(pt[1]);
      if (Math.abs(lon - snapLon) < 1e-5 && Math.abs(lat - snapLat) < 1e-5)
        return true;
    }
    return false;
  }

  function filterSiteLinksGeoJson(geojson) {
    if (!geojson || !geojson.features) return geojson;
    const chainPairs = seekSessionActive() ? seekChainSitePairKeys() : null;
    const features = geojson.features.filter((feature) => {
      const props = feature.properties || {};
      if (isSiteMapHidden(props.a) || isSiteMapHidden(props.b)) return false;
      if (editMode && editSlug) {
        if (props.a === editSlug || props.b === editSlug) return false;
      }
      if (linkFeatureTouchesSnapshotCoords(feature)) return false;
      if (chainPairs?.size) {
        const key = canonicalSitePairKey(String(props.a), String(props.b));
        if (chainPairs.has(key)) return false;
      }
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

  /** Warm a viewshed that was skipped while the site was hidden/filtered. */
  function ensureViewshedLoadedForSlug(slug) {
    if (isSiteMapHidden(slug) || !isViewshedVisible(slug)) return;
    if (map.getLayer(viewshedLayerId(slug))) return;
    if (sitePinSpinning(slug) || viewshedPendingEpoch.has(slug)) return;
    if (slug === DRAFT_VIEWSHED_SLUG) {
      const lat = draftPlacementLat ?? pendingCreateLat;
      const lon = draftPlacementLon ?? pendingCreateLon;
      if (lat != null && lon != null) void loadDraftViewshedAt(lat, lon);
      return;
    }
    const site = siteBySlug.get(slug);
    if (site) scheduleViewshedLoad(site);
  }

  function resetSiteProgress(slug) {
    siteViewshedReady.delete(slug);
    siteOutboundLinksReady.delete(slug);
    clearSitePinProgress(slug);
  }

  function markSiteViewshedReady(slug) {
    siteViewshedReady.add(slug);
    if (siteOutboundLinksReady.has(slug)) {
      clearSitePinProgress(slug);
    } else {
      setSitePinProgress(slug, { phase: "links" });
    }
    viewshedLoading.delete(slug);
    viewshedPendingEpoch.delete(slug);
    updatePinOverlays();
    if (slug === selectedSlug) syncViewshedCheckbox();
  }

  function markSiteOutboundLinksReady(slug) {
    siteOutboundLinksReady.add(slug);
    if (siteViewshedReady.has(slug)) {
      clearSitePinProgress(slug);
    }
    updatePinOverlays();
  }

  function sitePinSpinning(slug) {
    if (isSiteMapHidden(slug)) return false;
    if (!isViewshedVisible(slug)) return false;
    return !siteViewshedReady.has(slug) || !siteOutboundLinksReady.has(slug);
  }

  /** Parallel cache probe only — one warm-priority bump for the batch. */
  function probeViewshedCacheForSite(site) {
    removeViewshedLayer(site.slug);
    resetSiteProgress(site.slug);
    viewshedLoading.add(site.slug);
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch);
    void tryLoadViewshedFromCache(site.slug);
  }

  function ensureViewshedsForNewlyVisibleSites() {
    let queued = false;
    for (const site of sites) {
      if (isSiteMapHidden(site.slug) || !isViewshedVisible(site.slug)) continue;
      if (map.getLayer(viewshedLayerId(site.slug))) continue;
      if (sitePinSpinning(site.slug) || viewshedPendingEpoch.has(site.slug))
        continue;
      probeViewshedCacheForSite(site);
      queued = true;
    }
    if (queued) {
      updatePinOverlays();
      syncWarmPriorities();
    }
  }

  function applyEntityVisibility() {
    applySiteLayerFilters();
    for (const site of sites) {
      applyViewshedVisibilityForSite(site.slug);
    }
    refreshFilteredLinks();
    if (seekState?.running) refreshSeekAncillaryLinksDisplay();
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
      slug === DRAFT_VIEWSHED_SLUG ||
      String(slug).startsWith("_edit_hist_") ||
      String(slug).startsWith(SEEK_HOP_VIEWSHED_PREFIX)
    );
  }

  function isSiteMapHidden(slug) {
    if (isEphemeralViewshedSlug(slug)) return siteHidden.has(slug);
    if (siteHidden.has(slug)) return true;
    const site = siteBySlug.get(slug);
    if (!site) return true;
    if (isSiteInSeekPlan(slug)) return false;
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
    ensureViewshedsForNewlyVisibleSites();
    refreshSeekStartSelectIfOpen();
    scheduleSaveMapState();
  }

  function setTagFilterMode(mode) {
    const next = mode === "or" ? "or" : "and";
    if (tagFilterMode === next) return;
    tagFilterMode = next;
    applyEntityVisibility();
    renderEntityPanel();
    refreshSeekStartSelectIfOpen();
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

  function unregisterSite(slug) {
    const idx = sites.findIndex((s) => s.slug === slug);
    if (idx >= 0) sites.splice(idx, 1);
    siteBySlug.delete(slug);
    siteHidden.delete(slug);
    tagFilterBypassSlugs.delete(slug);
    viewshedVisible.delete(slug);
    removeViewshedLayer(slug);
    purgeSiteLinksForSlug(slug);
    if (selectedSlug === slug) deselectSite();
    addSiteLayers();
    renderEntityPanel();
    refreshSeekStartSelectIfOpen();
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
    if (bulkTagModalOpen) syncBulkTagModalStatus();
  }

  function onMapMoveEndForEntityPanel() {
    if (entityPanelFilterByViewport) renderEntityPanel();
  }

  function syncBulkTagButton() {
    if (!entityPanelBulkTag) return;
    const count = sidebarSiteSlugs().length;
    entityPanelBulkTag.disabled = !mapReady || count === 0;
  }

  function bulkTagListTags() {
    const found = new Set([
      ...allProjectTags(),
      ...bulkTagInitialCounts.keys(),
      ...bulkTagPending.keys(),
    ]);
    return [...found].sort((a, b) => a.localeCompare(b));
  }

  function bulkTagVisualState(tag) {
    const total = bulkTagTargetSlugs.length;
    if (!total) return "none";
    const pending = bulkTagPending.get(tag);
    if (pending === "all") return "full";
    if (pending === "none") return "none";
    const count = bulkTagInitialCounts.get(tag) || 0;
    if (count === 0) return "none";
    if (count >= total) return "full";
    return "partial";
  }

  function toggleBulkTag(tag) {
    const state = bulkTagVisualState(tag);
    if (state === "full") bulkTagPending.set(tag, "none");
    else bulkTagPending.set(tag, "all");
    renderBulkTagTags();
    syncBulkTagSaveButton();
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

  function bulkTagHasChanges() {
    const { addTags, removeTags } = computeBulkTagOps();
    return addTags.length > 0 || removeTags.length > 0;
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
        if (!bulkTagListTags().includes(tag)) bulkTagPending.delete(tag);
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
    renderBulkTagTags();
    syncBulkTagAddSuggestions();
    syncBulkTagSaveButton();
  }

  function renderBulkTagTags() {
    if (!bulkTagTagsEl) return;
    bulkTagTagsEl.innerHTML = "";
    for (const tag of bulkTagListTags()) {
      const state = bulkTagVisualState(tag);
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
      chip.addEventListener("click", () => toggleBulkTag(tag));
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
    bulkTagSave.disabled = !bulkTagTargetSlugs.length || !bulkTagHasChanges();
  }

  function addBulkTagFromInput() {
    if (!bulkTagAddInput) return;
    const tag = normalizeTagInput(bulkTagAddInput.value);
    bulkTagAddInput.value = "";
    if (!tag) return;
    if (!bulkTagInitialCounts.has(tag)) bulkTagInitialCounts.set(tag, 0);
    bulkTagPending.set(tag, "all");
    renderBulkTagTags();
    syncBulkTagSaveButton();
  }

  function closeBulkTagModal() {
    if (!bulkTagModal) return;
    bulkTagModal.open = false;
  }

  async function openBulkTagModal() {
    if (!bulkTagModal) return;
    setBulkTagError("");
    await customElements.whenDefined("wa-dialog");
    syncBulkTagModalStatus({ resetPending: true });
    bulkTagModal.open = true;
  }

  async function saveBulkTagModal() {
    addBulkTagFromInput();
    syncBulkTagModalStatus();
    const slugs = sidebarSiteSlugs();
    const { addTags, removeTags } = computeBulkTagOps();
    if (!slugs.length || (!addTags.length && !removeTags.length)) {
      setBulkTagError("Change at least one tag.");
      return;
    }
    setBulkTagError("");
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
        setBulkTagError(payload.error || `Tag update failed (${resp.status})`);
        return;
      }
      const updated = Array.isArray(payload.sites) ? payload.sites : [];
      closeBulkTagModal();
      for (const site of updated) {
        applySiteRowUpdate(site, { refreshGeoJson: false });
        syncTagFilterBypassForSite(site.slug);
      }
      applyEntityVisibility();
      renderTagFilters();
      scheduleSaveMapState();
    } catch (_) {
      setBulkTagError("Could not reach server.");
    } finally {
      syncBulkTagSaveButton();
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
    btn.innerHTML = mapToolIcon(icon, label);
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
    const eyeBtn = makeEntityPanelActionBtn({
      icon: siteVisible ? "eye" : "eye-slash",
      label: siteVisible ? "Hide site on map" : "Show site on map",
      active: siteVisible,
      onClick: () => setSiteHidden(site.slug, siteVisible),
    });

    const vsBtn = makeEntityPanelActionBtn({
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

    const delBtn = makeEntityPanelActionBtn({
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

  function registerSite(site) {
    const row = normalizeSiteFromApi(site);
    if (!row) return;
    const ix = sites.findIndex((s) => s.slug === row.slug);
    if (ix >= 0) sites[ix] = row;
    else sites.push(row);
    siteBySlug.set(row.slug, row);
    ensureSiteVisibleAfterAdd(row);
    addSiteLayers();
    applyEntityVisibility();
    renderEntityPanel();
    updateSelectedLayer();
    raiseSiteLayers();
    refreshSeekStartSelectIfOpen();
    scheduleSaveMapState();
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

  function openCreatePanel(lat, lon) {
    pendingCreateLat = lat;
    pendingCreateLon = lon;
    setCreateError("");
    sitePanelCreateName.value = "";
    sitePanelCreateCoords.textContent = `${formatCoord(lat)}, ${formatCoord(lon)}`;
    resetCreatePanelTags();
    syncCreateSlugPreview();
    syncCreatePanelForKind();
    resetCreatePrefetchUI();
    removeDraftMarker();
    const markerColor = DRAFT_MARKER_COLOR;
    draftMarker = new maplibregl.Marker({ color: markerColor })
      .setLngLat([lon, lat])
      .addTo(map);
    sitePanel.hidden = false;
    syncMapViewport();
    showPanelCreate();
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
    syncCreateViewshedCheckbox();
    void loadDraftViewshedAt(lat, lon);
    sitePanelCreateName.focus();
  }

  function resetCreatePrefetchUI() {
    setSectionVisible("site-panel-create-links-section", false);
    const linksEl = document.getElementById("site-panel-create-links");
    if (linksEl) linksEl.innerHTML = "";
    removeDraftLinksLayer();
  }

  function removeDraftLinksLayer() {
    if (map.getLayer(DRAFT_LINKS_LABELS_LAYER))
      map.removeLayer(DRAFT_LINKS_LABELS_LAYER);
    if (map.getLayer(DRAFT_LINKS_LAYER)) map.removeLayer(DRAFT_LINKS_LAYER);
    if (map.getSource(DRAFT_LINKS_SOURCE)) map.removeSource(DRAFT_LINKS_SOURCE);
  }

  function removeEditHistoryLinksLayer() {
    if (map.getLayer(EDIT_HISTORY_LINKS_LABELS_LAYER))
      map.removeLayer(EDIT_HISTORY_LINKS_LABELS_LAYER);
    if (map.getLayer(EDIT_HISTORY_LINKS_LAYER))
      map.removeLayer(EDIT_HISTORY_LINKS_LAYER);
    if (map.getSource(EDIT_HISTORY_LINKS_SOURCE))
      map.removeSource(EDIT_HISTORY_LINKS_SOURCE);
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

  function seekSiteCandidateLabelsLayerSpec(layerId, sourceId) {
    return {
      id: layerId,
      type: "symbol",
      source: sourceId,
      filter: [
        "any",
        [
          "all",
          ["boolean", ["get", "is_site"], false],
          ["has", "site_name"],
          ["!=", ["get", "site_name"], ""],
        ],
        [
          "all",
          ["!", ["boolean", ["get", "is_goal"], false]],
          ["!", ["boolean", ["get", "is_site"], false]],
          ["has", "elev_m"],
        ],
      ],
      layout: {
        "text-field": [
          "case",
          ["boolean", ["get", "is_site"], false],
          ["get", "site_name"],
          ["concat", ["to-string", ["round", ["get", "elev_m"]]], "m"],
        ],
        "text-size": 12,
        "text-offset": [0, -1.4],
        "text-anchor": "bottom",
        "text-font": MAP_LABEL_FONT,
        "text-allow-overlap": true,
        "text-ignore-placement": true,
        visibility: "visible",
      },
      paint: {
        "text-color": "#e8eaed",
        "text-halo-color": "#1a1a1a",
        "text-halo-width": 2,
      },
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
    if (map.getSource(DRAFT_LINKS_SOURCE)) {
      map.getSource(DRAFT_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(DRAFT_LINKS_LAYER)) {
        map.setPaintProperty(DRAFT_LINKS_LAYER, "line-color", [
          "case",
          ["get", "manual"],
          "#0d9488",
          "#4a6cf7",
        ]);
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
    map.addLayer(
      linkLabelsLayerSpec(
        DRAFT_LINKS_LABELS_LAYER,
        DRAFT_LINKS_SOURCE,
        "visible",
      ),
      SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  function editHistoryLinksLineColor() {
    return DRAFT_MARKER_COLOR;
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
    if (map.getSource(EDIT_HISTORY_LINKS_SOURCE)) {
      map.getSource(EDIT_HISTORY_LINKS_SOURCE).setData(labeled);
      if (map.getLayer(EDIT_HISTORY_LINKS_LAYER)) {
        map.setPaintProperty(EDIT_HISTORY_LINKS_LAYER, "line-color", lineColor);
      }
      raiseSiteLayers();
      return;
    }
    map.addSource(EDIT_HISTORY_LINKS_SOURCE, {
      type: "geojson",
      data: labeled,
    });
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
      linkLabelsLayerSpec(
        EDIT_HISTORY_LINKS_LABELS_LAYER,
        EDIT_HISTORY_LINKS_SOURCE,
        "visible",
      ),
      SITES_CIRCLE,
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
    resetCreatePanelTags();
    setCreateError("");
    if (selectedSlug) {
      sitePanel.hidden = false;
      syncMapViewport();
      showPanelView();
      renderPanel(siteBySlug.get(selectedSlug));
    } else {
      sitePanel.hidden = true;
      syncMapViewport();
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
      removeDraftViewshed();
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

  function landApiUrl() {
    return `/api/p/${projectSlug}/land`;
  }

  function landSidebarApiUrl() {
    return `/api/p/${projectSlug}/land/sidebar`;
  }

  function landDataGdbsUrl() {
    return `/api/p/${projectSlug}/land/data-gdbs`;
  }

  function landImportPreviewApiUrl() {
    return `/api/p/${projectSlug}/land/import/preview`;
  }

  function landImportApiUrl() {
    return `/api/p/${projectSlug}/land/import`;
  }

  function landSourceApiUrl(sourceId) {
    return `/api/p/${projectSlug}/land/sources/${encodeURIComponent(sourceId)}`;
  }

  function landLayerGeoJsonUrl(sourceId, layer) {
    return `/api/p/${projectSlug}/land/sources/${encodeURIComponent(sourceId)}/layers/${encodeURIComponent(layer)}/geojson`;
  }

  function landPreviewGeoJsonUrl(path, layer) {
    const params = new URLSearchParams({ path, layer });
    return `/api/p/${projectSlug}/land/preview/geojson?${params}`;
  }

  function landPreviewCacheKey(path, layer) {
    return `preview|${path}|${layer}`;
  }

  function landLayerCacheKey(sourceId, layer, digest) {
    const d = digest ? String(digest) : "";
    return d ? `layer|${sourceId}|${layer}|${d}` : `layer|${sourceId}|${layer}`;
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

  function landFieldsApiUrl(path, layer) {
    const params = new URLSearchParams({ path, layer });
    return `/api/p/${projectSlug}/land/import/preview/fields?${params}`;
  }

  function landValuesApiUrl(path, layer, field) {
    const params = new URLSearchParams({ path, layer, field });
    return `/api/p/${projectSlug}/land/import/preview/values?${params}`;
  }

  function landPreviewGeoJsonPostUrl() {
    return `/api/p/${projectSlug}/land/preview/geojson`;
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
      configs.set(layerName, defaultLandLayerConfig());
    return configs.get(layerName);
  }

  function normalizeRegisteredLayer(layer) {
    if (typeof layer === "string") {
      const name = layer;
      return {
        name,
        key: landLayerSlug(name),
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
      key: layer.key || landLayerSlug(name),
      role: layer.role || null,
      digest: layer.digest || null,
      style: layer.style || null,
      styleField: layer.styleField || layer.style_field || null,
      labelField: layer.labelField || layer.label_field || null,
      include: Array.isArray(layer.include) ? layer.include : [],
      exclude: Array.isArray(layer.exclude) ? layer.exclude : [],
    };
  }

  function landLayerShortLabel(spec) {
    if (spec.role === "include") return "Public land";
    if (spec.role === "exclude") return friendlyLandLayerName(spec.name);
    for (const filt of spec.include || []) {
      const values = (filt.values || []).filter(Boolean);
      if (values.length === 1) {
        if (filt.field === "ABBR") return values[0];
        return filt.field ? `${filt.field}: ${values[0]}` : values[0];
      }
      if (values.length > 1) return values.join(", ");
    }
    if (spec.id) return String(spec.id).toUpperCase();
    return friendlyLandLayerName(spec.name);
  }

  function landLayerDisplayName(spec) {
    const parts = [spec.name];
    const includeValues = (spec.include || [])
      .flatMap((filt) => (Array.isArray(filt.values) ? filt.values : []))
      .filter(Boolean);
    if (includeValues.length) parts.push(`(${includeValues.join(", ")})`);
    const excludeValues = (spec.exclude || [])
      .flatMap((filt) => (Array.isArray(filt.values) ? filt.values : []))
      .filter(Boolean);
    if (excludeValues.length) parts.push(`(−${excludeValues.join(", ")})`);
    return parts.join(" ");
  }

  function landPathBasename(path) {
    let raw = path;
    if (Array.isArray(raw)) raw = raw.join("/");
    raw = String(raw ?? "")
      .trim()
      .replace(/^data\//, "");
    const base = raw.split("/").pop() || raw;
    return base.replace(/\.gdb$/i, "");
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
    return humanizeLandText(String(name).replace(/^BLM[_\s-]+/i, ""));
  }

  function friendlyLandSourceTitle(source) {
    const explicit = String(source.label || "").trim();
    if (explicit && explicit !== source.id) return explicit;
    return humanizeLandText(landPathBasename(source.path));
  }

  function landSourceDisplayTitles(sources) {
    const titles = new Map();
    const groups = new Map();
    for (const source of sources) {
      const title = friendlyLandSourceTitle(source);
      titles.set(source.id, title);
      if (!groups.has(title)) groups.set(title, []);
      groups.get(title).push(source.id);
    }
    for (const ids of groups.values()) {
      if (ids.length <= 1) continue;
      ids.forEach((id, idx) => {
        const suffix = id.match(/-(\d+)$/);
        const base = titles.get(id);
        titles.set(
          id,
          suffix ? `${base} (${suffix[1]})` : `${base} (${idx + 1})`,
        );
      });
    }
    return titles;
  }

  function landLayerRoleBadgeSpec(role) {
    const normalized = String(role || "")
      .trim()
      .toLowerCase();
    if (normalized === "aoi") {
      return {
        label: "AOI",
        className:
          "entity-panel__land-role-badge entity-panel__land-role-badge--aoi",
        title: "Area of interest — unioned clip boundary for other layers",
      };
    }
    if (normalized === "include") {
      return {
        label: "Include",
        className:
          "entity-panel__land-role-badge entity-panel__land-role-badge--include",
        title: "Eligible land for goal seek (include − exclude)",
      };
    }
    if (normalized === "exclude") {
      return {
        label: "Exclude",
        className:
          "entity-panel__land-role-badge entity-panel__land-role-badge--exclude",
        title: "Subtracted from include layers for goal seek",
      };
    }
    return null;
  }

  function appendLandLayerRoleBadge(parent, role) {
    const spec = landLayerRoleBadgeSpec(role);
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
    controls.appendChild(buildLandLayerEyeBtn(sourceId, layerKey));
    if (labelField) {
      controls.appendChild(buildLandLayerLabelBtn(sourceId, layerKey));
    }
    return controls;
  }

  function appendLandLayerMeta(parent, spec, { showFilters = true, showLegend = true } = {}) {
    const meta = document.createElement("div");
    meta.className = "entity-panel__meta";
    let hasMeta = false;

    if (showFilters) {
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
    }

    const styleMap = styleMapFromLayerSpec(spec);
    if (showLegend && styleMap && Object.keys(styleMap).length) {
      const legend = document.createElement("div");
      legend.className = "entity-panel__land-legend";
      for (const [key, rawStyle] of Object.entries(styleMap)) {
        const style = normalizeLandLayerStyle(rawStyle);
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
    if (!spec?.style) return defaultLandLayerStyle();
    if (typeof spec.style === "object" && "color" in spec.style) {
      return normalizeLandLayerStyle(spec.style);
    }
    if (spec.styleField && typeof spec.style === "object") {
      const first = Object.values(spec.style)[0];
      return normalizeLandLayerStyle(first);
    }
    return defaultLandLayerStyle();
  }

  function buildStyleMatchExpression(styleMap, prop, fallback) {
    const normalized = normalizeLandLayerStyle(fallback);
    const expr = ["match", ["get", prop]];
    for (const [key, rawStyle] of Object.entries(styleMap || {})) {
      const style = normalizeLandLayerStyle(rawStyle);
      expr.push(String(key), style.color);
    }
    expr.push(normalized.color);
    return expr;
  }

  function buildOpacityMatchExpression(styleMap, prop, fallback) {
    const normalized = normalizeLandLayerStyle(fallback);
    const expr = ["match", ["get", prop]];
    for (const [key, rawStyle] of Object.entries(styleMap || {})) {
      const style = normalizeLandLayerStyle(rawStyle);
      expr.push(String(key), style.opacity);
    }
    expr.push(normalized.opacity);
    return expr;
  }

  function layerConfigToPayload(name, layerStyles, layerConfigs) {
    const config = layerConfigs.get(name) || defaultLandLayerConfig();
    const row = { name };
    if (config.role) row.role = config.role;
    if (config.labelField) row.labelField = config.labelField;
    if (config.include?.length) row.include = config.include;
    if (config.exclude?.length) row.exclude = config.exclude;
    if (layerStyles.has(name)) {
      row.style = normalizeLandLayerStyle(layerStyles.get(name));
    }
    return row;
  }

  function landColumnSortKey(name) {
    const upper = String(name).toUpperCase();
    if (upper === "NAME") return "0";
    if (upper === "ABBR") return "1";
    return `9${upper}`;
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
    const spec = normalizeRegisteredLayer(layer);
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

  function resolveRegisteredPreviewLayerName(registeredName, previewLayers) {
    const previewNames = (previewLayers || []).map((layer) => layer.name);
    if (previewNames.includes(registeredName)) return registeredName;
    if (previewNames.length === 1) return previewNames[0];
    return null;
  }

  function registeredLayersForPreviewName(previewName, registeredLayers, previewLayers) {
    return (registeredLayers || []).filter((raw) => {
      const spec = normalizeRegisteredLayer(raw);
      if (spec.name === previewName) return true;
      return resolveRegisteredPreviewLayerName(spec.name, previewLayers) === previewName;
    });
  }

  function initialEditLandSelected(source, previewLayers) {
    const selected = new Set();
    for (const rawLayer of source?.layers || []) {
      const spec = normalizeRegisteredLayer(rawLayer);
      const previewName = resolveRegisteredPreviewLayerName(
        spec.name,
        previewLayers,
      );
      if (previewName) selected.add(previewName);
    }
    const previewNames = previewLayers.map((layer) => layer.name);
    if (!selected.size && previewNames.length === 1 && (source?.layers || []).length) {
      selected.add(previewNames[0]);
    }
    return selected;
  }

  function buildEditLandLayerState(source, previewLayers) {
    const configs = new Map();
    const styles = new Map();
    for (const previewLayer of previewLayers) {
      const previewName = previewLayer.name;
      const matching = registeredLayersForPreviewName(
        previewName,
        source?.layers,
        previewLayers,
      );
      const primary =
        matching.find(
          (raw) => normalizeRegisteredLayer(raw).role === "include",
        ) || matching[0];
      if (!primary) continue;
      const spec = normalizeRegisteredLayer(primary);
      configs.set(previewName, configFromRegisteredLayer(primary));
      ensureLandLayerStyleState(
        styles,
        previewName,
        flatStyleFromLayerSpec(spec),
      );
    }
    return { configs, styles };
  }

  function registeredLayerToSavePayload(raw, previewName) {
    const spec = normalizeRegisteredLayer(raw);
    const row = { name: previewName };
    if (raw.id != null && raw.id !== "") row.id = raw.id;
    if (spec.role) row.role = spec.role;
    if (spec.labelField) row.labelField = spec.labelField;
    if (spec.include?.length) row.include = spec.include;
    if (spec.exclude?.length) row.exclude = spec.exclude;
    if (spec.styleField) row.styleField = spec.styleField;
    if (spec.style) row.style = normalizeLandLayerStyle(flatStyleFromLayerSpec(spec));
    return row;
  }

  function collectEditLandSavePayloads(
    listEl,
    layerStyles,
    layerConfigs,
    prevSource,
    previewLayers,
  ) {
    const selectedNames = collectSelectedLandLayers(listEl);
    if (!selectedNames.length) return [];
    const payloads = [];
    for (const previewName of selectedNames) {
      const prevMatching = registeredLayersForPreviewName(
        previewName,
        prevSource?.layers,
        previewLayers,
      );
      if (prevMatching.length > 1) {
        for (const raw of prevMatching) {
          payloads.push(registeredLayerToSavePayload(raw, previewName));
        }
        continue;
      }
      const payload = layerConfigToPayload(
        previewName,
        layerStyles,
        layerConfigs,
      );
      if (prevMatching.length === 1) {
        const raw = prevMatching[0];
        if (raw.id != null && raw.id !== "") payload.id = raw.id;
      }
      payloads.push(payload);
    }
    return payloads;
  }

  function collectSelectedLayerPayloads(listEl, layerStyles, layerConfigs) {
    return collectSelectedLandLayers(listEl).map((name) =>
      layerConfigToPayload(name, layerStyles, layerConfigs),
    );
  }

  async function fetchLandLayerFields(path, layer) {
    const resp = await fetch(landFieldsApiUrl(path, layer));
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok)
      throw new Error(payload.error || `Fields failed (${resp.status})`);
    return payload;
  }

  async function fetchLandFieldValues(path, layer, field) {
    const resp = await fetch(landValuesApiUrl(path, layer, field));
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok)
      throw new Error(payload.error || `Values failed (${resp.status})`);
    return payload;
  }

  function landPreviewLayerConfig(config) {
    if (!config) return null;
    if (
      config.labelField ||
      config.include?.length ||
      config.exclude?.length ||
      config.styleField
    ) {
      return config;
    }
    return null;
  }

  function decorateLandGeoJsonProperties(
    geojson,
    { labelField = "", styleField = "" } = {},
  ) {
    if (!geojson?.features?.length) return geojson;
    const labelKey = String(labelField || "").trim();
    const styleKey = String(styleField || "").trim();
    if (!labelKey && !styleKey) return geojson;
    for (const feat of geojson.features) {
      if (!feat.properties) feat.properties = {};
      if (labelKey) {
        const raw = feat.properties[labelKey];
        if (raw != null && raw !== "") feat.properties.label = String(raw);
      }
      if (styleKey) {
        const raw = feat.properties[styleKey];
        if (raw != null && raw !== "") feat.properties.style_key = String(raw);
      }
    }
    return geojson;
  }

  async function fetchLandPreviewGeoJson(path, layer, layerConfig) {
    const cacheKey = `${landPreviewCacheKey(path, layer)}|${JSON.stringify(layerConfig || {})}`;
    return fetchCachedGeoJson(
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
          };
          const resp = await fetch(landPreviewGeoJsonPostUrl(), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const payload = await resp.json().catch(() => ({}));
          if (!resp.ok || !payload.geojson) {
            throw new Error(payload.error || `Preview failed (${resp.status})`);
          }
          return decorateLandGeoJsonProperties(payload.geojson, layerConfig || {});
        }
        const resp = await fetch(landPreviewGeoJsonUrl(path, layer));
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok || !payload.geojson) {
          throw new Error(payload.error || `Preview failed (${resp.status})`);
        }
        return decorateLandGeoJsonProperties(payload.geojson, layerConfig || {});
      },
    );
  }

  async function fetchLandLayerGeoJson(sourceId, layerKey, digest) {
    return fetchCachedGeoJson(
      landLayerGeoJsonCache,
      landLayerGeoJsonInflight,
      landLayerCacheKey(sourceId, layerKey, digest),
      async () => {
        const resp = await fetch(landLayerGeoJsonUrl(sourceId, layerKey));
        if (!resp.ok) throw new Error(`GeoJSON failed (${resp.status})`);
        const data = await resp.json();
        const respDigest = resp.headers.get("X-Peaky-Digest");
        if (respDigest && respDigest !== digest) {
          landLayerGeoJsonCache.set(
            landLayerCacheKey(sourceId, layerKey, respDigest),
            data,
          );
        }
        return data;
      },
    );
  }

  function landPreviewMapPrefix(sourceIdPrefix) {
    return sourceIdPrefix === "import"
      ? IMPORT_LAND_PREVIEW_SOURCE
      : EDIT_LAND_PREVIEW_SOURCE;
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
    const overlay = ensureLandPreviewLoadingOverlay(mapEl);
    if (!overlay) return;
    overlay.hidden = !loading;
  }

  function beginLandPreviewMapFetch(mapEl) {
    if (!mapEl) return;
    const next = (landPreviewLoadingCounts.get(mapEl) || 0) + 1;
    landPreviewLoadingCounts.set(mapEl, next);
    setLandPreviewMapLoading(mapEl, true);
  }

  function endLandPreviewMapFetch(mapEl) {
    if (!mapEl) return;
    const next = Math.max(0, (landPreviewLoadingCounts.get(mapEl) || 0) - 1);
    landPreviewLoadingCounts.set(mapEl, next);
    setLandPreviewMapLoading(mapEl, next > 0);
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
      color: LAND_DEFAULT_FILL_COLOR,
      opacity: LAND_DEFAULT_FILL_OPACITY,
    };
  }

  function landLineColorFromFill(hex) {
    const normalized = String(hex || "").replace("#", "");
    if (normalized.length !== 6) return LAND_DEFAULT_LINE_COLOR;
    const r = Number.parseInt(normalized.slice(0, 2), 16);
    const g = Number.parseInt(normalized.slice(2, 4), 16);
    const b = Number.parseInt(normalized.slice(4, 6), 16);
    if ([r, g, b].some((n) => Number.isNaN(n))) return LAND_DEFAULT_LINE_COLOR;
    const factor = 0.55;
    const toHex = (n) =>
      Math.round(n * factor)
        .toString(16)
        .padStart(2, "0");
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
  }

  function normalizeLandLayerStyle(raw) {
    const defaults = defaultLandLayerStyle();
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
      return defaultLandLayerStyle();
    const spec = source.layers
      .map((layer) => normalizeRegisteredLayer(layer))
      .find((layer) => layer.key === layerKey);
    return flatStyleFromLayerSpec(spec);
  }

  function resolveLandLayerSpec(sourceId, layerKey) {
    const source = landSources.find((s) => s.id === sourceId);
    if (!source || !Array.isArray(source.layers)) return null;
    return (
      source.layers
        .map((layer) => normalizeRegisteredLayer(layer))
        .find((layer) => layer.key === layerKey) || null
    );
  }

  function landPreviewSourceId(prefix, layerName) {
    return `${prefix}-${landLayerSlug(layerName)}`;
  }

  function landLayerKey(sourceId, layer) {
    return `${sourceId}/${layer}`;
  }

  function landLayerSlug(text) {
    return (
      String(text)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "") || "layer"
    );
  }

  function landMapSourceId(sourceId, layer) {
    return `land-${sourceId}-${landLayerSlug(layer)}`;
  }

  function isLandLayerVisible(sourceId, layer) {
    const key = landLayerKey(sourceId, layer);
    if (!landVisible.has(key)) return false;
    return landVisible.get(key) === true;
  }

  function isLandSourceBatchVisible(sourceId) {
    if (!landSourceBatchVisible.has(sourceId)) return true;
    return landSourceBatchVisible.get(sourceId) === true;
  }

  function isLandLayerEffectivelyVisible(sourceId, layer) {
    if (!isLandSourceBatchVisible(sourceId)) return false;
    return isLandLayerVisible(sourceId, layer);
  }

  function setLandSourceBatchVisible(sourceId, visible) {
    landSourceBatchVisible.set(sourceId, !!visible);
    scheduleSaveMapState();
    const source = landSourceRecord(sourceId);
    if (!source || !Array.isArray(source.layers)) return;
    for (const rawLayer of source.layers) {
      const spec = normalizeRegisteredLayer(rawLayer);
      syncLandMapLayerVisibility(sourceId, spec.key);
      syncLandMapLabelLayer(sourceId, spec.key);
    }
  }

  function setLandLayerVisible(sourceId, layer, visible) {
    landVisible.set(landLayerKey(sourceId, layer), !!visible);
    scheduleSaveMapState();
    syncLandMapLayerVisibility(sourceId, layer);
    syncLandMapLabelLayer(sourceId, layer);
  }

  function landLayerHasLabels(sourceId, layer) {
    const spec = resolveLandLayerSpec(sourceId, layer);
    return !!spec?.labelField;
  }

  function isLandLayerLabelsVisible(sourceId, layer) {
    if (!landLayerHasLabels(sourceId, layer)) return false;
    const key = landLayerKey(sourceId, layer);
    if (!landLabelsVisible.has(key)) return true;
    return landLabelsVisible.get(key) === true;
  }

  function setLandLayerLabelsVisible(sourceId, layer, visible) {
    landLabelsVisible.set(landLayerKey(sourceId, layer), !!visible);
    scheduleSaveMapState();
    syncLandMapLabelLayer(sourceId, layer);
  }

  function normalizeLandSidebarInput(raw) {
    const folders = Array.isArray(raw?.folders)
      ? raw.folders
          .map((folder) => ({
            id: String(folder?.id || "").trim(),
            label: String(folder?.label || folder?.id || "").trim(),
            sources: Array.isArray(folder?.sources)
              ? folder.sources.map((sid) => String(sid).trim()).filter(Boolean)
              : [],
          }))
          .filter((folder) => folder.id)
      : [];
    const unfiledSources = Array.isArray(raw?.unfiledSources)
      ? raw.unfiledSources.map((sid) => String(sid).trim()).filter(Boolean)
      : Array.isArray(raw?.unfiled_sources)
        ? raw.unfiled_sources.map((sid) => String(sid).trim()).filter(Boolean)
        : [];
    return { folders, unfiledSources };
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

  function landSourceRecord(sourceId) {
    return landSources.find((source) => source.id === sourceId) || null;
  }

  function allLandSourceIds() {
    return landSources.map((source) => source.id);
  }

  function syncLandSidebarWithSources() {
    const valid = new Set(allLandSourceIds());
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
    for (const sid of allLandSourceIds()) {
      if (!assigned.has(sid)) unfiledSources.push(sid);
    }
    landSidebar = { folders, unfiledSources };
  }

  function orderedLandSourceIds() {
    syncLandSidebarWithSources();
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
      void persistLandSidebar();
    }, LAND_SIDEBAR_SAVE_MS);
  }

  async function persistLandSidebar({ refreshMap = false } = {}) {
    syncLandSidebarWithSources();
    try {
      const resp = await fetch(landSidebarApiUrl(), {
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
      if (refreshMap) await refreshLandMapLayers();
      return true;
    } catch (_) {
      window.alert("Could not reach server.");
      return false;
    }
  }

  async function maybeMigrateLandSidebarFromLayerOrder() {
    if (!landSidebarMigrationPending) return;
    landSidebarMigrationPending = false;
    await persistLandSidebar();
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
    for (const sid of allLandSourceIds()) {
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
    renderLandPanel();
  }

  function landFolderLayerRefs(folderId) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder) return [];
    const refs = [];
    for (const sourceId of folder.sources) {
      const source = landSourceRecord(sourceId);
      if (!source || !Array.isArray(source.layers)) continue;
      for (const rawLayer of source.layers) {
        const spec = normalizeRegisteredLayer(rawLayer);
        refs.push(landLayerKey(sourceId, spec.key));
      }
    }
    return refs;
  }

  function isLandFolderVisible(folderId) {
    const refs = landFolderLayerRefs(folderId);
    if (!refs.length) return false;
    return refs.every((key) => {
      const slash = key.indexOf("/");
      if (slash < 0) return false;
      const sourceId = key.slice(0, slash);
      const layerKey = key.slice(slash + 1);
      return isLandLayerEffectivelyVisible(sourceId, layerKey);
    });
  }

  function setLandFolderVisible(folderId, visible) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder) return;
    for (const sourceId of folder.sources) {
      setLandSourceBatchVisible(sourceId, visible);
      const source = landSourceRecord(sourceId);
      if (!source || !Array.isArray(source.layers)) continue;
      for (const rawLayer of source.layers) {
        const spec = normalizeRegisteredLayer(rawLayer);
        setLandLayerVisible(sourceId, spec.key, visible);
      }
    }
    renderLandPanel();
  }

  function landLayerRowsRaw() {
    const rows = [];
    for (const sourceId of orderedLandSourceIds()) {
      const source = landSourceRecord(sourceId);
      if (!source) continue;
      const layers = Array.isArray(source.layers) ? source.layers : [];
      for (const rawLayer of layers) {
        const spec = normalizeRegisteredLayer(rawLayer);
        rows.push({
          sourceId: source.id,
          label: source.label || source.id,
          path: source.path,
          layerKey: spec.key,
          spec,
        });
      }
    }
    return rows;
  }

  function landLayerRows() {
    return landLayerRowsRaw();
  }

  function applyLandSidebarMutation(mutator) {
    const next = cloneLandSidebar();
    mutator(next);
    landSidebar = next;
    syncLandSidebarWithSources();
    renderLandPanel();
    syncLandMapLayerOrder();
    schedulePersistLandSidebar();
  }

  function reorderLandFolder(fromFolderId, beforeFolderId) {
    if (!fromFolderId || fromFolderId === beforeFolderId) return;
    applyLandSidebarMutation((sidebar) => {
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
    applyLandSidebarMutation((sidebar) => {
      removeSourceFromSidebar(sidebar, sourceId);
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
    applyLandSidebarMutation((sidebar) => {
      removeSourceFromSidebar(sidebar, fromSourceId);
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

  let landFolderModalMode = "create";
  let landFolderEditId = null;

  function setLandFolderError(message) {
    if (!landFolderError) return;
    if (message) {
      landFolderError.textContent = message;
      landFolderError.hidden = false;
    } else {
      landFolderError.textContent = "";
      landFolderError.hidden = true;
    }
  }

  function closeLandFolderModal() {
    if (!landFolderModal) return;
    landFolderModal.open = false;
    landFolderModalMode = "create";
    landFolderEditId = null;
  }

  function resetLandFolderModal() {
    if (landFolderName) landFolderName.value = "";
    setLandFolderError("");
  }

  async function openCreateLandFolderModal() {
    if (!landFolderModal) return;
    landFolderModalMode = "create";
    landFolderEditId = null;
    landFolderModal.label = "New folder";
    resetLandFolderModal();
    await openWaDialog(landFolderModal);
    requestAnimationFrame(() => {
      landFolderName?.focus();
    });
  }

  async function openRenameLandFolderModal(folderId) {
    const folder = landSidebar.folders.find((item) => item.id === folderId);
    if (!folder || !landFolderModal) return;
    landFolderModalMode = "rename";
    landFolderEditId = folderId;
    landFolderModal.label = "Rename folder";
    setLandFolderError("");
    if (landFolderName) landFolderName.value = folder.label;
    await openWaDialog(landFolderModal);
    requestAnimationFrame(() => {
      landFolderName?.focus();
      landFolderName?.select();
    });
  }

  function saveLandFolderModal() {
    const trimmed = (landFolderName?.value || "").trim();
    if (!trimmed) {
      setLandFolderError("Name is required.");
      landFolderName?.focus();
      return;
    }
    if (landFolderModalMode === "rename") {
      const folder = landSidebar.folders.find((item) => item.id === landFolderEditId);
      if (!folder) {
        closeLandFolderModal();
        return;
      }
      if (trimmed === folder.label) {
        closeLandFolderModal();
        return;
      }
      applyLandSidebarMutation((sidebar) => {
        const target = sidebar.folders.find((item) => item.id === landFolderEditId);
        if (target) target.label = trimmed;
      });
    } else {
      const id = slugifyLandFolderId(trimmed, landSidebar.folders);
      applyLandSidebarMutation((sidebar) => {
        sidebar.folders.push({ id, label: trimmed, sources: [] });
      });
    }
    closeLandFolderModal();
  }

  function createLandFolder() {
    void openCreateLandFolderModal();
  }

  function renameLandFolder(folderId) {
    void openRenameLandFolderModal(folderId);
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
    applyLandSidebarMutation((sidebar) => {
      const idx = sidebar.folders.findIndex((item) => item.id === folderId);
      if (idx < 0) return;
      const [removed] = sidebar.folders.splice(idx, 1);
      sidebar.unfiledSources.push(...removed.sources);
    });
  }

  function syncLandMapLayerOrder() {
    if (!mapReady) return;
    const rows = landLayerRows();
    const anchor = viewshedLayerInsertBefore();
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const row = rows[i];
      const sourceMapId = landMapSourceId(row.sourceId, row.layerKey);
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
    handle.innerHTML = mapToolIcon("grip-vertical", label || "Drag to reorder");
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
        reorderLandFolder(landDragId, targetId);
        return;
      }
      if (landDragKind === "source") {
        if (targetKind === "folder") {
          moveLandSource(landDragId, { folderId: targetId });
          return;
        }
        if (targetKind === "unfiled") {
          moveLandSource(landDragId, {});
          return;
        }
        if (targetKind === "source") {
          reorderLandSource(landDragId, targetId, targetFolderId);
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
    if (next === "land") void maybeMigrateLandSidebarFromLayerOrder();
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
          "text-font": MAP_LABEL_FONT,
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
    const sourceMapId = landMapSourceId(sourceId, layerKey);
    const labelsId = `${sourceMapId}-labels`;
    const layerVisible = isLandLayerEffectivelyVisible(sourceId, layerKey);
    const labelsVisible =
      layerVisible && isLandLayerLabelsVisible(sourceId, layerKey);
    const vis = labelsVisible ? "visible" : "none";
    syncGeoJsonLabelLayer(
      map,
      sourceMapId,
      labelsId,
      landLayerHasLabels(sourceId, layerKey),
      vis,
    );
  }

  function syncLandMapLayerVisibility(sourceId, layer) {
    if (!mapReady) return;
    const sourceMapId = landMapSourceId(sourceId, layer);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const vis = isLandLayerEffectivelyVisible(sourceId, layer) ? "visible" : "none";
    if (map.getLayer(fillId)) map.setLayoutProperty(fillId, "visibility", vis);
    if (map.getLayer(lineId)) map.setLayoutProperty(lineId, "visibility", vis);
  }

  function applyLandMapLayerStyle(sourceId, layerKey) {
    if (!mapReady) return;
    const sourceMapId = landMapSourceId(sourceId, layerKey);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const spec = resolveLandLayerSpec(sourceId, layerKey);
    const styleMap = styleMapFromLayerSpec(spec);
    const fallback = flatStyleFromLayerSpec(spec);
    const lineFallback = landLineColorFromFill(fallback.color);
    if (map.getLayer(fillId)) {
      if (spec?.styleField && styleMap) {
        map.setPaintProperty(
          fillId,
          "fill-color",
          buildStyleMatchExpression(styleMap, "style_key", fallback),
        );
        map.setPaintProperty(
          fillId,
          "fill-opacity",
          buildOpacityMatchExpression(styleMap, "style_key", fallback),
        );
      } else {
        map.setPaintProperty(fillId, "fill-color", fallback.color);
        map.setPaintProperty(fillId, "fill-opacity", fallback.opacity);
      }
      map.setPaintProperty(fillId, "fill-outline-color", lineFallback);
    }
    if (map.getLayer(lineId)) {
      map.setPaintProperty(lineId, "line-color", lineFallback);
      map.setPaintProperty(lineId, "line-width", LAND_LINE_WIDTH);
    }
  }

  async function ensureLandMapLayer(
    sourceId,
    layerKey,
    { force = false } = {},
  ) {
    if (!mapReady) return;
    const sourceMapId = landMapSourceId(sourceId, layerKey);
    const fillId = `${sourceMapId}-fill`;
    const lineId = `${sourceMapId}-line`;
    const spec = resolveLandLayerSpec(sourceId, layerKey);
    const styleMap = styleMapFromLayerSpec(spec);
    const fallback = flatStyleFromLayerSpec(spec);
    const lineColor = landLineColorFromFill(fallback.color);
    const fillPaint =
      spec?.styleField && styleMap
        ? {
            "fill-color": buildStyleMatchExpression(
              styleMap,
              "style_key",
              fallback,
            ),
            "fill-opacity": buildOpacityMatchExpression(
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
      applyLandMapLayerStyle(sourceId, layerKey);
      syncLandMapLayerVisibility(sourceId, layerKey);
      syncLandMapLabelLayer(sourceId, layerKey);
      return;
    }
    if (force && map.getSource(sourceMapId)) {
      removeLandMapLayer(sourceId, layerKey);
    }
    try {
      const geojson = decorateLandGeoJsonProperties(
        await fetchLandLayerGeoJson(sourceId, layerKey, spec?.digest || ""),
        {
          labelField: spec?.labelField || "",
          styleField: spec?.styleField || "",
        },
      );
      map.addSource(sourceMapId, { type: "geojson", data: geojson });
      map.addLayer(
        {
          id: fillId,
          type: "fill",
          source: sourceMapId,
          paint: fillPaint,
          layout: {
            visibility: isLandLayerEffectivelyVisible(sourceId, layerKey)
              ? "visible"
              : "none",
          },
        },
        viewshedLayerInsertBefore(),
      );
      map.addLayer(
        {
          id: lineId,
          type: "line",
          source: sourceMapId,
          paint: {
            "line-color": lineColor,
            "line-width": LAND_LINE_WIDTH,
          },
          layout: {
            visibility: isLandLayerEffectivelyVisible(sourceId, layerKey)
              ? "visible"
              : "none",
          },
        },
        viewshedLayerInsertBefore(),
      );
      syncLandMapLabelLayer(sourceId, layerKey);
      raiseSiteLayers();
    } catch (_) {
      /* network */
    }
  }

  async function refreshLandMapLayer(sourceId, layerKey) {
    if (!mapReady) return;
    if (!isLandLayerEffectivelyVisible(sourceId, layerKey)) return;
    setLandSidebarRowLoading(sourceId, layerKey, true);
    try {
      clearLandLayerGeoJsonCacheForLayer(sourceId, layerKey);
      await ensureLandMapLayer(sourceId, layerKey, { force: true });
    } finally {
      setLandSidebarRowLoading(sourceId, layerKey, false);
    }
  }

  async function reloadClippedLandLayers() {
    const rows = landLayerRows().filter((row) => {
      if (row.spec.role === "aoi") return false;
      return isLandLayerEffectivelyVisible(row.sourceId, row.layerKey);
    });
    await Promise.all(
      rows.map((row) => refreshLandMapLayer(row.sourceId, row.layerKey)),
    );
  }

  function removeLandMapLayer(sourceId, layer) {
    if (!mapReady) return;
    const sourceMapId = landMapSourceId(sourceId, layer);
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
      landLayerRows().map((row) =>
        ensureLandMapLayer(row.sourceId, row.layerKey),
      ),
    );
    syncLandMapLayerOrder();
  }

  function buildLandFilterBadge(sourceId, spec) {
    const rowKey = landLayerKey(sourceId, spec.key);
    const active = isLandLayerVisible(sourceId, spec.key);
    const batchHidden = !isLandSourceBatchVisible(sourceId);
    const style = flatStyleFromLayerSpec(spec);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "entity-panel__land-filter-badge";
    btn.dataset.landKey = rowKey;
    if (active) btn.classList.add("entity-panel__land-filter-badge--active");
    if (batchHidden)
      btn.classList.add("entity-panel__land-filter-badge--batch-hidden");
    if (spec.role === "include")
      btn.classList.add("entity-panel__land-filter-badge--include");
    btn.style.setProperty("--land-filter-color", style.color);
    btn.textContent = landLayerShortLabel(spec);
    btn.title = landLayerDisplayName(spec);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    btn.addEventListener("click", () => {
      const next = !isLandLayerVisible(sourceId, spec.key);
      setLandLayerVisible(sourceId, spec.key, next);
      if (next) setLandSourceBatchVisible(sourceId, true);
      renderLandPanel();
      if (next && isLandSourceBatchVisible(sourceId)) {
        void ensureLandMapLayer(sourceId, spec.key);
      }
    });
    return btn;
  }

  function buildLandSourceFilterBadges(sourceId, layers) {
    const host = document.createElement("div");
    host.className = "entity-panel__land-source-filters";
    for (const rawLayer of layers) {
      const spec = normalizeRegisteredLayer(rawLayer);
      host.appendChild(buildLandFilterBadge(sourceId, spec));
    }
    return host;
  }

  function buildLandSourceEyeBtn(sourceId, layers) {
    const visible = isLandSourceBatchVisible(sourceId);
    return makeEntityPanelActionBtn({
      icon: visible ? "eye" : "eye-slash",
      label: visible ? "Hide all layers on map" : "Show all layers on map",
      active: visible,
      onClick: () => {
        const next = !isLandSourceBatchVisible(sourceId);
        setLandSourceBatchVisible(sourceId, next);
        renderLandPanel();
        if (!next) return;
        for (const rawLayer of layers) {
          const spec = normalizeRegisteredLayer(rawLayer);
          if (isLandLayerVisible(sourceId, spec.key)) {
            void ensureLandMapLayer(sourceId, spec.key);
          }
        }
      },
    });
  }

  function buildLandLayerEyeBtn(sourceId, layerKey) {
    const visible = isLandLayerVisible(sourceId, layerKey);
    return makeEntityPanelActionBtn({
      icon: visible ? "eye" : "eye-slash",
      label: visible ? "Hide layer on map" : "Show layer on map",
      active: visible,
      onClick: () => {
        const next = !isLandLayerVisible(sourceId, layerKey);
        setLandLayerVisible(sourceId, layerKey, next);
        renderLandPanel();
        if (next) void ensureLandMapLayer(sourceId, layerKey);
      },
    });
  }

  function buildLandLayerLabelBtn(sourceId, layerKey) {
    const layerVisible = isLandLayerVisible(sourceId, layerKey);
    const labelsVisible = isLandLayerLabelsVisible(sourceId, layerKey);
    return makeEntityPanelActionBtn({
      icon: "font",
      label: labelsVisible ? "Hide labels" : "Show labels",
      active: labelsVisible,
      disabled: !layerVisible,
      extraClass: "entity-panel__land-label-toggle",
      onClick: () => {
        setLandLayerLabelsVisible(
          sourceId,
          layerKey,
          !isLandLayerLabelsVisible(sourceId, layerKey),
        );
        renderLandPanel();
      },
    });
  }

  function buildLandSourceActionBtns(sourceId) {
    const editBtn = makeEntityPanelActionBtn({
      icon: "pen",
      label: "Edit layers",
      onClick: () => {
        void openEditLandModal(sourceId);
      },
    });
    const deleteBtn = makeEntityPanelActionBtn({
      icon: "trash",
      label: "Remove source",
      danger: true,
      onClick: () => {
        void deleteLandSource(sourceId);
      },
    });
    return [editBtn, deleteBtn];
  }

  function buildLandFolderEyeBtn(folderId) {
    const visible = isLandFolderVisible(folderId);
    return makeEntityPanelActionBtn({
      icon: visible ? "eye" : "eye-slash",
      label: visible
        ? "Hide all layers in folder"
        : "Show all layers in folder",
      active: visible,
      onClick: () => {
        setLandFolderVisible(folderId, !visible);
      },
    });
  }

  function buildLandFolderActionBtns(folderId) {
    const renameBtn = makeEntityPanelActionBtn({
      icon: "pen",
      label: "Rename folder",
      onClick: () => renameLandFolder(folderId),
    });
    const deleteBtn = makeEntityPanelActionBtn({
      icon: "trash",
      label: "Delete folder",
      danger: true,
      onClick: () => deleteLandFolder(folderId),
    });
    return [renameBtn, deleteBtn];
  }

  function buildLandFolderRow(folder) {
    const collapsed = isLandFolderCollapsed(folder.id);
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
    collapseBtn.innerHTML = mapToolIcon(
      collapsed ? "chevron-right" : "chevron-down",
      "Toggle folder",
    );
    collapseBtn.addEventListener("click", () => {
      setLandFolderCollapsed(folder.id, !collapsed);
    });

    header.appendChild(collapseBtn);
    header.appendChild(
      buildLandDragHandle({
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
    controls.appendChild(buildLandFolderEyeBtn(folder.id));
    for (const btn of buildLandFolderActionBtns(folder.id))
      controls.appendChild(btn);
    header.appendChild(controls);
    el.appendChild(header);

    const body = document.createElement("div");
    body.className = "entity-panel__land-folder-body";
    body.hidden = collapsed;
    for (const sourceId of folder.sources) {
      const group = buildLandSourceGroup(sourceId, { folderId: folder.id });
      if (group) body.appendChild(group);
    }
    el.appendChild(body);
    return el;
  }

  function buildLandSourceGroup(sourceId, { folderId = null } = {}) {
    const source = landSourceRecord(sourceId);
    if (!source) return null;
    const displayTitles = landSourceDisplayTitles(landSources);
    const sourceTitle = displayTitles.get(sourceId) || source.label || sourceId;
    const layers = Array.isArray(source.layers) ? source.layers : [];
    const singleLayer = layers.length === 1;
    const singleSpec = singleLayer ? normalizeRegisteredLayer(layers[0]) : null;
    const multiLayer = layers.length > 1;

    const el = document.createElement("div");
    el.className = "entity-panel__land-source-group";

    const header = document.createElement("div");
    header.className =
      "entity-panel__land-source entity-panel__land-source-draggable entity-panel__land-drop-row";
    header.dataset.landDragKind = "source";
    header.dataset.landDragId = sourceId;
    if (folderId) header.dataset.landFolderId = folderId;
    if (singleSpec)
      header.dataset.landKey = landLayerKey(sourceId, singleSpec.key);
    if (multiLayer && !isLandSourceBatchVisible(sourceId)) {
      header.classList.add("entity-panel__row--hidden");
    } else if (singleSpec && !isLandLayerVisible(sourceId, singleSpec.key)) {
      header.classList.add("entity-panel__row--hidden");
    }

    header.appendChild(
      buildLandDragHandle({
        kind: "source",
        id: sourceId,
        label: "Drag to move source",
      }),
    );

    const titleRow = document.createElement("div");
    titleRow.className = "entity-panel__land-source-title-row";
    if (singleSpec) appendLandLayerRoleBadge(titleRow, singleSpec.role);
    const title = document.createElement("div");
    title.className = "entity-panel__land-source-title";
    title.textContent = sourceTitle;
    title.title = sourceTitle;
    titleRow.appendChild(title);
    header.appendChild(titleRow);

    const controls = document.createElement("div");
    controls.className = "entity-panel__land-source-actions";
    if (multiLayer) {
      controls.appendChild(buildLandSourceEyeBtn(sourceId, layers));
    } else if (singleSpec) {
      controls.appendChild(
        buildLandLayerControls(sourceId, singleSpec.key, {
          labelField: singleSpec.labelField,
        }),
      );
    }
    for (const btn of buildLandSourceActionBtns(sourceId))
      controls.appendChild(btn);
    header.appendChild(controls);
    el.appendChild(header);

    if (singleSpec) {
      const metaHost = document.createElement("div");
      metaHost.className = "entity-panel__land-source-meta";
      appendLandLayerMeta(metaHost, singleSpec);
      if (metaHost.childNodes.length) el.appendChild(metaHost);
    } else if (multiLayer) {
      el.appendChild(buildLandSourceFilterBadges(sourceId, layers));
    }
    return el;
  }

  function buildLandUnfiledSection({ showHeader = true } = {}) {
    syncLandSidebarWithSources();
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
      const group = buildLandSourceGroup(sourceId);
      if (group) el.appendChild(group);
    }
    return el;
  }

  function renderLandPanel() {
    if (!entityPanelLandList) return;
    syncLandSidebarWithSources();
    const rows = landLayerRows();
    const sourceCount = orderedLandSourceIds().length;
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
      entityPanelLandList.appendChild(buildLandFolderRow(folder));
    }
    if (hasFolders) {
      const unfiled = buildLandUnfiledSection({ showHeader: true });
      if (unfiled) entityPanelLandList.appendChild(unfiled);
    } else {
      const unfiled = buildLandUnfiledSection({ showHeader: false });
      if (unfiled) entityPanelLandList.appendChild(unfiled);
    }
  }

  function setLandSidebarRowLoading(sourceId, layerKey, loading) {
    if (!entityPanelLandList) return;
    const rowKey = landLayerKey(sourceId, layerKey);
    const row =
      entityPanelLandList.querySelector(
        `.entity-panel__row--land[data-land-key="${rowKey}"]`,
      ) ||
      entityPanelLandList.querySelector(
        `.entity-panel__land-filter-badge[data-land-key="${rowKey}"]`,
      ) ||
      entityPanelLandList.querySelector(`[data-land-key="${rowKey}"]`);
    if (!row) return;
    row.classList.toggle("entity-panel__row--land-loading", !!loading);
    row.classList.toggle("entity-panel__land-filter-badge--loading", !!loading);
    const spinner = row.querySelector(".entity-panel__land-row-spinner");
    if (spinner) spinner.hidden = !loading;
  }

  async function reloadLandSources({ refreshMap = true } = {}) {
    const prevAoiDigest = landAoiDigest;
    try {
      const resp = await fetch(landApiUrl());
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) return;
      landSources = Array.isArray(payload.sources) ? payload.sources : [];
      if (payload.sidebar)
        landSidebar = normalizeLandSidebarInput(payload.sidebar);
      syncLandSidebarWithSources();
      const nextAoiDigest =
        typeof payload.aoiDigest === "string" ? payload.aoiDigest : "none";
      const aoiChanged = nextAoiDigest !== prevAoiDigest;
      landAoiDigest = nextAoiDigest;
      renderLandPanel();
      if (!refreshMap) return;
      if (aoiChanged) {
        clearAllLandLayerGeoJsonCache();
        await reloadClippedLandLayers();
      } else {
        await refreshLandMapLayers();
      }
    } catch (_) {
      /* network */
    }
  }

  async function deleteLandSource(sourceId) {
    if (!window.confirm(`Remove land source "${sourceId}"?`)) return;
    const prevAoiDigest = landAoiDigest;
    try {
      const resp = await fetch(landSourceApiUrl(sourceId), {
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
          const spec = normalizeRegisteredLayer(rawLayer);
          removeLandMapLayer(sourceId, spec.key);
          landVisible.delete(landLayerKey(sourceId, spec.key));
          landSourceBatchVisible.delete(sourceId);
          landLabelsVisible.delete(landLayerKey(sourceId, spec.key));
          clearLandLayerGeoJsonCacheForLayer(sourceId, spec.key);
        }
      }
      await reloadLandSources({ refreshMap: false });
      if (landAoiDigest !== prevAoiDigest) {
        clearAllLandLayerGeoJsonCache();
        await reloadClippedLandLayers();
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
      style: basemapStyle(currentBasemapKey),
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
    setLandPreviewMapLoading(mapEl, false);
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
      layerStyles.set(layerName, normalizeLandLayerStyle(seed));
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
          landColumnSortKey(a.name).localeCompare(landColumnSortKey(b.name)),
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
        const payload = await fetchLandFieldValues(
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
        const excluded = excludedValuesForField(config, config.labelField);
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
            setExcludedValueForField(
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
        populateLabelSelect();
        return;
      }
      if (config.fieldsLoading) return;
      config.fieldsLoading = true;
      labelSelect.disabled = true;
      try {
        const payload = await fetchLandLayerFields(gdbPath, layerName);
        config.fields = Array.isArray(payload.fields) ? payload.fields : [];
        populateLabelSelect();
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
      void loadLabelValues();
      if (onLabelFieldChange) onLabelFieldChange(layerName);
    });

    panel.appendChild(roleTitle);
    panel.appendChild(roleSelect);
    panel.appendChild(labelTitle);
    panel.appendChild(labelSelect);
    panel.appendChild(valuesTitle);
    panel.appendChild(valuesHint);
    panel.appendChild(valuesEl);

    void ensureFields().then(() => {
      if (config.labelField) void loadLabelValues();
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
      ensureLandLayerStyleState(layerStyles, layer.name);
      ensureLandLayerConfig(layerConfigs, layer.name);
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

      const attrsPanel = buildLandLayerAttrPanel(
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

  function landPreviewLayerSpecs(sourceName, fillId, lineId, style) {
    const normalized = normalizeLandLayerStyle(style);
    const lineColor = landLineColorFromFill(normalized.color);
    return [
      {
        id: fillId,
        type: "fill",
        source: sourceName,
        paint: {
          "fill-color": normalized.color,
          "fill-opacity": normalized.opacity,
          "fill-outline-color": lineColor,
        },
      },
      {
        id: lineId,
        type: "line",
        source: sourceName,
        paint: {
          "line-color": lineColor,
          "line-width": LAND_PREVIEW_LINE_WIDTH,
        },
      },
    ];
  }

  function applyLandPreviewLayerStyle(
    previewMap,
    sourceName,
    fillId,
    lineId,
    style,
  ) {
    if (!previewMap) return;
    const normalized = normalizeLandLayerStyle(style);
    const lineColor = landLineColorFromFill(normalized.color);
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
        LAND_PREVIEW_LINE_WIDTH,
      );
    }
  }

  function landPreviewLabelsLayerId(sourceName) {
    return `${sourceName}-labels`;
  }

  function syncLandPreviewLabelLayer(previewMap, sourceName, showLabels) {
    syncGeoJsonLabelLayer(
      previewMap,
      sourceName,
      landPreviewLabelsLayerId(sourceName),
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
          for (const spec of landPreviewLayerSpecs(
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
        applyLandPreviewLayerStyle(
          previewMap,
          sourceName,
          fillId,
          lineId,
          style,
        );
        syncLandPreviewLabelLayer(previewMap, sourceName, !!config?.labelField);
      } catch (_) {
        /* style/source race */
      }
    };
    if (previewMap.isStyleLoaded()) {
      apply();
      return Promise.resolve();
    }
    return whenPreviewMapReady(previewMap).then(apply);
  }

  function removeLandPreviewLayer(previewMap, prefix, layerName) {
    if (!previewMap) return;
    const sourceName = landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const labelsId = landPreviewLabelsLayerId(sourceName);
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
        removeLandPreviewLayer(previewMap, prefix, layerName);
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
    const config = layerConfigs?.get(layerName) || defaultLandLayerConfig();
    const prefix = landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const style = normalizeLandLayerStyle(layerStyles.get(layerName));
    await whenPreviewMapReady(previewMap);
    try {
      const geojson = await fetchLandPreviewGeoJson(
        path,
        layerName,
        landPreviewLayerConfig(config),
      );
      if (previewMap.getSource(sourceName)) {
        previewMap.getSource(sourceName).setData(geojson);
        applyLandPreviewLayerStyle(
          previewMap,
          sourceName,
          fillId,
          lineId,
          style,
        );
        syncLandPreviewLabelLayer(previewMap, sourceName, !!config.labelField);
      } else {
        await applyLandPreviewMapData(
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
    const config = layerConfigs?.get(layerName) || defaultLandLayerConfig();
    const prefix = landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = landPreviewSourceId(prefix, layerName);
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    const style = normalizeLandLayerStyle(layerStyles.get(layerName));
    await whenPreviewMapReady(previewMap);
    const hasSource = !!previewMap.getSource(sourceName);
    if (hasSource && !config.labelField) {
      applyLandPreviewLayerStyle(previewMap, sourceName, fillId, lineId, style);
      syncLandPreviewLabelLayer(previewMap, sourceName, false);
      return;
    }
    if (!hasSource) setLandLayerRowLoading(listEl, layerName, true);
    try {
      const geojson = await fetchLandPreviewGeoJson(
        path,
        layerName,
        landPreviewLayerConfig(config),
      );
      await applyLandPreviewMapData(
        previewMap,
        sourceName,
        fillId,
        lineId,
        geojson,
        style,
        config,
      );
      const layerMeta = layerMetaList.find((layer) => layer.name === layerName);
      if (layerMeta?.bbox) fitPreviewMapToBboxes(previewMap, [layerMeta.bbox]);
    } catch (_) {
      /* skip layer */
    } finally {
      if (!hasSource) setLandLayerRowLoading(listEl, layerName, false);
      resetLandPreviewMapLoading(mapEl);
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
    removeLandPreviewLayer(
      previewMap,
      landPreviewMapPrefix(sourceIdPrefix),
      layerName,
    );
    setLandLayerRowLoading(listEl, layerName, false);
    resetLandPreviewMapLoading(mapEl);
  }

  async function updateLandPreviewLayerStyle(
    previewMap,
    layerName,
    sourceIdPrefix,
    layerStyles,
  ) {
    if (!previewMap || !layerName) return;
    const prefix = landPreviewMapPrefix(sourceIdPrefix);
    const sourceName = landPreviewSourceId(prefix, layerName);
    if (!previewMap.getSource(sourceName)) return;
    const fillId = `${sourceName}-fill`;
    const lineId = `${sourceName}-line`;
    applyLandPreviewLayerStyle(
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
    const prefix = landPreviewMapPrefix(sourceIdPrefix);
    clearLandPreviewMapLayers(previewMap, prefix, allLayerNames, selected);
    await whenPreviewMapReady(previewMap);
    const bboxes = [];
    await Promise.all(
      selected.map(async (layerName) => {
        await showLandPreviewLayer(
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
    fitPreviewMapToBboxes(previewMap, bboxes);
  }

  function resetImportLandModal() {
    setImportLandError("");
    importLandPreviewLayers = [];
    importLandPreviewPath = "";
    importLandPreviewBusy = false;
    importLandLayerStyles = new Map();
    importLandLayerConfigs = new Map();
    if (importLandPreviewMapEl) {
      resetLandPreviewMapLoading(importLandPreviewMapEl);
    }
    if (importLandGdb) importLandGdb.value = "";
    if (importLandLabel) importLandLabel.value = "";
    if (importLandStatus)
      importLandStatus.textContent =
        "Choose a FileGDB under the project data folder.";
    if (importLandPreviewField) importLandPreviewField.hidden = true;
    if (importLandLayerList) importLandLayerList.innerHTML = "";
    if (importLandSave) importLandSave.disabled = true;
    destroyImportLandPreviewMap();
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
      fillImportLandGdbSelect(landDataGdbPaths);
      return;
    }
    importLandGdb.innerHTML = '<option value="">Loading GDB list…</option>';
    importLandGdb.disabled = true;
    try {
      const resp = await fetch(landDataGdbsUrl());
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        fillImportLandGdbSelect([]);
        return;
      }
      landDataGdbPaths = Array.isArray(payload.paths) ? payload.paths : [];
      fillImportLandGdbSelect(landDataGdbPaths);
    } catch (_) {
      fillImportLandGdbSelect([]);
    } finally {
      importLandGdb.disabled = false;
    }
  }

  async function previewImportLandPath(path) {
    if (!path) {
      resetImportLandModal();
      return;
    }
    setImportLandError("");
    importLandPreviewBusy = true;
    if (importLandSave) importLandSave.disabled = true;
    if (importLandStatus) importLandStatus.textContent = "Loading layers…";
    try {
      const resp = await fetch(landImportPreviewApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setImportLandError(payload.error || `Preview failed (${resp.status})`);
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
      importLandPreviewMap = ensureLandPreviewMap(
        importLandPreviewMapEl,
        importLandPreviewMap,
      );
      renderLandLayerChecklist(importLandLayerList, importLandPreviewLayers, {
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
            void showLandPreviewLayer(
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
            hideLandPreviewLayer(
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
            void updateLandPreviewLayerStyle(
              importLandPreviewMap,
              name,
              "import",
              importLandLayerStyles,
            );
          }
        },
        onLabelFieldChange: (name) => {
          if (!selected.has(name)) return;
          void refreshLandPreviewLayerConfig(
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
      await whenPreviewMapReady(importLandPreviewMap);
      requestAnimationFrame(() => importLandPreviewMap?.resize());
      for (const name of selected) {
        await showLandPreviewLayer(
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
      fitPreviewMapToBboxes(
        importLandPreviewMap,
        importLandPreviewLayers.map((layer) => layer.bbox).filter(Boolean),
      );
    } catch (_) {
      setImportLandError("Could not reach server.");
    } finally {
      importLandPreviewBusy = false;
    }
  }

  async function openImportLandModal() {
    if (!importLandModal) return;
    setAddPlacementMode(null);
    setEntityPanelOpen(true);
    setEntityTab("land");
    resetImportLandModal();
    await customElements.whenDefined("wa-dialog");
    importLandModal.open = true;
    void populateImportLandGdbSelect();
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
    const selected = collectSelectedLandLayers(importLandLayerList);
    if (!importLandPreviewPath || !selected.length) {
      setImportLandError("Select a GDB and at least one layer.");
      return;
    }
    setImportLandError("");
    if (importLandSave) importLandSave.disabled = true;
    const prevAoiDigest = landAoiDigest;
    try {
      const body = {
        path: importLandPreviewPath,
        layers: collectSelectedLayerPayloads(
          importLandLayerList,
          importLandLayerStyles,
          importLandLayerConfigs,
        ),
      };
      if (importLandLabel?.value.trim())
        body.label = importLandLabel.value.trim();
      const resp = await fetch(landImportApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setImportLandError(payload.error || `Import failed (${resp.status})`);
        return;
      }
      if (importLandModal) importLandModal.open = false;
      await reloadLandSources({ refreshMap: false });
      const source = payload.source;
      if (source?.id && Array.isArray(source.layers)) {
        for (const rawLayer of source.layers) {
          const spec = normalizeRegisteredLayer(rawLayer);
          setLandLayerVisible(source.id, spec.key, true);
        }
        setLandSourceBatchVisible(source.id, true);
        renderLandPanel();
        if (landAoiDigest !== prevAoiDigest) {
          clearAllLandLayerGeoJsonCache();
          await reloadClippedLandLayers();
        } else {
          await Promise.all(
            source.layers.map((rawLayer) => {
              const spec = normalizeRegisteredLayer(rawLayer);
              return refreshLandMapLayer(source.id, spec.key);
            }),
          );
        }
      } else {
        await refreshLandMapLayers();
      }
    } catch (_) {
      setImportLandError("Could not reach server.");
    } finally {
      if (importLandSave) importLandSave.disabled = false;
    }
  }

  async function openEditLandModal(sourceId) {
    const source = landSources.find((s) => s.id === sourceId);
    if (!source || !editLandModal) return;
    editLandSourceId = sourceId;
    editLandPreviewPath = source.path;
    setEditLandError("");
    if (editLandSourcePath) editLandSourcePath.textContent = source.path;
    if (editLandLabel) editLandLabel.value = source.label || source.id;
    if (editLandSave) editLandSave.disabled = true;
    try {
      const resp = await fetch(landImportPreviewApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: source.path }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const message = payload.error || `Preview failed (${resp.status})`;
        setEditLandError(message);
        window.alert(message);
        return;
      }
      editLandPreviewLayers = Array.isArray(payload.layers)
        ? payload.layers
        : [];
      const layerState = buildEditLandLayerState(source, editLandPreviewLayers);
      editLandLayerStyles = layerState.styles;
      editLandLayerConfigs = layerState.configs;
      const allLayerNames = editLandPreviewLayers.map((layer) => layer.name);
      const selected = initialEditLandSelected(source, editLandPreviewLayers);
      const refreshEditPreview = () => {
        void syncLandPreviewMap(
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
      renderLandLayerChecklist(editLandLayerList, editLandPreviewLayers, {
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
            void showLandPreviewLayer(
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
            hideLandPreviewLayer(
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
            void updateLandPreviewLayerStyle(
              editLandPreviewMap,
              name,
              "edit",
              editLandLayerStyles,
            );
          }
        },
        onLabelFieldChange: (name) => {
          if (!selected.has(name)) return;
          void refreshLandPreviewLayerConfig(
            editLandPreviewMap,
            editLandPreviewPath,
            name,
            "edit",
            editLandLayerStyles,
            editLandLayerConfigs,
          );
        },
      });
      editLandPreviewMap = ensureLandPreviewMap(
        editLandPreviewMapEl,
        editLandPreviewMap,
      );
      if (editLandSave) editLandSave.disabled = selected.size === 0;
      editLandPreviewRefresh = refreshEditPreview;
      if (selected.size) refreshEditPreview();
      await openWaDialog(editLandModal);
    } catch (_) {
      setEditLandError("Could not reach server.");
      window.alert("Could not reach server.");
    }
  }

  async function saveEditLandModal() {
    if (!editLandSourceId) return;
    const selected = collectSelectedLandLayers(editLandLayerList);
    if (!selected.length) {
      setEditLandError("Select at least one layer.");
      return;
    }
    setEditLandError("");
    if (editLandSave) editLandSave.disabled = true;
    const prevAoiDigest = landAoiDigest;
    const prevSource = landSources.find((s) => s.id === editLandSourceId);
    const prevKeys = new Set(
      (prevSource?.layers || []).map(
        (layer) => normalizeRegisteredLayer(layer).key,
      ),
    );
    try {
      const body = {
        layers: collectEditLandSavePayloads(
          editLandLayerList,
          editLandLayerStyles,
          editLandLayerConfigs,
          prevSource,
          editLandPreviewLayers,
        ),
        label: editLandLabel?.value.trim() || editLandSourceId,
      };
      const resp = await fetch(landSourceApiUrl(editLandSourceId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setEditLandError(payload.error || `Save failed (${resp.status})`);
        return;
      }
      if (editLandModal) editLandModal.open = false;
      const updated =
        payload.source || landSources.find((s) => s.id === editLandSourceId);
      const newKeys = new Set(
        (updated?.layers || []).map(
          (layer) => normalizeRegisteredLayer(layer).key,
        ),
      );
      for (const key of prevKeys) {
        if (!newKeys.has(key)) {
          removeLandMapLayer(editLandSourceId, key);
          landVisible.delete(landLayerKey(editLandSourceId, key));
          landLabelsVisible.delete(landLayerKey(editLandSourceId, key));
          clearLandLayerGeoJsonCacheForLayer(editLandSourceId, key);
        }
      }
      for (const key of newKeys) {
        if (!prevKeys.has(key))
          setLandLayerVisible(editLandSourceId, key, true);
      }
      await reloadLandSources({ refreshMap: false });
      if (landAoiDigest !== prevAoiDigest) {
        clearAllLandLayerGeoJsonCache();
        await reloadClippedLandLayers();
      } else {
        await Promise.all(
          [...newKeys].map((key) => refreshLandMapLayer(editLandSourceId, key)),
        );
      }
    } catch (_) {
      setEditLandError("Could not reach server.");
    } finally {
      if (editLandSave) editLandSave.disabled = false;
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
      tagFilterMode,
      filterByViewport: entityPanelFilterByViewport,
      viewshedVisible: Object.fromEntries(viewshedVisible),
      entityPanelOpen,
      entityPanelTab,
      landVisible: Object.fromEntries(landVisible),
      landSourceBatchVisible: Object.fromEntries(landSourceBatchVisible),
      landLabelsVisible: Object.fromEntries(landLabelsVisible),
      landFoldersCollapsed: Object.fromEntries(landFoldersCollapsed),
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
    if (map.getLayer(LINKS_LAYER))
      map.setLayoutProperty(LINKS_LAYER, "visibility", vis);
    if (map.getLayer(LINKS_LABELS_LAYER))
      map.setLayoutProperty(LINKS_LABELS_LAYER, "visibility", vis);
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
    const linePaint = siteLinksLinePaint();
    if (map.getSource(LINKS_SOURCE)) {
      map.getSource(LINKS_SOURCE).setData(labeled);
      if (map.getLayer(LINKS_LAYER)) {
        for (const [key, val] of Object.entries(linePaint)) {
          map.setPaintProperty(LINKS_LAYER, key, val);
        }
      }
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
        paint: linePaint,
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: linkVisibility,
        },
      },
      SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(LINKS_LABELS_LAYER, LINKS_SOURCE, linkVisibility),
      SITES_CIRCLE,
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
  async function loadSingleSiteLinks(slug) {
    if (!slug || singleSiteLinksInflight.has(slug)) return;
    singleSiteLinksInflight.add(slug);
    try {
      const resp = await fetch(siteLinksApiUrl(slug));
      if (!resp.ok) {
        scheduleSingleSiteLinksRetry(slug);
        return;
      }
      const payload = await resp.json();
      mergeSingleSiteLinks(slug, payload);
    } catch (_) {
      scheduleSingleSiteLinksRetry(slug);
    } finally {
      singleSiteLinksInflight.delete(slug);
    }
  }

  function scheduleSingleSiteLinksRetry(slug) {
    if (!slug || siteOutboundLinksReady.has(slug)) return;
    window.setTimeout(() => {
      if (sitePinSpinning(slug) && !siteOutboundLinksReady.has(slug)) {
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
    if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
  }

  function applySiteLinksPayload(payload) {
    if (!payload) return;
    if (payload.partial && payload.site) {
      mergeSingleSiteLinks(payload.site, payload);
      return;
    }
    const prevFeatures = siteLinksPayload?.geojson?.features;
    const prevCount = Array.isArray(prevFeatures) ? prevFeatures.length : 0;
    const nextFeatures = payload.geojson?.features;
    const nextCount = Array.isArray(nextFeatures) ? nextFeatures.length : 0;
    // Keep showing a ready mesh while a re-warm is pending (e.g. rename bumps
    // config mtime but not link geometry). Empty pending payloads used to wipe
    // all RF lines until the slow warm finished.
    if (
      payload.status === "pending" &&
      siteLinksPayload &&
      siteLinksPayload.status === "ready" &&
      siteLinksPayload.geojson &&
      prevCount > 0
    ) {
      return;
    }
    // Viewshed PNG warms can publish before GPKG footprints exist; do not wipe RF lines.
    if (
      payload.status === "ready" &&
      siteLinksPayload?.status === "ready" &&
      prevCount > 0 &&
      nextCount === 0
    ) {
      return;
    }
    siteLinksPayload = payload;
    if (payload.geojson) addSiteLinksLayer(payload.geojson);
    if (payload.status === "ready" && !payload.partial) {
      for (const site of sites) {
        markSiteOutboundLinksReady(site.slug);
      }
    }
    if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
  }

  function viewshedOverlaySlugs() {
    return [
      ...sites.map((site) => site.slug),
      DRAFT_VIEWSHED_SLUG,
      ...editCoordHistory.map((entry) => editHistorySlug(entry.id)),
      ...seekHopCoordViewshedSlugs,
    ];
  }

  function viewshedLayerInsertBefore() {
    return map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined;
  }

  /** Land layer reorder moves fills to the top; keep RF overlays above land, below sites. */
  function raiseViewshedLayers() {
    if (!mapReady) return;
    const beforeId = viewshedLayerInsertBefore();
    if (!beforeId) return;
    for (const slug of viewshedOverlaySlugs()) {
      const layerId = viewshedLayerId(slug);
      if (!map.getLayer(layerId)) continue;
      try {
        map.moveLayer(layerId, beforeId);
      } catch (_) {
        /* layer may be mid-remove */
      }
    }
  }

  function raiseSiteLayers() {
    raiseViewshedLayers();
    for (const id of [
      EDIT_HISTORY_LINKS_LAYER,
      EDIT_HISTORY_LINKS_LABELS_LAYER,
      DRAFT_LINKS_LAYER,
      DRAFT_LINKS_LABELS_LAYER,
      LINKS_LAYER,
      LINKS_LABELS_LAYER,
      SEEK_WEDGE_FILL_LAYER,
      SEEK_WEDGE_OUTLINE_LAYER,
      SEEK_ANCILLARY_LINES_LAYER,
      SEEK_ANCILLARY_LINES_LABELS_LAYER,
      SEEK_LINES_LAYER,
      SEEK_LINES_LABELS_LAYER,
      SEEK_PATH_LAYER,
      SEEK_GOAL_LINE_LAYER,
      SEEK_CANDIDATES_LAYER,
      SEEK_CANDIDATES_LABELS_LAYER,
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

  function basemapRasterOpacityForTerrain() {
    if (usesSkadiAnalysisDem()) return 0;
    return 0.9;
  }

  function showTerrainOverlays() {
    ensureTerrainSource();
    ensureHillshadeLayer();
    map.setTerrain({ source: TERRAIN_SOURCE, exaggeration: 1.35 });
    if (map.getLayer("basemap")) {
      map.setPaintProperty(
        "basemap",
        "raster-opacity",
        basemapRasterOpacityForTerrain(),
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
    if (map.getLayer(BASEMAP_REFERENCE_LAYER))
      map.removeLayer(BASEMAP_REFERENCE_LAYER);
    if (map.getSource(BASEMAP_REFERENCE_SOURCE))
      map.removeSource(BASEMAP_REFERENCE_SOURCE);
  }

  function viewshedSourceId(slug) {
    return `viewshed-${slug}`;
  }

  function viewshedLayerId(slug) {
    return `viewshed-${slug}-raster`;
  }

  function applyViewshedOpacityToAllLayers() {
    if (!mapReady) return;
    for (const slug of viewshedOverlaySlugs()) {
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
    params.set("quality", String(viewshedQuality));
    return params;
  }

  function viewshedPreviewSimQueryParams() {
    const params = new URLSearchParams();
    params.set("radius_km", String(viewshedRadiusKm));
    params.set("quality", String(VIEWSHED_PREVIEW_QUALITY));
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
    const params = preview
      ? viewshedPreviewSimQueryParams()
      : viewshedSimQueryParams();
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
    if (!viewshedAtTarget(vs)) {
      updatePinOverlays();
      return;
    }
    markSiteViewshedReady(vs.slug);
    if (vs.slug === DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
    }
  }

  function handleViewshedReady(vs, epoch) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) {
      if (vs?.slug) clearViewshedLoadingState(vs.slug);
      return;
    }
    const pendingEpoch = viewshedPendingEpoch.get(vs.slug);
    if (epoch != null && pendingEpoch != null && pendingEpoch !== epoch) return;
    if (String(vs.slug).startsWith("_edit_hist_")) {
      renderEditCoordHistory();
    }
    addViewshedLayer(vs);
    finalizeViewshedReady(vs);
    if (!isEphemeralViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug);
    } else {
      markSiteOutboundLinksReady(vs.slug);
    }
  }

  function acceptViewshedOverlay(vs) {
    if (!vs || !vs.slug || !vs.url || !vs.coordinates) return;
    if (isSiteMapHidden(vs.slug) || !isViewshedVisible(vs.slug)) return;
    addViewshedLayer(vs);
    finalizeViewshedReady(vs);
    if (!isEphemeralViewshedSlug(vs.slug)) {
      void loadSingleSiteLinks(vs.slug);
    } else {
      markSiteOutboundLinksReady(vs.slug);
    }
  }

  function routeDraftViewshedToSeekHops(data) {
    if (data.slug !== DRAFT_VIEWSHED_SLUG || data.status !== "ready")
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
      handleViewshedReady({ ...data, slug }, epoch);
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
        updatePinOverlays();
      }
      if (routeDraftViewshedToSeekHops(data)) return;
      const epoch = viewshedPendingEpoch.get(data.slug);
      if (epoch != null) {
        handleViewshedReady(data, epoch);
      }
      if (!siteViewshedReady.has(data.slug)) {
        acceptViewshedOverlay(data);
      }
      return;
    }
    const epoch = viewshedPendingEpoch.get(data.slug);
    if (epoch == null) return;
    if (data.status === "error") {
      viewshedPendingEpoch.delete(data.slug);
      viewshedLoading.delete(data.slug);
      clearSitePinProgress(data.slug);
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
    if (!mapReady) return;
    try {
      const active = new Set();
      for (const site of sites) {
        if (!sitePinSpinning(site.slug)) continue;
        if (!coordsUsableForMarker(site.lon, site.lat)) continue;
        active.add(site.slug);
        renderPinLoadOverlay(site.slug, site.lon, site.lat);
      }
      if (
        viewshedLoading.has(DRAFT_VIEWSHED_SLUG) &&
        draftPlacementLat != null &&
        draftPlacementLon != null
      ) {
        active.add(DRAFT_VIEWSHED_SLUG);
        renderPinLoadOverlay(
          DRAFT_VIEWSHED_SLUG,
          draftPlacementLon,
          draftPlacementLat,
        );
      }
      for (const entry of editCoordHistory) {
        const slug = editHistorySlug(entry.id);
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
          active.add(SEEK_SCAN_PIN);
          const marker = ensurePinLoadMarker(SEEK_SCAN_PIN, "spinner");
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

  async function applyViewshedOverlaysBatched(overlays) {
    for (let i = 0; i < overlays.length; i += VIEWSHED_OVERLAY_BATCH) {
      const batch = overlays.slice(i, i + VIEWSHED_OVERLAY_BATCH);
      for (const overlay of batch) {
        acceptViewshedOverlay(overlay);
      }
      if (i + VIEWSHED_OVERLAY_BATCH < overlays.length) {
        await waitForMapIdle();
      }
    }
  }

  let viewshedLoadEpoch = 0;

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

  function viewshedPrefetchMetaUrl(lat, lon) {
    const params = viewshedPreviewSimQueryParams();
    params.set("lat", String(lat));
    params.set("lon", String(lon));
    return `/api/p/${projectSlug}/viewsheds/prefetch?${params}`;
  }

  function clearViewshedLoadingState(slug) {
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
    clearSitePinProgress(slug);
    if (slug === DRAFT_VIEWSHED_SLUG) {
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
    }
    updatePinOverlays();
    if (slug === selectedSlug) syncViewshedCheckbox();
  }

  async function tryLoadViewshedFromCache(slug, coords) {
    try {
      const resp = await fetch(viewshedMetaUrl(slug, coords || {}));
      if (!resp.ok) return false;
      const overlay = await resp.json();
      if (overlay.url && overlay.coordinates) {
        acceptViewshedOverlay({ ...overlay, slug, status: "ready" });
        return true;
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false;
  }

  function viewshedIndexUrl() {
    const params = viewshedSimQueryParams();
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
    await Promise.all(Array.from({ length: workers }, () => worker()));
  }

  async function loadViewshedIndex() {
    try {
      const resp = await fetch(viewshedIndexUrl());
      if (!resp.ok) {
        ensureViewshedsForNewlyVisibleSites();
        return;
      }
      const index = await resp.json();
      const siteEntries = index.sites || {};
      const readySlugs = [];
      const readyOverlays = [];
      const missing = [];
      for (const site of sites) {
        if (isSiteMapHidden(site.slug) || !isViewshedVisible(site.slug))
          continue;
        const entry = siteEntries[site.slug];
        if (entry && entry.ready && entry.url && entry.coordinates) {
          readyOverlays.push({
            slug: site.slug,
            url: entry.url,
            coordinates: entry.coordinates,
            ...entry,
          });
          readySlugs.push(site.slug);
          if (entry.at_target === false) {
            missing.push(site);
          }
        } else {
          missing.push(site);
        }
      }
      await applyViewshedOverlaysBatched(readyOverlays);
      void fetchOutboundLinksParallel(readySlugs);
      for (const site of missing) {
        scheduleViewshedLoad(site);
      }
      if (missing.length) syncWarmPriorities();
    } catch (_) {
      ensureViewshedsForNewlyVisibleSites();
    }
  }

  function scheduleViewshedLoad(site) {
    removeViewshedLayer(site.slug);
    resetSiteProgress(site.slug);
    viewshedLoading.add(site.slug);
    viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch);
    updatePinOverlays();
    if (site.slug === selectedSlug) syncViewshedCheckbox();
    const priority =
      site.slug === selectedSlug
        ? WARM_PRIORITY_INTERACTIVE
        : WARM_PRIORITY_VIEWPORT;
    void bumpWarmPriorities([site.slug], priority);
    void tryLoadViewshedFromCache(site.slug);
  }

  function reloadViewshedsForSimChange() {
    bumpViewshedLoadEpoch();
    for (const site of sites) {
      resetSiteProgress(site.slug);
      if (!isSiteMapHidden(site.slug) && isViewshedVisible(site.slug)) {
        removeViewshedLayer(site.slug);
        viewshedLoading.add(site.slug);
        viewshedPendingEpoch.set(site.slug, viewshedLoadEpoch);
      } else {
        removeViewshedLayer(site.slug);
        viewshedLoading.delete(site.slug);
        viewshedPendingEpoch.delete(site.slug);
      }
    }
    updatePinOverlays();
    void loadViewshedIndex();
    if (
      createMode &&
      draftPlacementLat != null &&
      draftPlacementLon != null &&
      isViewshedVisible(DRAFT_VIEWSHED_SLUG)
    ) {
      void loadDraftViewshedAt(draftPlacementLat, draftPlacementLon, {
        refreshOnly: true,
      });
    }
    if (seekState?.running) syncSeekHopViewsheds();
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
    const current = readEditCoords();
    if (current) out.push({ lat: current.lat, lon: current.lon });
    for (const entry of editCoordHistory) {
      out.push({ lat: entry.lat, lon: entry.lon });
    }
    if (excludeLat == null || excludeLon == null) return out;
    return out.filter(
      (c) => !coordsMatchPair(c.lat, c.lon, excludeLat, excludeLon),
    );
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
    try {
      const sourceId = viewshedSourceId(DRAFT_VIEWSHED_SLUG);
      const layerId = viewshedLayerId(DRAFT_VIEWSHED_SLUG);
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
    } catch (_) {
      /* map may be mid-resize */
    }
    viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG);
    viewshedLoading.delete(DRAFT_VIEWSHED_SLUG);
    draftViewshedLoading = false;
    draftPlacementLat = null;
    draftPlacementLon = null;
    updatePinOverlays();
  }

  function applySavedSiteToMap(site, fallbackLat, fallbackLon) {
    const row = normalizeSiteFromApi({
      ...site,
      lat: site.lat ?? fallbackLat,
      lon: site.lon ?? fallbackLon,
    });
    if (!row) return false;
    registerSite(row);
    viewshedVisible.set(row.slug, true);
    scheduleViewshedLoad(row);
    void loadSiteLinks();
    selectSite(row.slug);
    return true;
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
      if (payload && (payload.links || payload.links_geojson)) {
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

  async function tryLoadCoordViewshedFromCache(slug, lat, lon) {
    try {
      const resp = await fetch(viewshedPrefetchMetaUrl(lat, lon));
      if (!resp.ok) return false;
      const overlay = await resp.json();
      if (overlay.url && overlay.coordinates) {
        acceptViewshedOverlay({ ...overlay, slug, status: "ready" });
        return true;
      }
    } catch (_) {
      /* cache probe optional */
    }
    return false;
  }

  async function tryLoadDraftViewshedFromCache(lat, lon) {
    return tryLoadCoordViewshedFromCache(DRAFT_VIEWSHED_SLUG, lat, lon);
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
    if (await tryLoadDraftViewshedFromCache(lat, lon)) {
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) === epoch) {
        viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG);
        draftViewshedLoading = false;
        syncCreateViewshedCheckbox();
        syncEditViewshedCheckbox();
      }
      return;
    }
    try {
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
      });
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return;
      if (!resp.ok) {
        clearDraftViewshedLoading();
        return;
      }
      const vs = await resp.json();
      if (viewshedPendingEpoch.get(DRAFT_VIEWSHED_SLUG) !== epoch) return;
      if (vs && vs.status === "ready") {
        handleViewshedReady({ ...vs, slug: DRAFT_VIEWSHED_SLUG }, epoch);
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
      sitePanelViewshedHint.textContent = sitePinSpinning(slug)
        ? sitePinProgressLabel(slug)
        : "";
    }
  }

  /** Mirror viewshedVisible state into every checkbox bound to this site slug. */
  function syncViewshedUiForSlug(slug) {
    if (!slug) return;
    syncEntityPanelViewshedToggle(slug);
    syncSitePanelViewshedToggle(slug);
    if (editMode && editSlug === slug) syncEditViewshedCheckbox();
    updatePinOverlays();
  }

  function setViewshedVisible(slug, visible) {
    viewshedVisible.set(slug, visible);
    if (map.getLayer(viewshedLayerId(slug))) {
      applyViewshedVisibilityForSite(slug);
    } else if (visible) {
      ensureViewshedLoadedForSlug(slug);
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
          viewshedLayerInsertBefore(),
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
        viewshedLayerInsertBefore(),
      );
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
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
      syncEditViewshedCheckbox();
      if (draftPlacementLat != null && draftPlacementLon != null) {
        void loadPlacementPrefetchAt(draftPlacementLat, draftPlacementLon);
      }
    }
    raiseSiteLayers();
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

  function setAddSiteError(message) {
    if (!addSiteError) return;
    if (message) {
      addSiteError.textContent = message;
      addSiteError.hidden = false;
    } else {
      addSiteError.textContent = "";
      addSiteError.hidden = true;
    }
  }

  function renderAddSiteTags() {
    if (!addSiteTagsEl) return;
    addSiteTagsEl.innerHTML = "";
    const known = allProjectTags();
    const selected = new Set(addSiteDraftTags);
    const shown = new Set([...known, ...addSiteDraftTags]);
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
          addSiteDraftTags = addSiteDraftTags.filter((t) => t !== tag);
        } else {
          addSiteDraftTags = [...addSiteDraftTags, tag];
        }
        renderAddSiteTags();
        syncAddSiteTagSuggestions();
      });
      addSiteTagsEl.appendChild(chip);
    }
  }

  function syncAddSiteTagSuggestions() {
    if (!addSiteTagSuggestions) return;
    addSiteTagSuggestions.innerHTML = "";
    const selected = new Set(addSiteDraftTags);
    for (const tag of allProjectTags()) {
      if (selected.has(tag)) continue;
      const opt = document.createElement("option");
      opt.value = tag;
      addSiteTagSuggestions.appendChild(opt);
    }
  }

  function resetAddSiteModal() {
    addSiteDraftTags = [];
    if (addSiteName) addSiteName.value = "";
    if (addSiteCoords) addSiteCoords.value = "";
    if (addSiteTagInput) addSiteTagInput.value = "";
    setAddSiteError("");
    renderAddSiteTags();
    syncAddSiteTagSuggestions();
  }

  async function openAddSiteModal() {
    if (!addSiteModal) return;
    setAddPlacementMode(null);
    setEntityPanelOpen(true);
    resetAddSiteModal();
    await openWaDialog(addSiteModal);
    requestAnimationFrame(() => {
      addSiteName?.focus();
    });
  }

  function closeAddSiteModal() {
    if (!addSiteModal) return;
    addSiteModal.open = false;
  }

  function addDraftTagFromInput() {
    if (!addSiteTagInput) return;
    const tag = normalizeTagInput(addSiteTagInput.value);
    addSiteTagInput.value = "";
    if (!tag) return;
    if (!addSiteDraftTags.includes(tag)) {
      addSiteDraftTags = [...addSiteDraftTags, tag];
      renderAddSiteTags();
      syncAddSiteTagSuggestions();
    }
  }

  async function saveAddSiteModal() {
    const name = (addSiteName?.value || "").trim();
    if (!name) {
      setAddSiteError("Name is required.");
      addSiteName?.focus();
      return;
    }
    const pair = parseCoordPairFromText(addSiteCoords?.value || "");
    if (!pair) {
      setAddSiteError(
        "Coordinates required — paste lat, lng like 40.65495, -119.35161.",
      );
      addSiteCoords?.focus();
      return;
    }
    if (pair.lat < -90 || pair.lat > 90 || pair.lon < -180 || pair.lon > 180) {
      setAddSiteError("Coordinates out of range.");
      addSiteCoords?.focus();
      return;
    }
    setAddSiteError("");
    if (addSiteSave) addSiteSave.disabled = true;
    try {
      const body = {
        name,
        lat: pair.lat,
        lon: pair.lon,
      };
      if (addSiteDraftTags.length) body.tags = [...addSiteDraftTags];
      const resp = await fetch(sitesApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setAddSiteError(payload.error || `Save failed (${resp.status})`);
        return;
      }
      const site = payload.site;
      if (!site || !site.slug) {
        setAddSiteError("Unexpected server response.");
        return;
      }
      closeAddSiteModal();
      applySavedSiteToMap(site, pair.lat, pair.lon);
      if (mapReady) {
        const row = siteBySlug.get(site.slug) || site;
        map.flyTo({
          center: [row.lon, row.lat],
          zoom: Math.max(map.getZoom(), 11),
        });
      }
    } catch (_) {
      setAddSiteError("Could not reach server.");
    } finally {
      if (addSiteSave) addSiteSave.disabled = false;
    }
  }

  function setImportSitesError(message) {
    if (!importSitesError) return;
    if (message) {
      importSitesError.textContent = message;
      importSitesError.hidden = false;
    } else {
      importSitesError.textContent = "";
      importSitesError.hidden = true;
    }
  }

  function haversineMeters(lat1, lon1, lat2, lon2) {
    const earthRadiusM = 6371000;
    const toRad = (deg) => (deg * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * earthRadiusM * Math.asin(Math.sqrt(a));
  }

  function bearingDeg(lat1, lon1, lat2, lon2) {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const toDeg = (rad) => (rad * 180) / Math.PI;
    const phi1 = toRad(lat1);
    const phi2 = toRad(lat2);
    const dLon = toRad(lon2 - lon1);
    const y = Math.sin(dLon) * Math.cos(phi2);
    const x =
      Math.cos(phi1) * Math.sin(phi2) -
      Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon);
    return (toDeg(Math.atan2(y, x)) + 360) % 360;
  }

  function seekHopRadiusM() {
    const km = Number(simDefaults.radius_km) || 50;
    return km * 1000;
  }

  function seekWedgeHalfAngleDeg(hopM, hopRadiusM) {
    if (hopRadiusM <= 0) return SEEK_WEDGE_FAR_DEG;
    const t = Math.min(1, Math.max(0, hopM / hopRadiusM));
    return SEEK_WEDGE_NEAR_DEG + t * (SEEK_WEDGE_FAR_DEG - SEEK_WEDGE_NEAR_DEG);
  }

  function destinationPointLatLon(lat, lon, bearingDegVal, distanceM) {
    const r = 6371000;
    const brng = (bearingDegVal * Math.PI) / 180;
    const lat1 = (lat * Math.PI) / 180;
    const lon1 = (lon * Math.PI) / 180;
    const ang = distanceM / r;
    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(ang) +
        Math.cos(lat1) * Math.sin(ang) * Math.cos(brng),
    );
    const lon2 =
      lon1 +
      Math.atan2(
        Math.sin(brng) * Math.sin(ang) * Math.cos(lat1),
        Math.cos(ang) - Math.sin(lat1) * Math.sin(lat2),
      );
    return [(lat2 * 180) / Math.PI, (lon2 * 180) / Math.PI];
  }

  function buildSeekWedgeFeature(from, goal, hopRadiusM) {
    const goalBearing = bearingDeg(from.lat, from.lon, goal.lat, goal.lon);
    const steps = 36;
    const ring = [[from.lon, from.lat]];
    for (let i = 0; i <= steps; i += 1) {
      const t = i / steps;
      const d = t * hopRadiusM;
      const half = seekWedgeHalfAngleDeg(d, hopRadiusM);
      const [lat, lon] = destinationPointLatLon(
        from.lat,
        from.lon,
        goalBearing - half,
        d,
      );
      ring.push([lon, lat]);
    }
    for (let i = steps; i >= 0; i -= 1) {
      const t = i / steps;
      const d = t * hopRadiusM;
      const half = seekWedgeHalfAngleDeg(d, hopRadiusM);
      const [lat, lon] = destinationPointLatLon(
        from.lat,
        from.lon,
        goalBearing + half,
        d,
      );
      ring.push([lon, lat]);
    }
    ring.push([from.lon, from.lat]);
    return {
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [ring] },
      properties: { kind: "seek-wedge" },
    };
  }

  function buildSeekGoalLineFeature(from, goal) {
    const distanceKm =
      haversineMeters(from.lat, from.lon, goal.lat, goal.lon) / 1000;
    return {
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: [
          [from.lon, from.lat],
          [goal.lon, goal.lat],
        ],
      },
      properties: {
        distance_km: Math.round(distanceKm * 10) / 10,
        bearing_deg: Math.round(bearingDeg(from.lat, from.lon, goal.lat, goal.lon)),
        kind: "goal",
      },
    };
  }

  function removeSeekGoalLineLayer() {
    if (map.getLayer(SEEK_GOAL_LINE_LAYER)) map.removeLayer(SEEK_GOAL_LINE_LAYER);
    if (map.getSource(SEEK_GOAL_LINE_SOURCE)) map.removeSource(SEEK_GOAL_LINE_SOURCE);
  }

  function removeSeekWedgeLayers() {
    if (map.getLayer(SEEK_WEDGE_OUTLINE_LAYER)) {
      map.removeLayer(SEEK_WEDGE_OUTLINE_LAYER);
    }
    if (map.getLayer(SEEK_WEDGE_FILL_LAYER)) map.removeLayer(SEEK_WEDGE_FILL_LAYER);
    if (map.getSource(SEEK_WEDGE_SOURCE)) map.removeSource(SEEK_WEDGE_SOURCE);
  }

  function syncSeekWedge() {
    if (!mapReady || !seekSessionActive()) {
      removeSeekWedgeLayers();
      return;
    }
    const from = seekCurrentFrom();
    const goal = seekGoalCoords();
    if (!from || !goal) {
      removeSeekWedgeLayers();
      return;
    }
    const data = {
      type: "FeatureCollection",
      features: [buildSeekWedgeFeature(from, goal, seekHopRadiusM())],
    };
    if (map.getSource(SEEK_WEDGE_SOURCE)) {
      map.getSource(SEEK_WEDGE_SOURCE).setData(data);
      return;
    }
    map.addSource(SEEK_WEDGE_SOURCE, { type: "geojson", data });
    map.addLayer(
      {
        id: SEEK_WEDGE_FILL_LAYER,
        type: "fill",
        source: SEEK_WEDGE_SOURCE,
        paint: {
          "fill-color": "#22c55e",
          "fill-opacity": 0.1,
        },
      },
      SITES_CIRCLE,
    );
    map.addLayer(
      {
        id: SEEK_WEDGE_OUTLINE_LAYER,
        type: "line",
        source: SEEK_WEDGE_SOURCE,
        paint: {
          "line-color": "#22c55e",
          "line-width": 1.5,
          "line-opacity": 0.45,
          "line-dasharray": [2, 2],
        },
        layout: { "line-cap": "round", "line-join": "round" },
      },
      SITES_CIRCLE,
    );
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
    if (map.getSource(SEEK_GOAL_LINE_SOURCE)) {
      map.getSource(SEEK_GOAL_LINE_SOURCE).setData(data);
    } else {
      map.addSource(SEEK_GOAL_LINE_SOURCE, { type: "geojson", data });
      map.addLayer(
        {
          id: SEEK_GOAL_LINE_LAYER,
          type: "line",
          source: SEEK_GOAL_LINE_SOURCE,
          paint: {
            "line-color": "#22c55e",
            "line-width": 2,
            "line-opacity": 0.75,
            "line-dasharray": [4, 3],
          },
          layout: { "line-cap": "round", "line-join": "round" },
        },
        SITES_CIRCLE,
      );
    }
    syncSeekWedge();
    raiseSiteLayers();
  }

  function annotateImportPoints(points) {
    return points.map((point) => {
      let nearest = null;
      let nearestDist = Infinity;
      for (const site of sites) {
        const dist = haversineMeters(point.lat, point.lon, site.lat, site.lon);
        if (dist < nearestDist) {
          nearestDist = dist;
          nearest = site;
        }
      }
      const duplicate = nearest !== null && nearestDist <= IMPORT_DEDUPE_METERS;
      return {
        name: point.name,
        lat: point.lat,
        lon: point.lon,
        ignored: duplicate,
        duplicate,
        duplicateDistM: duplicate ? Math.round(nearestDist) : null,
        duplicateSlug: duplicate ? nearest.slug : null,
        duplicateName: duplicate ? nearest.name : null,
      };
    });
  }

  function importablePreviewPoints() {
    return importPreviewPoints.filter((point) => !point.ignored);
  }

  function pendingImportTagInput() {
    if (!importSitesTagInput) return "";
    return normalizeTagInput(importSitesTagInput.value);
  }

  function effectiveImportDraftTags() {
    const tags = [...importDraftTags];
    const pending = pendingImportTagInput();
    if (pending && !tags.includes(pending)) tags.push(pending);
    return tags;
  }

  function refreshImportPreviewMapData() {
    if (!importPreviewMap || !importPreviewPoints.length) return;
    const source = importPreviewMap.getSource(IMPORT_PREVIEW_SOURCE);
    if (source) source.setData(importPreviewGeoJson(importPreviewPoints));
  }

  function setImportPointIgnored(index, ignored) {
    const point = importPreviewPoints[index];
    if (!point) return;
    importPreviewPoints[index] = { ...point, ignored: !!ignored };
    refreshImportPreviewMapData();
    renderImportPointList();
    syncImportSaveButton();
  }

  function setAllImportPointsIgnored(ignored) {
    if (!importPreviewPoints.length) return;
    importPreviewPoints = importPreviewPoints.map((point) => ({
      ...point,
      ignored: !!ignored,
    }));
    refreshImportPreviewMapData();
    renderImportPointList();
    syncImportSaveButton();
  }

  function syncImportBulkActions() {
    const enabled = importPreviewPoints.length > 0 && !importPreviewBusy;
    if (importSitesSelectAll) importSitesSelectAll.disabled = !enabled;
    if (importSitesClearAll) importSitesClearAll.disabled = !enabled;
  }

  function syncImportSaveButton() {
    if (!importSitesSave) return;
    const ready =
      importablePreviewPoints().length > 0 &&
      effectiveImportDraftTags().length > 0 &&
      !!importPreviewPayload &&
      !importPreviewBusy;
    importSitesSave.disabled = !ready;
  }

  function renderImportSiteTags() {
    if (!importSitesTagsEl) return;
    importSitesTagsEl.innerHTML = "";
    const known = allProjectTags();
    const selected = new Set(importDraftTags);
    const shown = new Set([...known, ...importDraftTags]);
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
          importDraftTags = importDraftTags.filter((t) => t !== tag);
        } else {
          importDraftTags = [...importDraftTags, tag];
        }
        renderImportSiteTags();
        syncImportSiteTagSuggestions();
        syncImportSaveButton();
      });
      importSitesTagsEl.appendChild(chip);
    }
  }

  function syncImportSiteTagSuggestions() {
    if (!importSitesTagSuggestions) return;
    importSitesTagSuggestions.innerHTML = "";
    const selected = new Set(importDraftTags);
    for (const tag of allProjectTags()) {
      if (selected.has(tag)) continue;
      const opt = document.createElement("option");
      opt.value = tag;
      importSitesTagSuggestions.appendChild(opt);
    }
  }

  function destroyImportPreviewMap() {
    if (importPreviewMap) {
      importPreviewMap.remove();
      importPreviewMap = null;
    }
  }

  function importPointVisibleInMap(point) {
    if (!importPreviewMap || !point) return true;
    return coordVisibleInMapViewport(importPreviewMap, point.lon, point.lat);
  }

  function syncImportListCount(shown, total) {
    if (!importSitesListCount) return;
    if (!total) {
      importSitesListCount.textContent = "";
      return;
    }
    const toImport = importPreviewPoints.filter(
      (point) => !point.ignored,
    ).length;
    let text = "";
    if (importFilterByViewport && shown < total) {
      text = `${shown} of ${total} visible`;
    } else {
      text = `${total} point${total === 1 ? "" : "s"}`;
    }
    if (toImport < total) {
      text += ` · ${toImport} to import`;
    }
    importSitesListCount.textContent = text;
  }

  function syncImportPreviewStatus() {
    if (
      !importSitesStatus ||
      !importPreviewFileName ||
      !importPreviewPoints.length
    )
      return;
    const skippedNote =
      importPreviewSkipped > 0
        ? ` (${importPreviewSkipped} placemark(s) skipped)`
        : "";
    let text = `${importPreviewPoints.length} point(s) ready from ${importPreviewFileName}${skippedNote}`;
    const dupes = importPreviewPoints.filter(
      (point) => point.duplicate && point.ignored,
    ).length;
    if (dupes > 0) {
      text += ` · ${dupes} near existing site(s), unchecked`;
    }
    importSitesStatus.textContent = text;
  }

  function renderImportPointList() {
    if (!importSitesPointList) return;
    const listScrollTop = importSitesPointList.scrollTop;
    importSitesPointList.innerHTML = "";
    const total = importPreviewPoints.length;
    if (!total) {
      syncImportListCount(0, 0);
      syncImportBulkActions();
      return;
    }

    let shown = 0;
    for (let index = 0; index < importPreviewPoints.length; index++) {
      const point = importPreviewPoints[index];
      if (
        importFilterByViewport &&
        importPreviewMap &&
        !importPointVisibleInMap(point)
      ) {
        continue;
      }
      shown += 1;
      const row = document.createElement("div");
      row.className = "import-sites-point-row";
      row.setAttribute("role", "listitem");
      if (index === importSelectedPointIndex) {
        row.classList.add("import-sites-point-row--selected");
      }
      if (point.ignored) {
        row.classList.add("import-sites-point-row--ignored");
      }
      if (point.duplicate) {
        row.classList.add("import-sites-point-row--duplicate");
      }

      const main = document.createElement("button");
      main.type = "button";
      main.className = "import-sites-point-row__main";
      const name = document.createElement("span");
      name.className = "import-sites-point-row__name";
      name.textContent = point.name || `Point ${index + 1}`;
      const meta = document.createElement("span");
      meta.className = "import-sites-point-row__meta";
      let metaText = `${formatCoord(point.lat)}, ${formatCoord(point.lon)}`;
      if (point.duplicate && point.duplicateName) {
        metaText += ` · near ${point.duplicateName} (${point.duplicateDistM} m)`;
      }
      meta.textContent = metaText;
      main.appendChild(name);
      main.appendChild(meta);
      main.addEventListener("click", () => {
        focusImportPreviewPoint(index);
      });

      const importLabel = document.createElement("label");
      importLabel.className = "import-sites-point-row__import pf-check";
      const importCheck = document.createElement("input");
      importCheck.type = "checkbox";
      importCheck.checked = !point.ignored;
      importCheck.setAttribute(
        "aria-label",
        `Import ${point.name || `point ${index + 1}`}`,
      );
      importCheck.addEventListener("click", (ev) => {
        ev.stopPropagation();
      });
      importCheck.addEventListener("change", () => {
        setImportPointIgnored(index, !importCheck.checked);
      });
      importLabel.appendChild(importCheck);

      row.appendChild(main);
      row.appendChild(importLabel);
      importSitesPointList.appendChild(row);
    }

    if (!shown) {
      const empty = document.createElement("p");
      empty.className = "import-sites-point-list__empty";
      empty.textContent = importFilterByViewport
        ? "No points in the current map view — pan or zoom out."
        : "No points to show.";
      importSitesPointList.appendChild(empty);
    }
    syncImportListCount(shown, total);
    importSitesPointList.scrollTop = listScrollTop;
    syncImportPreviewStatus();
    syncImportBulkActions();
  }

  function focusImportPreviewPoint(index) {
    const point = importPreviewPoints[index];
    if (!point || !importPreviewMap) return;
    importSelectedPointIndex = index;
    importPreviewMap.flyTo({
      center: [point.lon, point.lat],
      zoom: Math.max(importPreviewMap.getZoom(), 12),
      duration: 400,
    });
    renderImportPointList();
  }

  function onImportPreviewMapMoveEnd() {
    if (importFilterByViewport) renderImportPointList();
  }

  function ensureImportPreviewMapHandlers() {
    if (!importPreviewMap) return;
    importPreviewMap.off("moveend", onImportPreviewMapMoveEnd);
    importPreviewMap.on("moveend", onImportPreviewMapMoveEnd);
  }

  function importPreviewGeoJson(points) {
    return {
      type: "FeatureCollection",
      features: points.map((point, index) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [point.lon, point.lat] },
        properties: {
          name: point.name || `Point ${index + 1}`,
          ignored: !!point.ignored,
          index,
        },
      })),
    };
  }

  function fitImportPreviewBounds(points) {
    if (!importPreviewMap || !points.length) return;
    if (points.length === 1) {
      const point = points[0];
      importPreviewMap.jumpTo({
        center: [point.lon, point.lat],
        zoom: 11,
      });
      return;
    }
    const bounds = new maplibregl.LngLatBounds();
    for (const point of points) bounds.extend([point.lon, point.lat]);
    importPreviewMap.fitBounds(bounds, {
      padding: 36,
      maxZoom: 12,
      duration: 0,
    });
  }

  function renderImportPreview(points) {
    importPreviewPoints = annotateImportPoints(
      Array.isArray(points) ? points : [],
    );
    importSelectedPointIndex = -1;
    if (!importPreviewPoints.length) {
      if (importSitesPreviewField) importSitesPreviewField.hidden = true;
      destroyImportPreviewMap();
      renderImportPointList();
      syncImportSaveButton();
      return;
    }
    if (importSitesPreviewField) importSitesPreviewField.hidden = false;
    if (!importSitesPreviewMapEl) return;

    const geojson = importPreviewGeoJson(importPreviewPoints);
    const applyData = () => {
      if (!importPreviewMap) return;
      const source = importPreviewMap.getSource(IMPORT_PREVIEW_SOURCE);
      if (source) {
        source.setData(geojson);
      } else {
        importPreviewMap.addSource(IMPORT_PREVIEW_SOURCE, {
          type: "geojson",
          data: geojson,
        });
      }
      if (!importPreviewMap.getLayer(IMPORT_PREVIEW_LAYER)) {
        importPreviewMap.addLayer({
          id: IMPORT_PREVIEW_LAYER,
          type: "circle",
          source: IMPORT_PREVIEW_SOURCE,
          paint: IMPORT_PREVIEW_CIRCLE_PAINT,
        });
      }
      if (!importPreviewMap.getLayer(IMPORT_PREVIEW_LABEL_LAYER)) {
        importPreviewMap.addLayer({
          id: IMPORT_PREVIEW_LABEL_LAYER,
          type: "symbol",
          source: IMPORT_PREVIEW_SOURCE,
          layout: {
            "text-field": ["get", "name"],
            "text-size": 11,
            "text-offset": [0, -1.4],
            "text-anchor": "bottom",
            "text-font": MAP_LABEL_FONT,
            "text-allow-overlap": true,
          },
          paint: IMPORT_PREVIEW_LABEL_PAINT,
        });
      }
      importPreviewMap.resize();
      fitImportPreviewBounds(importPreviewPoints);
      ensureImportPreviewMapHandlers();
      renderImportPointList();
    };

    if (!importPreviewMap) {
      importPreviewMap = new maplibregl.Map({
        container: importSitesPreviewMapEl,
        style: basemapStyle(currentBasemapKey),
        attributionControl: false,
        dragRotate: false,
        pitchWithRotate: false,
        interactive: true,
      });
      importPreviewMap.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        "top-right",
      );
      if (importPreviewMap.loaded()) {
        applyData();
      } else {
        importPreviewMap.once("load", applyData);
      }
    } else {
      applyData();
    }
    syncImportSaveButton();
  }

  function resetImportSitesModal() {
    importDraftTags = ["imported"];
    importPreviewPoints = [];
    importPreviewPayload = null;
    importPreviewFileName = "";
    importPreviewSkipped = 0;
    importPreviewBusy = false;
    importFilterByViewport = true;
    importSelectedPointIndex = -1;
    if (importSitesFile) importSitesFile.value = "";
    if (importSitesTagInput) importSitesTagInput.value = "";
    if (importSitesFilterVisible) importSitesFilterVisible.checked = true;
    if (importSitesStatus) {
      importSitesStatus.textContent = "Choose a file with Point placemarks.";
    }
    if (importSitesPreviewField) importSitesPreviewField.hidden = true;
    destroyImportPreviewMap();
    renderImportPointList();
    setImportSitesError("");
    renderImportSiteTags();
    syncImportSiteTagSuggestions();
    syncImportSaveButton();
  }

  async function previewImportFile(file) {
    if (!file) return;
    const name = String(file.name || "").toLowerCase();
    const isKmz = name.endsWith(".kmz");
    const isKml = name.endsWith(".kml");
    if (!isKml && !isKmz) {
      setImportSitesError("Choose a .kml or .kmz file.");
      renderImportPreview([]);
      return;
    }

    importPreviewBusy = true;
    setImportSitesError("");
    if (importSitesStatus)
      importSitesStatus.textContent = `Parsing ${file.name}…`;
    syncImportSaveButton();
    syncImportBulkActions();

    try {
      let body;
      if (isKmz) {
        const buffer = await file.arrayBuffer();
        body = { kmz_b64: arrayBufferToBase64(buffer) };
      } else {
        body = { kml: await file.text() };
      }
      const resp = await fetch(sitesImportPreviewApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setImportSitesError(payload.error || `Preview failed (${resp.status})`);
        importPreviewPayload = null;
        renderImportPreview([]);
        if (importSitesStatus)
          importSitesStatus.textContent = "No points found.";
        return;
      }
      importPreviewPayload = body;
      const points = Array.isArray(payload.points) ? payload.points : [];
      importPreviewFileName = file.name;
      importPreviewSkipped = Number(payload.skipped) || 0;
      renderImportPreview(points);
      syncImportPreviewStatus();
    } catch (_) {
      setImportSitesError("Could not reach server.");
      importPreviewPayload = null;
      renderImportPreview([]);
      if (importSitesStatus) importSitesStatus.textContent = "Preview failed.";
    } finally {
      importPreviewBusy = false;
      syncImportSaveButton();
      syncImportBulkActions();
    }
  }

  async function openImportSitesModal() {
    if (!importSitesModal) return;
    setAddPlacementMode(null);
    setEntityPanelOpen(true);
    resetImportSitesModal();
    await customElements.whenDefined("wa-dialog");
    importSitesModal.open = true;
    requestAnimationFrame(() => {
      importSitesFile?.focus();
    });
  }

  function closeImportSitesModal() {
    if (!importSitesModal) return;
    importSitesModal.open = false;
  }

  function addImportDraftTagFromInput() {
    if (!importSitesTagInput) return;
    const tag = normalizeTagInput(importSitesTagInput.value);
    importSitesTagInput.value = "";
    if (!tag) return;
    if (!importDraftTags.includes(tag)) {
      importDraftTags = [...importDraftTags, tag];
      renderImportSiteTags();
      syncImportSiteTagSuggestions();
      syncImportSaveButton();
    }
  }

  async function saveImportSitesModal() {
    addImportDraftTagFromInput();
    const pointsToImport = importablePreviewPoints();
    const tags = effectiveImportDraftTags();
    if (!tags.length || !pointsToImport.length) {
      setImportSitesError(
        "Choose at least one point to import and at least one tag.",
      );
      return;
    }
    setImportSitesError("");
    if (importSitesSave) importSitesSave.disabled = true;
    try {
      const body = {
        tags,
        points: pointsToImport.map((point) => {
          const row = {
            name: point.name,
            lat: point.lat,
            lon: point.lon,
          };
          return row;
        }),
      };
      const resp = await fetch(sitesImportApiUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setImportSitesError(payload.error || `Import failed (${resp.status})`);
        return;
      }
      const imported = Array.isArray(payload.sites) ? payload.sites : [];
      if (!imported.length) {
        setImportSitesError("Import returned no sites.");
        return;
      }
      const importedTags = [...tags];
      closeImportSitesModal();
      for (const site of imported) {
        registerSite(site);
      }
      const firstTag = importedTags[0];
      if (firstTag) {
        activeTagFilters.clear();
        activeTagFilters.add(firstTag);
        pruneActiveTagFilters();
        renderEntityPanel();
      }
      if (mapReady && imported.length) {
        const bounds = new maplibregl.LngLatBounds();
        for (const site of imported) bounds.extend([site.lon, site.lat]);
        if (imported.length === 1) {
          const site = imported[0];
          map.flyTo({
            center: [site.lon, site.lat],
            zoom: Math.max(map.getZoom(), 11),
          });
        } else {
          map.fitBounds(bounds, { padding: 80, maxZoom: 12, duration: 800 });
        }
      }
      scheduleSaveMapState();
      if (imported[0]?.slug) selectSite(imported[0].slug);
    } catch (_) {
      setImportSitesError("Could not reach server.");
    } finally {
      syncImportSaveButton();
    }
  }

  function syncTagFilterBypassForSite(slug) {
    tagFilterBypassSlugs.delete(slug);
  }

  function applySiteTagChange(slug) {
    if (!siteBySlug.get(slug)) {
      tagFilterBypassSlugs.delete(slug);
      return;
    }
    syncTagFilterBypassForSite(slug);
    applyEntityVisibility();
    renderTagFilters();
  }

  const siteTagSaveQueue = new Map();

  async function patchSiteTags(slug, tags, { immediate = false } = {}) {
    let state = siteTagSaveQueue.get(slug);
    if (!state) {
      state = { pendingTags: null, timer: null, inflight: false, waiters: [] };
      siteTagSaveQueue.set(slug, state);
    }
    state.pendingTags = tags;
    const existing = siteBySlug.get(slug);
    if (existing) {
      applySiteRowUpdate(
        { ...existing, tags: [...tags] },
        { refreshGeoJson: false },
      );
    }
    if (state.timer) clearTimeout(state.timer);
    state.timer = null;
    const resultPromise = new Promise((resolve, reject) => {
      state.waiters.push({ resolve, reject });
    });
    if (immediate) {
      void flushSiteTagSave(slug);
    } else {
      state.timer = setTimeout(() => {
        state.timer = null;
        void flushSiteTagSave(slug);
      }, 75);
    }
    return resultPromise;
  }

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
      applySiteRowUpdate(site);
      applySiteTagChange(site.slug);
      for (const w of waiters) w.resolve(site);
    } catch (err) {
      for (const w of waiters) w.reject(err);
    } finally {
      state.inflight = false;
      if (state.pendingTags != null) void flushSiteTagSave(slug);
    }
  }

  function renderSiteTags(site) {
    if (!sitePanelTags || !site) return;
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
            const current = siteTags(siteBySlug.get(site.slug) || site);
            const next = current.filter((t) => t !== tag);
            const updated = await patchSiteTags(site.slug, next, {
              immediate: true,
            });
            if (selectedSlug === site.slug) renderSiteTags(updated);
          } catch (_) {
            if (selectedSlug === site.slug) {
              renderSiteTags(siteBySlug.get(site.slug) || site);
            }
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
      if (selectedSlug === site.slug)
        renderSiteTags(siteBySlug.get(site.slug) || site);
    };
    const commitPendingTag = async () => {
      if (!tagAddOpen) return;
      const tag = normalizeTagInput(input.value);
      tagAddOpen = false;
      const current = siteTags(siteBySlug.get(site.slug) || site);
      if (!tag || current.includes(tag)) {
        if (selectedSlug === site.slug)
          renderSiteTags(siteBySlug.get(site.slug) || site);
        return;
      }
      try {
        const updated = await patchSiteTags(site.slug, [...current, tag], {
          immediate: true,
        });
        if (selectedSlug === site.slug) renderSiteTags(updated);
      } catch (_) {
        if (selectedSlug === site.slug)
          renderSiteTags(siteBySlug.get(site.slug) || site);
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
    renderSiteTags(site);
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
    setSectionVisible("site-panel-edit-links-section", false);
    const linksEl = document.getElementById("site-panel-edit-links");
    if (linksEl) linksEl.innerHTML = "";
  }

  function resetEditPrefetchUI() {
    resetEditPrefetchPanelUI();
    removeDraftLinksLayer();
    removeEditHistoryLinksLayer();
  }

  function renderEditPrefetch(payload) {
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
    const atOriginal =
      coords && coordsMatchEditSnapshot(coords.lat, coords.lon);
    const hint = document.getElementById("site-panel-edit-viewshed-hint");
    if (atOriginal && editSlug) {
      sitePanelEditViewshed.checked = isViewshedVisible(editSlug);
      if (hint)
        hint.textContent = viewshedLoading.has(editSlug) ? "Loading…" : "";
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
      Math.sin(dphi / 2) ** 2 +
      Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlambda / 2) ** 2;
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
    return editCoordHistory.some((entry) =>
      coordsMatchPair(entry.lat, entry.lon, lat, lon),
    );
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
    if (
      coordsMatchPair(
        coords.lat,
        coords.lon,
        editCommittedCoords.lat,
        editCommittedCoords.lon,
      )
    ) {
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
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
      });
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
      const filtered = filterEditSitePrefetchPayload({
        links_geojson: linksGeojson,
      });
      entry.linksGeojson = filterHistoryEntryLinksGeojson(
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
    if (
      entry.linksGeojson &&
      entry.linksGeojson.features &&
      entry.linksGeojson.features.length
    ) {
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
      if (editShowsSitePreview() && viewshedLoading.has(slug))
        loadingParts.push("viewshed");
      if (entry.linksLoading) loadingParts.push("links");
      if (loadingParts.length)
        coordsEl.textContent += ` (${loadingParts.join(", ")}…)`;

      const actions = document.createElement("div");
      actions.className = "edit-coord-history__actions";

      const copyBtn = document.createElement("wa-button");
      copyBtn.appearance = "outlined";
      copyBtn.size = "s";
      copyBtn.className = "coord-action-btn";
      copyBtn.title = "Copy lat, lon";
      copyBtn.innerHTML =
        '<wa-icon name="copy" label="Copy coordinates"></wa-icon>';
      copyBtn.addEventListener("click", () => {
        void copyCoordPair(entry.lat, entry.lon);
      });

      const deleteBtn = document.createElement("wa-button");
      deleteBtn.appearance = "outlined";
      deleteBtn.size = "s";
      deleteBtn.className = "coord-action-btn";
      deleteBtn.title = "Remove from history";
      deleteBtn.innerHTML =
        '<wa-icon name="xmark" label="Remove from history"></wa-icon>';
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
    if (sitePanelEditHeight) {
      sitePanelEditHeight.value =
        entity.height_m != null && Number.isFinite(Number(entity.height_m))
          ? String(entity.height_m)
          : "";
    }
    syncEditHeightHint();
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
    syncMapViewport();
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
    if (sitePanelEditHeight) {
      const rawHeight = sitePanelEditHeight.value.trim();
      if (rawHeight) {
        const heightM = Number(rawHeight);
        if (!Number.isFinite(heightM) || heightM < 1) {
          setEditError("Antenna height must be at least 1 m.");
          sitePanelEditSave.disabled = false;
          return;
        }
        body.height_m = heightM;
      } else {
        body.height_m = null;
      }
    }
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

  function applySiteRowUpdate(site, { refreshGeoJson = true } = {}) {
    const row = normalizeSiteFromApi(site);
    if (!row) return;
    const ix = sites.findIndex((s) => s.slug === row.slug);
    if (ix >= 0) sites[ix] = row;
    else sites.push(row);
    siteBySlug.set(row.slug, row);
    if (refreshGeoJson && map.getSource(SITES_SOURCE)) {
      map.getSource(SITES_SOURCE).setData(sitesGeoJson());
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

  function selectSite(slug) {
    const site = siteBySlug.get(slug);
    if (!site) return;
    if (createMode) cancelCreate();
    if (editMode) cancelEdit();
    selectedSlug = slug;
    sitePanel.hidden = false;
    syncMapViewport();
    showPanelView();
    renderPanel(site);
    updateSelectedLayer();
    raiseSiteLayers();
    renderEntityPanel();
    syncWarmPriorities();
    void loadSingleSiteLinks(slug);
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
    syncMapViewport();
    updateSelectedLayer();
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

  function beginCreateAtMapPoint(lat, lon) {
    if (editMode) return;
    if (createMode) cancelCreate();
    setAddPlacementMode(null);
    openCreatePanel(lat, lon);
  }

  function wireMapLongPress() {
    const canvas = map.getCanvas();
    canvas.addEventListener(
      "touchstart",
      (ev) => {
        if (editMode || ev.touches.length !== 1) return;
        const touch = ev.touches[0];
        longPressStart = { x: touch.clientX, y: touch.clientY };
        clearLongPressTimer();
        longPressTimer = setTimeout(() => {
          longPressTimer = null;
          if (!longPressStart || !mapReady) return;
          const { x, y } = longPressStart;
          longPressStart = null;
          const lngLat = lngLatFromClientPoint(x, y);
          beginCreateAtMapPoint(lngLat.lat, lngLat.lng);
        }, LONG_PRESS_MS);
      },
      { passive: true },
    );
    canvas.addEventListener(
      "touchmove",
      (ev) => {
        if (!longPressStart || !longPressTimer || ev.touches.length !== 1)
          return;
        const touch = ev.touches[0];
        const dx = touch.clientX - longPressStart.x;
        const dy = touch.clientY - longPressStart.y;
        if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_PX) clearLongPressTimer();
      },
      { passive: true },
    );
    canvas.addEventListener("touchend", clearLongPressTimer);
    canvas.addEventListener("touchcancel", clearLongPressTimer);
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
    abortSeekInFlight();
    seekFetchEpoch += 1;
    if (seekFetchTimer) window.clearTimeout(seekFetchTimer);
    seekFetchTimer = null;
  }

  function cancelSeekScanUi() {
    invalidateSeekFetch();
    setSeekScanning(false);
  }

  function beginSeekFetch() {
    abortSeekInFlight();
    seekFetchEpoch += 1;
    const epoch = seekFetchEpoch;
    const ac = new AbortController();
    seekFetchAbort = ac;
    return { epoch, signal: ac.signal };
  }

  function seekSessionActive() {
    return Boolean(seekState?.running && !seekState?.complete);
  }

  function seekGoalFromState() {
    if (seekState?.goalLat != null && seekState?.goalLon != null) {
      return { lat: seekState.goalLat, lon: seekState.goalLon };
    }
    if (seekPendingGoalLat != null && seekPendingGoalLon != null) {
      return { lat: seekPendingGoalLat, lon: seekPendingGoalLon };
    }
    return null;
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

  function seekGoalCoords() {
    return seekGoalFromState();
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
      removeSeekGoalMarker();
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
      setSeekStatus("Click the map to set goal");
    } else if (seekPanelOpen && !seekSessionActive()) {
      const goal = seekGoalCoords();
      if (!goal) setSeekStatus("Pick a start site and set goal on the map");
      else setSeekStatus("Pick a start site to begin");
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
        clearSeekRedoStack();
        seekState.complete = false;
        saveSeekState({ immediatePlan: true });
      }
    }
    setSeekGoalPlacementMode(false);
    updateSeekGoalCoordsDisplay();
    syncSeekGoalMarker();
    if (refresh && seekSessionActive()) {
      if (moved) {
        applySeekLayers({
          candidates: { type: "FeatureCollection", features: [] },
          lines: { type: "FeatureCollection", features: [] },
        });
      }
      promptSeekManualRecalc();
    } else maybeAutoStartSeekFromSelects();
  }

  function syncSeekGoalUi() {
    updateSeekGoalCoordsDisplay();
    syncSeekGoalMarker();
    syncSeekGoalLine();
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

  function seekStateFromYamlPlan(plan) {
    if (!plan || typeof plan !== "object") return null;
    const startSlug = String(plan.start || "").trim();
    const startSite = siteBySlug.get(startSlug);
    if (!startSite) return null;
    const goal = plan.goal;
    if (!Array.isArray(goal) || goal.length !== 2) return null;
    const goalLat = Number(goal[0]);
    const goalLon = Number(goal[1]);
    if (!Number.isFinite(goalLat) || !Number.isFinite(goalLon)) return null;
    const hops = [];
    for (const item of plan.hops || []) {
      if (!item || typeof item !== "object") return null;
      if (item.site) {
        const site = siteBySlug.get(String(item.site));
        if (!site) return null;
        hops.push({
          lat: site.lat,
          lon: site.lon,
          elev_m: site.height_m ?? null,
          site_slug: site.slug,
          site_name: site.name,
        });
        continue;
      }
      const loc = item.loc;
      if (!Array.isArray(loc) || loc.length !== 2) return null;
      const lat = Number(loc[0]);
      const lon = Number(loc[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
      hops.push({
        lat,
        lon,
        elev_m: item.height_m != null ? Number(item.height_m) : null,
      });
    }
    if (!hops.length) return null;
    const last = hops[hops.length - 1];
    return {
      running: true,
      startSlug,
      goalLat,
      goalLon,
      hops,
      currentFrom: { lat: last.lat, lon: last.lon },
      complete: Boolean(plan.complete),
      redoStack: loadSeekRedoStack(),
    };
  }

  function seekStateToYamlPlan(state) {
    if (!state?.running || !state.startSlug) return null;
    if (state.goalLat == null || state.goalLon == null) return null;
    if (!Array.isArray(state.hops) || !state.hops.length) return null;
    return {
      start: state.startSlug,
      goal: [state.goalLat, state.goalLon],
      complete: Boolean(state.complete),
      hops: state.hops.map((hop) => {
        if (hop.site_slug) return { site: hop.site_slug };
        return { loc: [hop.lat, hop.lon] };
      }),
    };
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
          setSeekStatus(
            body.error || `Failed to clear seek plan (${resp.status})`,
          );
          return;
        }
        if (config.seek && typeof config.seek === "object") {
          config.seek.plan = null;
        }
      } catch (err) {
        if (seq !== seekPlanSaveSeq) return;
        setSeekStatus(String(err));
      }
      return;
    }
    const plan = seekStateToYamlPlan(seekState);
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
        setSeekStatus(
          body.error || `Failed to save seek plan (${resp.status})`,
        );
        return;
      }
      if (config.seek && typeof config.seek === "object") {
        config.seek.plan = plan;
      }
    } catch (err) {
      if (seq !== seekPlanSaveSeq) return;
      setSeekStatus(String(err));
    }
  }

  function persistSeekPlanToYaml({ immediate = false } = {}) {
    if (seekPlanSaveTimer) window.clearTimeout(seekPlanSaveTimer);
    if (immediate) {
      void flushSeekPlanToYaml({ immediate: true });
      return;
    }
    seekPlanSaveTimer = window.setTimeout(() => {
      seekPlanSaveTimer = null;
      void flushSeekPlanToYaml();
    }, SEEK_PLAN_SAVE_MS);
  }

  function loadSeekStateFromLocalStorage() {
    try {
      const raw = localStorage.getItem(SEEK_STATE_KEY);
      if (!raw) return null;
      const parsed = migrateSeekStateGoal(JSON.parse(raw));
      if (!parsed || typeof parsed !== "object") return null;
      if (!Array.isArray(parsed.redoStack))
        parsed.redoStack = loadSeekRedoStack();
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function rehydrateSeekStateFromConfig() {
    if (seekState?.running) return seekState;
    const fromYaml = config.seek?.plan
      ? seekStateFromYamlPlan(config.seek.plan)
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
    const fromYaml = yamlPlan ? seekStateFromYamlPlan(yamlPlan) : null;
    if (fromYaml) {
      localStorage.removeItem(SEEK_STATE_KEY);
      return fromYaml;
    }
    return loadSeekStateFromLocalStorage();
  }

  function loadSeekState() {
    return initSeekState();
  }

  let seekState = loadSeekState();
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
    saveSeekRedoStack();
    if (!seekState) {
      localStorage.removeItem(SEEK_STATE_KEY);
      persistSeekPlanToYaml({ immediate: immediatePlan });
      return;
    }
    persistSeekPlanToYaml({ immediate: immediatePlan });
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
    stopSeekProgressTick();
    seekProgressTickTimer = window.setInterval(() => {
      if (!seekScanning) return;
      updateSeekProgressUi(lastSeekProgress || { phase: "starting" });
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
    stopSeekProgressPoll();
    stopSeekProgressTick();
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
        await sleepMs(SEEK_PROGRESS_POLL_MS);
        continue;
      }
      if (!resp.ok) {
        await sleepMs(SEEK_PROGRESS_POLL_MS);
        continue;
      }
      const body = await resp.json().catch(() => ({}));
      if (body?.gen != null && body.gen !== expectedGen)
        return { cancelled: true };
      if (body?.progress) updateSeekProgressUi(body.progress);
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
      await sleepMs(SEEK_PROGRESS_POLL_MS);
    }
  }

  function setSeekScanning(active) {
    seekScanning = active;
    if (seekStatusEl) seekStatusEl.hidden = active;
    if (seekProgressEl) seekProgressEl.hidden = !active;
    if (active) {
      setSeekStatus("");
      seekScanStartedAt = Date.now();
      updateSeekProgressUi({ phase: "starting" });
      startSeekProgressTick();
    } else {
      stopSeekProgressUi();
      seekScanStartedAt = 0;
      if (seekProgressDetailEl) seekProgressDetailEl.textContent = "";
    }
    updatePinOverlays();
    syncSeekPanelUi();
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
    syncSeekGoalUi();
    syncSeekRefreshUi();
    syncSeekConvertSitesBtn();
  }

  function countSeekLocHops() {
    const plan = seekStateToYamlPlan(seekState) || config.seek?.plan;
    if (!plan || !Array.isArray(plan.hops)) return 0;
    return plan.hops.filter(
      (hop) => hop && typeof hop === "object" && hop.loc && !hop.site,
    ).length;
  }

  function countSeekUniqueLocHops() {
    const plan = seekStateToYamlPlan(seekState) || config.seek?.plan;
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
    const locHops = countSeekLocHops();
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
    const uniqueSites = countSeekUniqueLocHops();
    const ready =
      uniqueSites > 0 &&
      effectiveSeekConvertDraftTags().length > 0 &&
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
        renderSeekConvertTags();
        syncSeekConvertTagSuggestions();
        syncSeekConvertSaveButton();
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

  function seekConvertNamePreview(prefix, uniqueSites) {
    const p = String(prefix || "").trim();
    if (!p) return "prefix + number";
    const start = maxExistingPrefixedSiteNumber(p) + 1;
    if (uniqueSites <= 1) return `${p} ${start}`;
    return `${p} ${start}–${start + uniqueSites - 1}`;
  }

  function syncSeekConvertHopCountText() {
    if (!seekConvertHopCount) return;
    const locHops = countSeekLocHops();
    const uniqueSites = countSeekUniqueLocHops();
    const prefix = String(seekConvertNamePrefix?.value || "").trim();
    if (locHops === 0) {
      seekConvertHopCount.textContent = "No coordinate hops in the saved path.";
      return;
    }
    const names = seekConvertNamePreview(prefix, uniqueSites);
    if (uniqueSites === locHops) {
      seekConvertHopCount.textContent = `${locHops} coordinate hop(s) will become ${uniqueSites} site(s). Names: ${names}.`;
    } else {
      seekConvertHopCount.textContent = `${locHops} coordinate hop(s) will become ${uniqueSites} site(s) (duplicate coordinates reuse one site). Names: ${names}.`;
    }
  }

  function resetSeekConvertModal() {
    setSeekConvertError("");
    seekConvertDraftTags = [];
    if (seekConvertTagInput) seekConvertTagInput.value = "";
    if (seekConvertNamePrefix) seekConvertNamePrefix.value = "Relay";
    syncSeekConvertHopCountText();
    renderSeekConvertTags();
    syncSeekConvertTagSuggestions();
    syncSeekConvertSaveButton();
  }

  async function openSeekConvertModal() {
    if (!seekConvertSitesModal) return;
    if (countSeekLocHops() === 0) return;
    resetSeekConvertModal();
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
      renderSeekConvertTags();
      syncSeekConvertTagSuggestions();
      syncSeekConvertSaveButton();
    }
  }

  async function saveSeekConvertModal() {
    addSeekConvertTagFromInput();
    const namePrefix = String(seekConvertNamePrefix?.value || "").trim();
    const tags = effectiveSeekConvertDraftTags();
    if (!namePrefix) {
      setSeekConvertError("Name prefix is required.");
      return;
    }
    if (!tags.length) {
      setSeekConvertError("Choose at least one tag.");
      return;
    }
    if (countSeekLocHops() === 0) {
      setSeekConvertError("No coordinate hops to convert.");
      return;
    }
    setSeekConvertError("");
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
        setSeekConvertError(payload.error || `Convert failed (${resp.status})`);
        return;
      }
      const imported = Array.isArray(payload.sites) ? payload.sites : [];
      const plan = payload.plan;
      closeSeekConvertModal();
      for (const site of imported) {
        registerSite(site);
      }
      if (plan && config.seek && typeof config.seek === "object") {
        config.seek.plan = plan;
      }
      const fromYaml = plan ? seekStateFromYamlPlan(plan) : null;
      if (fromYaml) {
        seekState = fromYaml;
        if (seekState.goalLat != null && seekState.goalLon != null) {
          seekPendingGoalLat = seekState.goalLat;
          seekPendingGoalLon = seekState.goalLon;
        }
      }
      saveSeekState({ immediatePlan: true });
      const firstTag = tags[0];
      if (firstTag) {
        activeTagFilters.clear();
        activeTagFilters.add(firstTag);
        pruneActiveTagFilters();
        renderEntityPanel();
      }
      syncSeekPanelUi();
      updateSeekPathOverlay();
      populateSeekStartSelect();
      const converted = payload.converted ?? 0;
      const tagged = payload.tagged ?? 0;
      let status = `Converted ${converted} hop(s) to sites`;
      if (tagged > 0) status += `; tagged ${tagged} existing site(s)`;
      setSeekStatus(status);
      void loadSiteLinks();
      if (imported[0]?.slug) selectSite(imported[0].slug);
    } catch (err) {
      setSeekConvertError(String(err));
    } finally {
      syncSeekConvertSaveButton();
    }
  }

  function maybeAutoStartSeekFromSelects() {
    if (seekSelectsHydrating) return;
    if (seekState?.running) return;
    const startSlug = seekStartSelect?.value;
    if (!startSlug) {
      setSeekStatus("Pick a start site and set goal on the map");
      return;
    }
    const startSite = siteBySlug.get(startSlug);
    const goal = seekGoalCoords();
    if (!startSite || !goal) {
      if (!goal) setSeekStatus("Set goal on the map, then pick start site");
      return;
    }
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      SEEK_GOAL_SAME_AS_START_M
    ) {
      setSeekStatus("Goal overlaps start site — pick a different point");
      return;
    }
    startSeekRun();
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
    if (seekPanelOpen) populateSeekStartSelect();
  }

  function seekPanHintText() {
    const hop = seekState?.hops?.length || 1;
    return `Hop ${hop} — click Recalculate for candidates`;
  }

  function promptSeekManualRecalc() {
    if (!seekSessionActive() || seekState?.complete) return;
    setSeekStatus(seekPanHintText());
  }

  function toggleSeekPanel(force) {
    const nextOpen = typeof force === "boolean" ? force : !seekPanelOpen;
    if (seekPanelOpen && !nextOpen) {
      setSeekGoalPlacementMode(false);
      if (seekScanning) {
        cancelSeekScanUi();
        if (seekSessionActive()) {
          setSeekStatus(seekPanHintText());
        }
      }
    }
    seekPanelOpen = nextOpen;
    if (seekPanelOpen) {
      populateSeekStartSelect();
      if (seekState?.goalLat != null && seekState?.goalLon != null) {
        seekPendingGoalLat = seekState.goalLat;
        seekPendingGoalLon = seekState.goalLon;
      }
      syncSeekGoalUi();
      if (seekState?.running && !seekState?.complete) {
        seekRunning = true;
        if (!seekScanning) {
          promptSeekManualRecalc();
        }
      } else if (!seekState?.running) {
        setSeekStatus("Pick a start site and set goal on the map");
      }
    } else {
      syncSeekGoalUi();
    }
    syncSeekPanelUi();
  }

  function clearSeekMarkers() {
    for (const marker of seekMarkers) marker.remove();
    seekMarkers = [];
  }

  function removeSeekCandidateLayers() {
    const layerIds = [
      SEEK_LINES_LABELS_LAYER,
      SEEK_LINES_LAYER,
      SEEK_CANDIDATES_LABELS_LAYER,
      SEEK_CANDIDATES_LAYER,
      SEEK_GOAL_LINE_LAYER,
    ];
    for (const id of layerIds) {
      if (map.getLayer(id)) map.removeLayer(id);
    }
    for (const src of [
      SEEK_LINES_SOURCE,
      SEEK_CANDIDATES_SOURCE,
      SEEK_GOAL_LINE_SOURCE,
    ]) {
      if (map.getSource(src)) map.removeSource(src);
    }
  }

  function removeSeekLayers() {
    removeSeekCandidateLayers();
    if (map.getLayer(SEEK_PATH_LAYER)) map.removeLayer(SEEK_PATH_LAYER);
    if (map.getSource(SEEK_PATH_SOURCE)) map.removeSource(SEEK_PATH_SOURCE);
    clearSeekMarkers();
  }

  function seekLinesGeoJsonWithLabels(geojson) {
    if (!geojson || !geojson.features) return geojson;
    return {
      type: geojson.type || "FeatureCollection",
      features: geojson.features.map((feature) => {
        const props = feature.properties || {};
        const dist = formatLinkDistanceKm(props.distance_km);
        const bearing =
          props.bearing_deg != null
            ? `${Math.round(Number(props.bearing_deg))}°`
            : "";
        const label =
          dist && bearing ? `${dist} · ${bearing}` : dist || bearing || "";
        return { ...feature, properties: { ...props, label } };
      }),
    };
  }

  function seekCandidatesGeoJsonForDisplay(candidates) {
    if (!candidates?.features) return candidates;
    return {
      type: candidates.type || "FeatureCollection",
      features: candidates.features.map((feature) => {
        const props = feature.properties || {};
        const slug = props.site_slug;
        if (!props.is_site || !slug) return feature;
        const siteName = props.site_name || siteBySlug.get(slug)?.name || "";
        const labelName = isSiteMapHidden(slug) ? siteName : "";
        return { ...feature, properties: { ...props, site_name: labelName } };
      }),
    };
  }

  function seekRfViable(props) {
    const v = props?.rf_viable;
    return v === true || v === "true";
  }

  function seekSiteCandidateSlugsFromPayload(payload) {
    const slugs = new Set();
    for (const feature of payload?.candidates?.features || []) {
      const props = feature?.properties || {};
      if (props.is_site && props.site_slug && seekRfViable(props)) {
        slugs.add(String(props.site_slug));
      }
    }
    return slugs;
  }

  function applySeekLayers(payload) {
    if (!mapReady || !payload) return;
    removeSeekCandidateLayers();
    seekSiteCandidateSlugs = seekSiteCandidateSlugsFromPayload(payload);

    const lines = payload.lines;
    if (lines && lines.features && lines.features.length) {
      const filteredLines = filterSeekLineFeatures(lines.features);
      if (filteredLines.length) {
        const labeled = seekLinesGeoJsonWithLabels({
          ...lines,
          features: filteredLines,
        });
        map.addSource(SEEK_LINES_SOURCE, { type: "geojson", data: labeled });
        map.addLayer(
          {
            id: SEEK_LINES_LAYER,
            type: "line",
            source: SEEK_LINES_SOURCE,
            paint: {
              "line-color": [
                "case",
                ["boolean", ["get", "is_goal"], false],
                "#22c55e",
                ["boolean", ["get", "is_site"], false],
                DRAFT_MARKER_COLOR,
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
          SITES_CIRCLE,
        );
        map.addLayer(
          linkLabelsLayerSpec(
            SEEK_LINES_LABELS_LAYER,
            SEEK_LINES_SOURCE,
            "visible",
          ),
          SITES_CIRCLE,
        );
      }
    }

    const candidates = seekCandidatesGeoJsonForDisplay({
      ...payload.candidates,
      features: filterSeekCandidateFeatures(payload.candidates?.features),
    });
    if (candidates && candidates.features && candidates.features.length) {
      map.addSource(SEEK_CANDIDATES_SOURCE, {
        type: "geojson",
        data: candidates,
      });
      map.addLayer(
        {
          id: SEEK_CANDIDATES_LAYER,
          type: "circle",
          source: SEEK_CANDIDATES_SOURCE,
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
        SITES_CIRCLE,
      );
      map.addLayer(
        seekSiteCandidateLabelsLayerSpec(
          SEEK_CANDIDATES_LABELS_LAYER,
          SEEK_CANDIDATES_SOURCE,
        ),
        SITES_CIRCLE,
      );
    }

    if (seekState?.running && Array.isArray(seekState.hops)) {
      updateSeekPathOverlay();
    }

    syncSeekGoalLine();
    if (seekSessionActive() && siteLinksPayload?.geojson) {
      refreshFilteredLinks();
    }
    raiseSiteLayers();
  }

  function seekViewportBbox() {
    const b = seekScanBoundsForRequest();
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
      .map((v) => v.toFixed(6))
      .join(",");
  }

  /** Peak bin size (m) from map zoom: ~20 bins across viewport width, clamped 500–1500 m. */
  function seekPeakBinSizeM() {
    return seekPeakBinSizeMForBounds(seekScanBoundsForRequest());
  }

  function seekExcludeParam() {
    if (!seekState || !Array.isArray(seekState.hops)) return "";
    return seekState.hops.map((hop) => `${hop.lat},${hop.lon}`).join(";");
  }

  function seekExcludeSlugsParam() {
    const slugs = new Set();
    if (seekState?.startSlug) slugs.add(seekState.startSlug);
    for (const hop of seekState?.hops || []) {
      if (hop.site_slug) slugs.add(hop.site_slug);
    }
    return [...slugs].join(";");
  }

  function seekPathSiteSlugs() {
    const slugs = new Set();
    if (seekState?.startSlug) slugs.add(seekState.startSlug);
    for (const hop of seekState?.hops || []) {
      if (hop.site_slug) slugs.add(hop.site_slug);
    }
    return slugs;
  }

  function seekHopSiteSlug(hop) {
    if (!hop) return null;
    if (hop.site_slug) return hop.site_slug;
    return seekSiteSlugNear(hop.lat, hop.lon);
  }

  /** Site mesh pairs that duplicate the committed seek chain (both endpoints are consecutive hops). */
  function seekChainSitePairKeys() {
    const keys = new Set();
    const hops = seekState?.hops;
    if (!seekSessionActive() || !hops || hops.length < 2) return keys;
    for (let i = 0; i < hops.length - 1; i += 1) {
      const slugA = seekHopSiteSlug(hops[i]);
      const slugB = seekHopSiteSlug(hops[i + 1]);
      if (slugA && slugB) keys.add(canonicalSitePairKey(slugA, slugB));
    }
    return keys;
  }

  function seekSiteSlugNear(lat, lon, maxM = SEEK_GOAL_SAME_AS_START_M) {
    let best = null;
    let bestDist = maxM;
    for (const site of sites) {
      const dist = haversineMeters(lat, lon, site.lat, site.lon);
      if (dist <= bestDist) {
        bestDist = dist;
        best = site.slug;
      }
    }
    return best;
  }

  function seekSiteSlugForFrom(from) {
    if (!from) return null;
    for (const slug of seekPathSiteSlugs()) {
      const site = siteBySlug.get(slug);
      if (
        site &&
        haversineMeters(from.lat, from.lon, site.lat, site.lon) <=
          SEEK_GOAL_SAME_AS_START_M
      ) {
        return slug;
      }
    }
    return seekSiteSlugNear(from.lat, from.lon);
  }

  function seekCoordsNearGoal(lat, lon) {
    const goal = seekGoalCoords();
    if (!goal) return false;
    return (
      haversineMeters(lat, lon, goal.lat, goal.lon) <= SEEK_GOAL_SAME_AS_START_M
    );
  }

  function seekLineFeatureIsRedundant(feature) {
    const props = feature?.properties || {};
    const coords = feature?.geometry?.coordinates;
    if (!coords?.length) return false;
    const [lon, lat] = coords[coords.length - 1];
    if (props.is_site && props.site_slug && isSiteInSeekPlan(props.site_slug)) {
      return true;
    }
    if (props.is_goal || seekCoordsNearGoal(lat, lon)) return true;
    return false;
  }

  function filterSeekLineFeatures(features) {
    return (features || []).filter((f) => !seekLineFeatureIsRedundant(f));
  }

  function filterSeekCandidateFeatures(features) {
    return (features || []).filter((feature) => {
      const props = feature?.properties || {};
      if (props.is_goal) return true;
      if (props.is_site && props.site_slug && isSiteInSeekPlan(props.site_slug)) {
        return false;
      }
      const coords = feature?.geometry?.coordinates;
      if (coords?.length >= 2 && seekCoordsNearGoal(coords[1], coords[0])) {
        return false;
      }
      return true;
    });
  }

  function isSiteInSeekPlan(slug) {
    return Boolean(seekState?.running && seekPathSiteSlugs().has(slug));
  }

  function seekHopCoordViewshedSlug(lat, lon) {
    return `${SEEK_HOP_VIEWSHED_PREFIX}${Number(lat).toFixed(5)}_${Number(lon).toFixed(5)}`;
  }

  function cancelSeekHopViewshedLoad(slug) {
    seekHopViewshedGen.set(slug, (seekHopViewshedGen.get(slug) || 0) + 1);
    viewshedPendingEpoch.delete(slug);
    viewshedLoading.delete(slug);
  }

  function clearSeekHopViewshed(slug) {
    cancelSeekHopViewshedLoad(slug);
    removeViewshedLayer(slug);
    seekHopCoordViewshedSlugs.delete(slug);
    seekHopCoordViewshedCoords.delete(slug);
    viewshedVisible.delete(slug);
  }

  function clearAllSeekHopViewsheds() {
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      clearSeekHopViewshed(slug);
    }
    seekHopCoordViewshedSlugs.clear();
    seekHopCoordViewshedCoords.clear();
  }

  async function loadSeekHopCoordViewshed(slug, lat, lon) {
    if (!String(slug).startsWith(SEEK_HOP_VIEWSHED_PREFIX)) return;
    const gen = (seekHopViewshedGen.get(slug) || 0) + 1;
    seekHopViewshedGen.set(slug, gen);
    viewshedVisible.set(slug, true);
    seekHopCoordViewshedCoords.set(slug, { lat, lon });
    if (await tryLoadCoordViewshedFromCache(slug, lat, lon)) return;
    removeViewshedLayer(slug);
    viewshedLoading.add(slug);
    const epoch = viewshedLoadEpoch;
    viewshedPendingEpoch.set(slug, epoch);
    updatePinOverlays();
    try {
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
      });
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (!resp.ok) {
        cancelSeekHopViewshedLoad(slug);
        updatePinOverlays();
        return;
      }
      const vs = await resp.json();
      if ((seekHopViewshedGen.get(slug) || 0) !== gen) return;
      if (viewshedPendingEpoch.get(slug) !== epoch) return;
      if (vs && vs.status === "ready") {
        handleViewshedReady({ ...vs, slug }, epoch);
      }
    } catch (_) {
      if ((seekHopViewshedGen.get(slug) || 0) === gen) {
        cancelSeekHopViewshedLoad(slug);
        updatePinOverlays();
      }
    }
  }

  function syncSeekHopViewsheds() {
    if (!mapReady || !seekState?.running || !Array.isArray(seekState.hops)) {
      clearAllSeekHopViewsheds();
      cancelSeekAncillaryLinksFetch();
      seekAncillaryLinksGen += 1;
      removeSeekAncillaryLinksLayer();
      return;
    }
    const wantedCoordSlugs = new Set();
    for (const hop of seekState.hops) {
      const siteSlug = hop.site_slug || seekSiteSlugNear(hop.lat, hop.lon);
      if (siteSlug) {
        const coordSlug = seekHopCoordViewshedSlug(hop.lat, hop.lon);
        if (seekHopCoordViewshedSlugs.has(coordSlug)) {
          clearSeekHopViewshed(coordSlug);
        }
        viewshedVisible.set(siteSlug, true);
        ensureViewshedLoadedForSlug(siteSlug);
        if (map.getLayer(viewshedLayerId(siteSlug))) {
          applyViewshedVisibilityForSite(siteSlug);
        }
        continue;
      }
      const slug = seekHopCoordViewshedSlug(hop.lat, hop.lon);
      wantedCoordSlugs.add(slug);
      seekHopCoordViewshedSlugs.add(slug);
      viewshedVisible.set(slug, true);
      if (!map.getLayer(viewshedLayerId(slug)) && !viewshedLoading.has(slug)) {
        void loadSeekHopCoordViewshed(slug, hop.lat, hop.lon);
      } else if (map.getLayer(viewshedLayerId(slug))) {
        map.setLayoutProperty(viewshedLayerId(slug), "visibility", "visible");
      }
    }
    for (const slug of [...seekHopCoordViewshedSlugs]) {
      if (!wantedCoordSlugs.has(slug)) {
        clearSeekHopViewshed(slug);
      }
    }
    raiseViewshedLayers();
    scheduleSeekAncillaryLinks();
  }

  function seekHopEndpointKey(hop) {
    if (hop.site_slug) return `site:${hop.site_slug}`;
    return `coord:${Number(hop.lat).toFixed(5)}_${Number(hop.lon).toFixed(5)}`;
  }

  function seekChainNeighborKeys(hopIndex) {
    const hops = seekState?.hops;
    const keys = new Set();
    if (!hops || hopIndex < 0 || hopIndex >= hops.length) return keys;
    if (hopIndex > 0) keys.add(seekHopEndpointKey(hops[hopIndex - 1]));
    if (hopIndex < hops.length - 1)
      keys.add(seekHopEndpointKey(hops[hopIndex + 1]));
    return keys;
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
    if (map.getLayer(SEEK_ANCILLARY_LINES_LABELS_LAYER)) {
      map.removeLayer(SEEK_ANCILLARY_LINES_LABELS_LAYER);
    }
    if (map.getLayer(SEEK_ANCILLARY_LINES_LAYER))
      map.removeLayer(SEEK_ANCILLARY_LINES_LAYER);
    if (map.getSource(SEEK_ANCILLARY_LINES_SOURCE))
      map.removeSource(SEEK_ANCILLARY_LINES_SOURCE);
  }

  function seekAncillaryLinkFeatureVisible(feature) {
    const props = feature?.properties || {};
    if (props.a && props.b) {
      return (
        !isSiteMapHidden(String(props.a)) && !isSiteMapHidden(String(props.b))
      );
    }
    const slug = props.slug;
    if (slug) return !isSiteMapHidden(String(slug));
    return true;
  }

  function refreshSeekAncillaryLinksDisplay() {
    if (!seekAncillaryLinksRawFeatures.length) {
      removeSeekAncillaryLinksLayer();
      return;
    }
    addSeekAncillaryLinksLayer({
      type: "FeatureCollection",
      features: seekAncillaryLinksRawFeatures,
    });
  }

  function addSeekAncillaryLinksLayer(geojson) {
    const features = (geojson?.features || []).filter(
      seekAncillaryLinkFeatureVisible,
    );
    if (!mapReady || !features.length) {
      removeSeekAncillaryLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels({
      type: "FeatureCollection",
      features,
    });
    if (map.getSource(SEEK_ANCILLARY_LINES_SOURCE)) {
      map.getSource(SEEK_ANCILLARY_LINES_SOURCE).setData(labeled);
      raiseSiteLayers();
      return;
    }
    map.addSource(SEEK_ANCILLARY_LINES_SOURCE, {
      type: "geojson",
      data: labeled,
    });
    map.addLayer(
      {
        id: SEEK_ANCILLARY_LINES_LAYER,
        type: "line",
        source: SEEK_ANCILLARY_LINES_SOURCE,
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
      SITES_CIRCLE,
    );
    map.addLayer(
      linkLabelsLayerSpec(
        SEEK_ANCILLARY_LINES_LABELS_LAYER,
        SEEK_ANCILLARY_LINES_SOURCE,
        "visible",
      ),
      SITES_CIRCLE,
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
      const neighbors = seekChainNeighborKeys(hopIndex);

      if (hop.site_slug) {
        await ensureSiteLinksForSlug(hop.site_slug);
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null;
        for (const peerSlug of linkedPeersForSite(hop.site_slug)) {
          if (neighbors.has(`site:${peerSlug}`)) continue;
          if (isSiteMapHidden(peerSlug)) continue;
          const linkFeature = findSiteLinkFeature(hop.site_slug, peerSlug);
          if (!linkFeature) continue;
          if (!seekAncillaryLinkFeatureVisible(linkFeature)) continue;
          const props = linkFeature.properties || {};
          addFeature(
            linkFeature,
            canonicalSitePairKey(String(props.a), String(props.b)),
          );
        }
        continue;
      }

      try {
        const resp = await fetch(sitesPrefetchUrl(hop.lat, hop.lon), {
          signal,
        });
        if (signal?.aborted || gen !== seekAncillaryLinksGen) return null;
        if (!resp.ok) continue;
        const payload = await resp.json();
        const geojson = payload?.links_geojson;
        if (!geojson?.features?.length) continue;
        const fromKey = seekHopEndpointKey(hop);
        for (const feature of geojson.features) {
          const slug = feature.properties?.slug;
          if (!slug) continue;
          if (neighbors.has(`site:${slug}`)) continue;
          if (!seekAncillaryLinkFeatureVisible(feature)) continue;
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
    cancelSeekAncillaryLinksFetch();
    if (
      !mapReady ||
      !seekState?.running ||
      !Array.isArray(seekState.hops) ||
      !seekState.hops.length
    ) {
      seekAncillaryLinksRawFeatures = [];
      removeSeekAncillaryLinksLayer();
      return;
    }
    const gen = ++seekAncillaryLinksGen;
    const ac = new AbortController();
    seekAncillaryLinksAbort = ac;
    const features = await collectSeekAncillaryLinkFeatures(ac.signal, gen);
    if (gen !== seekAncillaryLinksGen) return;
    seekAncillaryLinksAbort = null;
    if (features == null) return;
    seekAncillaryLinksRawFeatures = features;
    addSeekAncillaryLinksLayer({
      type: "FeatureCollection",
      features: seekAncillaryLinksRawFeatures,
    });
  }

  function scheduleSeekAncillaryLinks() {
    if (seekAncillaryLinksTimer) window.clearTimeout(seekAncillaryLinksTimer);
    seekAncillaryLinksTimer = window.setTimeout(() => {
      seekAncillaryLinksTimer = null;
      void flushSeekAncillaryLinks();
    }, SEEK_ANCILLARY_LINKS_DEBOUNCE_MS);
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
    syncSeekHopViewsheds();
    if (
      !mapReady ||
      !seekState ||
      !Array.isArray(seekState.hops) ||
      seekState.hops.length < 2
    ) {
      if (map.getLayer(SEEK_PATH_LAYER)) map.removeLayer(SEEK_PATH_LAYER);
      if (map.getSource(SEEK_PATH_SOURCE)) map.removeSource(SEEK_PATH_SOURCE);
      clearSeekMarkers();
      return;
    }
    const coords = seekState.hops.map((hop) => [hop.lon, hop.lat]);
    const pathGeoJson = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {},
    };
    if (map.getSource(SEEK_PATH_SOURCE)) {
      map.getSource(SEEK_PATH_SOURCE).setData(pathGeoJson);
    } else {
      map.addSource(SEEK_PATH_SOURCE, { type: "geojson", data: pathGeoJson });
      map.addLayer(
        {
          id: SEEK_PATH_LAYER,
          type: "line",
          source: SEEK_PATH_SOURCE,
          paint: {
            "line-color": "#fbbf24",
            "line-width": 3,
            "line-opacity": 0.85,
          },
          layout: { "line-cap": "round", "line-join": "round" },
        },
        SITES_CIRCLE,
      );
    }
    clearSeekMarkers();
    appendSeekHopMarkers();
    applySiteLayerFilters();
    syncSeekGoalLine();
    if (seekSessionActive() && siteLinksPayload?.geojson) {
      refreshFilteredLinks();
    }
    raiseSiteLayers();
  }

  function seekCurrentFrom() {
    if (seekState?.currentFrom) return seekState.currentFrom;
    const startSlug = seekState?.startSlug || seekStartSelect?.value;
    const startSite = startSlug ? siteBySlug.get(startSlug) : null;
    if (!startSite) return null;
    return { lat: startSite.lat, lon: startSite.lon };
  }

  function seekFetchParamsKey() {
    if (!seekSessionActive()) return null;
    const from = seekCurrentFrom();
    const goal = seekGoalCoords();
    if (!from || !goal) return null;
    return [
      from.lat.toFixed(6),
      from.lon.toFixed(6),
      goal.lat.toFixed(6),
      goal.lon.toFixed(6),
      seekViewportBbox(),
      String(seekPeakBinSizeM()),
      seekExcludeParam(),
      seekExcludeSlugsParam(),
    ].join("|");
  }

  function applySeekCandidatePayload(payload) {
    seekGoalInRange = Boolean(
      payload.meta?.goal_in_viewshed ?? payload.meta?.goal_reachable,
    );
    applySeekLayers(payload);
    syncSeekPanelUi();
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
    setSeekStatus(statusText);
    resetSeekViewshedRetries();
  }

  async function warmDraftViewshedForSeek(lat, lon, signal) {
    const siteSlug = seekSiteSlugForFrom({ lat, lon });
    if (siteSlug) {
      setViewshedVisible(DRAFT_VIEWSHED_SLUG, false);
      removeViewshedLayer(DRAFT_VIEWSHED_SLUG);
      viewshedVisible.set(siteSlug, true);
      ensureViewshedLoadedForSlug(siteSlug);
      return Boolean(map.getLayer(viewshedLayerId(siteSlug)));
    }
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
    if (await tryLoadDraftViewshedFromCache(lat, lon)) return true;
    try {
      const resp = await fetch(viewshedPrefetchWarmUrl(lat, lon), {
        method: "POST",
        signal,
      });
      if (!resp.ok) return false;
      const vs = await resp.json().catch(() => null);
      if (vs?.status === "ready" && vs.url && vs.coordinates) {
        handleViewshedReady(
          { ...vs, slug: DRAFT_VIEWSHED_SLUG },
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
    resetSeekViewshedRetries();
    const hint =
      errorText && /tile|dem|skadi|mirror|waiting/i.test(errorText)
        ? "Skadi DEM tiles still loading"
        : errorText || "Seek scan not ready";
    setSeekStatus(`${hint} — click Recalculate`);
    return false;
  }

  async function refreshSeekCandidates() {
    if (!seekSessionActive() || !mapReady) return;
    const from = seekCurrentFrom();
    const goal = seekGoalCoords();
    if (!from || !goal) return;
    const fetchKey = seekFetchParamsKey();
    if (!fetchKey) return;
    seekActiveFetchKey = fetchKey;
    const { epoch, signal } = beginSeekFetch();
    let seekRetryScheduled = false;
    setSeekScanning(true);
    updateSeekProgressUi({ phase: "viewshed", detail: "Warming viewshed…" });
    try {
      await warmDraftViewshedForSeek(from.lat, from.lon, signal);
    } catch (err) {
      if (err?.name === "AbortError") return;
    }
    if (epoch !== seekFetchEpoch) return;
    updateSeekProgressUi({ phase: "starting" });
    const params = new URLSearchParams({
      from_lat: String(from.lat),
      from_lon: String(from.lon),
      goal_lat: String(goal.lat),
      goal_lon: String(goal.lon),
      bbox: seekViewportBbox(),
      peak_bin_size_m: String(seekPeakBinSizeM()),
    });
    const exclude = seekExcludeParam();
    if (exclude) params.set("exclude", exclude);
    const excludeSlugs = seekExcludeSlugsParam();
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
        syncSeekPanelUi();
        setSeekStatus(kickoff.error || `Seek failed (${resp.status})`);
        return;
      }
      if (resp.status !== 202 || kickoff.gen == null) {
        setSeekStatus("Unexpected seek response");
        return;
      }
      const outcome = await pollSeekUntilDone(kickoff.gen, signal, epoch);
      if (epoch !== seekFetchEpoch) return;
      if (outcome.cancelled) return;
      if (outcome.error) {
        seekGoalInRange = false;
        syncSeekPanelUi();
        if (outcome.notReady) {
          seekRetryScheduled = scheduleSeekViewshedRetry(
            from,
            outcome.error,
            epoch,
          );
        } else {
          resetSeekViewshedRetries();
          setSeekStatus(outcome.error);
        }
        return;
      }
      resetSeekViewshedRetries();
      applySeekCandidatePayload(outcome.payload);
    } catch (err) {
      if (err?.name === "AbortError") return;
      if (epoch !== seekFetchEpoch) return;
      seekGoalInRange = false;
      syncSeekPanelUi();
      setSeekStatus(String(err));
    } finally {
      if (epoch === seekFetchEpoch) seekFetchAbort = null;
      if (epoch === seekFetchEpoch && !seekRetryScheduled)
        setSeekScanning(false);
    }
  }

  function onMapMoveEndForSeek() {
    syncSeekRefreshUi();
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
      setSeekStatus("Pick a start site and set goal on the map");
      return;
    }
    const startSite = siteBySlug.get(startSlug);
    if (!startSite) return;
    if (
      haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
      SEEK_GOAL_SAME_AS_START_M
    ) {
      setSeekStatus("Goal overlaps start site — pick a different point");
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
    saveSeekState({ immediatePlan: true });
    syncSeekPanelUi();
    updateSeekPathOverlay();
    promptSeekManualRecalc();
  }

  function commitSeekCandidate(feature) {
    if (!seekSessionActive() || !feature?.geometry?.coordinates) return;
    const props = feature.properties || {};
    if (props.is_goal) return;
    if (props.site_slug) {
      if (!seekSiteCandidateSlugs.has(String(props.site_slug))) return;
    } else if (!seekRfViable(props)) {
      return;
    }
    abortSeekInFlight();
    clearSeekRedoStack();
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
    saveSeekState({ immediatePlan: true });
    syncSeekPanelUi();
    updateSeekPathOverlay();
    applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    promptSeekManualRecalc();
  }

  function undoSeekHop() {
    if (
      !seekState ||
      !Array.isArray(seekState.hops) ||
      seekState.hops.length <= 1
    )
      return;
    if (seekScanning) cancelSeekScanUi();
    seekState.complete = false;
    seekRunning = true;
    if (!Array.isArray(seekState.redoStack)) seekState.redoStack = [];
    const removed = seekState.hops.pop();
    seekState.redoStack.push(removed);
    const last = seekState.hops[seekState.hops.length - 1];
    seekState.currentFrom = { lat: last.lat, lon: last.lon };
    saveSeekState({ immediatePlan: true });
    syncSeekPanelUi();
    applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    promptSeekManualRecalc();
  }

  function redoSeekHop() {
    if (!seekState?.redoStack?.length) return;
    if (seekScanning) cancelSeekScanUi();
    const hop = seekState.redoStack.pop();
    seekState.hops.push(hop);
    seekState.currentFrom = { lat: hop.lat, lon: hop.lon };
    seekState.complete = false;
    seekRunning = true;
    saveSeekState({ immediatePlan: true });
    syncSeekPanelUi();
    applySeekLayers({
      candidates: { type: "FeatureCollection", features: [] },
      lines: { type: "FeatureCollection", features: [] },
    });
    promptSeekManualRecalc();
  }

  function resetSeekRun() {
    seekRunning = false;
    seekGoalInRange = false;
    seekSiteCandidateSlugs = new Set();
    seekActiveFetchKey = null;
    seekPendingGoalLat = null;
    seekPendingGoalLon = null;
    setSeekGoalPlacementMode(false);
    cancelSeekScanUi();
    cancelSeekAncillaryLinksFetch();
    seekAncillaryLinksGen += 1;
    if (seekAncillaryLinksTimer) {
      window.clearTimeout(seekAncillaryLinksTimer);
      seekAncillaryLinksTimer = null;
    }
    seekState = null;
    saveSeekState({ immediatePlan: true });
    removeSeekLayers();
    clearAllSeekHopViewsheds();
    removeSeekAncillaryLinksLayer();
    removeSeekGoalMarker();
    setViewshedVisible(DRAFT_VIEWSHED_SLUG, false);
    setSeekStatus("");
    syncSeekPanelUi();
    if (siteLinksPayload?.geojson) refreshFilteredLinks();
  }

  function restoreSeekSessionIfAny() {
    rehydrateSeekStateFromConfig();
    if (!seekState?.running) return;
    if (!config.seek?.plan) {
      const plan = seekStateToYamlPlan(seekState);
      if (plan) persistSeekPlanToYaml({ immediate: true });
    }
    applySiteLayerFilters();
    seekRunning = !seekState.complete;
    seekPanelOpen = true;
    if (seekState.goalLat != null && seekState.goalLon != null) {
      seekPendingGoalLat = seekState.goalLat;
      seekPendingGoalLon = seekState.goalLon;
    }
    populateSeekStartSelect();
    syncSeekGoalUi();
    syncSeekPanelUi();
    updateSeekPathOverlay();
    if (!seekState.complete) {
      promptSeekManualRecalc();
    } else {
      applySeekLayers({
        candidates: { type: "FeatureCollection", features: [] },
        lines: { type: "FeatureCollection", features: [] },
      });
    }
  }

  function wireMapInteractions() {
    const siteLayerIds = [SITES_CIRCLE, SITES_LABELS];
    map.on("mousemove", (ev) => {
      if (addPlacementMode || editMode || seekGoalPlacementMode) {
        map.getCanvas().style.cursor = "crosshair";
        return;
      }
      if (seekSessionActive()) {
        if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
          const seekFeats = map.queryRenderedFeatures(ev.point, {
            layers: [SEEK_CANDIDATES_LAYER],
          });
          if (seekFeats.length) {
            map.getCanvas().style.cursor = "pointer";
            return;
          }
        }
        const siteFeats = map.queryRenderedFeatures(ev.point, {
          layers: siteLayerIds,
        });
        if (siteFeats.length) {
          const slug = siteFeats[0].properties?.slug;
          if (slug && seekSiteCandidateSlugs.has(slug)) {
            map.getCanvas().style.cursor = "pointer";
            return;
          }
        }
      }
      syncMapCursor();
    });
    for (const layerId of siteLayerIds) {
      map.on("mouseenter", layerId, () => {
        if (addPlacementMode || editMode || seekGoalPlacementMode) {
          map.getCanvas().style.cursor = "crosshair";
          return;
        }
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", layerId, () => {
        syncMapCursor();
      });
    }
    map.on("contextmenu", (ev) => {
      if (editMode) return;
      ev.preventDefault();
      beginCreateAtMapPoint(ev.lngLat.lat, ev.lngLat.lng);
    });
    map.on("click", (ev) => {
      if (editMode) {
        sitePanelEditLat.value = formatCoord(ev.lngLat.lat);
        sitePanelEditLon.value = formatCoord(ev.lngLat.lng);
        onEditCoordsChanged();
        return;
      }
      if (seekGoalPlacementMode && seekPanelOpen) {
        setSeekGoalAt(ev.lngLat.lat, ev.lngLat.lng);
        return;
      }
      if (seekSessionActive()) {
        if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
          const seekFeats = map.queryRenderedFeatures(ev.point, {
            layers: [SEEK_CANDIDATES_LAYER],
          });
          if (seekFeats.length) {
            const props = seekFeats[0].properties || {};
            if (!props.is_goal) {
              commitSeekCandidate(seekFeats[0]);
            }
            return;
          }
        }
        const siteSeekFeats = map.queryRenderedFeatures(ev.point, {
          layers: siteLayerIds,
        });
        if (siteSeekFeats.length) {
          const slug = siteSeekFeats[0].properties?.slug;
          if (slug && seekSiteCandidateSlugs.has(slug)) {
            const site = siteBySlug.get(slug);
            if (site) {
              commitSeekCandidate({
                geometry: { type: "Point", coordinates: [site.lon, site.lat] },
                properties: {
                  is_site: true,
                  site_slug: slug,
                  site_name: site.name,
                  elev_m: site.height_m ?? null,
                },
              });
              return;
            }
          }
        }
      }
      const feats = map.queryRenderedFeatures(ev.point, {
        layers: siteLayerIds,
      });
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
    wireMapLongPress();
  }

  function fitSites() {
    const points = [...sites];
    if (!points.length) return;
    const lons = points.map((p) => p.lon);
    const lats = points.map((p) => p.lat);
    const centerLat = (Math.min(...lats) + Math.max(...lats)) / 2;
    const { latDelta, lonDelta } = kmToDegreeDeltas(
      centerLat,
      SITE_FIT_BUFFER_KM,
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
      hideTerrainOverlays();
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
    syncMapViewport();
    map.resize();
    addSiteLayers();
    wireMapInteractions();
    connectProjectEvents();
    renderEntityPanel();
    renderLandPanel();
    setEntityTab(entityPanelTab);
    applyEntityVisibility();
    syncBasemapMenu();
    setBasemap(currentBasemapKey);
    if (savedMapState) {
      setSiteLinksVisible(showSiteLinks);
      syncTerrainFromPitch();
    } else {
      fitSites();
    }
    void refreshLandMapLayers();
    restoreSeekSessionIfAny();
    restoring = false;
    map.once("idle", () => {
      void loadViewshedIndex();
      void loadSiteLinks();
    });
  });

  map.on("pitch", () => {
    syncTerrainFromPitch();
    scheduleSaveMapState();
    syncSeekRefreshUi();
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
      setViewshedOpacity(Number(ev.target.value) / 100);
      scheduleSaveMapState();
    });
  }
  sitePanelClose.addEventListener("click", deselectSite);
  if (sitePanelViewshedToggle) {
    sitePanelViewshedToggle.innerHTML = mapToolIcon(
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
  if (sitePanelCopyCoords) {
    sitePanelCopyCoords.addEventListener("click", () => {
      const site = selectedSlug ? siteBySlug.get(selectedSlug) : null;
      if (!site) return;
      void copyCoordPair(site.lat, site.lon);
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
      const atOriginal =
        coords && coordsMatchEditSnapshot(coords.lat, coords.lon);
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
    mapToolSeek.addEventListener("click", () => toggleSeekPanel());
  }
  if (seekPanelClose) {
    seekPanelClose.addEventListener("click", () => toggleSeekPanel(false));
  }
  if (seekStartSelect) {
    seekStartSelect.addEventListener("change", () =>
      maybeAutoStartSeekFromSelects(),
    );
  }
  if (seekSetGoalBtn) {
    seekSetGoalBtn.addEventListener("click", () => {
      if (!seekPanelOpen || seekScanning) return;
      setSeekGoalPlacementMode(!seekGoalPlacementMode);
    });
  }
  if (seekUndoBtn) {
    seekUndoBtn.addEventListener("click", () => undoSeekHop());
  }
  if (seekRedoBtn) {
    seekRedoBtn.addEventListener("click", () => redoSeekHop());
  }
  if (seekResetBtn) {
    seekResetBtn.addEventListener("click", () => resetSeekRun());
  }
  if (seekConvertSitesBtn) {
    seekConvertSitesBtn.addEventListener("click", () => {
      void openSeekConvertModal();
    });
  }
  if (seekConvertNamePrefix) {
    seekConvertNamePrefix.addEventListener("input", () => {
      syncSeekConvertHopCountText();
      syncSeekConvertSaveButton();
    });
  }
  if (seekConvertTagForm) {
    seekConvertTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      addSeekConvertTagFromInput();
    });
  }
  if (seekConvertSave) {
    seekConvertSave.addEventListener("click", () => {
      void saveSeekConvertModal();
    });
  }
  if (seekRefreshBtn) {
    seekRefreshBtn.addEventListener("click", () => {
      if (!seekSessionActive() || seekScanning) return;
      void refreshSeekCandidates();
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
      void openImportSitesModal();
    });
  }
  if (entityPanelImportLand) {
    entityPanelImportLand.addEventListener("click", () => {
      void openImportLandModal();
    });
  }
  if (entityPanelAddLandFolder) {
    entityPanelAddLandFolder.addEventListener("click", () => {
      void openCreateLandFolderModal();
    });
  }
  if (landFolderSave) {
    landFolderSave.addEventListener("click", () => {
      saveLandFolderModal();
    });
  }
  if (landFolderName) {
    landFolderName.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        saveLandFolderModal();
      }
    });
  }
  queueLandSidebarMigrationFromLayerOrder();
  for (const tabBtn of entityPanelTabs) {
    tabBtn.addEventListener("click", () => {
      setEntityTab(tabBtn.getAttribute("data-entity-tab") || "sites");
    });
  }
  initLandPanelDragDrop();
  if (importLandGdb) {
    importLandGdb.addEventListener("change", () => {
      void previewImportLandPath(importLandGdb.value);
    });
  }
  if (importLandSave) {
    importLandSave.addEventListener("click", () => {
      void saveImportLandModal();
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
      resetImportLandModal();
    });
  }
  if (editLandSave) {
    editLandSave.addEventListener("click", () => {
      void saveEditLandModal();
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
      destroyEditLandPreviewMap();
      editLandSourceId = "";
      editLandPreviewRefresh = null;
      editLandPreviewLayers = [];
      editLandPreviewPath = "";
      editLandLayerStyles = new Map();
      editLandLayerConfigs = new Map();
      if (editLandLayerList) editLandLayerList.innerHTML = "";
      setEditLandError("");
    });
  }
  if (entityPanelBulkTag) {
    entityPanelBulkTag.addEventListener("click", () => {
      void openBulkTagModal();
    });
  }
  if (bulkTagSave) {
    bulkTagSave.addEventListener("click", () => {
      void saveBulkTagModal();
    });
  }
  if (bulkTagAddForm) {
    bulkTagAddForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      addBulkTagFromInput();
    });
  }
  if (bulkTagModal) {
    bulkTagModal.addEventListener("wa-after-show", () => {
      bulkTagModalOpen = true;
    });
    bulkTagModal.addEventListener("wa-after-hide", () => {
      bulkTagModalOpen = false;
      setBulkTagError("");
    });
  }
  if (bulkTagAddInput) {
    bulkTagAddInput.addEventListener("input", () => {
      syncBulkTagSaveButton();
    });
  }
  if (importSitesFile) {
    importSitesFile.addEventListener("change", () => {
      const file = importSitesFile.files && importSitesFile.files[0];
      if (file) void previewImportFile(file);
    });
  }
  if (importSitesSave) {
    importSitesSave.addEventListener("click", () => {
      void saveImportSitesModal();
    });
  }
  if (importSitesTagForm) {
    importSitesTagForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      addImportDraftTagFromInput();
    });
  }
  if (importSitesTagInput) {
    importSitesTagInput.addEventListener("input", () => {
      syncImportSaveButton();
    });
  }
  if (importSitesFilterVisible) {
    importSitesFilterVisible.addEventListener("change", () => {
      importFilterByViewport = !!importSitesFilterVisible.checked;
      renderImportPointList();
    });
  }
  if (importSitesSelectAll) {
    importSitesSelectAll.addEventListener("click", () => {
      setAllImportPointsIgnored(false);
    });
  }
  if (importSitesClearAll) {
    importSitesClearAll.addEventListener("click", () => {
      setAllImportPointsIgnored(true);
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
      resetImportSitesModal();
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
      setViewshedVisible(DRAFT_VIEWSHED_SLUG, visible);
      if (!visible) removeDraftViewshed();
    });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (createMode) {
      cancelCreate();
      return;
    }
    if (seekPanelOpen && seekRunning) {
      resetSeekRun();
      toggleSeekPanel(false);
      return;
    }
    if (seekPanelOpen) {
      toggleSeekPanel(false);
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
