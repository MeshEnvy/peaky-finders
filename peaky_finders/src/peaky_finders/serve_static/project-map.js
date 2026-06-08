(function () {
  const config = window.PEAKY_PROJECT || {};
  const projectSlug = config.slug;
  let sites = config.sites || [];
  let goals = config.goals || [];

  const TERRAIN_SOURCE = "terrain-dem";
  const TERRAIN_HILLSHADE = "terrain-hillshade";
  const BASEMAP_REFERENCE_SOURCE = "basemap-reference";
  const BASEMAP_REFERENCE_LAYER = "basemap-reference";
  const SITES_SOURCE = "sites";
  const SITES_CIRCLE = "sites-circle";
  const SITES_LABELS = "sites-labels";
  const SITES_SELECTED = "sites-selected";
  const GOALS_SOURCE = "goals";
  const GOALS_CIRCLE = "goals-circle";
  const GOALS_LABELS = "goals-labels";
  const GOALS_SELECTED = "goals-selected";
  const LINKS_SOURCE = "site-links";
  const LINKS_LAYER = "site-links-line";
  const LINKS_LABELS_LAYER = "site-links-label";
  const GOAL_LINKS_SOURCE = "goal-links";
  const GOAL_LINKS_LAYER = "goal-links-line";
  const DRAFT_LINKS_SOURCE = "draft-site-links";
  const DRAFT_LINKS_LAYER = "draft-site-links-line";
  const DRAFT_LINKS_LABELS_LAYER = "draft-site-links-label";
  const VIEWSHED_OPACITY_DEFAULT = 0.75;
  const DRAFT_VIEWSHED_SLUG = "_draft";
  const PITCH_TERRAIN_ON = 12;
  const PITCH_TERRAIN_OFF = 6;
  const SITE_FIT_BUFFER_KM = 30;
  const MAP_STATE_KEY = `peaky.map.v1.${projectSlug}`;
  const MAP_STATE_SAVE_MS = 400;

  const BASEMAPS = {
    street: {
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      maxzoom: 19,
    },
    topo: {
      tiles: ["https://tile.opentopomap.org/{z}/{x}/{y}.png"],
      maxzoom: 17,
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
  let viewshedOpacity = savedMapState?.viewshedOpacity ?? VIEWSHED_OPACITY_DEFAULT;

  function syncToolbarFromSaved(saved) {
    if (!saved) return;
    const basemapEl = document.getElementById("basemap");
    if (basemapEl && BASEMAPS[saved.basemap]) basemapEl.value = saved.basemap;
    const linksEl = document.getElementById("show-links");
    if (linksEl && typeof saved.showLinks === "boolean") linksEl.checked = saved.showLinks;
    const goalLinksEl = document.getElementById("show-goal-links");
    if (goalLinksEl && typeof saved.showGoalLinks === "boolean") goalLinksEl.checked = saved.showGoalLinks;
    const opacityEl = document.getElementById("viewshed-opacity");
    if (opacityEl && typeof saved.viewshedOpacity === "number") {
      opacityEl.value = Math.round(saved.viewshedOpacity * 100);
    }
  }

  syncToolbarFromSaved(savedMapState);

  function basemapStyle(key) {
    const bm = BASEMAPS[key] || BASEMAPS.street;
    return {
      version: 8,
      glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
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

  function goalsGeoJson() {
    return {
      type: "FeatureCollection",
      features: goals.map((goal) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [goal.lon, goal.lat] },
        properties: { name: goal.name, slug: goal.slug, kind: "goal" },
      })),
    };
  }

  function sitesGeoJson() {
    return {
      type: "FeatureCollection",
      features: sites.map((site) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [site.lon, site.lat] },
        properties: { name: site.name, slug: site.slug, type: site.type },
      })),
    };
  }

  const map = new maplibregl.Map({
    container: "map",
    style: basemapStyle(savedMapState ? savedMapState.basemap : "street"),
    center: savedMapState ? savedMapState.center : [-98.35, 39.5],
    zoom: savedMapState ? savedMapState.zoom : 4,
    maxPitch: 85,
    bearing: savedMapState ? savedMapState.bearing || 0 : 0,
    pitch: savedMapState ? savedMapState.pitch || 0 : 0,
    attributionControl: { compact: true },
  });
  const navControl = new maplibregl.NavigationControl({ visualizePitch: true });
  map.addControl(navControl, "top-right");
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
  let selectedGoalSlug = null;
  let addPlacementMode = null;
  let createMode = false;
  let pendingCreateLat = null;
  let pendingCreateLon = null;
  let draftMarker = null;
  let siteLinksPayload = null;
  let goalLinksPayload = null;
  const viewshedVisible = new Map();
  if (savedMapState?.viewshedVisible && typeof savedMapState.viewshedVisible === "object") {
    for (const [slug, visible] of Object.entries(savedMapState.viewshedVisible)) {
      viewshedVisible.set(slug, visible !== false);
    }
  }
  const viewshedLoading = new Set();
  const siteBySlug = new Map(sites.map((s) => [s.slug, s]));
  const goalBySlug = new Map(goals.map((g) => [g.slug, g]));
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
  const entityPanel = document.getElementById("entity-panel");
  const entityPanelToggle = document.getElementById("entity-panel-toggle");
  const entityPanelSitesList = document.getElementById("entity-panel-sites-list");
  const entityPanelGoalsList = document.getElementById("entity-panel-goals-list");
  const entityPanelSitesPane = document.getElementById("entity-panel-sites-pane");
  const entityPanelGoalsPane = document.getElementById("entity-panel-goals-pane");
  const entityPanelAddSite = document.getElementById("entity-panel-add-site");
  const entityPanelAddGoal = document.getElementById("entity-panel-add-goal");
  const siteCountBadge = document.getElementById("site-count-badge");
  const goalCountBadge = document.getElementById("goal-count-badge");
  const siteHidden = new Set(savedMapState?.hiddenSites || []);
  const goalHidden = new Set(savedMapState?.hiddenGoals || []);
  let entityPanelOpen = false;
  let entityPanelTab = "sites";

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

  function goalLinksApiUrl() {
    return `/api/p/${projectSlug}/goal-links`;
  }

  function goalsApiUrl() {
    return `/api/p/${projectSlug}/goals`;
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
    const taken = new Set([...siteBySlug.keys(), ...goalBySlug.keys()]);
    if (!taken.has(base)) return base;
    let n = 2;
    while (taken.has(`${base}-${n}`)) n += 1;
    return `${base}-${n}`;
  }

  function showPanelView() {
    createMode = false;
    sitePanelView.hidden = false;
    sitePanelCreate.hidden = true;
  }

  function showPanelCreate() {
    createMode = true;
    selectedSlug = null;
    selectedGoalSlug = null;
    updateSelectedLayer();
    updateGoalSelectedLayer();
    sitePanelView.hidden = true;
    sitePanelCreate.hidden = false;
    syncCreatePanelForKind();
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

  function syncMapCursor() {
    if (!mapReady) return;
    map.getCanvas().style.cursor = addPlacementMode ? "crosshair" : "";
  }

  function setAddPlacementMode(kind) {
    addPlacementMode = kind;
    if (entityPanelAddSite) {
      entityPanelAddSite.classList.toggle("active", kind === "site");
    }
    if (entityPanelAddGoal) {
      entityPanelAddGoal.classList.toggle("active", kind === "goal");
    }
    if (mapShell) {
      mapShell.classList.toggle("add-placement-mode", !!kind);
      mapShell.classList.toggle("add-site-mode", !!kind);
    }
    syncMapCursor();
    if (!kind) cancelCreate();
  }

  function setEntityPanelOpen(open) {
    entityPanelOpen = !!open;
    if (entityPanel) entityPanel.hidden = !entityPanelOpen;
    if (mapShell) mapShell.classList.toggle("entity-panel-open", entityPanelOpen);
    if (entityPanelToggle) {
      entityPanelToggle.classList.toggle("active", entityPanelOpen);
      entityPanelToggle.setAttribute("aria-expanded", entityPanelOpen ? "true" : "false");
    }
  }

  function setEntityTab(tab) {
    entityPanelTab = tab === "goals" ? "goals" : "sites";
    for (const btn of document.querySelectorAll("[data-entity-tab]")) {
      const active = btn.getAttribute("data-entity-tab") === entityPanelTab;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (entityPanelSitesPane) entityPanelSitesPane.hidden = entityPanelTab !== "sites";
    if (entityPanelGoalsPane) entityPanelGoalsPane.hidden = entityPanelTab !== "goals";
  }

  function siteVisibilityFilter() {
    if (!siteHidden.size) return null;
    return ["!", ["in", ["get", "slug"], ["literal", [...siteHidden]]]];
  }

  function goalVisibilityFilter() {
    if (!goalHidden.size) return null;
    return ["!", ["in", ["get", "slug"], ["literal", [...goalHidden]]]];
  }

  function applySiteLayerFilters() {
    if (!mapReady) return;
    const hiddenFilter = siteVisibilityFilter();
    for (const layerId of [SITES_CIRCLE, SITES_LABELS]) {
      if (!map.getLayer(layerId)) continue;
      map.setFilter(layerId, hiddenFilter || true);
    }
    updateSelectedLayer();
  }

  function applyGoalLayerFilters() {
    if (!mapReady) return;
    const hiddenFilter = goalVisibilityFilter();
    for (const layerId of [GOALS_CIRCLE, GOALS_LABELS]) {
      if (!map.getLayer(layerId)) continue;
      map.setFilter(layerId, hiddenFilter || true);
    }
    updateGoalSelectedLayer();
  }

  function filterSiteLinksGeoJson(geojson) {
    if (!geojson || !geojson.features || !siteHidden.size) return geojson;
    return {
      type: geojson.type || "FeatureCollection",
      features: geojson.features.filter((feature) => {
        const props = feature.properties || {};
        return !siteHidden.has(props.a) && !siteHidden.has(props.b);
      }),
    };
  }

  function filterGoalLinksGeoJson(geojson) {
    if (!geojson || !geojson.features) return geojson;
    if (!goalHidden.size && !siteHidden.size) return geojson;
    return {
      type: geojson.type || "FeatureCollection",
      features: geojson.features.filter((feature) => {
        const props = feature.properties || {};
        return !goalHidden.has(props.goal) && !siteHidden.has(props.site);
      }),
    };
  }

  function refreshFilteredLinks() {
    if (siteLinksPayload && siteLinksPayload.geojson) {
      addSiteLinksLayer(siteLinksPayload.geojson);
    }
    if (goalLinksPayload && goalLinksPayload.geojson) {
      addGoalLinksLayer(goalLinksPayload.geojson);
    }
  }

  function applyViewshedVisibilityForSite(slug) {
    const layerId = viewshedLayerId(slug);
    if (!map.getLayer(layerId)) return;
    const visible = !siteHidden.has(slug) && isViewshedVisible(slug);
    map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }

  function applyEntityVisibility() {
    applySiteLayerFilters();
    applyGoalLayerFilters();
    for (const site of sites) {
      applyViewshedVisibilityForSite(site.slug);
    }
    refreshFilteredLinks();
    renderEntityPanel();
  }

  function isSiteHidden(slug) {
    return siteHidden.has(slug);
  }

  function isGoalHidden(slug) {
    return goalHidden.has(slug);
  }

  function setSiteHidden(slug, hidden) {
    if (hidden) siteHidden.add(slug);
    else siteHidden.delete(slug);
    applyEntityVisibility();
    scheduleSaveMapState();
  }

  function setGoalHidden(slug, hidden) {
    if (hidden) goalHidden.add(slug);
    else goalHidden.delete(slug);
    applyEntityVisibility();
    scheduleSaveMapState();
  }

  function removeViewshedLayer(slug) {
    const layerId = viewshedLayerId(slug);
    const sourceId = viewshedSourceId(slug);
    if (map.getLayer(layerId)) map.removeLayer(layerId);
    if (map.getSource(sourceId)) map.removeSource(sourceId);
    viewshedLoading.delete(slug);
  }

  function siteDeleteUrl(slug) {
    return `/api/p/${projectSlug}/sites/${encodeURIComponent(slug)}`;
  }

  function goalDeleteUrl(slug) {
    return `/api/p/${projectSlug}/goals/${encodeURIComponent(slug)}`;
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
    updateSiteCountBadge();
    renderEntityPanel();
  }

  function unregisterGoal(slug) {
    const idx = goals.findIndex((g) => g.slug === slug);
    if (idx >= 0) goals.splice(idx, 1);
    goalBySlug.delete(slug);
    goalHidden.delete(slug);
    if (selectedGoalSlug === slug) deselectSite();
    addGoalLayers();
    updateGoalCountBadge();
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
      await loadGoalLinks();
    } catch (_) {
      /* network error */
    }
  }

  async function deleteGoal(slug) {
    const goal = goalBySlug.get(slug);
    if (!goal) return;
    if (!confirm(`Delete goal "${goal.name}" permanently?`)) return;
    try {
      const resp = await fetch(goalDeleteUrl(slug), { method: "DELETE" });
      if (!resp.ok) return;
      unregisterGoal(slug);
      await loadGoalLinks();
    } catch (_) {
      /* network error */
    }
  }

  function renderEntityPanel() {
    if (entityPanelSitesList) {
      entityPanelSitesList.innerHTML = "";
      const sortedSites = [...sites].sort((a, b) => a.name.localeCompare(b.name));
      if (!sortedSites.length) {
        const empty = document.createElement("div");
        empty.className = "entity-panel__empty";
        empty.textContent = "No sites yet.";
        entityPanelSitesList.appendChild(empty);
      }
      for (const site of sortedSites) {
        entityPanelSitesList.appendChild(buildSiteEntityRow(site));
      }
    }
    if (entityPanelGoalsList) {
      entityPanelGoalsList.innerHTML = "";
      const sortedGoals = [...goals].sort((a, b) => a.name.localeCompare(b.name));
      if (!sortedGoals.length) {
        const empty = document.createElement("div");
        empty.className = "entity-panel__empty";
        empty.textContent = "No goals yet.";
        entityPanelGoalsList.appendChild(empty);
      }
      for (const goal of sortedGoals) {
        entityPanelGoalsList.appendChild(buildGoalEntityRow(goal));
      }
    }
  }

  function buildSiteEntityRow(site) {
    const row = document.createElement("div");
    row.className = "entity-panel__row";
    if (selectedSlug === site.slug) row.classList.add("entity-panel__row--selected");
    if (isSiteHidden(site.slug)) row.classList.add("entity-panel__row--hidden");

    const main = document.createElement("div");
    main.className = "entity-panel__main";
    const name = document.createElement("div");
    name.className = "entity-panel__name";
    name.textContent = site.name;
    const meta = document.createElement("div");
    meta.className = "entity-panel__meta";
    meta.textContent = site.type || "installed";
    main.appendChild(name);
    main.appendChild(meta);

    const controls = document.createElement("div");
    controls.className = "entity-panel__controls";

    const eyeBtn = document.createElement("button");
    eyeBtn.type = "button";
    eyeBtn.className = "btn btn-sm btn-outline-secondary";
    eyeBtn.title = isSiteHidden(site.slug) ? "Show site" : "Hide site";
    eyeBtn.textContent = isSiteHidden(site.slug) ? "Show" : "Hide";
    eyeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setSiteHidden(site.slug, !isSiteHidden(site.slug));
    });

    const vsCheck = document.createElement("input");
    vsCheck.type = "checkbox";
    vsCheck.className = "form-check-input";
    vsCheck.title = "Show viewshed";
    vsCheck.checked = isViewshedVisible(site.slug);
    vsCheck.disabled = isSiteHidden(site.slug);
    vsCheck.addEventListener("click", (ev) => ev.stopPropagation());
    vsCheck.addEventListener("change", (ev) => {
      ev.stopPropagation();
      setViewshedVisible(site.slug, ev.target.checked);
      applyViewshedVisibilityForSite(site.slug);
      scheduleSaveMapState();
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-sm btn-outline-danger";
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

  function buildGoalEntityRow(goal) {
    const row = document.createElement("div");
    row.className = "entity-panel__row";
    if (selectedGoalSlug === goal.slug) row.classList.add("entity-panel__row--selected");
    if (isGoalHidden(goal.slug)) row.classList.add("entity-panel__row--hidden");

    const main = document.createElement("div");
    main.className = "entity-panel__main";
    const name = document.createElement("div");
    name.className = "entity-panel__name";
    name.textContent = goal.name;
    const meta = document.createElement("div");
    meta.className = "entity-panel__meta";
    meta.textContent = "goal";
    main.appendChild(name);
    main.appendChild(meta);

    const controls = document.createElement("div");
    controls.className = "entity-panel__controls";

    const eyeBtn = document.createElement("button");
    eyeBtn.type = "button";
    eyeBtn.className = "btn btn-sm btn-outline-secondary";
    eyeBtn.title = isGoalHidden(goal.slug) ? "Show goal" : "Hide goal";
    eyeBtn.textContent = isGoalHidden(goal.slug) ? "Show" : "Hide";
    eyeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      setGoalHidden(goal.slug, !isGoalHidden(goal.slug));
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-sm btn-outline-danger";
    delBtn.title = "Delete goal";
    delBtn.textContent = "Del";
    delBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      void deleteGoal(goal.slug);
    });

    controls.appendChild(eyeBtn);
    controls.appendChild(delBtn);

    row.appendChild(main);
    row.appendChild(controls);
    row.addEventListener("click", () => selectGoal(goal.slug));
    return row;
  }

  function updateSiteCountBadge() {
    if (!siteCountBadge) return;
    const n = sites.length;
    siteCountBadge.textContent = `${n} ${n === 1 ? "site" : "sites"}`;
  }

  function updateGoalCountBadge() {
    if (!goalCountBadge) return;
    const n = goals.length;
    goalCountBadge.textContent = `${n} ${n === 1 ? "goal" : "goals"}`;
  }

  function registerSite(site) {
    sites.push(site);
    siteBySlug.set(site.slug, site);
    addSiteLayers();
    updateSiteCountBadge();
    renderEntityPanel();
  }

  function registerGoal(goal) {
    goals.push(goal);
    goalBySlug.set(goal.slug, goal);
    addGoalLayers();
    updateGoalCountBadge();
    renderEntityPanel();
  }

  function syncCreateSlugPreview() {
    const name = sitePanelCreateName.value;
    sitePanelSlugPreview.textContent = previewSlugForName(name);
  }

  function syncCreatePanelForKind() {
    const isGoal = addPlacementMode === "goal";
    if (sitePanelCreateTitle) {
      sitePanelCreateTitle.textContent = isGoal ? "New goal" : "New site";
    }
    if (sitePanelCreateBadge) {
      sitePanelCreateBadge.textContent = isGoal ? "goal" : "planned";
      sitePanelCreateBadge.className = `site-panel__badge badge site-panel__badge--${isGoal ? "goal" : "planned"} mb-3`;
    }
    if (sitePanelCreateLinksLabel) {
      sitePanelCreateLinksLabel.textContent = isGoal ? "Linked repeaters" : "Linked sites";
    }
    if (sitePanelCreateViewshedSection) {
      sitePanelCreateViewshedSection.hidden = isGoal;
    }
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
    const markerColor = addPlacementMode === "goal" ? "#f59e0b" : "#fbbf24";
    draftMarker = new maplibregl.Marker({ color: markerColor })
      .setLngLat([lon, lat])
      .addTo(map);
    sitePanel.hidden = false;
    showPanelCreate();
    if (addPlacementMode === "site") {
      viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
      syncCreateViewshedCheckbox();
      void loadDraftViewshedAt(lat, lon);
      void loadPlacementPrefetchAt(lat, lon);
    } else {
      removeDraftViewshed();
      void loadGoalPlacementPrefetchAt(lat, lon);
    }
    sitePanelCreateName.focus();
  }

  function resetCreatePrefetchUI() {
    setSectionVisible("site-panel-create-plss-section", false);
    setSectionVisible("site-panel-create-mlrs-section", false);
    setSectionVisible("site-panel-create-links-section", false);
    document.getElementById("site-panel-create-plss").textContent = "";
    document.getElementById("site-panel-create-mlrs").textContent = "";
    const linksEl = document.getElementById("site-panel-create-links");
    if (linksEl) linksEl.innerHTML = "";
    removeDraftLinksLayer();
  }

  function removeDraftLinksLayer() {
    if (map.getLayer(DRAFT_LINKS_LABELS_LAYER)) map.removeLayer(DRAFT_LINKS_LABELS_LAYER);
    if (map.getLayer(DRAFT_LINKS_LAYER)) map.removeLayer(DRAFT_LINKS_LAYER);
    if (map.getSource(DRAFT_LINKS_SOURCE)) map.removeSource(DRAFT_LINKS_SOURCE);
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
        "text-font": ["Noto Sans Regular"],
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

  function addDraftLinksLayer(geojson, { goal = false } = {}) {
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
          goal ? "#f59e0b" : ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
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
          "line-color": goal ? "#f59e0b" : ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
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

  function formatLinkDistanceKm(distanceKm) {
    if (distanceKm == null || Number.isNaN(Number(distanceKm))) return null;
    return `${Number(distanceKm).toFixed(1)} km`;
  }

  function renderCreatePrefetch(payload, { goal = false } = {}) {
    const plss = payload.plss || "";
    setSectionVisible("site-panel-create-plss-section", !!plss);
    document.getElementById("site-panel-create-plss").textContent = plss || "—";
    const mlrs = payload.mlrs || "";
    setSectionVisible("site-panel-create-mlrs-section", !!mlrs);
    document.getElementById("site-panel-create-mlrs").textContent = mlrs || "—";
    const links = Array.isArray(payload.links) ? payload.links : [];
    const linked = links.filter((row) => row.linked !== false);
    setSectionVisible("site-panel-create-links-section", linked.length > 0);
    const linksEl = document.getElementById("site-panel-create-links");
    linksEl.innerHTML = "";
    for (const row of linked) {
      const slug = goal ? row.site : row.slug;
      const site = siteBySlug.get(slug);
      const label = site ? site.name : slug;
      const dist = formatLinkDistanceKm(row.distance_km);
      const li = document.createElement("li");
      li.textContent = dist ? `${label} — ${dist}` : label;
      linksEl.appendChild(li);
    }
    if (payload.links_geojson) addDraftLinksLayer(payload.links_geojson, { goal });
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
    } else if (selectedGoalSlug) {
      sitePanel.hidden = false;
      showPanelView();
      renderGoalPanel(goalBySlug.get(selectedGoalSlug));
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
    const isGoal = addPlacementMode === "goal";
    setCreateError("");
    sitePanelCreateSave.disabled = true;
    try {
      const resp = await fetch(isGoal ? goalsApiUrl() : sitesApiUrl(), {
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
      if (isGoal) {
        const goal = payload.goal;
        if (!goal || !goal.slug) {
          setCreateError("Unexpected server response.");
          return;
        }
        removeDraftMarker();
        pendingCreateLat = null;
        pendingCreateLon = null;
        createMode = false;
        setAddPlacementMode(null);
        registerGoal(goal);
        void loadGoalLinks();
        selectGoal(goal.slug);
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
      void loadViewshedForSite(site);
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
      basemap: document.getElementById("basemap").value,
      showLinks: document.getElementById("show-links").checked,
      showGoalLinks: document.getElementById("show-goal-links").checked,
      viewshedOpacity,
      hiddenSites: [...siteHidden],
      hiddenGoals: [...goalHidden],
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

  function setGoalLinksVisible(visible) {
    if (!mapReady) return;
    const vis = visible ? "visible" : "none";
    if (map.getLayer(GOAL_LINKS_LAYER)) map.setLayoutProperty(GOAL_LINKS_LAYER, "visibility", vis);
  }

  function addGoalLinksLayer(geojson) {
    const filtered = filterGoalLinksGeoJson(geojson);
    if (!filtered || !filtered.features || !filtered.features.length) {
      if (map.getLayer(GOAL_LINKS_LAYER)) map.removeLayer(GOAL_LINKS_LAYER);
      if (map.getSource(GOAL_LINKS_SOURCE)) map.removeSource(GOAL_LINKS_SOURCE);
      raiseSiteLayers();
      return;
    }
    const linkVisibility = document.getElementById("show-goal-links").checked ? "visible" : "none";
    if (map.getSource(GOAL_LINKS_SOURCE)) {
      map.getSource(GOAL_LINKS_SOURCE).setData(filtered);
      setGoalLinksVisible(document.getElementById("show-goal-links").checked);
      raiseSiteLayers();
      return;
    }
    map.addSource(GOAL_LINKS_SOURCE, { type: "geojson", data: filtered });
    map.addLayer(
      {
        id: GOAL_LINKS_LAYER,
        type: "line",
        source: GOAL_LINKS_SOURCE,
        paint: {
          "line-color": [
            "case",
            ["get", "captured"],
            "#d97706",
            "#f59e0b",
          ],
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
    raiseSiteLayers();
  }

  async function loadGoalLinks() {
    try {
      const resp = await fetch(goalLinksApiUrl());
      if (!resp.ok) return;
      const payload = await resp.json();
      goalLinksPayload = payload;
      if (payload && payload.geojson) addGoalLinksLayer(payload.geojson);
      if (selectedGoalSlug) renderGoalPanel(goalBySlug.get(selectedGoalSlug));
      if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
    } catch (_) {
      /* goal links optional */
    }
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
    const linkVisibility = document.getElementById("show-links").checked ? "visible" : "none";
    if (map.getSource(LINKS_SOURCE)) {
      map.getSource(LINKS_SOURCE).setData(labeled);
      setSiteLinksVisible(document.getElementById("show-links").checked);
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
      siteLinksPayload = payload;
      if (payload && payload.geojson) addSiteLinksLayer(payload.geojson);
      if (selectedSlug) renderPanel(siteBySlug.get(selectedSlug));
    } catch (_) {
      /* links optional */
    }
  }

  function raiseSiteLayers() {
    for (const id of [
      DRAFT_LINKS_LAYER,
      DRAFT_LINKS_LABELS_LAYER,
      GOAL_LINKS_LAYER,
      LINKS_LAYER,
      LINKS_LABELS_LAYER,
      GOALS_CIRCLE,
      GOALS_LABELS,
      GOALS_SELECTED,
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
    const slugs = [...sites.map((site) => site.slug), DRAFT_VIEWSHED_SLUG];
    for (const slug of slugs) {
      const layerId = viewshedLayerId(slug);
      if (map.getLayer(layerId)) {
        map.setPaintProperty(layerId, "raster-opacity", viewshedOpacity);
      }
    }
  }

  function setViewshedOpacity(opacity) {
    viewshedOpacity = Math.max(0, Math.min(1, opacity));
    applyViewshedOpacityToAllLayers();
  }

  function viewshedMetaUrl(siteSlug) {
    return `/api/p/${projectSlug}/viewsheds/${siteSlug}`;
  }

  function viewshedPrefetchUrl(lat, lon) {
    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    return `/api/p/${projectSlug}/viewsheds/prefetch?${params}`;
  }

  function goalsPrefetchUrl(lat, lon) {
    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    return `/api/p/${projectSlug}/goals/prefetch?${params}`;
  }

  async function loadGoalPlacementPrefetchAt(lat, lon) {
    const gen = ++placementPrefetchGen;
    resetCreatePrefetchUI();
    try {
      const resp = await fetch(goalsPrefetchUrl(lat, lon));
      if (gen !== placementPrefetchGen) return;
      if (!resp.ok) return;
      const payload = await resp.json();
      if (gen !== placementPrefetchGen) return;
      if (payload && (payload.links || payload.links_geojson)) {
        renderCreatePrefetch(payload, { goal: true });
      }
    } catch (_) {
      /* goal placement prefetch optional */
    }
  }

  function sitesPrefetchUrl(lat, lon) {
    const params = new URLSearchParams({
      lat: String(lat),
      lon: String(lon),
    });
    return `/api/p/${projectSlug}/sites/prefetch?${params}`;
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
      if (payload && (payload.plss || payload.mlrs || payload.links || payload.links_geojson)) {
        renderCreatePrefetch(payload);
      }
    } catch (_) {
      /* placement prefetch optional */
    }
  }

  async function loadDraftViewshedAt(lat, lon) {
    removeDraftViewshed();
    draftViewshedLoading = true;
    syncCreateViewshedCheckbox();
    try {
      const resp = await fetch(viewshedPrefetchUrl(lat, lon));
      if (!resp.ok) return;
      const vs = await resp.json();
      if (vs && vs.url && vs.coordinates) {
        addViewshedLayer({ ...vs, slug: DRAFT_VIEWSHED_SLUG });
      }
    } catch (_) {
      /* draft viewshed optional */
    } finally {
      draftViewshedLoading = false;
      syncCreateViewshedCheckbox();
    }
  }

  function isViewshedVisible(slug) {
    return viewshedVisible.get(slug) !== false;
  }

  function setViewshedVisible(slug, visible) {
    viewshedVisible.set(slug, visible);
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      applyViewshedVisibilityForSite(slug);
    } else if (visible && !siteHidden.has(slug)) {
      const site = siteBySlug.get(slug);
      if (site) void loadViewshedForSite(site);
    }
    if (slug === selectedSlug) syncViewshedCheckbox();
    if (slug === DRAFT_VIEWSHED_SLUG) syncCreateViewshedCheckbox();
  }

  function syncViewshedCheckbox() {
    if (!selectedSlug) return;
    sitePanelViewshed.checked = isViewshedVisible(selectedSlug);
    sitePanelViewshedHint.textContent = viewshedLoading.has(selectedSlug) ? "Loading…" : "";
  }

  function addViewshedLayer(vs) {
    const sourceId = viewshedSourceId(vs.slug);
    const layerId = viewshedLayerId(vs.slug);
    if (!map.getSource(sourceId)) {
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
    if (!isViewshedVisible(vs.slug) || siteHidden.has(vs.slug)) {
      map.setLayoutProperty(layerId, "visibility", "none");
    } else {
      map.setLayoutProperty(layerId, "visibility", "visible");
    }
    viewshedLoading.delete(vs.slug);
    if (vs.slug === selectedSlug) syncViewshedCheckbox();
    if (vs.slug === DRAFT_VIEWSHED_SLUG) syncCreateViewshedCheckbox();
    raiseSiteLayers();
  }

  async function loadViewshedForSite(site) {
    viewshedLoading.add(site.slug);
    if (site.slug === selectedSlug) syncViewshedCheckbox();
    try {
      const resp = await fetch(viewshedMetaUrl(site.slug));
      if (!resp.ok) {
        viewshedLoading.delete(site.slug);
        if (site.slug === selectedSlug) syncViewshedCheckbox();
        return;
      }
      const vs = await resp.json();
      if (vs && vs.url && vs.coordinates) addViewshedLayer(vs);
      else {
        viewshedLoading.delete(site.slug);
        if (site.slug === selectedSlug) syncViewshedCheckbox();
      }
    } catch (_) {
      viewshedLoading.delete(site.slug);
      if (site.slug === selectedSlug) syncViewshedCheckbox();
    }
  }

  function loadAllViewsheds() {
    for (const site of sites) {
      void loadViewshedForSite(site);
    }
  }

  function addGoalLayers() {
    if (map.getSource(GOALS_SOURCE)) {
      map.getSource(GOALS_SOURCE).setData(goalsGeoJson());
      applyGoalLayerFilters();
      return;
    }
    map.addSource(GOALS_SOURCE, { type: "geojson", data: goalsGeoJson() });
    map.addLayer({
      id: GOALS_CIRCLE,
      type: "circle",
      source: GOALS_SOURCE,
      paint: {
        "circle-radius": 7,
        "circle-color": "#f59e0b",
        "circle-stroke-width": 2,
        "circle-stroke-color": "#fff",
      },
    });
    map.addLayer({
      id: GOALS_LABELS,
      type: "symbol",
      source: GOALS_SOURCE,
      layout: {
        "text-field": ["get", "name"],
        "text-size": 12,
        "text-offset": [0, -1.4],
        "text-anchor": "bottom",
        "text-font": ["Noto Sans Bold"],
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#fde68a",
        "text-halo-color": "#1a1a1a",
        "text-halo-width": 2,
      },
    });
    map.addLayer({
      id: GOALS_SELECTED,
      type: "circle",
      source: GOALS_SOURCE,
      filter: ["==", ["get", "slug"], ""],
      paint: {
        "circle-radius": 11,
        "circle-color": "#f59e0b",
        "circle-stroke-width": 3,
        "circle-stroke-color": "#fbbf24",
        "circle-opacity": 0.35,
      },
    });
    applyGoalLayerFilters();
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
        "text-font": ["Noto Sans Bold"],
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

  function updateGoalSelectedLayer() {
    if (!map.getLayer(GOALS_SELECTED)) return;
    const hiddenFilter = goalVisibilityFilter();
    if (hiddenFilter) {
      map.setFilter(GOALS_SELECTED, [
        "all",
        ["==", ["get", "slug"], selectedGoalSlug || ""],
        hiddenFilter,
      ]);
    } else {
      map.setFilter(GOALS_SELECTED, ["==", ["get", "slug"], selectedGoalSlug || ""]);
    }
  }

  function updateSelectedLayer() {
    if (!map.getLayer(SITES_SELECTED)) return;
    const hiddenFilter = siteVisibilityFilter();
    if (hiddenFilter) {
      map.setFilter(SITES_SELECTED, [
        "all",
        ["==", ["get", "slug"], selectedSlug || ""],
        hiddenFilter,
      ]);
    } else {
      map.setFilter(SITES_SELECTED, ["==", ["get", "slug"], selectedSlug || ""]);
    }
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

  function linkedRepeatersForGoal(slug) {
    if (!goalLinksPayload || !Array.isArray(goalLinksPayload.links)) return [];
    const peers = [];
    for (const row of goalLinksPayload.links) {
      if (!row.linked) continue;
      if (row.goal === slug) peers.push(row.site);
    }
    return peers.sort();
  }

  function linkedGoalsForSite(slug) {
    if (!goalLinksPayload || !Array.isArray(goalLinksPayload.links)) return [];
    const peers = [];
    for (const row of goalLinksPayload.links) {
      if (!row.linked) continue;
      if (row.site === slug) peers.push(row.goal);
    }
    return peers.sort();
  }

  function renderGoalPanel(goal) {
    if (!goal) return;
    document.getElementById("site-panel-name").textContent = goal.name;
    const badge = document.getElementById("site-panel-type");
    badge.textContent = "goal";
    badge.className = "site-panel__badge badge site-panel__badge--goal mb-3";
    document.getElementById("site-panel-coords").textContent =
      `${formatCoord(goal.lat)}, ${formatCoord(goal.lon)}`;
    const elevEl = document.getElementById("site-panel-elevation");
    if (goal.elevation_m != null) {
      elevEl.textContent = `${goal.elevation_m} m`;
      elevEl.classList.remove("text-muted");
    } else {
      elevEl.textContent = "—";
      elevEl.classList.add("text-muted");
    }
    const plss = goal.plss || "";
    setSectionVisible("site-panel-plss-section", !!plss);
    document.getElementById("site-panel-plss").textContent = plss;
    const mlrs = goal.mlrs || "";
    setSectionVisible("site-panel-mlrs-section", !!mlrs);
    document.getElementById("site-panel-mlrs").textContent = mlrs;
    const desc = goal.description || "";
    setSectionVisible("site-panel-desc-section", !!desc);
    document.getElementById("site-panel-desc").textContent = desc;
    const rationale = goal.rationale || "";
    setSectionVisible("site-panel-rationale-section", !!rationale);
    document.getElementById("site-panel-rationale").textContent = rationale;
    setSectionVisible("site-panel-links-section", false);
    const repeaters = linkedRepeatersForGoal(goal.slug);
    setSectionVisible("site-panel-goal-links-section", repeaters.length > 0);
    const goalLinksEl = document.getElementById("site-panel-goal-links");
    goalLinksEl.innerHTML = "";
    for (const peer of repeaters) {
      const site = siteBySlug.get(peer);
      const li = document.createElement("li");
      li.textContent = site ? site.name : peer;
      goalLinksEl.appendChild(li);
    }
    if (sitePanelViewshedSection) sitePanelViewshedSection.hidden = true;
  }

  function renderPanel(site) {
    if (!site) return;
    if (sitePanelViewshedSection) sitePanelViewshedSection.hidden = false;
    setSectionVisible("site-panel-goal-links-section", false);
    document.getElementById("site-panel-name").textContent = site.name;
    const badge = document.getElementById("site-panel-type");
    badge.textContent = site.type;
    badge.className = `site-panel__badge badge site-panel__badge--${site.type}`;
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
    const mlrs = site.mlrs || "";
    setSectionVisible("site-panel-mlrs-section", !!mlrs);
    document.getElementById("site-panel-mlrs").textContent = mlrs;
    const desc = site.description || "";
    setSectionVisible("site-panel-desc-section", !!desc);
    document.getElementById("site-panel-desc").textContent = desc;
    const rationale = site.rationale || "";
    setSectionVisible("site-panel-rationale-section", !!rationale);
    document.getElementById("site-panel-rationale").textContent = rationale;
    const peers = linkedPeersForSite(site.slug);
    setSectionVisible("site-panel-links-section", peers.length > 0);
    const linksEl = document.getElementById("site-panel-links");
    linksEl.innerHTML = "";
    for (const peer of peers) {
      const li = document.createElement("li");
      li.textContent = peer;
      linksEl.appendChild(li);
    }
    const goalPeers = linkedGoalsForSite(site.slug);
    setSectionVisible("site-panel-goal-links-section", goalPeers.length > 0);
    const goalLinksEl = document.getElementById("site-panel-goal-links");
    goalLinksEl.innerHTML = "";
    for (const peer of goalPeers) {
      const goal = goalBySlug.get(peer);
      const li = document.createElement("li");
      li.textContent = goal ? goal.name : peer;
      goalLinksEl.appendChild(li);
    }
    syncViewshedCheckbox();
  }

  function selectGoal(slug) {
    const goal = goalBySlug.get(slug);
    if (!goal) return;
    if (createMode) cancelCreate();
    selectedSlug = null;
    selectedGoalSlug = slug;
    sitePanel.hidden = false;
    showPanelView();
    renderGoalPanel(goal);
    updateSelectedLayer();
    updateGoalSelectedLayer();
    raiseSiteLayers();
    renderEntityPanel();
  }

  function selectSite(slug) {
    const site = siteBySlug.get(slug);
    if (!site) return;
    if (createMode) cancelCreate();
    selectedGoalSlug = null;
    selectedSlug = slug;
    sitePanel.hidden = false;
    showPanelView();
    renderPanel(site);
    updateSelectedLayer();
    updateGoalSelectedLayer();
    raiseSiteLayers();
    renderEntityPanel();
  }

  function deselectSite() {
    if (createMode) {
      cancelCreate();
      return;
    }
    selectedSlug = null;
    selectedGoalSlug = null;
    sitePanel.hidden = true;
    updateSelectedLayer();
    updateGoalSelectedLayer();
  }

  function wireMapInteractions() {
    const siteLayerIds = [SITES_CIRCLE, SITES_LABELS];
    const goalLayerIds = [GOALS_CIRCLE, GOALS_LABELS];
    map.on("mousemove", () => {
      if (addPlacementMode) map.getCanvas().style.cursor = "crosshair";
    });
    for (const layerId of [...siteLayerIds, ...goalLayerIds]) {
      map.on("mouseenter", layerId, () => {
        if (addPlacementMode) {
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
      const goalFeats = map.queryRenderedFeatures(ev.point, { layers: goalLayerIds });
      if (goalFeats.length) {
        const slug = goalFeats[0].properties && goalFeats[0].properties.slug;
        if (slug) {
          ev.preventDefault();
          if (addPlacementMode) setAddPlacementMode(null);
          selectGoal(slug);
        }
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
    const points = [...sites, ...goals];
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
    addGoalLayers();
    wireMapInteractions();
    void loadSiteLinks();
    void loadGoalLinks();
    loadAllViewsheds();
    updateGoalCountBadge();
    setEntityTab("sites");
    renderEntityPanel();
    applyEntityVisibility();
    const basemapKey = document.getElementById("basemap").value;
    setBasemap(basemapKey);
    if (savedMapState) {
      setSiteLinksVisible(document.getElementById("show-links").checked);
      setGoalLinksVisible(document.getElementById("show-goal-links").checked);
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
  document.getElementById("basemap").addEventListener("change", (ev) => {
    setBasemap(ev.target.value);
    scheduleSaveMapState();
  });
  document.getElementById("show-links").addEventListener("change", (ev) => {
    setSiteLinksVisible(ev.target.checked);
    scheduleSaveMapState();
  });
  document.getElementById("viewshed-opacity").addEventListener("input", (ev) => {
    setViewshedOpacity(Number(ev.target.value) / 100);
    scheduleSaveMapState();
  });
  document.getElementById("show-goal-links").addEventListener("change", (ev) => {
    setGoalLinksVisible(ev.target.checked);
    scheduleSaveMapState();
  });
  sitePanelClose.addEventListener("click", deselectSite);
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
      setEntityPanelOpen(!entityPanelOpen);
    });
  }
  for (const btn of document.querySelectorAll("[data-entity-tab]")) {
    btn.addEventListener("click", () => {
      setEntityTab(btn.getAttribute("data-entity-tab") || "sites");
    });
  }
  if (entityPanelAddSite) {
    entityPanelAddSite.addEventListener("click", () => {
      setEntityPanelOpen(true);
      setEntityTab("sites");
      setAddPlacementMode(addPlacementMode === "site" ? null : "site");
    });
  }
  if (entityPanelAddGoal) {
    entityPanelAddGoal.addEventListener("click", () => {
      setEntityPanelOpen(true);
      setEntityTab("goals");
      setAddPlacementMode(addPlacementMode === "goal" ? null : "goal");
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
    if (selectedSlug) deselectSite();
  });
})();
