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
export function installImportSites(scope) {
function annotateImportPoints(points) {
  return points.map((point) => {
    let nearest = null;
    let nearestDist = Infinity;
    for (const site of scope.sites) {
      const dist = scope.haversineMeters(point.lat, point.lon, site.lat, site.lon);
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
  if (!scope.importSitesSave) return;
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
  const known = scope.allProjectTags();
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
  for (const tag of scope.allProjectTags()) {
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
    let metaText = `${scope.formatCoord(point.lat)}, ${scope.formatCoord(point.lon)}`;
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
      body = { kmz_b64: scope.arrayBufferToBase64(buffer) };
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
  if (!scope.importSitesModal) return;
  setAddPlacementMode(null);
  scope.setEntityPanelOpen(true);
  resetImportSitesModal();
  await customElements.whenDefined("wa-dialog");
  importSitesModal.open = true;
  requestAnimationFrame(() => {
    importSitesFile?.focus();
  });
}

function closeImportSitesModal() {
  if (!scope.importSitesModal) return;
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
  if (scope.importSitesSave) importSitesSave.disabled = true;
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
      scope.registerSite(site);
    }
    const firstTag = importedTags[0];
    if (firstTag) {
      activeTagFilters.clear();
      activeTagFilters.add(firstTag);
      pruneActiveTagFilters();
      scope.renderEntityPanel();
    }
    if (scope.mapReady && imported.length) {
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
    scope.scheduleSaveMapState();
    if (imported[0]?.slug) scope.selectSite(imported[0].slug);
  } catch (_) {
    setImportSitesError("Could not reach server.");
  } finally {
    syncImportSaveButton();
  }


  scope.annotateImportPoints = annotateImportPoints
  scope.setImportSitesError = setImportSitesError
  scope.renderImportSitesList = renderImportSitesList
  scope.openImportSitesModal = openImportSitesModal
  scope.closeImportSitesModal = closeImportSitesModal
  scope.saveImportSitesModal = saveImportSitesModal
  scope.ensureImportPreviewMap = ensureImportPreviewMap
  scope.destroyImportPreviewMap = destroyImportPreviewMap
  scope.fitImportPreviewToPoints = fitImportPreviewToPoints
  scope.refreshImportPreviewLayers = refreshImportPreviewLayers
}
