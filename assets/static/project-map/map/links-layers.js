// @ts-check

import {
  LINKS_LAYER,
  LINKS_LABELS_LAYER,
  LINKS_SOURCE,
  DRAFT_LINKS_SOURCE,
  DRAFT_LINKS_LAYER,
  DRAFT_LINKS_LABELS_LAYER,
  MAP_TEXT_FONT,
  SITES_CIRCLE,
} from '../constants.js'

/** @param {number|null|undefined} distanceKm */
export function formatLinkDistanceKm(distanceKm) {
  if (distanceKm == null || Number.isNaN(Number(distanceKm))) return null
  return `${Number(distanceKm).toFixed(1)} km`
}

/** @param {import('geojson').FeatureCollection|null|undefined} geojson */
export function linksGeoJsonWithLabels(geojson) {
  if (!geojson || !geojson.features) return geojson
  return {
    type: geojson.type || 'FeatureCollection',
    features: geojson.features.map((feature) => {
      const props = feature.properties || {}
      const label = formatLinkDistanceKm(props.distance_km)
      return {
        ...feature,
        properties: { ...props, label: label || '' },
      }
    }),
  }
}

/**
 * @param {string} layerId
 * @param {string} sourceId
 * @param {string} visibility
 */
export function linkLabelsLayerSpec(layerId, sourceId, visibility) {
  return {
    id: layerId,
    type: 'symbol',
    source: sourceId,
    filter: ['!=', ['get', 'label'], ''],
    layout: {
      'symbol-placement': 'line-center',
      'text-field': ['get', 'label'],
      'text-size': 11,
      'text-font': MAP_TEXT_FONT,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
      visibility,
    },
    paint: {
      'text-color': '#e8eaed',
      'text-halo-color': '#1a1a1a',
      'text-halo-width': 2,
    },
  }
}

/** Site-link mesh line paint (manual / weak / strong). */
export function linksLinePaint() {
  return {
    'line-color': [
      'case',
      ['get', 'manual'],
      '#0d9488',
      ['==', ['get', 'strength'], 'weak'],
      '#ef4444',
      '#4a6cf7',
    ],
    'line-width': 2.5,
    'line-opacity': 0.85,
    'line-dasharray': [
      'case',
      ['==', ['get', 'strength'], 'weak'],
      ['literal', [4, 3]],
      ['literal', [1, 0]],
    ],
  }
}

/**
 * @param {string} visibility
 */
function linksLabelsLayerSpec(visibility) {
  return {
    id: LINKS_LABELS_LAYER,
    type: 'symbol',
    source: LINKS_SOURCE,
    filter: ['!=', ['get', 'label'], ''],
    layout: {
      'symbol-placement': 'line-center',
      'text-field': ['get', 'label'],
      'text-size': 11,
      'text-font': MAP_TEXT_FONT,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
      visibility,
    },
    paint: {
      'text-color': '#e8eaed',
      'text-halo-color': '#1a1a1a',
      'text-halo-width': 2,
    },
  }
}

/** @param {maplibregl.Map} map */
export function clearLinksMesh(map) {
  if (map.getLayer(LINKS_LABELS_LAYER)) map.removeLayer(LINKS_LABELS_LAYER)
  if (map.getLayer(LINKS_LAYER)) map.removeLayer(LINKS_LAYER)
  if (map.getSource(LINKS_SOURCE)) map.removeSource(LINKS_SOURCE)
}

/**
 * Apply (or clear) the site-links mesh GeoJSON on the map.
 * @param {maplibregl.Map} map
 * @param {import('geojson').FeatureCollection|null} geojson
 * @param {{ visible?: boolean, beforeId?: string }} [opts]
 */
