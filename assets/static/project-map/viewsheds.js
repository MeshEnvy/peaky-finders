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
export function installViewsheds(scope) {
function applyViewshedVisibilityForSite(slug) {
  const layerId = viewshedLayerId(slug);
  if (!map.getLayer(layerId)) return;
  const visible = !isSiteMapHidden(slug) && scope.isViewshedVisible(slug);
  map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
}

/** Warm a viewshed that was skipped while the site was hidden/filtered. */
function ensureViewshedLoadedForSlug(slug) {
  if (isSiteMapHidden(slug) || !scope.isViewshedVisible(slug)) return;
  if (map.getLayer(viewshedLayerId(slug))) return;
  if (sitePinSpinning(slug) || viewshedPendingEpoch.has(slug)) return;
  if (slug === DRAFT_VIEWSHED_SLUG) {
    const lat = draftPlacementLat ?? pendingCreateLat;
    const lon = draftPlacementLon ?? pendingCreateLon;
    if (lat != null && lon != null) void scope.loadDraftViewshedAt(lat, lon);
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
  if (slug === scope.selectedSlug) syncViewshedCheckbox();
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
  if (!scope.isViewshedVisible(slug)) return false;
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
  for (const site of scope.sites) {
    if (isSiteMapHidden(site.slug) || !scope.isViewshedVisible(site.slug)) continue;
    if (map.getLayer(viewshedLayerId(site.slug))) continue;
    if (sitePinSpinning(site.slug) || viewshedPendingEpoch.has(site.slug))

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
  if (!scope.mapReady) return;
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
  scope.raiseSiteLayers();
}

function hideTerrainOverlays() {
  map.setTerrain(null);
  removeTerrainSource();
  if (map.getLayer("basemap")) {
    map.setPaintProperty("basemap", "raster-opacity", 1);
  }
}

function syncTerrainFromPitch() {
  if (!scope.mapReady) return;
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
  scope.raiseSiteLayers();
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
  if (!scope.mapReady) return;
  for (const slug of viewshedOverlaySlugs()) {
    const layerId = viewshedLayerId(slug);
    if (map.getLayer(layerId)) {
      map.setPaintProperty(layerId, "raster-opacity", scope.viewshedOpacity);
    }
  }
}

function setViewshedOpacity(opacity) {
  scope.viewshedOpacity = Math.max(0, Math.min(1, opacity));
  syncOpacitySlider();
  applyViewshedOpacityToAllLayers();
}

function viewshedSimQueryParams() {
  const params = new URLSearchParams();
  params.set("radius_km", String(scope.viewshedRadiusKm));
  params.set("quality", String(scope.viewshedQuality));
  return params;
}

function viewshedPreviewSimQueryParams() {
  const params = new URLSearchParams();
  params.set("radius_km", String(scope.viewshedRadiusKm));
  params.set("quality", String(VIEWSHED_PREVIEW_QUALITY));
  return params;
}

function projectEventsUrl() {
  return `/api/p/${scope.projectSlug}/events`;
}

function viewshedWarmUrl(siteSlug) {
  const params = viewshedSimQueryParams();
  return `/api/p/${scope.projectSlug}/viewsheds/${siteSlug}/warm?${params}`;
}

function viewshedPrefetchWarmUrl(lat, lon, { preview = true } = {}) {
  const params = preview
    ? viewshedPreviewSimQueryParams()
    : viewshedSimQueryParams();
  params.set("lat", String(lat));
  params.set("lon", String(lon));
  return `/api/p/${scope.projectSlug}/viewsheds/prefetch/warm?${params}`;
}

let serveEventsSource = null;
const viewshedPendingEpoch = new Map();

function reconcilePendingViewsheds() {
  scope.syncWarmPriorities();
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
      scope.applySiteLinksPayload(data);
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
  scope.markSiteViewshedReady(vs.slug);
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
    scope.markSiteOutboundLinksReady(vs.slug);
  }
}

function acceptViewshedOverlay(vs) {
  if (!vs || !vs.slug || !vs.url || !vs.coordinates) return;
  if (isSiteMapHidden(vs.slug) || !scope.isViewshedVisible(vs.slug)) return;
  addViewshedLayer(vs);
  finalizeViewshedReady(vs);
  if (!isEphemeralViewshedSlug(vs.slug)) {
    void loadSingleSiteLinks(vs.slug);
  } else {
    scope.markSiteOutboundLinksReady(vs.slug);
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
    if (data.slug === scope.selectedSlug) syncViewshedCheckbox();
  }
}

function updatePinOverlays() {
  if (!scope.mapReady) return;
  try {
    const active = new Set();
    for (const site of scope.sites) {
      if (!sitePinSpinning(site.slug)) continue;
      if (!scope.coordsUsableForMarker(site.lon, site.lat)) continue;
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
      if (!scope.coordsUsableForMarker(entry.lon, entry.lat)) continue;
      active.add(slug);
      renderPinLoadOverlay(slug, entry.lon, entry.lat);
    }
    for (const slug of seekHopCoordViewshedSlugs) {
      if (!viewshedLoading.has(slug)) continue;
      const coords = seekHopCoordViewshedCoords.get(slug);
      if (!coords || !scope.coordsUsableForMarker(coords.lon, coords.lat)) continue;
      active.add(slug);
      renderPinLoadOverlay(slug, coords.lon, coords.lat);
    }
    if (seekScanning && scope.seekSessionActive()) {
      const from = scope.seekCurrentFrom();
      if (from && scope.coordsUsableForMarker(from.lon, from.lat)) {
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
    if (!scope.mapReady) {
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
    return `/api/p/${scope.projectSlug}/viewsheds/prefetch?${params}`;
  }
  return `/api/p/${scope.projectSlug}/viewsheds/${siteSlug}?${params}`;
}

function viewshedPrefetchMetaUrl(lat, lon) {
  const params = viewshedPreviewSimQueryParams();
  params.set("lat", String(lat));
  params.set("lon", String(lon));
  return `/api/p/${scope.projectSlug}/viewsheds/prefetch?${params}`;
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
  if (slug === scope.selectedSlug) syncViewshedCheckbox();
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
  return `/api/p/${scope.projectSlug}/viewsheds/index?${params}`;
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
    for (const site of scope.sites) {
      if (isSiteMapHidden(site.slug) || !scope.isViewshedVisible(site.slug))
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
    if (missing.length) scope.syncWarmPriorities();
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
  if (site.slug === scope.selectedSlug) syncViewshedCheckbox();
  const priority =
    site.slug === scope.selectedSlug
      ? WARM_PRIORITY_INTERACTIVE
      : WARM_PRIORITY_VIEWPORT;
  void scope.bumpWarmPriorities([site.slug], priority);
  void tryLoadViewshedFromCache(site.slug);
}

function reloadViewshedsForSimChange() {
  bumpViewshedLoadEpoch();
  for (const site of scope.sites) {
    resetSiteProgress(site.slug);
    if (!isSiteMapHidden(site.slug) && scope.isViewshedVisible(site.slug)) {
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
    scope.createMode &&
    draftPlacementLat != null &&
    draftPlacementLon != null &&
    scope.isViewshedVisible(DRAFT_VIEWSHED_SLUG)
  ) {
    void scope.loadDraftViewshedAt(draftPlacementLat, draftPlacementLon, {
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
  return `/api/p/${scope.projectSlug}/sites/prefetch?${params}`;
}

function filterEditSitePrefetchPayload(payload) {
  if (!payload || !scope.editSlug) return payload;
  const links = Array.isArray(payload.links)
    ? payload.links.filter((row) => row.slug !== scope.editSlug)
    : payload.links;
  let linksGeojson = payload.links_geojson;
  if (linksGeojson && Array.isArray(linksGeojson.features)) {
    linksGeojson = {
      ...linksGeojson,
      features: linksGeojson.features.filter(
        (feature) => (feature.properties || {}).slug !== scope.editSlug,
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
    if (scope.editSlug && props.slug === scope.editSlug) return false;
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
  if (!sitePanelCreateViewshed || !scope.createMode) return;
  sitePanelCreateViewshed.checked = scope.isViewshedVisible(DRAFT_VIEWSHED_SLUG);
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
    /* scope.map may be mid-resize */
  }
  viewshedPendingEpoch.delete(DRAFT_VIEWSHED_SLUG);
  viewshedLoading.delete(DRAFT_VIEWSHED_SLUG);
  draftViewshedLoading = false;
  draftPlacementLat = null;
  draftPlacementLon = null;
  updatePinOverlays();
}

function applySavedSiteToMap(site, fallbackLat, fallbackLon) {
  const row = scope.normalizeSiteFromApi({
    ...site,
    lat: site.lat ?? fallbackLat,
    lon: site.lon ?? fallbackLon,
  });
  if (!row) return false;
  scope.registerSite(row);
  viewshedVisible.set(row.slug, true);
  scheduleViewshedLoad(row);
  void loadSiteLinks();
  scope.selectSite(row.slug);
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
  const visible = scope.isViewshedVisible(slug);
  const hidden = isSiteHidden(slug);
  btn.disabled = hidden;
  btn.setAttribute("aria-pressed", visible ? "true" : "false");
  btn.classList.toggle("entity-panel__action--active", visible);
}

function syncSitePanelViewshedToggle(slug) {
  if (!sitePanelViewshedToggle || slug !== scope.selectedSlug) return;
  if (sitePanelView && sitePanelView.hidden) return;
  const visible = scope.isViewshedVisible(slug);
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

/** Mirror scope.viewshedVisible state into every checkbox bound to this site slug. */
function syncViewshedUiForSlug(slug) {
  if (!slug) return;
  syncEntityPanelViewshedToggle(slug);
  syncSitePanelViewshedToggle(slug);
  if (scope.editMode && scope.editSlug === slug) syncEditViewshedCheckbox();
  updatePinOverlays();
}

function setViewshedVisible(slug, visible) {
  viewshedVisible.set(slug, visible);
  if (map.getLayer(viewshedLayerId(slug))) {
    applyViewshedVisibilityForSite(slug);
  } else if (visible) {
    scope.ensureViewshedLoadedForSlug(slug);
  }
  syncViewshedUiForSlug(slug);
  if (slug === DRAFT_VIEWSHED_SLUG) {
    syncCreateViewshedCheckbox();
    if (scope.editMode) syncEditViewshedCheckbox();
  }
}

function syncViewshedCheckbox() {
  if (!scope.selectedSlug) return;
  syncViewshedUiForSlug(scope.selectedSlug);
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
            "raster-opacity": scope.viewshedOpacity,
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
          "raster-opacity": scope.viewshedOpacity,
          "raster-fade-duration": 0,
        },
      },
      viewshedLayerInsertBefore(),
    );
  }
  if (!scope.isViewshedVisible(vs.slug) || isSiteMapHidden(vs.slug)) {
    map.setLayoutProperty(layerId, "visibility", "none");
  } else {
    map.setLayoutProperty(layerId, "visibility", "visible");
  }
  viewshedLoading.delete(vs.slug);
  updatePinOverlays();
  if (vs.slug === scope.selectedSlug) syncViewshedCheckbox();
  if (vs.slug === DRAFT_VIEWSHED_SLUG) {
    draftViewshedLoading = false;
    syncCreateViewshedCheckbox();
    syncEditViewshedCheckbox();
    if (draftPlacementLat != null && draftPlacementLon != null) {
      void loadPlacementPrefetchAt(draftPlacementLat, draftPlacementLon);
    }
  }
  scope.raiseSiteLayers();


  scope.applyViewshedVisibilityForSite = applyViewshedVisibilityForSite
  scope.resetSiteProgress = resetSiteProgress
  scope.sitePinSpinning = sitePinSpinning
  scope.probeViewshedCacheForSite = probeViewshedCacheForSite
  scope.ensureViewshedsForNewlyVisibleSites = ensureViewshedsForNewlyVisibleSites
  scope.viewshedOverlaySlugs = viewshedOverlaySlugs
  scope.viewshedLayerInsertBefore = viewshedLayerInsertBefore
  scope.raiseViewshedLayers = raiseViewshedLayers
  scope.basemapRasterOpacityForTerrain = basemapRasterOpacityForTerrain
  scope.showTerrainOverlays = showTerrainOverlays
  scope.hideTerrainOverlays = hideTerrainOverlays
  scope.syncTerrainFromPitch = syncTerrainFromPitch
  scope.ensureBasemapReference = ensureBasemapReference
  scope.removeBasemapReference = removeBasemapReference
  scope.viewshedSourceId = viewshedSourceId
  scope.viewshedLayerId = viewshedLayerId
  scope.applyViewshedOpacityToAllLayers = applyViewshedOpacityToAllLayers
  scope.setViewshedOpacity = setViewshedOpacity
  scope.viewshedSimQueryParams = viewshedSimQueryParams
  scope.viewshedPreviewSimQueryParams = viewshedPreviewSimQueryParams
  scope.projectEventsUrl = projectEventsUrl
  scope.viewshedWarmUrl = viewshedWarmUrl
  scope.viewshedPrefetchWarmUrl = viewshedPrefetchWarmUrl
  scope.reconcilePendingViewsheds = reconcilePendingViewsheds
  scope.connectProjectEvents = connectProjectEvents
  scope.viewshedAtTarget = viewshedAtTarget
  scope.finalizeViewshedReady = finalizeViewshedReady
  scope.handleViewshedReady = handleViewshedReady
  scope.acceptViewshedOverlay = acceptViewshedOverlay
  scope.routeDraftViewshedToSeekHops = routeDraftViewshedToSeekHops
  scope.handleViewshedEvent = handleViewshedEvent
  scope.updatePinOverlays = updatePinOverlays
  scope.waitForMapIdle = waitForMapIdle
  scope.applyViewshedOverlaysBatched = applyViewshedOverlaysBatched
  scope.bumpViewshedLoadEpoch = bumpViewshedLoadEpoch
  scope.viewshedMetaUrl = viewshedMetaUrl
  scope.viewshedPrefetchMetaUrl = viewshedPrefetchMetaUrl
  scope.clearViewshedLoadingState = clearViewshedLoadingState
  scope.tryLoadViewshedFromCache = tryLoadViewshedFromCache
  scope.viewshedIndexUrl = viewshedIndexUrl
  scope.fetchOutboundLinksParallel = fetchOutboundLinksParallel
  scope.worker = worker
  scope.loadViewshedIndex = loadViewshedIndex
  scope.scheduleViewshedLoad = scheduleViewshedLoad
  scope.sitesPrefetchUrl = sitesPrefetchUrl
  scope.filterEditSitePrefetchPayload = filterEditSitePrefetchPayload
  scope.editSiteCopyCoords = editSiteCopyCoords
  scope.filterHistoryEntryLinksGeojson = filterHistoryEntryLinksGeojson
  scope.syncCreateViewshedCheckbox = syncCreateViewshedCheckbox
  scope.removeDraftViewshed = removeDraftViewshed
  scope.loadPlacementPrefetchAt = loadPlacementPrefetchAt
  scope.clearDraftViewshedLoading = clearDraftViewshedLoading
  scope.tryLoadCoordViewshedFromCache = tryLoadCoordViewshedFromCache
  scope.tryLoadDraftViewshedFromCache = tryLoadDraftViewshedFromCache
  scope.syncEntityPanelViewshedToggle = syncEntityPanelViewshedToggle
  scope.syncSitePanelViewshedToggle = syncSitePanelViewshedToggle
  scope.syncViewshedUiForSlug = syncViewshedUiForSlug
  scope.syncViewshedCheckbox = syncViewshedCheckbox
  scope.addViewshedLayer = addViewshedLayer
}
