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
export function installLinks(scope) {
function linksApiUrl() {
  return `/api/p/${scope.projectSlug}/links`;
}

function siteLinksApiUrl(slug) {
  return `/api/p/${scope.projectSlug}/sites/${encodeURIComponent(slug)}/links`;
}

function linksWarmApiUrl() {
  return `/api/p/${scope.projectSlug}/links/warm`;
}

function warmPrioritiesApiUrl() {
  return `/api/p/${scope.projectSlug}/warm/priorities`;
}

const WARM_PRIORITY_INTERACTIVE = 0;
const WARM_PRIORITY_VIEWPORT = 10;
const WARM_VIEWPORT_SLUG_CAP = 48;
let warmPrioritiesTimer = null;

function warmPrioritySlugsInViewport() {
  const slugs = [];
  for (const site of scope.sites) {
    if (isSiteMapHidden(site.slug) || !scope.isViewshedVisible(site.slug)) continue;
    if (siteVisibleInMap(site)) slugs.push(site.slug);
    if (slugs.length >= WARM_VIEWPORT_SLUG_CAP) break;
  }
  return slugs;
}

function bumpWarmPriorities(slugs, priority) {
  const list = Array.isArray(slugs) ? slugs.filter(Boolean) : [];
  if (!list.length) return;
  fetch(scope.warmPrioritiesApiUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slugs: list, priority }),
  }).catch(() => {
    /* background warm is best-effort */
  });
}

function syncWarmPriorities() {
  if (!scope.mapReady) return;
  const viewport = scope.warmPrioritySlugsInViewport();
  const slugs = new Set(viewport);
  if (scope.selectedSlug && scope.isViewshedVisible(scope.selectedSlug))
    slugs.add(scope.selectedSlug);
  if (!slugs.size) return;
  if (scope.selectedSlug && slugs.has(scope.selectedSlug)) {
    void scope.bumpWarmPriorities([scope.selectedSlug], WARM_PRIORITY_INTERACTIVE);
    slugs.delete(scope.selectedSlug);
  }
  if (slugs.size) {
    void scope.bumpWarmPriorities([...slugs], WARM_PRIORITY_VIEWPORT);
  }
}

function scheduleWarmPrioritiesSync() {
  if (warmPrioritiesTimer) clearTimeout(warmPrioritiesTimer);
  warmPrioritiesTimer = setTimeout(() => {
    warmPrioritiesTimer = null;
    scope.syncWarmPriorities();
  }, 300);
}

function onMapMoveEndForWarmPriorities() {
  if (scope.isMapTiltedView()) return;
  scope.scheduleWarmPrioritiesSync();
}

const WA_DIALOG_WAIT_MS = 5000;


function filterSiteLinksGeoJson(geojson) {
  if (!geojson || !geojson.features) return geojson;
  const chainPairs = scope.seekSessionActive() ? seekChainSitePairKeys() : null;
  const features = geojson.features.filter((feature) => {
    const props = feature.properties || {};
    if (isSiteMapHidden(props.a) || isSiteMapHidden(props.b)) return false;
    if (scope.editMode && scope.editSlug) {
      if (props.a === scope.editSlug || props.b === scope.editSlug) return false;
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
  if (scope.siteLinksPayload && siteLinksPayload.geojson) {
    addSiteLinksLayer(siteLinksPayload.geojson);
  }
}

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
  viewshedPendingEpoch.set(site.slug, scope.viewshedLoadEpoch);
  void tryLoadViewshedFromCache(site.slug);
}

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
  scope.siteLinksPayload = {
    ...base,
    links,
    geojson: { type: "FeatureCollection", features: mergedFeatures },
  };
  addSiteLinksLayer(siteLinksPayload.geojson);
  if (payload.outbound_ready !== false) {
    scope.markSiteOutboundLinksReady(slug);
  }
  if (scope.selectedSlug) renderPanel(siteBySlug.get(scope.selectedSlug));
}

function applySiteLinksPayload(payload) {
  if (!payload) return;
  if (payload.partial && payload.site) {
    mergeSingleSiteLinks(payload.site, payload);
    return;
  }
  const prevFeatures = scope.siteLinksPayload?.geojson?.features;
  const prevCount = Array.isArray(prevFeatures) ? prevFeatures.length : 0;
  const nextFeatures = payload.geojson?.features;
  const nextCount = Array.isArray(nextFeatures) ? nextFeatures.length : 0;
  // Keep showing a ready mesh while a re-warm is pending (e.g. rename bumps
  // scope.config mtime but not link geometry). Empty pending payloads used to wipe
  // all RF lines until the slow warm finished.
  if (
    payload.status === "pending" &&
    scope.siteLinksPayload &&
    siteLinksPayload.status === "ready" &&
    siteLinksPayload.geojson &&
    prevCount > 0
  ) {
    return;
  }
  // Viewshed PNG warms can publish before GPKG footprints exist; do not wipe RF lines.
  if (
    payload.status === "ready" &&
    scope.siteLinksPayload?.status === "ready" &&
    prevCount > 0 &&
    nextCount === 0
  ) {
    return;
  }
  scope.siteLinksPayload = payload;
  if (payload.geojson) addSiteLinksLayer(payload.geojson);
  if (payload.status === "ready" && !payload.partial) {
    for (const site of scope.sites) {
      scope.markSiteOutboundLinksReady(site.slug);
    }
  }
  if (scope.selectedSlug) renderPanel(siteBySlug.get(scope.selectedSlug));


  scope.filterSiteLinksGeoJson = filterSiteLinksGeoJson
  scope.applyViewshedVisibilityForSite = applyViewshedVisibilityForSite
  scope.resetSiteProgress = resetSiteProgress
  scope.sitePinSpinning = sitePinSpinning
  scope.probeViewshedCacheForSite = probeViewshedCacheForSite
}
