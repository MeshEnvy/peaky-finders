// @ts-check

import { compareHuman } from './geo.js'
import {
  LAND_DEFAULT_FILL_COLOR,
  LAND_DEFAULT_FILL_OPACITY,
  LAND_DEFAULT_LINE_COLOR,
  MAP_LABEL_FONT,
} from './constants.js'

/** @typedef {'eligible'|'blocked'|'boundary'|'overlay'} LandRulePurpose */
/** @typedef {'keep'|'remove'} LandFilterMode */

/** @typedef {object} LandRule
 * @property {string} uid
 * @property {string} layerName
 * @property {string|null} slug
 * @property {LandRulePurpose} purpose
 * @property {Array<{field: string, values: string[]}>} include
 * @property {Array<{field: string, values: string[]}>} exclude
 * @property {string} labelField
 * @property {{ color: string, opacity: number }} style
 * @property {boolean} expanded
 * @property {boolean} previewOn
 * @property {Array<{name: string}>|null} fields
 * @property {boolean} fieldsLoading
 * @property {string} filterField
 * @property {LandFilterMode} filterMode
 * @property {Set<string>} filterValues
 */

const PREVIEW_PREFIX = 'land-source-preview'
const OVERLAY_COLORS = ['#c4a035', '#228b22', '#4a90d9', '#8e44ad', '#e67e22', '#16a085']
const AGENCY_FIELDS = new Set(['ABBR', 'ADMIN', 'SMA_ID', 'PROPERTY_STATUS', 'NAME'])

const PURPOSE_OPTIONS = [
  { value: 'eligible', label: 'Eligible land', title: 'Adds to public land for site seek' },
  { value: 'blocked', label: 'Blocked land', title: 'Subtracts from eligible land' },
  { value: 'boundary', label: 'Project boundary', title: 'Clips other layers to this AOI' },
  { value: 'overlay', label: 'Map overlay', title: 'Visual only, does not affect seek' },
]

/**
 * @param {object} options
 * @param {object} options.elements
 * @param {object} options.api
 * @param {object} options.callbacks
 * @param {object} options.utils
 */