export function applyLinksMeshGeoJson(map, geojson, opts = {}) {
  const features = geojson?.features
  if (!features || !features.length) {
    clearLinksMesh(map)
    return
  }
  const visible = opts.visible !== false
  const linkVisibility = visible ? 'visible' : 'none'
  const beforeId =
    opts.beforeId || (map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined)
  const linePaint = linksLinePaint()
  const data = geojson

  if (map.getSource(LINKS_SOURCE)) {
    map.getSource(LINKS_SOURCE).setData(data)
    if (map.getLayer(LINKS_LAYER)) {
      for (const [key, val] of Object.entries(linePaint)) {
        map.setPaintProperty(LINKS_LAYER, key, val)
      }
    }
    setLinksLayerVisibility(map, visible)
    return
  }

  map.addSource(LINKS_SOURCE, { type: 'geojson', data })
  const lineLayer = {
    id: LINKS_LAYER,
    type: 'line',
    source: LINKS_SOURCE,
    paint: linePaint,
    layout: {
      'line-cap': 'round',
      'line-join': 'round',
      visibility: linkVisibility,
    },
  }
  if (beforeId) map.addLayer(lineLayer, beforeId)
  else map.addLayer(lineLayer)

  const labels = linksLabelsLayerSpec(linkVisibility)
  if (beforeId) map.addLayer(labels, beforeId)
  else map.addLayer(labels)
}

/** @param {maplibregl.Map} map @param {boolean} visible */
export function setLinksLayerVisibility(map, visible) {
  const vis = visible ? 'visible' : 'none'
  for (const id of [LINKS_LAYER, LINKS_LABELS_LAYER]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis)
  }
}

/**
 * @param {import('geojson').FeatureCollection|null|undefined} geojson
 * @param {(feature: import('geojson').Feature) => boolean} [featureVisible]
 */
export function visibleLinkFeatures(geojson, featureVisible) {
  const visible = featureVisible || (() => true)
  return (geojson?.features || []).filter(visible)
}

/** @param {maplibregl.Map} map */
export function removeDraftLinksLayer(map) {
  if (map.getLayer(DRAFT_LINKS_LABELS_LAYER)) map.removeLayer(DRAFT_LINKS_LABELS_LAYER)
  if (map.getLayer(DRAFT_LINKS_LAYER)) map.removeLayer(DRAFT_LINKS_LAYER)
  if (map.getSource(DRAFT_LINKS_SOURCE)) map.removeSource(DRAFT_LINKS_SOURCE)
}

/**
 * @param {maplibregl.Map} map
 * @param {import('geojson').FeatureCollection|null|undefined} geojson
 * @param {(feature: import('geojson').Feature) => boolean} [featureVisible]
 * @param {() => void} [onRaiseLayers]
 */
export function applyDraftLinksLayer(map, geojson, featureVisible, onRaiseLayers) {
  const features = visibleLinkFeatures(geojson, featureVisible)
  if (!features.length) {
    removeDraftLinksLayer(map)
    return
  }
  const labeled = linksGeoJsonWithLabels({ type: 'FeatureCollection', features })
  if (map.getSource(DRAFT_LINKS_SOURCE)) {
    map.getSource(DRAFT_LINKS_SOURCE).setData(labeled)
    if (map.getLayer(DRAFT_LINKS_LAYER)) {
      map.setPaintProperty(DRAFT_LINKS_LAYER, 'line-color', [
        'case',
        ['get', 'manual'],
        '#0d9488',
        '#4a6cf7',
      ])
    }
    if (onRaiseLayers) onRaiseLayers()
    return
  }
  map.addSource(DRAFT_LINKS_SOURCE, { type: 'geojson', data: labeled })
  map.addLayer(
    {
      id: DRAFT_LINKS_LAYER,
      type: 'line',
      source: DRAFT_LINKS_SOURCE,
      paint: {
        'line-color': ['case', ['get', 'manual'], '#0d9488', '#4a6cf7'],
        'line-width': 2.5,
        'line-opacity': 0.85,
      },
      layout: {
        'line-cap': 'round',
        'line-join': 'round',
        visibility: 'visible',
      },
    },
    SITES_CIRCLE,
  )
  map.addLayer(
    linkLabelsLayerSpec(DRAFT_LINKS_LABELS_LAYER, DRAFT_LINKS_SOURCE, 'visible'),
    SITES_CIRCLE,
  )
  if (onRaiseLayers) onRaiseLayers()
}

