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
export function installSitesEdit(scope) {
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
          if (scope.selectedSlug === site.slug) renderSiteTags(updated);
        } catch (_) {
          if (scope.selectedSlug === site.slug) {
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
  for (const tag of scope.allProjectTags()) {
    if (currentTags.includes(tag)) continue;
    const opt = document.createElement("option");
    opt.value = tag;
    list.appendChild(opt);
  }
  form.appendChild(input);
  form.appendChild(list);
  const finish = () => {
    tagAddOpen = false;
    if (scope.selectedSlug === site.slug)
      renderSiteTags(siteBySlug.get(site.slug) || site);
  };
  const commitPendingTag = async () => {
    if (!tagAddOpen) return;
    const tag = normalizeTagInput(input.value);
    tagAddOpen = false;
    const current = siteTags(siteBySlug.get(site.slug) || site);
    if (!tag || current.includes(tag)) {
      if (scope.selectedSlug === site.slug)
        renderSiteTags(siteBySlug.get(site.slug) || site);
      return;
    }
    try {
      const updated = await patchSiteTags(site.slug, [...current, tag], {
        immediate: true,
      });
      if (scope.selectedSlug === site.slug) renderSiteTags(updated);
    } catch (_) {
      if (scope.selectedSlug === site.slug)
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
    `${scope.formatCoord(site.lat)}, ${scope.formatCoord(site.lon)}`;
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
    if (scope.editMode && scope.editSlug && row.slug === scope.editSlug) return false;
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
  const pair = scope.parseCoordPairFromText(text);
  if (!pair) return false;
  sitePanelEditLat.value = scope.formatCoord(pair.lat);
  sitePanelEditLon.value = scope.formatCoord(pair.lon);
  onEditCoordsChanged();
  return true;
}

function editShowsSitePreview() {
  return scope.editMode;
}

function editWantsDraftViewshed() {
  if (!scope.editMode || !editShowsSitePreview()) return false;
  return !sitePanelEditViewshed || sitePanelEditViewshed.checked;
}

function ensureEditDraftViewshedEnabled() {
  viewshedVisible.set(DRAFT_VIEWSHED_SLUG, true);
  if (sitePanelEditViewshed) sitePanelEditViewshed.checked = true;
}

function loadEditDraftViewshedAt(lat, lon) {
  if (!editWantsDraftViewshed()) return;
  ensureEditDraftViewshedEnabled();
  scope.hideViewshedLayerForEdit(scope.editSlug);
  void scope.loadDraftViewshedAt(lat, lon);
}

function updateEditDraftMarker(lat, lon) {
  removeDraftMarker();
  const markerColor = DRAFT_MARKER_COLOR;
  draftMarker = new maplibregl.Marker({ color: markerColor })
    .setLngLat([lon, lat])
    .addTo(scope.map);
}

function syncEditViewshedCheckbox() {
  if (!sitePanelEditViewshed || !scope.editMode) return;
  const coords = readEditCoords();
  const atOriginal =
    coords && coordsMatchEditSnapshot(coords.lat, coords.lon);
  const hint = document.getElementById("site-panel-edit-viewshed-hint");
  if (atOriginal && scope.editSlug) {
    sitePanelEditViewshed.checked = scope.isViewshedVisible(scope.editSlug);
    if (hint)
      hint.textContent = viewshedLoading.has(scope.editSlug) ? "Loading…" : "";
    return;
  }
  sitePanelEditViewshed.checked = scope.isViewshedVisible(DRAFT_VIEWSHED_SLUG);
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
      .addTo(scope.map),
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
  const epoch = scope.viewshedLoadEpoch;
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
    const url = sitesPrefetchUrl(lat, lon, scope.editSlug);
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
  const text = `${scope.formatCoord(lat)}, ${scope.formatCoord(lon)}`;
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
    coordsEl.textContent = `${scope.formatCoord(entry.lat)}, ${scope.formatCoord(entry.lon)}`;
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
  scope.refreshFilteredLinks();
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
    const url = sitesPrefetchUrl(lat, lon, scope.editSlug);
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
  if (!scope.editMode) return;
  const coords = readEditCoords();
  if (!coords) return;
  const atOriginal = coordsMatchEditSnapshot(coords.lat, coords.lon);
  updateEditDraftMarker(coords.lat, coords.lon);
  if (!atOriginal) {
    removeDraftLinksLayer();
  }
  scope.refreshFilteredLinks();
  scheduleEditPrefetch();
}

function scheduleEditPrefetch() {
  if (!scope.editMode) return;
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
  const slug = scope.selectedSlug;
  const entity = siteBySlug.get(slug);
  if (!entity || !slug) return;
  scope.editKind = "site";
  scope.editSlug = slug;
  editSnapshot = { ...entity };
  clearEditCoordHistory();
  editCommittedCoords = { lat: Number(entity.lat), lon: Number(entity.lon) };
  setEditError("");
  document.getElementById("site-panel-edit-title").textContent = entity.name;
  sitePanelEditSlug.textContent = slug;
  sitePanelEditName.value = entity.name;
  sitePanelEditLat.value = scope.formatCoord(entity.lat);
  sitePanelEditLon.value = scope.formatCoord(entity.lon);
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
  scope.refreshFilteredLinks();
  syncEditViewshedCheckbox();
  void runEditPrefetchAt(entity.lat, entity.lon);
}

function cancelEdit() {
  if (!scope.editMode) return;
  scope.editMode = false;
  scope.editKind = null;
  scope.editSlug = null;
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
  scope.refreshFilteredLinks();
  syncEditMapShell();
  sitePanel.hidden = false;
  syncMapViewport();
  showPanelView();
  if (scope.selectedSlug) renderPanel(siteBySlug.get(scope.selectedSlug));
}

async function saveEdit() {
  if (!scope.editMode || !scope.editSlug) return;
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
  const savedEditSlug = scope.editSlug;
  const apiUrl = `/api/p/${scope.projectSlug}/sites/${savedEditSlug}`;
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
      scope.registerSite(site);
      scope.selectedSlug = site.slug;
      viewshedVisible.set(site.slug, true);
      scheduleViewshedLoad(site);
      showPanelView();
      renderPanel(site);
      scope.renderEntityPanel();
      finishEditSaveUi();
      void loadSiteLinks();
      return;
    }
    if (payload.site) {
      const site = payload.site;
      applySiteRowUpdate(site);
      scope.selectedSlug = site.slug;
      viewshedVisible.set(site.slug, true);
      scheduleViewshedLoad(site);
      showPanelView();
      renderPanel(site);
      scope.renderEntityPanel();
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
  const row = scope.normalizeSiteFromApi(site);
  if (!row) return;
  const ix = sites.findIndex((s) => s.slug === row.slug);
  if (ix >= 0) scope.sites[ix] = row;
  else sites.push(row);
  siteBySlug.set(row.slug, row);
  if (refreshGeoJson && map.getSource(SITES_SOURCE)) {
    map.getSource(SITES_SOURCE).setData(sitesGeoJson());
  }
  if (refreshGeoJson) {
    applySiteLayerFilters();
    updateSelectedLayer();
    scope.raiseSiteLayers();
  }
}

function finishEditSaveUi() {
  applySiteLayerFilters();
  scope.refreshFilteredLinks();
  syncEditMapShell();
  updateSelectedLayer();
  scope.raiseSiteLayers();
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
  scope.editMode = false;
  scope.editKind = null;
  scope.editSlug = null;
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
  if (scope.createMode) cancelCreate();
  if (scope.editMode) cancelEdit();
  scope.selectedSlug = slug;
  sitePanel.hidden = false;
  syncMapViewport();
  showPanelView();
  renderPanel(site);
  updateSelectedLayer();
  scope.raiseSiteLayers();
  scope.renderEntityPanel();
  scope.syncWarmPriorities();
  void loadSingleSiteLinks(slug);
}

function deselectSite() {
  if (scope.createMode) {
    cancelCreate();
    return;
  }
  if (scope.editMode) {
    cancelEdit();
    return;
  }
  scope.selectedSlug = null;
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
  if (scope.editMode) return;
  if (scope.createMode) cancelCreate();
  setAddPlacementMode(null);
  openCreatePanel(lat, lon);
}


  scope.syncTagFilterBypassForSite = syncTagFilterBypassForSite
  scope.applySiteTagChange = applySiteTagChange
  scope.patchSiteTags = patchSiteTags
  scope.flushSiteTagSave = flushSiteTagSave
  scope.renderSiteTags = renderSiteTags
  scope.buildTagAddForm = buildTagAddForm
  scope.renderPanel = renderPanel
  scope.setEditError = setEditError
  scope.resetEditPrefetchPanelUI = resetEditPrefetchPanelUI
  scope.resetEditPrefetchUI = resetEditPrefetchUI
  scope.renderEditPrefetch = renderEditPrefetch
  scope.readEditCoords = readEditCoords
  scope.applyCoordPaste = applyCoordPaste
  scope.editShowsSitePreview = editShowsSitePreview
  scope.editWantsDraftViewshed = editWantsDraftViewshed
  scope.ensureEditDraftViewshedEnabled = ensureEditDraftViewshedEnabled
  scope.loadEditDraftViewshedAt = loadEditDraftViewshedAt
  scope.updateEditDraftMarker = updateEditDraftMarker
  scope.syncEditViewshedCheckbox = syncEditViewshedCheckbox
  scope.coordsMatchPair = coordsMatchPair
  scope.coordSeparationM = coordSeparationM
  scope.editHistorySlug = editHistorySlug
  scope.removeEditHistoryMarker = removeEditHistoryMarker
  scope.cancelEditHistoryViewshedLoad = cancelEditHistoryViewshedLoad
  scope.hideEditHistoryViewshed = hideEditHistoryViewshed
  scope.showEditHistoryViewshed = showEditHistoryViewshed
  scope.removeEditHistoryMapArtifacts = removeEditHistoryMapArtifacts
  scope.clearEditCoordHistory = clearEditCoordHistory
  scope.historyHasCoords = historyHasCoords
  scope.pushEditCoordHistory = pushEditCoordHistory
  scope.commitEditCoordMove = commitEditCoordMove
  scope.updateEditHistoryMarker = updateEditHistoryMarker
  scope.invalidateEditCoordHistoryPrefetch = invalidateEditCoordHistoryPrefetch
  scope.clearEditHistoryViewshed = clearEditHistoryViewshed
  scope.loadEditHistoryViewshed = loadEditHistoryViewshed
  scope.loadEditHistoryLinks = loadEditHistoryLinks
  scope.loadEditHistoryMapArtifacts = loadEditHistoryMapArtifacts
  scope.setEditHistoryEntryVisible = setEditHistoryEntryVisible
  scope.deleteEditHistoryEntry = deleteEditHistoryEntry
  scope.copyCoordPair = copyCoordPair
  scope.renderEditCoordHistory = renderEditCoordHistory
  scope.coordsMatchEditSnapshot = coordsMatchEditSnapshot
  scope.runEditPrefetchAt = runEditPrefetchAt
  scope.onEditCoordsChanged = onEditCoordsChanged
  scope.scheduleEditPrefetch = scheduleEditPrefetch
  scope.restoreEditHiddenViewshed = restoreEditHiddenViewshed
  scope.openEditPanel = openEditPanel
  scope.cancelEdit = cancelEdit
  scope.saveEdit = saveEdit
  scope.applySiteRowUpdate = applySiteRowUpdate
  scope.finishEditSaveUi = finishEditSaveUi
  scope.cleanupEditSaveArtifacts = cleanupEditSaveArtifacts
  scope.copyEditCoords = copyEditCoords
  scope.copyPlssFromElement = copyPlssFromElement
  scope.clearLongPressTimer = clearLongPressTimer
  scope.lngLatFromClientPoint = lngLatFromClientPoint
  scope.beginCreateAtMapPoint = beginCreateAtMapPoint
}
