import * as C from '../constants.js'
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
} from '../geo.js'
import { normalizeLandSidebarInput } from './sidebar-model.js'

/** @param {Record<string, unknown>} scope */
export function installLand(scope) {
function landApiUrl() {
  return `/api/p/${scope.projectSlug}/land`;
}

function landSidebarApiUrl() {
  return `/api/p/${scope.projectSlug}/land/sidebar`;
}

function landDataGdbsUrl() {
  return `/api/p/${scope.projectSlug}/land/data-gdbs`;
}

function landImportPreviewApiUrl() {
  return `/api/p/${scope.projectSlug}/land/import/preview`;
}

function landImportApiUrl() {
  return `/api/p/${scope.projectSlug}/land/import`;
}

function landSourceApiUrl(sourceId) {
  return `/api/p/${scope.projectSlug}/land/sources/${encodeURIComponent(sourceId)}`;
}

function landLayerGeoJsonUrl(sourceId, layer) {
  return `/api/p/${scope.projectSlug}/land/sources/${encodeURIComponent(sourceId)}/layers/${encodeURIComponent(layer)}/geojson`;
}

function landPreviewGeoJsonUrl(path, layer) {
  const params = new URLSearchParams({ path, layer });
  return `/api/p/${scope.projectSlug}/land/preview/geojson?${params}`;
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
  return `/api/p/${scope.projectSlug}/land/import/preview/fields?${params}`;
}

function landValuesApiUrl(path, layer, field) {
  const params = new URLSearchParams({ path, layer, field });
  return `/api/p/${scope.projectSlug}/land/import/preview/values?${params}`;
}

function landPreviewGeoJsonPostUrl() {
  return `/api/p/${scope.projectSlug}/land/preview/geojson`;
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

  const styleMap = styleMapFromLayerSpec(spec);
  if (styleMap && Object.keys(styleMap).length) {
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

function excludedValuesForField(scope.config, field) {
  if (!field) return new Set();
  const filt = (config.exclude || []).find((item) => item.field === field);
  return new Set(Array.isArray(filt?.values) ? filt.values : []);
}

function setExcludedValueForField(scope.config, field, value, excluded) {
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

function landPreviewLayerConfig(scope.config) {
  if (!scope.config) return null;
  if (
    config.labelField ||
    config.include?.length ||
    config.exclude?.length ||
    config.styleField
  ) {
    return scope.config;
  }
  return null;
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
          role: layerConfig.role || undefined,
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
        return payload.geojson;
      }
      const resp = await fetch(landPreviewGeoJsonUrl(path, layer));
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || !payload.geojson) {
        throw new Error(payload.error || `Preview failed (${resp.status})`);
      }
      return payload.geojson;
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

function setLandLayerVisible(sourceId, layer, visible) {
  landVisible.set(landLayerKey(sourceId, layer), !!visible);
  scope.scheduleSaveMapState();
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
  scope.scheduleSaveMapState();
  syncLandMapLabelLayer(sourceId, layer);
}


function cloneLandSidebar(sidebar = scope.landSidebar) {
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
  scope.landSidebar = { folders, unfiledSources };
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
      body: JSON.stringify(scope.landSidebar),
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      window.alert(payload.error || `Save sidebar failed (${resp.status})`);
      return false;
    }
    if (payload.sidebar)
      scope.landSidebar = normalizeLandSidebarInput(payload.sidebar);
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
  scope.landSidebar = { folders: [], unfiledSources: order };
  landSidebarMigrationPending = true;
}

function isLandFolderCollapsed(folderId) {
  return landFoldersCollapsed.get(folderId) === true;
}

function setLandFolderCollapsed(folderId, collapsed) {
  landFoldersCollapsed.set(folderId, !!collapsed);
  scope.scheduleSaveMapState();
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
  return refs.every((key) => landVisible.get(key) === true);
}

function setLandFolderVisible(folderId, visible) {
  const folder = landSidebar.folders.find((item) => item.id === folderId);
  if (!folder) return;
  for (const sourceId of folder.sources) {
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
  scope.landSidebar = next;
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

function createLandFolder() {
  void openCreateLandFolderModal();
}

function renameLandFolder(folderId) {
  void openRenameLandFolderModal(folderId);
}

let landFolderModalMode = "create";
let landFolderEditId = null;

const landFolderModal = document.getElementById("land-folder-modal");
const landFolderName = document.getElementById("land-folder-name");
const landFolderError = document.getElementById("land-folder-error");
const landFolderSave = document.getElementById("land-folder-save");

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
  if (scope.openWaDialog) await scope.openWaDialog(landFolderModal);
  else {
    await customElements.whenDefined("wa-dialog");
    landFolderModal.open = true;
  }
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
  if (scope.openWaDialog) await scope.openWaDialog(landFolderModal);
  else {
    await customElements.whenDefined("wa-dialog");
    landFolderModal.open = true;
  }
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

function initLandFolderModal() {
  if (!landFolderModal || landFolderModal.dataset.bound) return;
  landFolderModal.dataset.bound = "1";
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
}

initLandFolderModal();

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
  if (!scope.mapReady) return;
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
  scope.raiseSiteLayers();
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
  scope.entityPanelTab = next;
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
  scope.scheduleSaveMapState();
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
  if (!scope.mapReady) return;
  const sourceMapId = landMapSourceId(sourceId, layerKey);
  const labelsId = `${sourceMapId}-labels`;
  const layerVisible = isLandLayerVisible(sourceId, layerKey);
  const labelsVisible =
    layerVisible && isLandLayerLabelsVisible(sourceId, layerKey);
  const vis = labelsVisible ? "visible" : "none";
  syncGeoJsonLabelLayer(
    scope.map,
    sourceMapId,
    labelsId,
    landLayerHasLabels(sourceId, layerKey),
    vis,
  );
}

function syncLandMapLayerVisibility(sourceId, layer) {
  if (!scope.mapReady) return;
  const sourceMapId = landMapSourceId(sourceId, layer);
  const fillId = `${sourceMapId}-fill`;
  const lineId = `${sourceMapId}-line`;
  const vis = isLandLayerVisible(sourceId, layer) ? "visible" : "none";
  if (map.getLayer(fillId)) map.setLayoutProperty(fillId, "visibility", vis);
  if (map.getLayer(lineId)) map.setLayoutProperty(lineId, "visibility", vis);
}

function applyLandMapLayerStyle(sourceId, layerKey) {
  if (!scope.mapReady) return;
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
  if (!scope.mapReady) return;
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
    const geojson = await fetchLandLayerGeoJson(
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
          visibility: isLandLayerVisible(sourceId, layerKey)
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
          visibility: isLandLayerVisible(sourceId, layerKey)
            ? "visible"
            : "none",
        },
      },
      viewshedLayerInsertBefore(),
    );
    syncLandMapLabelLayer(sourceId, layerKey);
    scope.raiseSiteLayers();
  } catch (_) {
    /* network */
  }
}

