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
  const VIEWSHED_RASTER_OPACITY = 0.75;
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

  function syncToolbarFromSaved(saved) {
    if (!saved) return;
    const basemapEl = document.getElementById("basemap");
    if (basemapEl && BASEMAPS[saved.basemap]) basemapEl.value = saved.basemap;
    const linksEl = document.getElementById("show-links");
    if (linksEl && typeof saved.showLinks === "boolean") linksEl.checked = saved.showLinks;
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
  let addSiteMode = false;
  let createMode = false;
  let pendingCreateLat = null;
  let pendingCreateLon = null;
  let draftMarker = null;
  let siteLinksPayload = null;
  const viewshedVisible = new Map();
  const viewshedLoading = new Set();
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
  const addSiteModeBtn = document.getElementById("add-site-mode");
  const siteCountBadge = document.getElementById("site-count-badge");

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
    if (!siteBySlug.has(base)) return base;
    let n = 2;
    while (siteBySlug.has(`${base}-${n}`)) n += 1;
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
    updateSelectedLayer();
    sitePanelView.hidden = true;
    sitePanelCreate.hidden = false;
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
    map.getCanvas().style.cursor = addSiteMode ? "crosshair" : "";
  }

  function setAddSiteMode(enabled) {
    addSiteMode = enabled;
    if (addSiteModeBtn) {
      addSiteModeBtn.classList.toggle("active", enabled);
      addSiteModeBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
    }
    if (mapShell) mapShell.classList.toggle("add-site-mode", enabled);
    syncMapCursor();
    if (!enabled) cancelCreate();
  }

  function updateSiteCountBadge() {
    if (!siteCountBadge) return;
    const n = sites.length;
    siteCountBadge.textContent = `${n} ${n === 1 ? "site" : "sites"}`;
  }

  function registerSite(site) {
    sites.push(site);
    siteBySlug.set(site.slug, site);
    addSiteLayers();
    updateSiteCountBadge();
  }

  function syncCreateSlugPreview() {
    const name = sitePanelCreateName.value;
    sitePanelSlugPreview.textContent = previewSlugForName(name);
  }

  function openCreatePanel(lat, lon) {
    pendingCreateLat = lat;
    pendingCreateLon = lon;
    setCreateError("");
    sitePanelCreateName.value = "";
    sitePanelCreateCoords.textContent = `${formatCoord(lat)}, ${formatCoord(lon)}`;
    syncCreateSlugPreview();
    viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
    syncCreateViewshedCheckbox();
    resetCreatePrefetchUI();
    removeDraftMarker();
    draftMarker = new maplibregl.Marker({ color: "#fbbf24" })
      .setLngLat([lon, lat])
      .addTo(map);
    sitePanel.hidden = false;
    showPanelCreate();
    void loadDraftViewshedAt(lat, lon);
    void loadPlacementPrefetchAt(lat, lon);
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

  function addDraftLinksLayer(geojson) {
    if (!mapReady || !geojson || !geojson.features || !geojson.features.length) {
      removeDraftLinksLayer();
      return;
    }
    const labeled = linksGeoJsonWithLabels(geojson);
    if (map.getSource(DRAFT_LINKS_SOURCE)) {
      map.getSource(DRAFT_LINKS_SOURCE).setData(labeled);
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

  function formatLinkDistanceKm(distanceKm) {
    if (distanceKm == null || Number.isNaN(Number(distanceKm))) return null;
    return `${Number(distanceKm).toFixed(1)} km`;
  }

  function renderCreatePrefetch(payload) {
    const plss = payload.plss || "";
    setSectionVisible("site-panel-create-plss-section", !!plss);
    document.getElementById("site-panel-create-plss").textContent = plss || "—";
    const mlrs = payload.mlrs || "";
    setSectionVisible("site-panel-create-mlrs-section", !!mlrs);
    document.getElementById("site-panel-create-mlrs").textContent = mlrs || "—";
    const links = Array.isArray(payload.links) ? payload.links : [];
    const linked = links.filter((row) => row.linked);
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

  async function saveNewSite() {
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
      setAddSiteMode(false);
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
    if (!geojson || !geojson.features || !geojson.features.length) return;
    const labeled = linksGeoJsonWithLabels(geojson);
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
      map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
    } else if (visible) {
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
          "raster-opacity": VIEWSHED_RASTER_OPACITY,
          "raster-fade-duration": 0,
        },
      });
    }
    if (!isViewshedVisible(vs.slug)) {
      map.setLayoutProperty(layerId, "visibility", "none");
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

  function addSiteLayers() {
    if (map.getSource(SITES_SOURCE)) {
      map.getSource(SITES_SOURCE).setData(sitesGeoJson());
      updateSelectedLayer();
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
  }

  function updateSelectedLayer() {
    if (!map.getLayer(SITES_SELECTED)) return;
    map.setFilter(SITES_SELECTED, ["==", ["get", "slug"], selectedSlug || ""]);
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

  function renderPanel(site) {
    if (!site) return;
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
    syncViewshedCheckbox();
  }

  function selectSite(slug) {
    const site = siteBySlug.get(slug);
    if (!site) return;
    if (createMode) cancelCreate();
    selectedSlug = slug;
    sitePanel.hidden = false;
    showPanelView();
    renderPanel(site);
    updateSelectedLayer();
    raiseSiteLayers();
  }

  function deselectSite() {
    if (createMode) {
      cancelCreate();
      return;
    }
    selectedSlug = null;
    sitePanel.hidden = true;
    updateSelectedLayer();
  }

  function wireSiteInteractions() {
    const siteLayerIds = [SITES_CIRCLE, SITES_LABELS];
    map.on("mousemove", () => {
      if (addSiteMode) map.getCanvas().style.cursor = "crosshair";
    });
    for (const layerId of siteLayerIds) {
      map.on("mouseenter", layerId, () => {
        if (addSiteMode) {
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
      const feats = map.queryRenderedFeatures(ev.point, { layers: siteLayerIds });
      if (feats.length) {
        const slug = feats[0].properties && feats[0].properties.slug;
        if (slug) {
          ev.preventDefault();
          if (addSiteMode) setAddSiteMode(false);
          selectSite(slug);
        }
        return;
      }
      if (addSiteMode) {
        openCreatePanel(ev.lngLat.lat, ev.lngLat.lng);
        return;
      }
      deselectSite();
    });
  }

  function fitSites() {
    if (!sites.length) return;
    const lons = sites.map((site) => site.lon);
    const lats = sites.map((site) => site.lat);
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
    wireSiteInteractions();
    void loadSiteLinks();
    loadAllViewsheds();
    const basemapKey = document.getElementById("basemap").value;
    setBasemap(basemapKey);
    if (savedMapState) {
      setSiteLinksVisible(document.getElementById("show-links").checked);
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
  sitePanelClose.addEventListener("click", deselectSite);
  sitePanelCreateClose.addEventListener("click", cancelCreate);
  sitePanelCreateCancel.addEventListener("click", cancelCreate);
  sitePanelCreateSave.addEventListener("click", () => {
    void saveNewSite();
  });
  sitePanelCreateName.addEventListener("input", syncCreateSlugPreview);
  sitePanelCreateName.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      void saveNewSite();
    }
  });
  if (addSiteModeBtn) {
    addSiteModeBtn.addEventListener("click", () => {
      setAddSiteMode(!addSiteMode);
    });
  }
  sitePanelViewshed.addEventListener("change", (ev) => {
    if (selectedSlug) setViewshedVisible(selectedSlug, ev.target.checked);
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
