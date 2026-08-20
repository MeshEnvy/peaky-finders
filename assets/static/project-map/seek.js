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
export function installSeek(scope) {
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
  const goalBearing = scope.bearingDeg(from.lat, from.lon, goal.lat, goal.lon);
  const steps = 36;
  const ring = [[from.lon, from.lat]];
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps;
    const d = t * hopRadiusM;
    const half = scope.seekWedgeHalfAngleDeg(d, hopRadiusM);
    const [lat, lon] = scope.destinationPointLatLon(
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
    const half = scope.seekWedgeHalfAngleDeg(d, hopRadiusM);
    const [lat, lon] = scope.destinationPointLatLon(
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
    scope.haversineMeters(from.lat, from.lon, goal.lat, goal.lon) / 1000;
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
      bearing_deg: Math.round(scope.bearingDeg(from.lat, from.lon, goal.lat, goal.lon)),
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
  if (!scope.mapReady || !scope.seekSessionActive()) {
    scope.removeSeekWedgeLayers();
    return;
  }
  const from = scope.seekCurrentFrom();
  const goal = scope.seekGoalCoords();
  if (!from || !goal) {
    scope.removeSeekWedgeLayers();
    return;
  }
  const data = {
    type: "FeatureCollection",
    features: [scope.buildSeekWedgeFeature(from, goal, scope.seekHopRadiusM())],
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
  if (!scope.mapReady) return;
  if (!scope.seekSessionActive()) {
    scope.removeSeekGoalLineLayer();
    scope.removeSeekWedgeLayers();
    return;
  }
  const from = scope.seekCurrentFrom();
  const goal = scope.seekGoalCoords();
  if (!from || !goal) {
    scope.removeSeekGoalLineLayer();
    scope.removeSeekWedgeLayers();
    return;
  }
  const data = {
    type: "FeatureCollection",
    features: [scope.buildSeekGoalLineFeature(from, goal)],
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
  scope.syncSeekWedge();
  scope.raiseSiteLayers();
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
  const goal = scope.seekGoalCoords();
  if (seekGoalCoordsEl) {
    seekGoalCoordsEl.textContent = goal
      ? `${scope.formatCoord(goal.lat)}, ${scope.formatCoord(goal.lon)}`
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
  if (!scope.mapReady) return;
  const goal = scope.seekGoalCoords();
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
  seekGoalMarker.setLngLat([goal.lon, goal.lat]).addTo(scope.map);
}

function setSeekGoalPlacementMode(active) {
  seekGoalPlacementMode = active;
  if (mapShell) mapShell.classList.toggle("seek-goal-placement-mode", active);
  if (seekSetGoalBtn) {
    seekSetGoalBtn.setAttribute("aria-pressed", active ? "true" : "false");
    seekSetGoalBtn.classList.toggle("active", active);
  }
  scope.syncMapCursor();
  if (active) {
    setSeekStatus("Click the map to set goal");
  } else if (seekPanelOpen && !scope.seekSessionActive()) {
    const goal = scope.seekGoalCoords();
    if (!goal) setSeekStatus("Pick a start site and set goal on the map");
    else setSeekStatus("Pick a start site to begin");
  }
}

function setSeekGoalAt(lat, lon, { refresh = true } = {}) {
  const prev = scope.seekGoalCoords();
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
  if (refresh && scope.seekSessionActive()) {
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
    const raw = localStorage.getItem(scope.SEEK_REDO_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function saveSeekRedoStack() {
  if (!seekState?.redoStack?.length) {
    localStorage.removeItem(scope.SEEK_REDO_KEY);
    return;
  }
  localStorage.setItem(scope.SEEK_REDO_KEY, JSON.stringify(seekState.redoStack));
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
      const resp = await fetch(`/api/p/${scope.projectSlug}/seek/plan`, {
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
    const resp = await fetch(`/api/p/${scope.projectSlug}/seek/plan`, {
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
    const raw = localStorage.getItem(scope.SEEK_STATE_KEY);
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
    localStorage.removeItem(scope.SEEK_STATE_KEY);
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
    localStorage.removeItem(scope.SEEK_STATE_KEY);
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
      resp = await fetch(`/api/p/${scope.projectSlug}/seek/scan-progress`, {
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
  const known = scope.allProjectTags();
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
  for (const tag of scope.allProjectTags()) {
    if (selected.has(tag)) continue;
    const opt = document.createElement("option");
    opt.value = tag;
    seekConvertTagSuggestions.appendChild(opt);
  }
}

function maxExistingPrefixedSiteNumber(prefix) {
  const p = String(prefix || "").trim();
  if (!p) return 0;
  const slugBase = scope.slugifyName(p);
  let max = 0;
  for (const site of scope.sites) {
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
      `/api/p/${scope.projectSlug}/seek/plan/convert-to-sites`,
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
      scope.registerSite(site);
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
      scope.renderEntityPanel();
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
    if (imported[0]?.slug) scope.selectSite(imported[0].slug);
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
  const goal = scope.seekGoalCoords();
  if (!startSite || !goal) {
    if (!goal) setSeekStatus("Set goal on the map, then pick start site");
    return;
  }
  if (
    scope.haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
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
  const eligible = scope.sites
    .filter(
      (site) =>
        scope.sitePassesTagFilter(site) || tagFilterBypassSlugs.has(site.slug),
    )
    .slice()
    .sort((a, b) => scope.compareHuman(a.name, b.name));
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
  if (!scope.seekSessionActive() || seekState?.complete) return;
  setSeekStatus(seekPanHintText());
}

function toggleSeekPanel(force) {
  const nextOpen = typeof force === "boolean" ? force : !seekPanelOpen;
  if (seekPanelOpen && !nextOpen) {
    setSeekGoalPlacementMode(false);
    if (seekScanning) {
      cancelSeekScanUi();
      if (scope.seekSessionActive()) {
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
  if (!scope.mapReady || !payload) return;
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
  if (scope.seekSessionActive() && scope.siteLinksPayload?.geojson) {
    scope.refreshFilteredLinks();
  }
  scope.raiseSiteLayers();
}

function seekViewportBbox() {
  const b = scope.seekScanBoundsForRequest();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(6))
    .join(",");
}

/** Peak bin size (m) from map zoom: ~20 bins across viewport width, clamped 500–1500 m. */
function seekPeakBinSizeM() {
  return scope.seekPeakBinSizeMForBounds(scope.seekScanBoundsForRequest());
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
  if (!scope.seekSessionActive() || !hops || hops.length < 2) return keys;
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
  for (const site of scope.sites) {
    const dist = scope.haversineMeters(lat, lon, site.lat, site.lon);
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
      scope.haversineMeters(from.lat, from.lon, site.lat, site.lon) <=
        SEEK_GOAL_SAME_AS_START_M
    ) {
      return slug;
    }
  }
  return seekSiteSlugNear(from.lat, from.lon);
}

function seekCoordsNearGoal(lat, lon) {
  const goal = scope.seekGoalCoords();
  if (!goal) return false;
  return (
    scope.haversineMeters(lat, lon, goal.lat, goal.lon) <= SEEK_GOAL_SAME_AS_START_M
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
  const epoch = scope.viewshedLoadEpoch;
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
  if (!scope.mapReady || !seekState?.running || !Array.isArray(seekState.hops)) {
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
      scope.ensureViewshedLoadedForSlug(siteSlug);
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
  const features = scope.siteLinksPayload?.geojson?.features;
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
  const hasLinks = (scope.siteLinksPayload?.links || []).some(
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
  if (!scope.mapReady || !features.length) {
    removeSeekAncillaryLinksLayer();
    return;
  }
  const labeled = linksGeoJsonWithLabels({
    type: "FeatureCollection",
    features,
  });
  if (map.getSource(SEEK_ANCILLARY_LINES_SOURCE)) {
    map.getSource(SEEK_ANCILLARY_LINES_SOURCE).setData(labeled);
    scope.raiseSiteLayers();
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
  scope.raiseSiteLayers();
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
    if (scope.seekSessionActive() && hopIndex === hops.length - 1) continue;

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
    !scope.mapReady ||
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
            .addTo(scope.map),
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
        .addTo(scope.map),
    );
  });
}

function updateSeekPathOverlay() {
  syncSeekHopViewsheds();
  if (
    !scope.mapReady ||
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
  if (scope.seekSessionActive() && scope.siteLinksPayload?.geojson) {
    scope.refreshFilteredLinks();
  }
  scope.raiseSiteLayers();
}

function seekCurrentFrom() {
  if (seekState?.currentFrom) return seekState.currentFrom;
  const startSlug = seekState?.startSlug || seekStartSelect?.value;
  const startSite = startSlug ? siteBySlug.get(startSlug) : null;
  if (!startSite) return null;
  return { lat: startSite.lat, lon: startSite.lon };
}

function seekFetchParamsKey() {
  if (!scope.seekSessionActive()) return null;
  const from = scope.seekCurrentFrom();
  const goal = scope.seekGoalCoords();
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
  if (scope.isMapTiltedView()) {
    statusText += " · scanning hop range in goal wedge";
  }
  setSeekStatus(statusText);
  resetSeekViewshedRetries();
}

async function warmDraftViewshedForSeek(lat, lon, signal) {
  const siteSlug = seekSiteSlugForFrom({ lat, lon });
  if (siteSlug) {
    scope.setViewshedVisible(DRAFT_VIEWSHED_SLUG, false);
    removeViewshedLayer(DRAFT_VIEWSHED_SLUG);
    viewshedVisible.set(siteSlug, true);
    scope.ensureViewshedLoadedForSlug(siteSlug);
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
        scope.viewshedLoadEpoch,
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
  if (!scope.seekSessionActive() || !scope.mapReady) return;
  const from = scope.seekCurrentFrom();
  const goal = scope.seekGoalCoords();
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
      `/api/p/${scope.projectSlug}/seek/candidates?${params}`,
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
  const showRefresh = Boolean(scope.seekSessionActive() && !seekState?.complete);
  if (seekRefreshBtn) {
    seekRefreshBtn.hidden = !showRefresh;
    seekRefreshBtn.disabled = seekScanning || !scope.seekSessionActive();
  }
}

function startSeekRun() {
  const startSlug = seekStartSelect?.value;
  const goal = scope.seekGoalCoords();
  if (!startSlug || !goal) {
    setSeekStatus("Pick a start site and set goal on the map");
    return;
  }
  const startSite = siteBySlug.get(startSlug);
  if (!startSite) return;
  if (
    scope.haversineMeters(startSite.lat, startSite.lon, goal.lat, goal.lon) <=
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
  if (!scope.seekSessionActive() || !feature?.geometry?.coordinates) return;
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
  scope.setViewshedVisible(DRAFT_VIEWSHED_SLUG, false);
  setSeekStatus("");
  syncSeekPanelUi();
  if (scope.siteLinksPayload?.geojson) scope.refreshFilteredLinks();
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


  scope.syncSeekGoalLine = syncSeekGoalLine
  scope.abortSeekInFlight = abortSeekInFlight
  scope.invalidateSeekFetch = invalidateSeekFetch
  scope.cancelSeekScanUi = cancelSeekScanUi
  scope.beginSeekFetch = beginSeekFetch
  scope.seekGoalFromState = seekGoalFromState
  scope.migrateSeekStateGoal = migrateSeekStateGoal
  scope.updateSeekGoalCoordsDisplay = updateSeekGoalCoordsDisplay
  scope.removeSeekGoalMarker = removeSeekGoalMarker
  scope.syncSeekGoalMarker = syncSeekGoalMarker
  scope.setSeekGoalPlacementMode = setSeekGoalPlacementMode
  scope.setSeekGoalAt = setSeekGoalAt
  scope.syncSeekGoalUi = syncSeekGoalUi
  scope.loadSeekRedoStack = loadSeekRedoStack
  scope.saveSeekRedoStack = saveSeekRedoStack
  scope.seekStateFromYamlPlan = seekStateFromYamlPlan
  scope.seekStateToYamlPlan = seekStateToYamlPlan
  scope.flushSeekPlanToYaml = flushSeekPlanToYaml
  scope.persistSeekPlanToYaml = persistSeekPlanToYaml
  scope.loadSeekStateFromLocalStorage = loadSeekStateFromLocalStorage
  scope.rehydrateSeekStateFromConfig = rehydrateSeekStateFromConfig
  scope.initSeekState = initSeekState
  scope.loadSeekState = loadSeekState
  scope.clearSeekRedoStack = clearSeekRedoStack
  scope.saveSeekState = saveSeekState
  scope.setSeekStatus = setSeekStatus
  scope.updateSeekProgressUi = updateSeekProgressUi
  scope.startSeekProgressTick = startSeekProgressTick
  scope.stopSeekProgressTick = stopSeekProgressTick
  scope.stopSeekProgressPoll = stopSeekProgressPoll
  scope.stopSeekProgressUi = stopSeekProgressUi
  scope.sleepMs = sleepMs
  scope.pollSeekUntilDone = pollSeekUntilDone
  scope.setSeekScanning = setSeekScanning
  scope.syncSeekPanelUi = syncSeekPanelUi
  scope.countSeekLocHops = countSeekLocHops
  scope.countSeekUniqueLocHops = countSeekUniqueLocHops
  scope.syncSeekConvertSitesBtn = syncSeekConvertSitesBtn
  scope.setSeekConvertError = setSeekConvertError
  scope.effectiveSeekConvertDraftTags = effectiveSeekConvertDraftTags
  scope.syncSeekConvertSaveButton = syncSeekConvertSaveButton
  scope.renderSeekConvertTags = renderSeekConvertTags
  scope.syncSeekConvertTagSuggestions = syncSeekConvertTagSuggestions
  scope.maxExistingPrefixedSiteNumber = maxExistingPrefixedSiteNumber
  scope.seekConvertNamePreview = seekConvertNamePreview
  scope.syncSeekConvertHopCountText = syncSeekConvertHopCountText
  scope.resetSeekConvertModal = resetSeekConvertModal
  scope.openSeekConvertModal = openSeekConvertModal
  scope.closeSeekConvertModal = closeSeekConvertModal
  scope.addSeekConvertTagFromInput = addSeekConvertTagFromInput
  scope.saveSeekConvertModal = saveSeekConvertModal
  scope.maybeAutoStartSeekFromSelects = maybeAutoStartSeekFromSelects
  scope.populateSeekStartSelect = populateSeekStartSelect
  scope.refreshSeekStartSelectIfOpen = refreshSeekStartSelectIfOpen
  scope.seekPanHintText = seekPanHintText
  scope.promptSeekManualRecalc = promptSeekManualRecalc
  scope.toggleSeekPanel = toggleSeekPanel
  scope.clearSeekMarkers = clearSeekMarkers
  scope.removeSeekCandidateLayers = removeSeekCandidateLayers
  scope.removeSeekLayers = removeSeekLayers
  scope.seekLinesGeoJsonWithLabels = seekLinesGeoJsonWithLabels
  scope.seekCandidatesGeoJsonForDisplay = seekCandidatesGeoJsonForDisplay
  scope.seekRfViable = seekRfViable
  scope.seekSiteCandidateSlugsFromPayload = seekSiteCandidateSlugsFromPayload
  scope.applySeekLayers = applySeekLayers
  scope.seekViewportBbox = seekViewportBbox
  scope.seekPeakBinSizeM = seekPeakBinSizeM
  scope.seekExcludeParam = seekExcludeParam
  scope.seekExcludeSlugsParam = seekExcludeSlugsParam
  scope.seekPathSiteSlugs = seekPathSiteSlugs
  scope.seekHopSiteSlug = seekHopSiteSlug
  scope.seekChainSitePairKeys = seekChainSitePairKeys
  scope.seekSiteSlugNear = seekSiteSlugNear
  scope.seekSiteSlugForFrom = seekSiteSlugForFrom
  scope.seekCoordsNearGoal = seekCoordsNearGoal
  scope.seekLineFeatureIsRedundant = seekLineFeatureIsRedundant
  scope.filterSeekLineFeatures = filterSeekLineFeatures
  scope.filterSeekCandidateFeatures = filterSeekCandidateFeatures
  scope.isSiteInSeekPlan = isSiteInSeekPlan
  scope.seekHopCoordViewshedSlug = seekHopCoordViewshedSlug
  scope.cancelSeekHopViewshedLoad = cancelSeekHopViewshedLoad
  scope.clearSeekHopViewshed = clearSeekHopViewshed
  scope.clearAllSeekHopViewsheds = clearAllSeekHopViewsheds
  scope.loadSeekHopCoordViewshed = loadSeekHopCoordViewshed
  scope.syncSeekHopViewsheds = syncSeekHopViewsheds
  scope.seekHopEndpointKey = seekHopEndpointKey
  scope.seekChainNeighborKeys = seekChainNeighborKeys
  scope.canonicalSitePairKey = canonicalSitePairKey
  scope.findSiteLinkFeature = findSiteLinkFeature
  scope.ensureSiteLinksForSlug = ensureSiteLinksForSlug
  scope.removeSeekAncillaryLinksLayer = removeSeekAncillaryLinksLayer
  scope.seekAncillaryLinkFeatureVisible = seekAncillaryLinkFeatureVisible
  scope.refreshSeekAncillaryLinksDisplay = refreshSeekAncillaryLinksDisplay
  scope.addSeekAncillaryLinksLayer = addSeekAncillaryLinksLayer
  scope.cancelSeekAncillaryLinksFetch = cancelSeekAncillaryLinksFetch
  scope.collectSeekAncillaryLinkFeatures = collectSeekAncillaryLinkFeatures
  scope.flushSeekAncillaryLinks = flushSeekAncillaryLinks
  scope.scheduleSeekAncillaryLinks = scheduleSeekAncillaryLinks
  scope.appendSeekHopMarkers = appendSeekHopMarkers
  scope.updateSeekPathOverlay = updateSeekPathOverlay
  scope.seekFetchParamsKey = seekFetchParamsKey
  scope.applySeekCandidatePayload = applySeekCandidatePayload
  scope.warmDraftViewshedForSeek = warmDraftViewshedForSeek
  scope.resetSeekViewshedRetries = resetSeekViewshedRetries
  scope.scheduleSeekViewshedRetry = scheduleSeekViewshedRetry
  scope.refreshSeekCandidates = refreshSeekCandidates
  scope.onMapMoveEndForSeek = onMapMoveEndForSeek
  scope.syncSeekRefreshUi = syncSeekRefreshUi
  scope.startSeekRun = startSeekRun
  scope.commitSeekCandidate = commitSeekCandidate
  scope.undoSeekHop = undoSeekHop
  scope.redoSeekHop = redoSeekHop
  scope.resetSeekRun = resetSeekRun
  scope.restoreSeekSessionIfAny = restoreSeekSessionIfAny
}