async function refreshLandMapLayer(sourceId, layerKey) {
  if (!scope.mapReady) return;
  if (!isLandLayerVisible(sourceId, layerKey)) return;
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
    return isLandLayerVisible(row.sourceId, row.layerKey);
  });
  await Promise.all(
    rows.map((row) => refreshLandMapLayer(row.sourceId, row.layerKey)),
  );
}

function removeLandMapLayer(sourceId, layer) {
  if (!scope.mapReady) return;
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
  const displayTitles = landSourceDisplayTitles(scope.landSources);
  const sourceTitle = displayTitles.get(sourceId) || source.label || sourceId;
  const layers = Array.isArray(source.layers) ? source.layers : [];
  const singleLayer = layers.length === 1;
  const singleSpec = singleLayer ? normalizeRegisteredLayer(layers[0]) : null;

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
  if (singleSpec && !isLandLayerVisible(sourceId, singleSpec.key)) {
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
  if (singleSpec) {
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
  } else if (layers.length > 1) {
    const layerList = document.createElement("div");
    layerList.className = "entity-panel__land-source-layers";
    for (const rawLayer of layers) {
      const spec = normalizeRegisteredLayer(rawLayer);
      layerList.appendChild(
        buildLandEntityRow(
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
    ) || entityPanelLandList.querySelector(`[data-land-key="${rowKey}"]`);
  if (!row) return;
  row.classList.toggle("entity-panel__row--land-loading", !!loading);
  const spinner = row.querySelector(".entity-panel__land-row-spinner");
  if (spinner) spinner.hidden = !loading;
}

function buildLandEntityRow(row, { showName = false } = {}) {
  const rowKey = landLayerKey(row.sourceId, row.layerKey);
  const el = document.createElement("div");
  el.className =
    "entity-panel__row entity-panel__row--land entity-panel__row--land-nested";
  el.dataset.landKey = rowKey;
  const visible = isLandLayerVisible(row.sourceId, row.layerKey);
  if (!visible) el.classList.add("entity-panel__row--hidden");

  const main = document.createElement("div");
  main.className = "entity-panel__main";
  if (showName) {
    const titleRow = document.createElement("div");
    titleRow.className = "entity-panel__land-title-row";
    appendLandLayerRoleBadge(titleRow, row.spec.role);
    const name = document.createElement("div");
    name.className = "entity-panel__name";
    const nameText = friendlyLandLayerName(row.spec.name);
    name.textContent = nameText;
    name.title = nameText;
    titleRow.appendChild(name);
    main.appendChild(titleRow);
  }
  appendLandLayerMeta(main, row.spec);

  const controls = document.createElement("div");
  controls.className = "entity-panel__controls";
  controls.appendChild(
    buildLandLayerControls(row.sourceId, row.layerKey, {
      labelField: row.spec.labelField,
    }),
  );

  el.appendChild(main);
  el.appendChild(controls);
  return el;
}

async function reloadLandSources({ refreshMap = true } = {}) {
  const prevAoiDigest = scope.landAoiDigest;
  try {
    const resp = await fetch(landApiUrl());
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok) return;
    scope.landSources = Array.isArray(payload.sources) ? payload.sources : [];
    if (payload.sidebar)
      scope.landSidebar = normalizeLandSidebarInput(payload.sidebar);
    syncLandSidebarWithSources();
    const nextAoiDigest =
      typeof payload.aoiDigest === "string" ? payload.aoiDigest : "none";
    const aoiChanged = nextAoiDigest !== prevAoiDigest;
    scope.landAoiDigest = nextAoiDigest;
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
  const prevAoiDigest = scope.landAoiDigest;
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
        landLabelsVisible.delete(landLayerKey(sourceId, spec.key));
        clearLandLayerGeoJsonCacheForLayer(sourceId, spec.key);
      }
    }
    await reloadLandSources({ refreshMap: false });
    if (scope.landAoiDigest !== prevAoiDigest) {
      clearAllLandLayerGeoJsonCache();
      await reloadClippedLandLayers();
    }
    scope.scheduleSaveMapState();
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
  scope.config,
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
      const excluded = excludedValuesForField(scope.config, config.labelField);
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
            scope.config,
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
      scope.config,
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
  scope.config,
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
      syncLandPreviewLabelLayer(previewMap, sourceName, !!scope.config?.labelField);
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
      landPreviewLayerConfig(scope.config),
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
        scope.config,
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
      landPreviewLayerConfig(scope.config),
    );
    await applyLandPreviewMapData(
      previewMap,
      sourceName,
      fillId,
      lineId,
      geojson,
      style,
      scope.config,
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
    fillImportLandGdbSelect(scope.landDataGdbPaths);
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
    scope.landDataGdbPaths = Array.isArray(payload.paths) ? payload.paths : [];
    fillImportLandGdbSelect(scope.landDataGdbPaths);
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
  scope.setEntityPanelOpen(true);
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
  const prevAoiDigest = scope.landAoiDigest;
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
      renderLandPanel();
      if (scope.landAoiDigest !== prevAoiDigest) {
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
      setEditLandError(payload.error || `Preview failed (${resp.status})`);
      return;
    }
    editLandPreviewLayers = Array.isArray(payload.layers)
      ? payload.layers
      : [];
    editLandLayerStyles = new Map();
    editLandLayerConfigs = new Map();
    const registeredByName = new Map();
    for (const rawLayer of source.layers || []) {
      const spec = normalizeRegisteredLayer(rawLayer);
      registeredByName.set(spec.name, rawLayer);
      editLandLayerConfigs.set(
        spec.name,
        configFromRegisteredLayer(rawLayer),
      );
      ensureLandLayerStyleState(
        editLandLayerStyles,
        spec.name,
        flatStyleFromLayerSpec(spec),
      );
    }
    const allLayerNames = editLandPreviewLayers.map((layer) => layer.name);
    const selected = new Set([...registeredByName.keys()]);
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
    await customElements.whenDefined("wa-dialog");
    editLandModal.open = true;
  } catch (_) {
    setEditLandError("Could not reach server.");
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
  const prevAoiDigest = scope.landAoiDigest;
  const prevSource = landSources.find((s) => s.id === editLandSourceId);
  const prevKeys = new Set(
    (prevSource?.layers || []).map(
      (layer) => normalizeRegisteredLayer(layer).key,
    ),
  );
  try {
    const body = {
      layers: collectSelectedLayerPayloads(
        editLandLayerList,
        editLandLayerStyles,
        editLandLayerConfigs,
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
    if (scope.landAoiDigest !== prevAoiDigest) {
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


  scope.landApiUrl = landApiUrl
  scope.landSidebarApiUrl = landSidebarApiUrl
  scope.landDataGdbsUrl = landDataGdbsUrl
  scope.landImportPreviewApiUrl = landImportPreviewApiUrl
  scope.landImportApiUrl = landImportApiUrl
  scope.landSourceApiUrl = landSourceApiUrl
  scope.landLayerGeoJsonUrl = landLayerGeoJsonUrl
  scope.landPreviewGeoJsonUrl = landPreviewGeoJsonUrl
  scope.landPreviewCacheKey = landPreviewCacheKey
  scope.landLayerCacheKey = landLayerCacheKey
  scope.clearLandLayerGeoJsonCacheForLayer = clearLandLayerGeoJsonCacheForLayer
  scope.clearAllLandLayerGeoJsonCache = clearAllLandLayerGeoJsonCache
  scope.fetchCachedGeoJson = fetchCachedGeoJson
  scope.landFieldsApiUrl = landFieldsApiUrl
  scope.landValuesApiUrl = landValuesApiUrl
  scope.landPreviewGeoJsonPostUrl = landPreviewGeoJsonPostUrl
  scope.defaultLandLayerConfig = defaultLandLayerConfig
  scope.ensureLandLayerConfig = ensureLandLayerConfig
  scope.normalizeRegisteredLayer = normalizeRegisteredLayer
  scope.landLayerDisplayName = landLayerDisplayName
  scope.landPathBasename = landPathBasename
  scope.humanizeLandText = humanizeLandText
  scope.friendlyLandLayerName = friendlyLandLayerName
  scope.friendlyLandSourceTitle = friendlyLandSourceTitle
  scope.landSourceDisplayTitles = landSourceDisplayTitles
  scope.landLayerRoleBadgeSpec = landLayerRoleBadgeSpec
  scope.appendLandLayerRoleBadge = appendLandLayerRoleBadge
  scope.buildLandLayerControls = buildLandLayerControls
  scope.appendLandLayerMeta = appendLandLayerMeta
  scope.styleMapFromLayerSpec = styleMapFromLayerSpec
  scope.flatStyleFromLayerSpec = flatStyleFromLayerSpec
  scope.buildStyleMatchExpression = buildStyleMatchExpression
  scope.buildOpacityMatchExpression = buildOpacityMatchExpression
  scope.layerConfigToPayload = layerConfigToPayload
  scope.landColumnSortKey = landColumnSortKey
  scope.excludedValuesForField = excludedValuesForField
  scope.setExcludedValueForField = setExcludedValueForField
  scope.configFromRegisteredLayer = configFromRegisteredLayer
  scope.collectSelectedLayerPayloads = collectSelectedLayerPayloads
  scope.fetchLandLayerFields = fetchLandLayerFields
  scope.fetchLandFieldValues = fetchLandFieldValues
  scope.landPreviewLayerConfig = landPreviewLayerConfig
  scope.fetchLandPreviewGeoJson = fetchLandPreviewGeoJson
  scope.fetchLandLayerGeoJson = fetchLandLayerGeoJson
  scope.landPreviewMapPrefix = landPreviewMapPrefix
  scope.ensureLandPreviewLoadingOverlay = ensureLandPreviewLoadingOverlay
  scope.setLandPreviewMapLoading = setLandPreviewMapLoading
  scope.beginLandPreviewMapFetch = beginLandPreviewMapFetch
  scope.endLandPreviewMapFetch = endLandPreviewMapFetch
  scope.setLandLayerRowLoading = setLandLayerRowLoading
  scope.defaultLandLayerStyle = defaultLandLayerStyle
  scope.landLineColorFromFill = landLineColorFromFill
  scope.normalizeLandLayerStyle = normalizeLandLayerStyle
  scope.resolveLandLayerStyle = resolveLandLayerStyle
  scope.resolveLandLayerSpec = resolveLandLayerSpec
  scope.landPreviewSourceId = landPreviewSourceId
  scope.landLayerKey = landLayerKey
  scope.landLayerSlug = landLayerSlug
  scope.landMapSourceId = landMapSourceId
  scope.isLandLayerVisible = isLandLayerVisible
  scope.setLandLayerVisible = setLandLayerVisible
  scope.landLayerHasLabels = landLayerHasLabels
  scope.isLandLayerLabelsVisible = isLandLayerLabelsVisible
  scope.setLandLayerLabelsVisible = setLandLayerLabelsVisible
  scope.cloneLandSidebar = cloneLandSidebar
  scope.landSourceRecord = landSourceRecord
  scope.allLandSourceIds = allLandSourceIds
  scope.syncLandSidebarWithSources = syncLandSidebarWithSources
  scope.orderedLandSourceIds = orderedLandSourceIds
  scope.slugifyLandFolderId = slugifyLandFolderId
  scope.removeSourceFromSidebar = removeSourceFromSidebar
  scope.schedulePersistLandSidebar = schedulePersistLandSidebar
  scope.persistLandSidebar = persistLandSidebar
  scope.maybeMigrateLandSidebarFromLayerOrder = maybeMigrateLandSidebarFromLayerOrder
  scope.queueLandSidebarMigrationFromLayerOrder = queueLandSidebarMigrationFromLayerOrder
  scope.isLandFolderCollapsed = isLandFolderCollapsed
  scope.setLandFolderCollapsed = setLandFolderCollapsed
  scope.landFolderLayerRefs = landFolderLayerRefs
  scope.isLandFolderVisible = isLandFolderVisible
  scope.setLandFolderVisible = setLandFolderVisible
  scope.landLayerRowsRaw = landLayerRowsRaw
  scope.landLayerRows = landLayerRows
  scope.applyLandSidebarMutation = applyLandSidebarMutation
  scope.reorderLandFolder = reorderLandFolder
  scope.moveLandSource = moveLandSource
  scope.reorderLandSource = reorderLandSource
  scope.createLandFolder = createLandFolder
  scope.renameLandFolder = renameLandFolder
  scope.openCreateLandFolderModal = openCreateLandFolderModal
  scope.openRenameLandFolderModal = openRenameLandFolderModal
  scope.saveLandFolderModal = saveLandFolderModal
  scope.deleteLandFolder = deleteLandFolder
  scope.syncLandMapLayerOrder = syncLandMapLayerOrder
  scope.buildLandDragHandle = buildLandDragHandle
  scope.clearLandDragState = clearLandDragState
  scope.initLandPanelDragDrop = initLandPanelDragDrop
  scope.setEntityTab = setEntityTab
  scope.syncGeoJsonLabelLayer = syncGeoJsonLabelLayer
  scope.syncLandMapLabelLayer = syncLandMapLabelLayer
  scope.syncLandMapLayerVisibility = syncLandMapLayerVisibility
  scope.applyLandMapLayerStyle = applyLandMapLayerStyle
  scope.ensureLandMapLayer = ensureLandMapLayer
  scope.refreshLandMapLayer = refreshLandMapLayer
  scope.reloadClippedLandLayers = reloadClippedLandLayers
  scope.removeLandMapLayer = removeLandMapLayer
  scope.refreshLandMapLayers = refreshLandMapLayers
  scope.buildLandLayerEyeBtn = buildLandLayerEyeBtn
  scope.buildLandLayerLabelBtn = buildLandLayerLabelBtn
  scope.buildLandSourceActionBtns = buildLandSourceActionBtns
  scope.buildLandFolderEyeBtn = buildLandFolderEyeBtn
  scope.buildLandFolderActionBtns = buildLandFolderActionBtns
  scope.buildLandFolderRow = buildLandFolderRow
  scope.buildLandSourceGroup = buildLandSourceGroup
  scope.buildLandUnfiledSection = buildLandUnfiledSection
  scope.renderLandPanel = renderLandPanel
  scope.setLandSidebarRowLoading = setLandSidebarRowLoading
  scope.buildLandEntityRow = buildLandEntityRow
  scope.reloadLandSources = reloadLandSources
  scope.deleteLandSource = deleteLandSource
  scope.setImportLandError = setImportLandError
  scope.setEditLandError = setEditLandError
  scope.destroyImportLandPreviewMap = destroyImportLandPreviewMap
  scope.destroyEditLandPreviewMap = destroyEditLandPreviewMap
  scope.ensureLandPreviewMap = ensureLandPreviewMap
  scope.whenPreviewMapReady = whenPreviewMapReady
  scope.resetLandPreviewMapLoading = resetLandPreviewMapLoading
  scope.fitPreviewMapToBboxes = fitPreviewMapToBboxes
  scope.ensureLandLayerStyleState = ensureLandLayerStyleState
  scope.buildLandLayerAttrPanel = buildLandLayerAttrPanel
  scope.populateLabelSelect = populateLabelSelect
  scope.loadLabelValues = loadLabelValues
  scope.ensureFields = ensureFields
  scope.renderLandLayerChecklist = renderLandLayerChecklist
  scope.landPreviewLayerSpecs = landPreviewLayerSpecs
  scope.applyLandPreviewLayerStyle = applyLandPreviewLayerStyle
  scope.landPreviewLabelsLayerId = landPreviewLabelsLayerId
  scope.syncLandPreviewLabelLayer = syncLandPreviewLabelLayer
  scope.applyLandPreviewMapData = applyLandPreviewMapData
  scope.removeLandPreviewLayer = removeLandPreviewLayer
  scope.clearLandPreviewMapLayers = clearLandPreviewMapLayers
  scope.refreshLandPreviewLayerConfig = refreshLandPreviewLayerConfig
  scope.showLandPreviewLayer = showLandPreviewLayer
  scope.hideLandPreviewLayer = hideLandPreviewLayer
  scope.updateLandPreviewLayerStyle = updateLandPreviewLayerStyle
  scope.syncLandPreviewMap = syncLandPreviewMap
  scope.resetImportLandModal = resetImportLandModal
  scope.fillImportLandGdbSelect = fillImportLandGdbSelect
  scope.populateImportLandGdbSelect = populateImportLandGdbSelect
  scope.previewImportLandPath = previewImportLandPath
  scope.openImportLandModal = openImportLandModal
  scope.collectSelectedLandLayers = collectSelectedLandLayers
  scope.saveImportLandModal = saveImportLandModal
  scope.openEditLandModal = openEditLandModal
  scope.saveEditLandModal = saveEditLandModal
}
