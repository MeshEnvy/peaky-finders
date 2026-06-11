(function () {
  "use strict";

  const state = {
    simulation: null,
    defaults: null,
    overrides: new Set(),
    modemNames: [],
    environmentNames: [],
    modems: {},
    environments: {},
    selectedModem: null,
    selectedEnv: null,
    modemIsNew: false,
    envIsNew: false,
    radiusMin: 1,
    radiusMax: 100,
    rasterMin: 128,
    rasterMax: 4096,
  };

  function onProjectMap() {
    return Boolean(window.PEAKY_PROJECT && window.PEAKY_PROJECT.slug);
  }

  function projectSlug() {
    return onProjectMap() ? String(window.PEAKY_PROJECT.slug) : "";
  }

  function notifyRfChange() {
    if (onProjectMap() && window.PEAKY_MAP) {
      if (typeof window.PEAKY_MAP.setViewshedSimulation === "function" && state.simulation) {
        window.PEAKY_MAP.setViewshedSimulation(
          state.simulation.radius_km,
          state.simulation.raster_dimension
        );
      }
      if (typeof window.PEAKY_MAP.reloadViewshedsForSimChange === "function") {
        window.PEAKY_MAP.reloadViewshedsForSimChange();
      }
    }
    updateGearSummary();
  }

  function updateGearSummary() {
    const btn = document.getElementById("home-settings-open");
    const sim =
      state.simulation || (onProjectMap() ? window.PEAKY_PROJECT && window.PEAKY_PROJECT.simulation : null);
    if (!btn) return;
    if (!onProjectMap() || !sim) {
      btn.title = "Settings";
      return;
    }
    const text = `${Math.round(Number(sim.radius_km))} km · ${sim.raster_dimension} px`;
    btn.title = `Settings — ${text}`;
  }

  function showError(message) {
    const el = document.getElementById("home-settings-error");
    if (!el) return;
    if (!message) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = message;
  }

  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    let payload = null;
    try {
      payload = await resp.json();
    } catch (_e) {
      payload = null;
    }
    if (!resp.ok) {
      const msg = payload && payload.error ? payload.error : resp.statusText || "Request failed";
      throw new Error(msg);
    }
    return payload;
  }

  function presetRefName(value) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object" && value.preset) return String(value.preset);
    return "";
  }

  function fillSelect(selectEl, names, selected) {
    if (!selectEl) return;
    selectEl.innerHTML = "";
    for (const name of names) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      selectEl.appendChild(opt);
    }
    if (selected && names.includes(selected)) {
      selectEl.value = selected;
    } else if (names.length) {
      selectEl.value = names[0];
    }
  }

  function syncRangeLabel(inputId, labelId) {
    const input = document.getElementById(inputId);
    const label = document.getElementById(labelId);
    if (input && label) label.textContent = input.value;
  }

  const SKIP_LIVE_OVERRIDE_KEYS = new Set(["max_workers.splatter"]);

  const FORM_FIELD_BY_PATH = {
    modem: "home-sim-modem",
    environment: "home-sim-environment",
    radius_km: "home-sim-radius-km",
    raster_dimension: "home-sim-raster-dimension",
    "transmitter.height_m": "home-sim-tx-height",
    "transmitter.gain_dbi": "home-sim-tx-gain",
    "transmitter.loss_db": "home-sim-tx-loss",
    "receiver.height_m": "home-sim-rx-height",
    "receiver.gain_dbi": "home-sim-rx-gain",
    "receiver.loss_db": "home-sim-rx-loss",
    "max_workers.splatter": "home-sim-splatter-workers",
  };

  function getDefaultAtPath(path) {
    const def = state.defaults;
    if (!def) return undefined;
    const parts = path.split(".");
    let cur = def;
    for (const part of parts) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[part];
    }
    return cur;
  }

  function getFormValueAtPath(path) {
    const elId = FORM_FIELD_BY_PATH[path];
    if (!elId) return undefined;
    const el = document.getElementById(elId);
    if (!el) return undefined;
    if (el.tagName === "SELECT") return el.value;
    if (el.type === "range" || el.type === "number") {
      const n = Number(el.value);
      return Number.isFinite(n) ? n : undefined;
    }
    return el.value;
  }

  function setFormValueAtPath(path, value) {
    const elId = FORM_FIELD_BY_PATH[path];
    if (!elId) return;
    const el = document.getElementById(elId);
    if (!el || value == null) return;
    if (path === "modem" || path === "environment") {
      el.value = presetRefName(value) || String(value);
    } else {
      el.value = String(value);
    }
    if (path === "radius_km") syncRangeLabel("home-sim-radius-km", "home-sim-radius-km-value");
    if (path === "raster_dimension") {
      syncRangeLabel("home-sim-raster-dimension", "home-sim-raster-dimension-value");
    }
  }

  function valuesDiffer(path, current, defaultVal) {
    if (path === "modem" || path === "environment") {
      const cur = String(current ?? "");
      const def = presetRefName(defaultVal) || String(defaultVal ?? "");
      return cur !== def;
    }
    if (typeof current === "number" || typeof defaultVal === "number") {
      return Math.abs(Number(current) - Number(defaultVal)) > 1e-9;
    }
    return String(current ?? "") !== String(defaultVal ?? "");
  }

  function isFieldOverridden(path) {
    if (!onProjectMap() || !state.defaults) return false;
    if (state.overrides.has(path)) return true;
    if (SKIP_LIVE_OVERRIDE_KEYS.has(path)) return false;
    const current = getFormValueAtPath(path);
    const defaultVal = getDefaultAtPath(path);
    if (current === undefined && defaultVal === undefined) return false;
    return valuesDiffer(path, current, defaultVal);
  }

  function applyOverrideStyles() {
    const isProject = onProjectMap();
    document.querySelectorAll(".home-sim-field[data-override-key]").forEach((field) => {
      const key = field.getAttribute("data-override-key");
      if (!key) return;
      const overridden = isProject && isFieldOverridden(key);
      field.classList.toggle("is-overridden", overridden);
      field.querySelectorAll(".home-sim-reset").forEach((btn) => {
        btn.hidden = !overridden;
      });
    });
    updateSaveButtonLabel();
  }

  function activeSettingsTab() {
    const tabGroup = document.getElementById("home-settings-tabs");
    if (!tabGroup) return "simulation";
    return tabGroup.active || "simulation";
  }

  function updateSaveButtonLabel() {
    const saveBtn = document.getElementById("home-settings-save");
    if (!saveBtn) return;
    const tab = activeSettingsTab();
    if (tab === "modems") {
      saveBtn.textContent = "Save modem";
    } else if (tab === "environments") {
      saveBtn.textContent = "Save environment";
    } else {
      saveBtn.textContent = onProjectMap() ? "Save project simulation" : "Save global defaults";
    }
  }

  function closeSettingsModal() {
    const dialog = document.getElementById("home-settings-modal");
    if (!dialog) return;
    dialog.open = false;
  }

  function openSettingsModal() {
    const dialog = document.getElementById("home-settings-modal");
    if (!dialog) return;
    dialog.open = true;
  }

  function wireSettingsOpenButtons() {
    document.addEventListener("click", (ev) => {
      const opener = ev
        .composedPath()
        .find((el) => el instanceof Element && el.id === "home-settings-open");
      if (!opener) return;
      ev.preventDefault();
      openSettingsModal();
    });
  }

  function onSimulationFieldInput() {
    applyOverrideStyles();
  }

  function hydrateSimulationForm() {
    const sim = state.simulation;
    if (!sim) return;
    fillSelect(document.getElementById("home-sim-modem"), state.modemNames, presetRefName(sim.modem));
    fillSelect(
      document.getElementById("home-sim-environment"),
      state.environmentNames,
      presetRefName(sim.environment)
    );
    const radiusEl = document.getElementById("home-sim-radius-km");
    const rasterEl = document.getElementById("home-sim-raster-dimension");
    if (radiusEl) {
      radiusEl.min = String(state.radiusMin);
      radiusEl.max = String(state.radiusMax);
      radiusEl.value = String(sim.radius_km);
    }
    if (rasterEl) {
      rasterEl.min = String(state.rasterMin);
      rasterEl.max = String(state.rasterMax);
      rasterEl.value = String(sim.raster_dimension);
    }
    syncRangeLabel("home-sim-radius-km", "home-sim-radius-km-value");
    syncRangeLabel("home-sim-raster-dimension", "home-sim-raster-dimension-value");
    const tx = sim.transmitter || {};
    const rx = sim.receiver || {};
    const setNum = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val != null ? String(val) : "";
    };
    setNum("home-sim-tx-height", tx.height_m);
    setNum("home-sim-tx-gain", tx.gain_dbi);
    setNum("home-sim-tx-loss", tx.loss_db);
    setNum("home-sim-rx-height", rx.height_m);
    setNum("home-sim-rx-gain", rx.gain_dbi);
    setNum("home-sim-rx-loss", rx.loss_db);
    const workers = sim.max_workers || {};
    setNum("home-sim-splatter-workers", workers.splatter != null ? workers.splatter : 1);
    applyOverrideStyles();
    updateGearSummary();
  }

  function applySimulationPayload(payload) {
    state.simulation = payload.simulation;
    state.defaults = payload.defaults || null;
    state.overrides = new Set(payload.overrides || []);
    state.modemNames = payload.modem_names || [];
    state.environmentNames = payload.environment_names || [];
    if (payload.radius_km_min != null) state.radiusMin = Number(payload.radius_km_min);
    if (payload.radius_km_max != null) state.radiusMax = Number(payload.radius_km_max);
    if (payload.raster_dimension_min != null) state.rasterMin = Number(payload.raster_dimension_min);
    if (payload.raster_dimension_max != null) state.rasterMax = Number(payload.raster_dimension_max);
    hydrateSimulationForm();
  }

  function renderPresetList(listEl, names, selected, onSelect) {
    if (!listEl) return;
    listEl.innerHTML = "";
    for (const name of names) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "home-settings-list__item" + (name === selected ? " is-active" : "");
      btn.textContent = name;
      btn.addEventListener("click", () => onSelect(name));
      listEl.appendChild(btn);
    }
  }

  function hydrateModemForm(name) {
    const nameEl = document.getElementById("home-modem-name");
    if (nameEl) {
      nameEl.readOnly = !state.modemIsNew;
      nameEl.value = name || "";
    }
    const body = name && state.modems[name] ? state.modems[name] : {};
    const setNum = (id, val, fallback) => {
      const el = document.getElementById(id);
      if (el) el.value = val != null ? String(val) : fallback != null ? String(fallback) : "";
    };
    setNum("home-modem-frequency", body.frequency_mhz, 915);
    setNum("home-modem-bandwidth", body.bandwidth_khz, 125);
    setNum("home-modem-sf", body.spreading_factor, 7);
    setNum("home-modem-cr", body.coding_rate, 5);
    setNum("home-modem-margin", body.implementation_margin_db, 3);
    setNum("home-modem-power", body.power_dbm, 22);
    setNum("home-modem-sensitivity", body.sensitivity_dbm, -121);
  }

  function hydrateEnvironmentForm(name) {
    const nameEl = document.getElementById("home-env-name");
    if (nameEl) {
      nameEl.readOnly = !state.envIsNew;
      nameEl.value = name || "";
    }
    const body = name && state.environments[name] ? state.environments[name] : {};
    const setVal = (id, val, fallback) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.tagName === "SELECT") {
        el.value = val != null ? String(val) : fallback != null ? String(fallback) : el.value;
      } else {
        el.value = val != null ? String(val) : fallback != null ? String(fallback) : "";
      }
    };
    setVal("home-env-description", body.description, "");
    setVal("home-env-climate", body.climate, "continental_temperate");
    setVal("home-env-polarization", body.polarization, "vertical");
    setVal("home-env-clutter", body.clutter_height_m, 0);
    setVal("home-env-fresnel", body.fresnel_clearance_fraction, 0.6);
    setVal("home-env-pessimism", body.coverage_pessimism_db, 0);
    setVal("home-env-situation", body.situation_pct, 95);
    setVal("home-env-time", body.time_pct, 95);
    setVal("home-env-dielectric", body.ground_dielectric_v_m, 15);
    setVal("home-env-conductivity", body.ground_conductivity_s_m, 0.005);
    setVal("home-env-bending", body.atmosphere_bending_n, 301);
  }

  function selectModem(name) {
    state.selectedModem = name;
    state.modemIsNew = false;
    renderPresetList(
      document.getElementById("home-modem-list"),
      Object.keys(state.modems).sort(),
      name,
      selectModem
    );
    hydrateModemForm(name);
  }

  function selectEnvironment(name) {
    state.selectedEnv = name;
    state.envIsNew = false;
    renderPresetList(
      document.getElementById("home-env-list"),
      Object.keys(state.environments).sort(),
      name,
      selectEnvironment
    );
    hydrateEnvironmentForm(name);
  }

  async function loadCatalogs() {
    const [modemPayload, envPayload] = await Promise.all([
      fetchJson("/api/home/modems"),
      fetchJson("/api/home/environments"),
    ]);
    state.modems = modemPayload.presets || {};
    state.environments = envPayload.presets || {};
    const modemNames = Object.keys(state.modems).sort();
    const envNames = Object.keys(state.environments).sort();
    if (!state.selectedModem || !state.modems[state.selectedModem]) {
      state.selectedModem = modemNames[0] || null;
    }
    if (!state.selectedEnv || !state.environments[state.selectedEnv]) {
      state.selectedEnv = envNames[0] || null;
    }
    state.modemIsNew = false;
    state.envIsNew = false;
    if (state.selectedModem) selectModem(state.selectedModem);
    else hydrateModemForm(null);
    if (state.selectedEnv) selectEnvironment(state.selectedEnv);
    else hydrateEnvironmentForm(null);
  }

  async function loadSimulation() {
    if (onProjectMap()) {
      const payload = await fetchJson(`/api/p/${encodeURIComponent(projectSlug())}/simulation`);
      applySimulationPayload(payload);
      return;
    }
    const payload = await fetchJson("/api/home/simulation");
    applySimulationPayload({
      simulation: payload.simulation,
      defaults: payload.simulation,
      overrides: [],
      modem_names: payload.modem_names,
      environment_names: payload.environment_names,
      radius_km_min: 1,
      radius_km_max: 100,
      raster_dimension_min: 128,
      raster_dimension_max: 4096,
    });
  }

  async function loadAll() {
    showError("");
    await Promise.all([loadSimulation(), loadCatalogs()]);
  }

  function readSimulationPatch() {
    const num = (id) => {
      const el = document.getElementById(id);
      return el && el.value !== "" ? Number(el.value) : undefined;
    };
    return {
      modem: document.getElementById("home-sim-modem")?.value || undefined,
      environment: document.getElementById("home-sim-environment")?.value || undefined,
      radius_km: num("home-sim-radius-km"),
      raster_dimension: num("home-sim-raster-dimension"),
      transmitter: {
        height_m: num("home-sim-tx-height"),
        gain_dbi: num("home-sim-tx-gain"),
        loss_db: num("home-sim-tx-loss"),
      },
      receiver: {
        height_m: num("home-sim-rx-height"),
        gain_dbi: num("home-sim-rx-gain"),
        loss_db: num("home-sim-rx-loss"),
      },
      max_workers: {
        splatter: num("home-sim-splatter-workers"),
      },
    };
  }

  function readModemBody() {
    const num = (id) => Number(document.getElementById(id)?.value);
    return {
      frequency_mhz: num("home-modem-frequency"),
      bandwidth_khz: num("home-modem-bandwidth"),
      spreading_factor: num("home-modem-sf"),
      coding_rate: num("home-modem-cr"),
      implementation_margin_db: num("home-modem-margin"),
      power_dbm: num("home-modem-power"),
      sensitivity_dbm: num("home-modem-sensitivity"),
    };
  }

  function readEnvironmentBody() {
    const num = (id) => Number(document.getElementById(id)?.value);
    const str = (id) => document.getElementById(id)?.value || "";
    return {
      description: str("home-env-description"),
      climate: str("home-env-climate"),
      polarization: str("home-env-polarization"),
      clutter_height_m: num("home-env-clutter"),
      fresnel_clearance_fraction: num("home-env-fresnel"),
      coverage_pessimism_db: num("home-env-pessimism"),
      situation_pct: num("home-env-situation"),
      time_pct: num("home-env-time"),
      ground_dielectric_v_m: num("home-env-dielectric"),
      ground_conductivity_s_m: num("home-env-conductivity"),
      atmosphere_bending_n: num("home-env-bending"),
    };
  }

  async function saveSimulation() {
    const url = onProjectMap()
      ? `/api/p/${encodeURIComponent(projectSlug())}/simulation`
      : "/api/home/simulation";
    const payload = await fetchJson(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readSimulationPatch()),
    });
    if (onProjectMap()) {
      applySimulationPayload(payload);
    } else {
      applySimulationPayload({
        simulation: payload.simulation,
        defaults: payload.simulation,
        overrides: [],
        modem_names: state.modemNames,
        environment_names: state.environmentNames,
      });
    }
    notifyRfChange();
    return true;
  }

  async function resetSimulationField(overrideKey) {
    if (!onProjectMap()) return;
    showError("");
    const defaultVal = getDefaultAtPath(overrideKey);
    setFormValueAtPath(overrideKey, defaultVal);
    applyOverrideStyles();

    const savedOverride = state.overrides.has(overrideKey);
    if (!savedOverride) return;

    try {
      const payload = await fetchJson(`/api/p/${encodeURIComponent(projectSlug())}/simulation`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reset: [overrideKey] }),
      });
      applySimulationPayload(payload);
      notifyRfChange();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function saveModem() {
    const name = document.getElementById("home-modem-name")?.value?.trim();
    if (!name) throw new Error("modem name is required");
    const body = readModemBody();
    let result;
    if (state.modemIsNew) {
      result = await fetchJson("/api/home/modems", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, ...body }),
      });
    } else {
      result = await fetchJson(`/api/home/modems/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    state.modemIsNew = false;
    state.selectedModem = result.name;
    await loadAll();
    notifyRfChange();
    return true;
  }

  async function duplicateModem() {
    const source = state.selectedModem;
    if (!source || state.modemIsNew || !state.modems[source]) return;
    const suggested = `${source}-copy`;
    const newName = window.prompt("Duplicate modem preset as:", suggested);
    if (!newName) return;
    showError("");
    try {
      const result = await fetchJson("/api/home/modems", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim(), ...state.modems[source] }),
      });
      state.selectedModem = result.name;
      await loadAll();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function deleteModem() {
    const name = state.selectedModem;
    if (!name || state.modemIsNew) return;
    if (!window.confirm(`Delete modem preset "${name}"?`)) return;
    showError("");
    try {
      await fetchJson(`/api/home/modems/${encodeURIComponent(name)}`, { method: "DELETE" });
      state.selectedModem = null;
      await loadAll();
      notifyRfChange();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function saveEnvironment() {
    const name = document.getElementById("home-env-name")?.value?.trim();
    if (!name) throw new Error("environment name is required");
    const body = readEnvironmentBody();
    let result;
    if (state.envIsNew) {
      result = await fetchJson("/api/home/environments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, ...body }),
      });
    } else {
      result = await fetchJson(`/api/home/environments/${encodeURIComponent(name)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
    state.envIsNew = false;
    state.selectedEnv = result.name;
    await loadAll();
    notifyRfChange();
    return true;
  }

  async function saveActiveTab() {
    showError("");
    const btn = document.getElementById("home-settings-save");
    if (btn) btn.disabled = true;
    try {
      const tab = activeSettingsTab();
      if (tab === "modems") await saveModem();
      else if (tab === "environments") await saveEnvironment();
      else await saveSimulation();
      closeSettingsModal();
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function duplicateEnvironment() {
    const source = state.selectedEnv;
    if (!source || state.envIsNew || !state.environments[source]) return;
    const suggested = `${source}-copy`;
    const newName = window.prompt("Duplicate environment preset as:", suggested);
    if (!newName) return;
    showError("");
    try {
      const result = await fetchJson("/api/home/environments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim(), ...state.environments[source] }),
      });
      state.selectedEnv = result.name;
      await loadAll();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function deleteEnvironment() {
    const name = state.selectedEnv;
    if (!name || state.envIsNew) return;
    if (!window.confirm(`Delete environment preset "${name}"?`)) return;
    showError("");
    try {
      await fetchJson(`/api/home/environments/${encodeURIComponent(name)}`, { method: "DELETE" });
      state.selectedEnv = null;
      await loadAll();
      notifyRfChange();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  function addModemDraft() {
    state.modemIsNew = true;
    state.selectedModem = null;
    renderPresetList(document.getElementById("home-modem-list"), Object.keys(state.modems).sort(), null, selectModem);
    hydrateModemForm("");
    const nameEl = document.getElementById("home-modem-name");
    if (nameEl) {
      nameEl.readOnly = false;
      nameEl.value = "";
      nameEl.focus();
    }
  }

  function addEnvironmentDraft() {
    state.envIsNew = true;
    state.selectedEnv = null;
    renderPresetList(
      document.getElementById("home-env-list"),
      Object.keys(state.environments).sort(),
      null,
      selectEnvironment
    );
    hydrateEnvironmentForm("");
    const nameEl = document.getElementById("home-env-name");
    if (nameEl) {
      nameEl.readOnly = false;
      nameEl.value = "";
      nameEl.focus();
    }
  }

  async function bindUi() {
    const dialog = document.getElementById("home-settings-modal");
    if (!dialog) return;
    await customElements.whenDefined("wa-dialog");
    wireSettingsOpenButtons();

    dialog.addEventListener("wa-show", () => {
      updateSaveButtonLabel();
      void loadAll().catch((err) => showError(err.message || String(err)));
    });

    const tabGroup = document.getElementById("home-settings-tabs");
    tabGroup?.addEventListener("wa-tab-show", updateSaveButtonLabel);

    document.getElementById("home-settings-save")?.addEventListener("click", () => void saveActiveTab());
    document.getElementById("home-modem-add")?.addEventListener("click", addModemDraft);
    document.getElementById("home-env-add")?.addEventListener("click", addEnvironmentDraft);
    document.getElementById("home-modem-duplicate")?.addEventListener("click", () => void duplicateModem());
    document.getElementById("home-env-duplicate")?.addEventListener("click", () => void duplicateEnvironment());
    document.getElementById("home-modem-delete")?.addEventListener("click", () => void deleteModem());
    document.getElementById("home-env-delete")?.addEventListener("click", () => void deleteEnvironment());

    document.querySelectorAll(".home-sim-reset").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-override-key");
        if (key) void resetSimulationField(key);
      });
    });

    document.getElementById("home-sim-radius-km")?.addEventListener("input", () => {
      syncRangeLabel("home-sim-radius-km", "home-sim-radius-km-value");
      onSimulationFieldInput();
    });
    document.getElementById("home-sim-raster-dimension")?.addEventListener("input", () => {
      syncRangeLabel("home-sim-raster-dimension", "home-sim-raster-dimension-value");
      onSimulationFieldInput();
    });

    const simPane = document.querySelector('wa-tab-panel[name="simulation"]');
    if (simPane) {
      simPane.addEventListener("input", (ev) => {
        const t = ev.target;
        if (!(t instanceof HTMLElement)) return;
        if (t.closest(".home-sim-field[data-override-key]")) onSimulationFieldInput();
      });
      simPane.addEventListener("change", (ev) => {
        const t = ev.target;
        if (!(t instanceof HTMLElement)) return;
        if (t.closest(".home-sim-field[data-override-key]")) onSimulationFieldInput();
      });
    }

    if (onProjectMap()) {
      updateGearSummary();
      void loadSimulation().catch((err) => showError(err.message || String(err)));
    }
  }

  window.PEAKY_HOME_SETTINGS = {
    updateGearSummary,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => void bindUi());
  } else {
    void bindUi();
  }
})();