export function createLandSourceEditor({ elements, api, callbacks, utils }) {
  /** @type {import('maplibre-gl').Map|null} */
  let previewMap = null
  /** @type {object|null} */
  let state = null
  let previewMapFitted = false
  /** @type {Map<string, { rows: Array<{ value: string, count: number, bbox?: number[] }>, scopedToAoi: boolean, truncated: boolean }>} */
  const fieldValuesCache = new Map()

  function setError(message) {
    const { errorEl } = elements
    if (!errorEl) return
    if (message) {
      errorEl.textContent = message
      errorEl.hidden = false
    } else {
      errorEl.textContent = ''
      errorEl.hidden = true
    }
  }

  function newRuleId() {
    return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }

  /** @param {string|null|undefined} role */
  function roleToPurpose(role) {
    if (role === 'include') return 'eligible'
    if (role === 'exclude') return 'blocked'
    if (role === 'aoi') return 'boundary'
    return 'overlay'
  }

  /** @param {LandRulePurpose} purpose */
  function purposeToRole(purpose) {
    if (purpose === 'eligible') return 'include'
    if (purpose === 'blocked') return 'exclude'
    if (purpose === 'boundary') return 'aoi'
    return ''
  }

  /** @param {LandRulePurpose} purpose */
  function purposeLabel(purpose) {
    return PURPOSE_OPTIONS.find((opt) => opt.value === purpose)?.label || purpose
  }

  function defaultStyle() {
    return { color: LAND_DEFAULT_FILL_COLOR, opacity: LAND_DEFAULT_FILL_OPACITY }
  }

  function normalizeStyle(raw) {
    const defaults = defaultStyle()
    if (!raw || typeof raw !== 'object') return { ...defaults }
    const color =
      typeof raw.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(raw.color)
        ? raw.color.toLowerCase()
        : defaults.color
    const opacityRaw = Number(raw.opacity)
    const opacity = Number.isFinite(opacityRaw)
      ? Math.min(1, Math.max(0, opacityRaw))
      : defaults.opacity
    return { color, opacity }
  }

  function lineColorFromFill(hex) {
    const normalized = String(hex || '').replace('#', '')
    if (normalized.length !== 6) return LAND_DEFAULT_LINE_COLOR
    const r = Number.parseInt(normalized.slice(0, 2), 16)
    const g = Number.parseInt(normalized.slice(2, 4), 16)
    const b = Number.parseInt(normalized.slice(4, 6), 16)
    if ([r, g, b].some((n) => Number.isNaN(n))) return LAND_DEFAULT_LINE_COLOR
    const factor = 0.55
    const toHex = (n) =>
      Math.round(n * factor)
        .toString(16)
        .padStart(2, '0')
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`
  }

  /** @param {object} raw */
  function ruleFromRegisteredLayer(raw, previewLayers) {
    const spec = utils.normalizeRegisteredLayer(raw)
    const previewNames = (previewLayers || []).map((l) => l.name)
    let layerName = spec.name
    if (!previewNames.includes(layerName) && previewNames.length === 1) {
      layerName = previewNames[0]
    }
    const include = Array.isArray(spec.include) ? spec.include : []
    const exclude = Array.isArray(spec.exclude) ? spec.exclude : []
    let filterField = ''
    let filterMode = /** @type {LandFilterMode} */ ('remove')
    const filterValues = new Set()
    const primaryFilter = include[0] || exclude[0]
    if (primaryFilter?.field) {
      filterField = primaryFilter.field
      if (include.length) {
        filterMode = 'keep'
        for (const v of primaryFilter.values || []) filterValues.add(String(v))
      } else {
        filterMode = 'remove'
        for (const v of primaryFilter.values || []) filterValues.add(String(v))
      }
    }
    return /** @type {LandRule} */ ({
      uid: newRuleId(),
      layerName,
      slug: raw.id != null && raw.id !== '' ? String(raw.id) : null,
      purpose: roleToPurpose(spec.role),
      include: include.map((f) => ({
        field: f.field,
        values: [...(f.values || [])],
      })),
      exclude: exclude.map((f) => ({
        field: f.field,
        values: [...(f.values || [])],
      })),
      labelField: spec.labelField || '',
      style: normalizeStyle(utils.flatStyleFromLayerSpec(spec)),
      expanded: false,
      previewOn: true,
      fields: null,
      fieldsLoading: false,
      filterField,
      filterMode,
      filterValues,
    })
  }

  /** @param {LandRule} rule */
  function syncRuleFilters(rule) {
    rule.include = []
    rule.exclude = []
    if (!rule.filterField || !rule.filterValues.size) return
    const values = [...rule.filterValues]
    if (rule.filterMode === 'keep') {
      rule.include = [{ field: rule.filterField, values }]
    } else {
      rule.exclude = [{ field: rule.filterField, values }]
    }
  }

  /** @param {LandRule} rule */
  function ruleToPayload(rule) {
    syncRuleFilters(rule)
    const row = { name: rule.layerName }
    if (rule.slug) row.id = rule.slug
    const role = purposeToRole(rule.purpose)
    if (role) row.role = role
    if (rule.labelField) row.labelField = rule.labelField
    if (rule.include.length) row.include = rule.include
    if (rule.exclude.length) row.exclude = rule.exclude
    row.style = normalizeStyle(rule.style)
    return row
  }

  /** @param {LandRule} rule */
  function ruleSummary(rule) {
    syncRuleFilters(rule)
    const parts = [purposeLabel(rule.purpose)]
    if (rule.slug) parts.push(rule.slug.toUpperCase())
    if (rule.filterField && rule.filterValues.size) {
      const vals = [...rule.filterValues].slice(0, 3)
      const suffix = rule.filterValues.size > 3 ? '…' : ''
      const mode = rule.filterMode === 'keep' ? 'Keep' : 'Remove'
      parts.push(`${mode} ${rule.filterField}: ${vals.join(', ')}${suffix}`)
    }
    return parts.join(' · ')
  }

  /** @param {LandRule} rule */
  function previewLayerConfig(rule) {
    syncRuleFilters(rule)
    const role = purposeToRole(rule.purpose)
    return {
      role: role || undefined,
      include: rule.include.length ? rule.include : undefined,
      exclude: rule.exclude.length ? rule.exclude : undefined,
      labelField: rule.labelField || undefined,
    }
  }

  /** @param {object} geojson @param {string} labelField */
  function ensureLabelProperties(geojson, labelField) {
    const key = String(labelField || '').trim()
    if (!key || !geojson?.features?.length) return geojson
    for (const feat of geojson.features) {
      if (!feat.properties) feat.properties = {}
      if (feat.properties.label) continue
      const raw = feat.properties[key]
      if (raw != null && raw !== '') feat.properties.label = String(raw)
    }
    return geojson
  }

  function previewSourceName(ruleUid) {
    return `${PREVIEW_PREFIX}-${ruleUid}`
  }

  function destroyPreviewMap() {
    previewMapFitted = false
    if (previewMap) {
      previewMap.remove()
      previewMap = null
    }
  }

  function fieldValuesCacheKey(filePath, layer, field) {
    return `${filePath}\0${layer}\0${field}`
  }

  /** @param {number[]} a @param {number[]} b */
  function bboxIntersects(a, b) {
    return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]
  }

  /** @param {import('maplibre-gl').Map|null} map */
  function viewportBbox(map) {
    if (!map) return null
    const bounds = map.getBounds()
    return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
  }

  /**
   * @param {Array<{ value: string, count: number, bbox?: number[] }>} rows
   * @param {number[]|null} viewBbox
   * @param {Set<string>} selected
   */
  function rowsForViewport(rows, viewBbox, selected) {
    if (!viewBbox) return rows
    const visible = rows.filter(
      (row) => Array.isArray(row.bbox) && bboxIntersects(row.bbox, viewBbox),
    )
    const seen = new Set(visible.map((row) => row.value))
    for (const row of rows) {
      if (selected.has(row.value) && !seen.has(row.value)) {
        visible.push(row)
        seen.add(row.value)
      }
    }
    return visible.slice().sort((a, b) => compareHuman(a.value, b.value))
  }

  function refreshFilterValueLists() {
    if (!state || !previewMap) return
    const viewBbox = viewportBbox(previewMap)
    for (const rule of state.rules) {
      if (!rule.filterField) continue
      const card = elements.rulesListEl?.querySelector(`[data-rule-uid="${rule.uid}"]`)
      const valuesEl = card?.querySelector('.land-source-filter-values')
      if (!valuesEl?.dataset.valuesLoaded) continue
      const key = fieldValuesCacheKey(state.filePath, rule.layerName, rule.filterField)
      const entry = fieldValuesCache.get(key)
      if (entry) renderFilterValuesList(rule, valuesEl, entry, viewBbox)
    }
  }

  function ensurePreviewMap() {
    const { previewMapEl } = elements
    if (!previewMapEl) return null
    if (previewMap) {
      previewMap.resize()
      return previewMap
    }
    previewMap = new maplibregl.Map({
      container: previewMapEl,
      style: utils.basemapStyle(utils.currentBasemapKey()),
      center: utils.mainMapCenter(),
      zoom: utils.mainMapZoom(),
      bearing: 0,
      pitch: 0,
      attributionControl: false,
    })
    previewMap.on('moveend', () => refreshFilterValueLists())
    return previewMap
  }

  function whenPreviewReady(map) {
    if (!map) return Promise.resolve()
    if (map.isStyleLoaded()) return Promise.resolve()
    return new Promise((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        resolve(undefined)
      }
      map.once('load', finish)
      map.once('error', finish)
      window.setTimeout(finish, 8000)
    })
  }

  /** @param {import('maplibre-gl').Map} map @param {number[][]} bboxes */
  function fitBboxes(map, bboxes) {
    if (!map || !bboxes.length) return
    const bounds = new maplibregl.LngLatBounds()
    for (const bbox of bboxes) {
      if (!Array.isArray(bbox) || bbox.length !== 4) continue
      bounds.extend([bbox[0], bbox[1]])
      bounds.extend([bbox[2], bbox[3]])
    }
    fitMapBounds(map, bounds)
  }

  /** @param {import('maplibre-gl').Map} map @param {maplibregl.LngLatBounds} bounds */
  function fitMapBounds(map, bounds) {
    if (!map || bounds.isEmpty()) return
    const fit = () => {
      try {
        map.resize()
        if (bounds.isEmpty()) return
        map.fitBounds(bounds, { padding: 40, maxZoom: 11, duration: 0 })
      } catch (_) {
        /* map not ready */
      }
    }
    if (map.loaded() && map.isStyleLoaded()) {
      requestAnimationFrame(() => requestAnimationFrame(fit))
    } else {
      map.once('load', () => requestAnimationFrame(fit))
      map.once('idle', fit)
    }
  }

  /** @param {object} geojson */
  function boundsFromGeoJson(geojson) {
    const bounds = new maplibregl.LngLatBounds()
    let has = false

    /** @param {unknown} coords */
    function extendCoords(coords) {
      if (!Array.isArray(coords) || !coords.length) return
      if (typeof coords[0] === 'number' && typeof coords[1] === 'number') {
        const lng = Number(coords[0])
        const lat = Number(coords[1])
        if (!Number.isFinite(lng) || !Number.isFinite(lat)) return
        bounds.extend([lng, lat])
        has = true
        return
      }
      for (const part of coords) extendCoords(part)
    }

    for (const feat of geojson?.features || []) {
      if (feat?.geometry?.coordinates) extendCoords(feat.geometry.coordinates)
    }
    return has ? bounds : null
  }

  function previewLabelsLayerId(sourceName) {
    return `${sourceName}-labels`
  }

  /** @param {import('maplibre-gl').Map} map @param {string} sourceName @param {boolean} showLabels */
  function syncPreviewLabelLayer(map, sourceName, showLabels) {
    if (!map) return
    const labelsId = previewLabelsLayerId(sourceName)
    if (!showLabels) {
      if (map.getLayer(labelsId)) map.removeLayer(labelsId)
      return
    }
    if (!map.getSource(sourceName)) return
    if (!map.getLayer(labelsId)) {
      map.addLayer({
        id: labelsId,
        type: 'symbol',
        source: sourceName,
        filter: ['all', ['has', 'label'], ['!=', ['get', 'label'], '']],
        layout: {
          'text-field': ['get', 'label'],
          'text-size': 11,
          'text-font': MAP_LABEL_FONT,
          'text-allow-overlap': true,
          'text-ignore-placement': true,
          'text-max-width': 14,
        },
        paint: {
          'text-color': '#f0f4ff',
          'text-halo-color': '#141820',
          'text-halo-width': 1.5,
        },
      })
    } else if (typeof map.moveLayer === 'function') {
      try {
        map.moveLayer(labelsId)
      } catch (_) {
        /* ignore */
      }
    }
  }

  /** @param {LandRule} rule */
  function removePreviewRule(rule) {
    if (!previewMap) return
    const sourceName = previewSourceName(rule.uid)
    const fillId = `${sourceName}-fill`
    const lineId = `${sourceName}-line`
    const labelsId = previewLabelsLayerId(sourceName)
    if (previewMap.getLayer(labelsId)) previewMap.removeLayer(labelsId)
    if (previewMap.getLayer(lineId)) previewMap.removeLayer(lineId)
    if (previewMap.getLayer(fillId)) previewMap.removeLayer(fillId)
    if (previewMap.getSource(sourceName)) previewMap.removeSource(sourceName)
  }

  /** @param {LandRule} rule @returns {Promise<maplibregl.LngLatBounds|null>} */
  async function showPreviewRule(rule) {
    if (!state || !rule.previewOn) {
      removePreviewRule(rule)
      return null
    }
    const map = ensurePreviewMap()
    if (!map || !state.filePath || !rule.layerName) return null
    await whenPreviewReady(map)
    const sourceName = previewSourceName(rule.uid)
    const fillId = `${sourceName}-fill`
    const lineId = `${sourceName}-line`
    const style = normalizeStyle(rule.style)
    const lineColor = lineColorFromFill(style.color)
    try {
      const geojson = ensureLabelProperties(
        await api.fetchPreviewGeoJson(
          state.filePath,
          rule.layerName,
          previewLayerConfig(rule),
        ),
        rule.labelField,
      )
      if (!map.getSource(sourceName)) {
        map.addSource(sourceName, { type: 'geojson', data: geojson })
        map.addLayer({
          id: fillId,
          type: 'fill',
          source: sourceName,
          paint: {
            'fill-color': style.color,
            'fill-opacity': style.opacity,
            'fill-outline-color': lineColor,
          },
        })
        map.addLayer({
          id: lineId,
          type: 'line',
          source: sourceName,
          paint: { 'line-color': lineColor, 'line-width': 1 },
        })
      } else {
        map.getSource(sourceName).setData(geojson)
        map.setPaintProperty(fillId, 'fill-color', style.color)
        map.setPaintProperty(fillId, 'fill-opacity', style.opacity)
        map.setPaintProperty(fillId, 'fill-outline-color', lineColor)
        map.setPaintProperty(lineId, 'line-color', lineColor)
      }
      // Re-sync labels after data so properties.label from this fetch are used.
      if (map.getLayer(previewLabelsLayerId(sourceName))) {
        map.removeLayer(previewLabelsLayerId(sourceName))
      }
      syncPreviewLabelLayer(map, sourceName, !!rule.labelField)
      return boundsFromGeoJson(geojson)
    } catch (_) {
      /* skip layer */
      return null
    }
  }

  async function syncPreviewMap(options = {}) {
    const fitBounds = options.fitBounds === true
    if (!state) return
    const map = ensurePreviewMap()
    if (!map) return
    try {
      map.resize()
    } catch (_) {
      /* ignore */
    }
    await whenPreviewReady(map)
    const activeUids = new Set(
      state.rules.filter((r) => r.previewOn).map((r) => r.uid),
    )
    for (const rule of state.rules) {
      if (!activeUids.has(rule.uid)) removePreviewRule(rule)
    }
    const featureBounds = await Promise.all(
      state.rules.filter((r) => r.previewOn).map(showPreviewRule),
    )
    const merged = new maplibregl.LngLatBounds()
    let hasBounds = false
    for (const bounds of featureBounds) {
      if (!bounds || bounds.isEmpty()) continue
      merged.extend(bounds.getSouthWest())
      merged.extend(bounds.getNorthEast())
      hasBounds = true
    }
    if (fitBounds && hasBounds) {
      fitMapBounds(map, merged)
      previewMapFitted = true
      map.once('idle', () => refreshFilterValueLists())
      return
    }
    if (fitBounds) {
      const layerBboxes = state.previewLayers.map((l) => l.bbox).filter(Boolean)
      fitBboxes(map, layerBboxes)
      previewMapFitted = true
      map.once('idle', () => refreshFilterValueLists())
    }
  }

  function updateSaveButton() {
    const { saveBtn } = elements
    if (!saveBtn || !state) return
    if (state.mode === 'edit') {
      saveBtn.disabled = false
      saveBtn.textContent = 'Save'
      return
    }
    saveBtn.disabled = !state.filePath || state.rules.length === 0
    saveBtn.textContent = 'Import'
  }

  function updateRulesCount() {
    const { rulesCountEl } = elements
    if (!rulesCountEl || !state) return
    rulesCountEl.textContent = `${state.rules.length} rule${state.rules.length === 1 ? '' : 's'}`
  }

  /** @param {LandRule} rule */
  async function ensureRuleFields(rule) {
    if (!state || rule.fields || rule.fieldsLoading) return
    rule.fieldsLoading = true
    renderRules()
    try {
      const payload = await api.fetchFields(state.filePath, rule.layerName)
      rule.fields = Array.isArray(payload.fields) ? payload.fields : []
    } catch (_) {
      rule.fields = []
    } finally {
      rule.fieldsLoading = false
      renderRules()
    }
  }

  /** @param {LandRule} rule */
  async function fetchFieldValueIndex(rule) {
    if (!state || !rule.filterField) return null
    const key = fieldValuesCacheKey(state.filePath, rule.layerName, rule.filterField)
    if (fieldValuesCache.has(key)) return fieldValuesCache.get(key)
    const payload = await api.fetchValues(
      state.filePath,
      rule.layerName,
      rule.filterField,
    )
    const entry = {
      rows: (Array.isArray(payload.values) ? payload.values : [])
        .slice()
        .sort((a, b) => compareHuman(a.value, b.value)),
      scopedToAoi: !!payload.scopedToAoi,
      truncated: !!payload.truncated,
    }
    fieldValuesCache.set(key, entry)
    return entry
  }

  /**
   * @param {LandRule} rule
   * @param {HTMLElement} valuesEl
   * @param {{ rows: Array<{ value: string, count: number, bbox?: number[] }>, scopedToAoi: boolean, truncated: boolean }} entry
   * @param {number[]|null} [viewBbox]
   */
  function renderFilterValuesList(rule, valuesEl, entry, viewBbox = null) {
    const bbox = viewBbox ?? viewportBbox(previewMap)
    const rows = rowsForViewport(entry.rows, bbox, rule.filterValues)
    valuesEl.innerHTML = ''
    valuesEl.dataset.valuesLoaded = '1'
    if (!rows.length) {
      valuesEl.innerHTML =
        '<span class="wa-caption pf-muted">No values in map view</span>'
      return
    }
    const hint = document.createElement('div')
    hint.className = 'wa-caption pf-muted land-source-filter-hint'
    const modeHint =
      rule.filterMode === 'keep'
        ? 'Checked values are kept'
        : 'Checked values are removed'
    if (entry.scopedToAoi) {
      hint.textContent = `${modeHint} (values in project AOI).`
    } else if (entry.truncated) {
      hint.textContent = `${modeHint} (top 100 values).`
    } else {
      hint.textContent = `${modeHint}.`
    }
    valuesEl.appendChild(hint)
    for (const row of rows) {
      const label = document.createElement('label')
      label.className = 'land-source-filter-value'
      const cb = document.createElement('input')
      cb.type = 'checkbox'
      cb.checked = rule.filterValues.has(row.value)
      cb.addEventListener('change', () => {
        if (cb.checked) rule.filterValues.add(row.value)
        else rule.filterValues.delete(row.value)
        syncRuleFilters(rule)
        void syncPreviewMap()
        updateRuleCardSummary(rule.uid)
      })
      const text = document.createElement('span')
      const labelText = `${row.value} (${row.count})`
      text.textContent = labelText
      text.title = labelText
      label.appendChild(cb)
      label.appendChild(text)
      valuesEl.appendChild(label)
    }
  }

  /** @param {LandRule} rule @param {HTMLElement} valuesEl */
  async function loadFilterValues(rule, valuesEl) {
    if (!state || !rule.filterField) {
      valuesEl.innerHTML = ''
      delete valuesEl.dataset.valuesLoaded
      return
    }
    valuesEl.innerHTML = '<span class="wa-caption pf-muted">Loading values…</span>'
    delete valuesEl.dataset.valuesLoaded
    try {
      const entry = await fetchFieldValueIndex(rule)
      if (!entry) {
        valuesEl.innerHTML = '<span class="wa-caption pf-muted">No values</span>'
        return
      }
      renderFilterValuesList(rule, valuesEl, entry)
    } catch (_) {
      valuesEl.innerHTML =
        '<span class="wa-caption pf-muted">Could not load values</span>'
    }
  }

  function updateRuleCardSummary(ruleUid) {
    const { rulesListEl } = elements
    if (!rulesListEl || !state) return
    const rule = state.rules.find((r) => r.uid === ruleUid)
    if (!rule) return
    const card = rulesListEl.querySelector(`[data-rule-uid="${ruleUid}"]`)
    const summaryEl = card?.querySelector('.land-source-rule-summary')
    if (summaryEl) summaryEl.textContent = ruleSummary(rule)
  }

  /** @param {string} ruleUid */
  function toggleRuleExpanded(ruleUid) {
    if (!state) return
    const rule = state.rules.find((r) => r.uid === ruleUid)
    if (!rule) return
    const willExpand = !rule.expanded
    for (const r of state.rules) {
      r.expanded = r.uid === ruleUid && willExpand
    }
    renderRules()
  }

  /** @param {string|null} [keepUid] */
  function collapseAllRulesExcept(keepUid = null) {
    if (!state) return
    for (const r of state.rules) {
      r.expanded = keepUid != null && r.uid === keepUid
    }
  }

  /** @param {LandRule} rule */
  function buildRuleCard(rule) {
    const card = document.createElement('div')
    card.className = 'land-source-rule-card'
    card.dataset.ruleUid = rule.uid
    if (rule.expanded) card.classList.add('land-source-rule-card--expanded')

    const header = document.createElement('div')
    header.className = 'land-source-rule-header'
    header.tabIndex = 0
    header.setAttribute('role', 'button')
    header.setAttribute('aria-expanded', rule.expanded ? 'true' : 'false')

    const chevron = document.createElement('span')
    chevron.className = 'land-source-rule-chevron'
    chevron.textContent = rule.expanded ? '▼' : '▶'
    chevron.setAttribute('aria-hidden', 'true')

    const titleWrap = document.createElement('div')
    titleWrap.className = 'land-source-rule-title-wrap'
    const badge = document.createElement('span')
    badge.className = `land-source-rule-purpose land-source-rule-purpose--${rule.purpose}`
    badge.textContent = purposeLabel(rule.purpose)
    const summary = document.createElement('div')
    summary.className = 'land-source-rule-summary wa-caption pf-muted'
    summary.textContent = ruleSummary(rule)
    titleWrap.appendChild(badge)
    titleWrap.appendChild(summary)

    const deleteBtn = document.createElement('button')
    deleteBtn.type = 'button'
    deleteBtn.className = 'land-source-rule-delete'
    deleteBtn.textContent = 'Remove'
    deleteBtn.addEventListener('click', (event) => {
      event.stopPropagation()
      if (!state) return
      removePreviewRule(rule)
      state.rules = state.rules.filter((r) => r.uid !== rule.uid)
      renderRules()
      void syncPreviewMap()
      updateSaveButton()
    })

    header.addEventListener('click', () => toggleRuleExpanded(rule.uid))
    header.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        toggleRuleExpanded(rule.uid)
      }
    })

    header.appendChild(chevron)
    header.appendChild(titleWrap)
    header.appendChild(deleteBtn)
    card.appendChild(header)

    if (!rule.expanded) return card

    const body = document.createElement('div')
    body.className = 'land-source-rule-body'

    const purposeRow = document.createElement('div')
    purposeRow.className = 'land-source-rule-field'
    const purposeLabelEl = document.createElement('label')
    purposeLabelEl.className = 'pf-label'
    purposeLabelEl.textContent = 'Purpose'
    const purposeSelect = document.createElement('select')
    purposeSelect.className = 'land-source-select'
    for (const opt of PURPOSE_OPTIONS) {
      const el = document.createElement('option')
      el.value = opt.value
      el.textContent = opt.label
      el.title = opt.title
      purposeSelect.appendChild(el)
    }
    purposeSelect.value = rule.purpose
    purposeSelect.addEventListener('change', () => {
      rule.purpose = /** @type {LandRulePurpose} */ (purposeSelect.value)
      updateRuleCardSummary(rule.uid)
      void syncPreviewMap()
      renderRules()
    })
    purposeRow.appendChild(purposeLabelEl)
    purposeRow.appendChild(purposeSelect)
    body.appendChild(purposeRow)

    if (state && state.previewLayers.length > 1) {
      const layerRow = document.createElement('div')
      layerRow.className = 'land-source-rule-field'
      const layerLabel = document.createElement('label')
      layerLabel.className = 'pf-label'
      layerLabel.textContent = 'Physical layer'
      const layerSelect = document.createElement('select')
      layerSelect.className = 'land-source-select'
      for (const layer of state.previewLayers) {
        const opt = document.createElement('option')
        opt.value = layer.name
        opt.textContent = `${layer.name} (${layer.geometry || 'layer'}, ${layer.count || 0})`
        layerSelect.appendChild(opt)
      }
      layerSelect.value = rule.layerName
      layerSelect.addEventListener('change', () => {
        rule.layerName = layerSelect.value
        rule.fields = null
        void ensureRuleFields(rule)
        void syncPreviewMap({ fitBounds: true })
      })
      layerRow.appendChild(layerLabel)
      layerRow.appendChild(layerSelect)
      body.appendChild(layerRow)
    }

    const filterRow = document.createElement('div')
    filterRow.className = 'land-source-rule-field'
    const filterLabel = document.createElement('label')
    filterLabel.className = 'pf-label'
    filterLabel.textContent = 'Filter'
    const filterControls = document.createElement('div')
    filterControls.className = 'land-source-filter-controls'

    const fieldSelect = document.createElement('select')
    fieldSelect.className = 'land-source-select'
    fieldSelect.innerHTML = '<option value="">None</option>'
    if (rule.fieldsLoading) {
      fieldSelect.disabled = true
      const opt = document.createElement('option')
      opt.textContent = 'Loading fields…'
      fieldSelect.appendChild(opt)
    } else {
      for (const field of (rule.fields || [])
        .slice()
        .sort((a, b) => compareHuman(a.name, b.name))) {
        const opt = document.createElement('option')
        opt.value = field.name
        opt.textContent = field.name
        fieldSelect.appendChild(opt)
      }
    }
    fieldSelect.value = rule.filterField
    fieldSelect.addEventListener('change', () => {
      rule.filterField = fieldSelect.value
      rule.filterValues = new Set()
      syncRuleFilters(rule)
      renderRules()
      void syncPreviewMap()
    })

    const modeSelect = document.createElement('select')
    modeSelect.className = 'land-source-select land-source-filter-mode'
    for (const [val, label] of [
      ['keep', 'Keep only'],
      ['remove', 'Remove'],
    ]) {
      const opt = document.createElement('option')
      opt.value = val
      opt.textContent = label
      modeSelect.appendChild(opt)
    }
    modeSelect.value = rule.filterMode
    modeSelect.disabled = !rule.filterField
    modeSelect.addEventListener('change', () => {
      rule.filterMode = /** @type {LandFilterMode} */ (modeSelect.value)
      syncRuleFilters(rule)
      const key = fieldValuesCacheKey(state.filePath, rule.layerName, rule.filterField)
      const entry = fieldValuesCache.get(key)
      if (entry) renderFilterValuesList(rule, valuesEl, entry)
      void syncPreviewMap()
    })

    filterControls.appendChild(fieldSelect)
    filterControls.appendChild(modeSelect)
    filterRow.appendChild(filterLabel)
    filterRow.appendChild(filterControls)
    body.appendChild(filterRow)

    const valuesEl = document.createElement('div')
    valuesEl.className = 'land-source-filter-values'
    body.appendChild(valuesEl)
    if (rule.filterField) void loadFilterValues(rule, valuesEl)

    if (
      rule.purpose === 'overlay' &&
      rule.filterField &&
      AGENCY_FIELDS.has(rule.filterField.toUpperCase())
    ) {
      const splitBtn = document.createElement('button')
      splitBtn.type = 'button'
      splitBtn.className = 'land-source-split-btn'
      splitBtn.textContent = 'Split by field values'
      splitBtn.addEventListener('click', () => {
        void splitRuleByFieldValues(rule)
      })
      body.appendChild(splitBtn)
    }

    if (rule.purpose === 'overlay' || rule.purpose === 'boundary') {
      const styleRow = document.createElement('div')
      styleRow.className = 'land-source-rule-field land-source-style-row'
      const styleLabel = document.createElement('label')
      styleLabel.className = 'pf-label'
      styleLabel.textContent = 'Appearance'
      const styleWrap = document.createElement('div')
      styleWrap.className = 'land-source-style-controls'
      const colorInput = document.createElement('input')
      colorInput.type = 'color'
      colorInput.className = 'land-source-color-input'
      colorInput.value = rule.style.color
      colorInput.addEventListener('input', () => {
        rule.style.color = colorInput.value.toLowerCase()
        void syncPreviewMap()
      })
      const opacityInput = document.createElement('input')
      opacityInput.type = 'range'
      opacityInput.min = '0'
      opacityInput.max = '100'
      opacityInput.step = '1'
      opacityInput.value = String(Math.round(rule.style.opacity * 100))
      opacityInput.addEventListener('input', () => {
        rule.style.opacity = Number(opacityInput.value) / 100
        opacityLabel.textContent = `${opacityInput.value}%`
        void syncPreviewMap()
      })
      const opacityLabel = document.createElement('span')
      opacityLabel.className = 'land-source-opacity-label'
      opacityLabel.textContent = `${opacityInput.value}%`
      styleWrap.appendChild(colorInput)
      styleWrap.appendChild(opacityInput)
      styleWrap.appendChild(opacityLabel)
      styleRow.appendChild(styleLabel)
      styleRow.appendChild(styleWrap)
      body.appendChild(styleRow)
    }

    if (rule.purpose === 'overlay') {
      const labelRow = document.createElement('details')
      labelRow.className = 'land-source-label-details'
      if (rule.labelField) labelRow.open = true
      const labelSummary = document.createElement('summary')
      labelSummary.textContent = 'Map labels'
      labelRow.appendChild(labelSummary)
      const labelSelect = document.createElement('select')
      labelSelect.className = 'land-source-select'
      labelSelect.innerHTML = '<option value="">None</option>'
      for (const field of (rule.fields || [])
        .slice()
        .sort((a, b) => compareHuman(a.name, b.name))) {
        const opt = document.createElement('option')
        opt.value = field.name
        opt.textContent = field.name
        labelSelect.appendChild(opt)
      }
      labelSelect.value = rule.labelField
      labelSelect.addEventListener('change', () => {
        rule.labelField = labelSelect.value
        void syncPreviewMap()
      })
      labelRow.appendChild(labelSelect)
      body.appendChild(labelRow)
    }

    card.appendChild(body)
    void ensureRuleFields(rule)
    return card
  }

  /** @param {LandRule} rule */
  async function splitRuleByFieldValues(rule) {
    if (!state || !rule.filterField) return
    try {
      const entry = await fetchFieldValueIndex(rule)
      const rows = entry?.rows || []
      if (!rows.length) return
      const idx = state.rules.findIndex((r) => r.uid === rule.uid)
      const newRules = []
      rows.forEach((row, i) => {
        const slug = String(row.value)
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-|-$/g, '')
        newRules.push(
          /** @type {LandRule} */ ({
            uid: newRuleId(),
            layerName: rule.layerName,
            slug: slug || `val-${i}`,
            purpose: 'overlay',
            include: [{ field: rule.filterField, values: [row.value] }],
            exclude: [],
            labelField: '',
            style: {
              color: OVERLAY_COLORS[i % OVERLAY_COLORS.length],
              opacity: 0.42,
            },
            expanded: false,
            previewOn: true,
            fields: rule.fields,
            fieldsLoading: false,
            filterField: rule.filterField,
            filterMode: 'keep',
            filterValues: new Set([row.value]),
          }),
        )
      })
      removePreviewRule(rule)
      state.rules.splice(idx, 1, ...newRules)
      renderRules()
      void syncPreviewMap()
      updateSaveButton()
    } catch (_) {
      /* skip */
    }
  }

  function renderRules() {
    const { rulesListEl } = elements
    if (!rulesListEl || !state) return
    rulesListEl.innerHTML = ''
    for (const rule of state.rules) {
      rulesListEl.appendChild(buildRuleCard(rule))
    }
    updateRulesCount()
  }

  /** @param {LandRulePurpose} purpose */
  function addRule(purpose = 'eligible') {
    if (!state) return
    const layerName =
      state.previewLayers.length === 1
        ? state.previewLayers[0].name
        : state.previewLayers[0]?.name || ''
    const rule = {
      uid: newRuleId(),
      layerName,
      slug: null,
      purpose,
      include: [],
      exclude: [],
      labelField: '',
      style: defaultStyle(),
      expanded: true,
      previewOn: true,
      fields: null,
      fieldsLoading: false,
      filterField: '',
      filterMode: /** @type {LandFilterMode} */ ('remove'),
      filterValues: new Set(),
    }
    state.rules.push(rule)
    collapseAllRulesExcept(rule.uid)
    renderRules()
    updateSaveButton()
    void syncPreviewMap({ fitBounds: true })
  }

  function showEditorBody(show) {
    const { editorBody, mapSection, fileField, pathEl } = elements
    if (editorBody) editorBody.hidden = !show
    if (mapSection) mapSection.hidden = !show
    if (fileField && state?.mode === 'edit') fileField.hidden = true
    else if (fileField) fileField.hidden = false
    if (pathEl) {
      if (state?.mode === 'edit' && state.filePath) {
        pathEl.textContent = state.filePath
        pathEl.hidden = false
      } else {
        pathEl.hidden = true
      }
    }
  }

  async function loadFilePreview(path) {
    if (!state) return
    setError('')
    state.filePath = path
    const { fileStatusEl } = elements
    if (fileStatusEl) fileStatusEl.textContent = 'Loading layers…'
    try {
      const payload = await api.fetchPreview(path)
      state.previewLayers = Array.isArray(payload.layers) ? payload.layers : []
      if (fileStatusEl) {
        fileStatusEl.textContent = `${state.previewLayers.length} layer(s) in ${path}`
      }
      if (state.mode === 'import' && state.rules.length === 0) {
        addRule('eligible')
      }
      showEditorBody(!!path)
      ensurePreviewMap()
      renderRules()
      updateSaveButton()
      await syncPreviewMap({ fitBounds: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed')
      showEditorBody(false)
    }
  }

  function resetModal() {
    setError('')
    fieldValuesCache.clear()
    destroyPreviewMap()
    state = null
    const {
      fileSelect,
      labelInput,
      fileStatusEl,
      rulesListEl,
      modal,
    } = elements
    if (fileSelect) fileSelect.value = ''
    if (labelInput) labelInput.value = ''
    if (fileStatusEl) {
      fileStatusEl.textContent =
        'Choose a GeoJSON or FileGDB under the project data folder.'
    }
    if (rulesListEl) rulesListEl.innerHTML = ''
    showEditorBody(false)
    updateSaveButton()
    if (modal) modal.label = 'Land source'
  }

  async function populateFileSelect() {
    const { fileSelect } = elements
    if (!fileSelect) return
    fileSelect.innerHTML = '<option value="">Loading…</option>'
    fileSelect.disabled = true
    try {
      const paths = await api.fetchDataFiles()
      fileSelect.innerHTML = '<option value="">Select a file…</option>'
      for (const path of paths) {
        const opt = document.createElement('option')
        opt.value = path
        opt.textContent = path.replace(/^data\//, '')
        fileSelect.appendChild(opt)
      }
    } catch (_) {
      fileSelect.innerHTML = '<option value="">Could not load file list</option>'
    } finally {
      fileSelect.disabled = false
    }
  }

  async function openImport() {
    resetModal()
    state = {
      mode: 'import',
      sourceId: null,
      filePath: '',
      label: '',
      previewLayers: [],
      rules: [],
    }
    const { modal, labelInput, fileField } = elements
    if (modal) modal.label = 'Import land source'
    if (labelInput) labelInput.value = ''
    if (fileField) fileField.hidden = false
    await populateFileSelect()
    callbacks.onImportOpen?.()
    if (modal) void utils.openWaDialog(modal)
  }

  async function openEdit(sourceId) {
    resetModal()
    const source = callbacks.getSource(sourceId)
    if (!source) return
    state = {
      mode: 'edit',
      sourceId,
      filePath: source.path,
      label: source.label || sourceId,
      previewLayers: [],
      rules: (source.layers || []).map((raw) =>
        ruleFromRegisteredLayer(raw, []),
      ),
    }
    const { modal, labelInput, fileField } = elements
    if (modal) modal.label = 'Edit land source'
    if (labelInput) labelInput.value = state.label
    if (fileField) fileField.hidden = true
    setError('')
    try {
      const payload = await api.fetchPreview(source.path)
      state.previewLayers = Array.isArray(payload.layers) ? payload.layers : []
      state.rules = (source.layers || []).map((raw) =>
        ruleFromRegisteredLayer(raw, state.previewLayers),
      )
      if (state.rules.length) {
        collapseAllRulesExcept(state.rules[0].uid)
      }
      showEditorBody(true)
      renderRules()
      updateSaveButton()
      await utils.openWaDialog(modal)
      ensurePreviewMap()
      await syncPreviewMap({ fitBounds: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed')
      window.alert(err instanceof Error ? err.message : 'Preview failed')
    }
  }

  async function save() {
    if (!state) return
    const { modal, labelInput, saveBtn } = elements
    const label = labelInput?.value.trim() || state.sourceId || ''
    const layers = state.rules.map(ruleToPayload)
    if (saveBtn) saveBtn.disabled = true
    setError('')
    try {
      if (state.mode === 'import') {
        if (!state.filePath || !layers.length) return
        const body = { path: state.filePath, layers }
        if (label) body.label = label
        await api.postImport(body)
      } else if (state.sourceId) {
        await api.patchSource(state.sourceId, { label, layers })
        await callbacks.onEditSaved?.(state.sourceId, layers)
      }
      if (modal) modal.open = false
      await callbacks.onSaved?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      updateSaveButton()
    }
  }

  function bindEvents() {
    const { modal, fileSelect, saveBtn, addRuleBtn, labelInput } = elements
    if (fileSelect) {
      fileSelect.addEventListener('change', () => {
        if (!state || state.mode !== 'import') return
        if (fileSelect.value) void loadFilePreview(fileSelect.value)
        else showEditorBody(false)
      })
    }
    if (saveBtn) saveBtn.addEventListener('click', () => void save())
    if (addRuleBtn) addRuleBtn.addEventListener('click', () => addRule('eligible'))
    if (labelInput) {
      labelInput.addEventListener('input', () => {
        if (state) state.label = labelInput.value
        updateSaveButton()
      })
    }
    if (modal) {
      modal.addEventListener('wa-after-show', () => {
        if (!previewMap) return
        requestAnimationFrame(() => {
          previewMap?.resize()
          void syncPreviewMap()
        })
      })
      modal.addEventListener('wa-after-hide', () => resetModal())
    }
  }

  bindEvents()

  return { openImport, openEdit, ruleSummary, purposeLabel, roleToPurpose }
}
