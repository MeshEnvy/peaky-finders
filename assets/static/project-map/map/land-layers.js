// @ts-check

import {
  LAND_DEFAULT_FILL_COLOR,
  LAND_DEFAULT_FILL_OPACITY,
  LAND_DEFAULT_LINE_COLOR,
  LAND_LINE_WIDTH,
  MAP_LABEL_FONT,
} from '../constants.js'
import { LAND_OVERLAY_SOURCE_ID } from './land-overlays.js'
import { landMapStackRank } from '../stores/land-sidebar-view.js'

export function defaultLandLayerStyle() {
  return {
    color: LAND_DEFAULT_FILL_COLOR,
    opacity: LAND_DEFAULT_FILL_OPACITY,
  }
}

/** @param {string} hex */
export function landLineColorFromFill(hex) {
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

/** @param {object|null|undefined} raw */
export function normalizeLandLayerStyle(raw) {
  const defaults = defaultLandLayerStyle()
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

/** @param {Record<string, unknown>|null} styleMap @param {string} prop @param {object} fallback */
export function buildStyleMatchExpression(styleMap, prop, fallback) {
  const normalized = normalizeLandLayerStyle(fallback)
  const expr = ['match', ['get', prop]]
  for (const [key, rawStyle] of Object.entries(styleMap || {})) {
    const style = normalizeLandLayerStyle(rawStyle)
    expr.push(String(key), style.color)
  }
  expr.push(normalized.color)
  return expr
}

/** @param {Record<string, unknown>|null} styleMap @param {string} prop @param {object} fallback */
export function buildOpacityMatchExpression(styleMap, prop, fallback) {
  const normalized = normalizeLandLayerStyle(fallback)
  const expr = ['match', ['get', prop]]
  for (const [key, rawStyle] of Object.entries(styleMap || {})) {
    const style = normalizeLandLayerStyle(rawStyle)
    expr.push(String(key), style.opacity)
  }
  expr.push(normalized.opacity)
  return expr
}

/**
 * @param {import('geojson').FeatureCollection|null|undefined} geojson
 * @param {{ labelField?: string, styleField?: string }} [opts]
 */
export function decorateLandGeoJsonProperties(geojson, { labelField = '', styleField = '' } = {}) {
  if (!geojson?.features?.length) return geojson
  const labelKey = String(labelField || '').trim()
  const styleKey = String(styleField || '').trim()
  if (!labelKey && !styleKey) return geojson
  for (const feat of geojson.features) {
    if (!feat.properties) feat.properties = {}
    if (labelKey) {
      const raw = feat.properties[labelKey]
      if (raw != null && raw !== '') feat.properties.label = String(raw)
    }
    if (styleKey) {
      const raw = feat.properties[styleKey]
      if (raw != null && raw !== '') feat.properties.style_key = String(raw)
    }
  }
  return geojson
}

/** @param {string} text */
export function landLayerSlug(text) {
  return (
    String(text)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'layer'
  )
}

/** @param {string} sourceId @param {string} layer */
export function landMapSourceId(sourceId, layer) {
  return `land-${sourceId}-${landLayerSlug(layer)}`
}

/** @param {object|null|undefined} spec */
export function styleMapFromLayerSpec(spec) {
  if (!spec?.style) return null
  if (spec.styleField && typeof spec.style === 'object' && !('color' in spec.style)) {
    return spec.style
  }
  return null
}

/** @param {object|null|undefined} spec */
export function flatStyleFromLayerSpec(spec) {
  if (!spec?.style) return defaultLandLayerStyle()
  if (typeof spec.style === 'object' && 'color' in spec.style) {
    return normalizeLandLayerStyle(spec.style)
  }
  if (spec.styleField && typeof spec.style === 'object') {
    const first = Object.values(spec.style)[0]
    return normalizeLandLayerStyle(first)
  }
  return defaultLandLayerStyle()
}

/**
 * @param {maplibregl.Map} mapInstance
 * @param {string} sourceId
 * @param {string} labelsId
 * @param {boolean} showLabels
 * @param {string} visibility
 */
export function syncGeoJsonLabelLayer(mapInstance, sourceId, labelsId, showLabels, visibility) {
  if (!mapInstance) return
  const vis = visibility === 'none' ? 'none' : 'visible'
  if (!showLabels) {
    if (mapInstance.getLayer(labelsId)) mapInstance.removeLayer(labelsId)
    return
  }
  if (!mapInstance.getSource(sourceId)) return
  if (!mapInstance.getLayer(labelsId)) {
    mapInstance.addLayer({
      id: labelsId,
      type: 'symbol',
      source: sourceId,
      filter: ['all', ['has', 'label'], ['!=', ['get', 'label'], '']],
      layout: {
        'text-field': ['get', 'label'],
        'text-size': 11,
        'text-font': MAP_LABEL_FONT,
        'text-allow-overlap': true,
        'text-max-width': 14,
        visibility: vis,
      },
      paint: {
        'text-color': '#f0f4ff',
        'text-halo-color': '#141820',
        'text-halo-width': 1.5,
      },
    })
  } else {
    mapInstance.setLayoutProperty(labelsId, 'visibility', vis)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {string} sourceId
 * @param {string} layerKey
 * @param {object} spec
 * @param {boolean} layerVisible
 * @param {boolean} labelsVisible
 * @param {boolean} hasLabels
 */
export function syncLandMapLabelLayer(map, sourceId, layerKey, spec, layerVisible, labelsVisible, hasLabels) {
  const sourceMapId = landMapSourceId(sourceId, layerKey)
  const labelsId = `${sourceMapId}-labels`
  const vis = layerVisible && labelsVisible ? 'visible' : 'none'
  syncGeoJsonLabelLayer(map, sourceMapId, labelsId, hasLabels, vis)
}

/**
 * @param {maplibregl.Map} map
 * @param {string} sourceId
 * @param {string} layer
 * @param {boolean} visible
 */
export function syncLandMapLayerVisibility(map, sourceId, layer, visible) {
  const sourceMapId = landMapSourceId(sourceId, layer)
  const fillId = `${sourceMapId}-fill`
  const lineId = `${sourceMapId}-line`
  const vis = visible ? 'visible' : 'none'
  if (map.getLayer(fillId)) map.setLayoutProperty(fillId, 'visibility', vis)
  if (map.getLayer(lineId)) map.setLayoutProperty(lineId, 'visibility', vis)
}

/**
 * @param {maplibregl.Map} map
 * @param {string} sourceId
 * @param {string} layerKey
 * @param {object|null} spec
 */
export function applyLandMapLayerStyle(map, sourceId, layerKey, spec) {
  const sourceMapId = landMapSourceId(sourceId, layerKey)
  const fillId = `${sourceMapId}-fill`
  const lineId = `${sourceMapId}-line`
  const styleMap = styleMapFromLayerSpec(spec)
  const fallback = flatStyleFromLayerSpec(spec)
  const lineFallback = landLineColorFromFill(fallback.color)
  if (map.getLayer(fillId)) {
    if (spec?.styleField && styleMap) {
      map.setPaintProperty(
        fillId,
        'fill-color',
        buildStyleMatchExpression(styleMap, 'style_key', fallback),
      )
      map.setPaintProperty(
        fillId,
        'fill-opacity',
        buildOpacityMatchExpression(styleMap, 'style_key', fallback),
      )
    } else {
      map.setPaintProperty(fillId, 'fill-color', fallback.color)
      map.setPaintProperty(fillId, 'fill-opacity', fallback.opacity)
    }
    map.setPaintProperty(fillId, 'fill-outline-color', lineFallback)
  }
  if (map.getLayer(lineId)) {
    map.setPaintProperty(lineId, 'line-color', lineFallback)
    map.setPaintProperty(lineId, 'line-width', LAND_LINE_WIDTH)
  }
}

/** @param {maplibregl.Map} map @param {string} sourceId @param {string} layer */
export function removeLandMapLayer(map, sourceId, layer) {
  const sourceMapId = landMapSourceId(sourceId, layer)
  const fillId = `${sourceMapId}-fill`
  const lineId = `${sourceMapId}-line`
  const labelsId = `${sourceMapId}-labels`
  if (map.getLayer(labelsId)) map.removeLayer(labelsId)
  if (map.getLayer(lineId)) map.removeLayer(lineId)
  if (map.getLayer(fillId)) map.removeLayer(fillId)
  if (map.getSource(sourceMapId)) map.removeSource(sourceMapId)
}

/** @param {string} overlayId @param {string} [partId] */
export function landOverlayMapIds(overlayId, partId = '') {
  const layerKey = partId ? `${overlayId}-${partId}` : overlayId
  const sourceMapId = landMapSourceId(LAND_OVERLAY_SOURCE_ID, layerKey)
  return {
    sourceMapId,
    fillId: `${sourceMapId}-fill`,
    lineId: `${sourceMapId}-line`,
  }
}

/**
 * @param {string} overlayId
 * @param {string[]} partIds
 */
export function landOverlayPartMapIds(overlayId, partIds) {
  if (!partIds.length) return [landOverlayMapIds(overlayId)]
  return partIds.map((partId) => landOverlayMapIds(overlayId, partId))
}

/**
 * @param {maplibregl.Map} map
 * @param {string} overlayId
 * @param {string[]} partIds
 * @param {boolean} visible
 */
export function syncLandOverlayVisibility(map, overlayId, partIds, visible) {
  const vis = visible ? 'visible' : 'none'
  for (const { fillId, lineId } of landOverlayPartMapIds(overlayId, partIds)) {
    if (map.getLayer(fillId)) map.setLayoutProperty(fillId, 'visibility', vis)
    if (map.getLayer(lineId)) map.setLayoutProperty(lineId, 'visibility', vis)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {string} overlayId
 * @param {string[]} partIds
 */
export function removeLandOverlayLayer(map, overlayId, partIds) {
  for (const { sourceMapId, fillId, lineId } of landOverlayPartMapIds(overlayId, partIds)) {
    if (map.getLayer(lineId)) map.removeLayer(lineId)
    if (map.getLayer(fillId)) map.removeLayer(fillId)
    if (map.getSource(sourceMapId)) map.removeSource(sourceMapId)
  }
  const leftover = landOverlayMapIds(overlayId)
  if (map.getLayer(leftover.lineId)) map.removeLayer(leftover.lineId)
  if (map.getLayer(leftover.fillId)) map.removeLayer(leftover.fillId)
  if (map.getSource(leftover.sourceMapId)) map.removeSource(leftover.sourceMapId)
}

/**
 * @param {maplibregl.Map} map
 * @param {string} overlayId
 * @param {string} partId
 * @param {import('geojson').FeatureCollection} geojson
 * @param {{ id: string, color: string, opacity: number }} spec
 * @param {string|undefined} beforeId
 */
export function addLandOverlayPartLayer(map, overlayId, partId, geojson, spec, beforeId) {
  const { sourceMapId, fillId } = landOverlayMapIds(overlayId, partId)
  if (map.getSource(sourceMapId)) {
    map.getSource(sourceMapId).setData(geojson)
    return
  }
  map.addSource(sourceMapId, { type: 'geojson', data: geojson })
  map.addLayer(
    {
      id: fillId,
      type: 'fill',
      source: sourceMapId,
      paint: {
        'fill-color': spec.color,
        'fill-opacity': spec.opacity,
      },
      layout: { visibility: 'visible' },
    },
    beforeId,
  )
}

/**
 * Add registered land layer fill/line sources on the map.
 * @param {maplibregl.Map} map
 * @param {string} sourceId
 * @param {string} layerKey
 * @param {import('geojson').FeatureCollection} geojson
 * @param {object|null} spec
 * @param {boolean} visible
 * @param {string|undefined} beforeId
 */
export function addLandRegisteredLayer(map, sourceId, layerKey, geojson, spec, visible, beforeId) {
  const sourceMapId = landMapSourceId(sourceId, layerKey)
  const fillId = `${sourceMapId}-fill`
  const lineId = `${sourceMapId}-line`
  const styleMap = styleMapFromLayerSpec(spec)
  const fallback = flatStyleFromLayerSpec(spec)
  const lineColor = landLineColorFromFill(fallback.color)
  const fillPaint =
    spec?.styleField && styleMap
      ? {
          'fill-color': buildStyleMatchExpression(styleMap, 'style_key', fallback),
          'fill-opacity': buildOpacityMatchExpression(styleMap, 'style_key', fallback),
          'fill-outline-color': lineColor,
        }
      : {
          'fill-color': fallback.color,
          'fill-opacity': fallback.opacity,
          'fill-outline-color': lineColor,
        }
  map.addSource(sourceMapId, { type: 'geojson', data: geojson })
  map.addLayer(
    {
      id: fillId,
      type: 'fill',
      source: sourceMapId,
      paint: fillPaint,
      layout: { visibility: visible ? 'visible' : 'none' },
    },
    beforeId,
  )
  map.addLayer(
    {
      id: lineId,
      type: 'line',
      source: sourceMapId,
      paint: {
        'line-color': lineColor,
        'line-width': LAND_LINE_WIDTH,
      },
      layout: { visibility: visible ? 'visible' : 'none' },
    },
    beforeId,
  )
}

/**
 * Reorder land layers by role stack rank.
 * @param {maplibregl.Map} map
 * @param {object[]} rows
 * @param {string|undefined} anchor
 * @param {() => void} [raiseSiteLayers]
 */
export function syncLandMapLayerOrder(map, rows, anchor, raiseSiteLayers) {
  const sorted = [...rows].sort(
    (a, b) => landMapStackRank(a.spec?.role) - landMapStackRank(b.spec?.role),
  )
  for (const row of sorted) {
    const sourceMapId = landMapSourceId(row.sourceId, row.layerKey)
    for (const suffix of ['-fill', '-line', '-labels']) {
      const id = `${sourceMapId}${suffix}`
      if (map.getLayer(id)) {
        try {
          map.moveLayer(id, anchor)
        } catch (_) {
          /* layer may be mid-remove */
        }
      }
    }
  }
  if (raiseSiteLayers) raiseSiteLayers()
}
