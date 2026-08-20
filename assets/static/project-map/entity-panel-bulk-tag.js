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
export function installBulkTag(scope) {
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
  if (!scope.bulkTagError) return;
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
  if (scope.entityPanelFilterByViewport) scopeParts.push("in the current map view");
  const filterSuffix = scopeParts.length ? ` ${scopeParts.join(" and ")}` : "";
  bulkTagStatus.textContent =
    count === 0
      ? "No sites match the current sidebar filters."
      : count === 1
        ? `Apply to 1 site${filterSuffix}`
        : `Apply to ${count} sites${filterSuffix}`;
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
  for (const tag of scope.allProjectTags()) {
    const opt = document.createElement("option");
    opt.value = tag;
    bulkTagAddSuggestions.appendChild(opt);
  }
}

function syncBulkTagSaveButton() {
  if (!scope.bulkTagSave) return;
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
  if (!scope.bulkTagModal) return;
  bulkTagModal.open = false;
}

async function openBulkTagModal() {
  if (!scope.bulkTagModal) return;
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
  if (scope.bulkTagSave) bulkTagSave.disabled = true;
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
    scope.scheduleSaveMapState();
  } catch (_) {
    setBulkTagError("Could not reach server.");
  } finally {
    syncBulkTagSaveButton();
  }
}

function makeEntityPanelActionBtn({
  icon,


  scope.bulkTagListTags = bulkTagListTags
  scope.bulkTagVisualState = bulkTagVisualState
  scope.bulkTagHasChanges = bulkTagHasChanges
  scope.renderBulkTagTags = renderBulkTagTags
  scope.openBulkTagModal = openBulkTagModal
  scope.saveBulkTagModal = saveBulkTagModal
}
