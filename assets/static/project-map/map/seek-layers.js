// @ts-check

import {
  DRAFT_MARKER_COLOR,
  MAP_LABEL_FONT,
  SITES_CIRCLE,
  SEEK_CANDIDATES_SOURCE,
  SEEK_CANDIDATES_LAYER,
  SEEK_CANDIDATES_LABELS_LAYER,
  SEEK_LINES_SOURCE,
  SEEK_LINES_LAYER,
  SEEK_LINES_LABELS_LAYER,
  SEEK_PATH_SOURCE,
  SEEK_PATH_LAYER,
  SEEK_GOAL_LINE_SOURCE,
  SEEK_GOAL_LINE_LAYER,
  SEEK_WEDGE_SOURCE,
  SEEK_WEDGE_FILL_LAYER,
  SEEK_WEDGE_OUTLINE_LAYER,
} from '../constants.js'
import { buildSeekWedgeFeature, buildSeekGoalLineFeature } from '../geo.js'
import { linkLabelsLayerSpec, linksGeoJsonWithLabels } from './links-layers.js'

/** @param {string} layerId @param {string} sourceId */
export function seekSiteCandidateLabelsLayerSpec(layerId, sourceId) {
  return {
    id: layerId,
    type: 'symbol',
    source: sourceId,
    filter: [
      'any',
      [
        'all',
        ['boolean', ['get', 'is_site'], false],
        ['has', 'site_name'],
        ['!=', ['get', 'site_name'], ''],
      ],
      [
        'all',
        ['!', ['boolean', ['get', 'is_goal'], false]],
        ['!', ['boolean', ['get', 'is_site'], false]],
        ['has', 'elev_m'],
      ],
    ],
    layout: {
      'text-field': [
        'case',
        ['boolean', ['get', 'is_site'], false],
        ['get', 'site_name'],
        ['concat', ['to-string', ['round', ['get', 'elev_m']]], 'm'],
      ],
      'text-size': 12,
      'text-offset': [0, -1.4],
      'text-anchor': 'bottom',
      'text-font': MAP_LABEL_FONT,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
      visibility: 'visible',
    },
    paint: {
      'text-color': '#e8eaed',
      'text-halo-color': '#1a1a1a',
      'text-halo-width': 2,
    },
  }
}

/** @param {import('geojson').FeatureCollection|null|undefined} geojson */
export function seekLinesGeoJsonWithLabels(geojson) {
  if (!geojson || !geojson.features) return geojson
  return {
    type: geojson.type || 'FeatureCollection',
    features: geojson.features.map((feature) => {
      const props = feature.properties || {}
      const dist =
        props.distance_km != null && !Number.isNaN(Number(props.distance_km))
          ? `${Number(props.distance_km).toFixed(1)} km`
          : null
      const bearing =
        props.bearing_deg != null ? `${Math.round(Number(props.bearing_deg))}°` : ''
      const label = dist && bearing ? `${dist} · ${bearing}` : dist || bearing || ''
      return { ...feature, properties: { ...props, label } }
    }),
  }
}

/**
 * @param {import('geojson').FeatureCollection|null|undefined} candidates
 * @param {(slug: string) => string} siteNameForSlug
 * @param {(slug: string) => boolean} isSiteHidden
 */
export function seekCandidatesGeoJsonForDisplay(candidates, siteNameForSlug, isSiteHidden) {
  if (!candidates?.features) return candidates
  return {
    type: candidates.type || 'FeatureCollection',
    features: candidates.features.map((feature) => {
      const props = feature.properties || {}
      const slug = props.site_slug
      if (!props.is_site || !slug) return feature
      const siteName = props.site_name || siteNameForSlug(String(slug)) || ''
      const labelName = isSiteHidden(String(slug)) ? siteName : ''
      return { ...feature, properties: { ...props, site_name: labelName } }
    }),
  }
}

/** @param {maplibregl.Map} map */
export function removeSeekCandidateLayers(map) {
  const layerIds = [
    SEEK_LINES_LABELS_LAYER,
    SEEK_LINES_LAYER,
    SEEK_CANDIDATES_LABELS_LAYER,
    SEEK_CANDIDATES_LAYER,
    SEEK_GOAL_LINE_LAYER,
  ]
  for (const id of layerIds) {
    if (map.getLayer(id)) map.removeLayer(id)
  }
  for (const src of [SEEK_LINES_SOURCE, SEEK_CANDIDATES_SOURCE, SEEK_GOAL_LINE_SOURCE]) {
    if (map.getSource(src)) map.removeSource(src)
  }
}

/** @param {maplibregl.Map} map @param {() => void} [clearMarkers] */
export function removeSeekLayers(map, clearMarkers) {
  removeSeekCandidateLayers(map)
  if (map.getLayer(SEEK_PATH_LAYER)) map.removeLayer(SEEK_PATH_LAYER)
  if (map.getSource(SEEK_PATH_SOURCE)) map.removeSource(SEEK_PATH_SOURCE)
  removeSeekWedgeLayers(map)
  if (clearMarkers) clearMarkers()
}

/** @param {maplibregl.Map} map */
export function removeSeekGoalLineLayer(map) {
  if (map.getLayer(SEEK_GOAL_LINE_LAYER)) map.removeLayer(SEEK_GOAL_LINE_LAYER)
  if (map.getSource(SEEK_GOAL_LINE_SOURCE)) map.removeSource(SEEK_GOAL_LINE_SOURCE)
}

/** @param {maplibregl.Map} map */
export function removeSeekWedgeLayers(map) {
  if (map.getLayer(SEEK_WEDGE_OUTLINE_LAYER)) map.removeLayer(SEEK_WEDGE_OUTLINE_LAYER)
  if (map.getLayer(SEEK_WEDGE_FILL_LAYER)) map.removeLayer(SEEK_WEDGE_FILL_LAYER)
  if (map.getSource(SEEK_WEDGE_SOURCE)) map.removeSource(SEEK_WEDGE_SOURCE)
}

/**
 * @param {maplibregl.Map} map
 * @param {{ lat: number, lon: number }} from
 * @param {{ lat: number, lon: number }} goal
 * @param {number} hopRadiusM
 */
export function syncSeekWedge(map, from, goal, hopRadiusM) {
  const data = {
    type: 'FeatureCollection',
    features: [buildSeekWedgeFeature(from, goal, hopRadiusM)],
  }
  if (map.getSource(SEEK_WEDGE_SOURCE)) {
    map.getSource(SEEK_WEDGE_SOURCE).setData(data)
    return
  }
  map.addSource(SEEK_WEDGE_SOURCE, { type: 'geojson', data })
  map.addLayer(
    {
      id: SEEK_WEDGE_FILL_LAYER,
      type: 'fill',
      source: SEEK_WEDGE_SOURCE,
      paint: {
        'fill-color': '#22c55e',
        'fill-opacity': 0.1,
      },
    },
    SITES_CIRCLE,
  )
  map.addLayer(
    {
      id: SEEK_WEDGE_OUTLINE_LAYER,
      type: 'line',
      source: SEEK_WEDGE_SOURCE,
      paint: {
        'line-color': '#22c55e',
        'line-width': 1.5,
        'line-opacity': 0.45,
        'line-dasharray': [2, 2],
      },
      layout: { 'line-cap': 'round', 'line-join': 'round' },
    },
    SITES_CIRCLE,
  )
}

/**
 * @param {maplibregl.Map} map
 * @param {{ lat: number, lon: number }} from
 * @param {{ lat: number, lon: number }} goal
 * @param {number} hopRadiusM
 * @param {() => void} [raiseSiteLayers]
 */
export function syncSeekGoalLine(map, from, goal, hopRadiusM, raiseSiteLayers) {
  const data = {
    type: 'FeatureCollection',
    features: [buildSeekGoalLineFeature(from, goal)],
  }
  if (map.getSource(SEEK_GOAL_LINE_SOURCE)) {
    map.getSource(SEEK_GOAL_LINE_SOURCE).setData(data)
  } else {
    map.addSource(SEEK_GOAL_LINE_SOURCE, { type: 'geojson', data })
    map.addLayer(
      {
        id: SEEK_GOAL_LINE_LAYER,
        type: 'line',
        source: SEEK_GOAL_LINE_SOURCE,
        paint: {
          'line-color': '#22c55e',
          'line-width': 2,
          'line-opacity': 0.75,
          'line-dasharray': [4, 3],
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      },
      SITES_CIRCLE,
    )
  }
  syncSeekWedge(map, from, goal, hopRadiusM)
  if (raiseSiteLayers) raiseSiteLayers()
}

/**
 * @param {maplibregl.Map} map
 * @param {object[]} hops
 * @param {() => void} [onClearMarkers]
 * @param {(markers: maplibregl.Marker[]) => void} [setMarkers]
 * @param {() => void} [appendHopMarkers]
 * @param {() => void} [onAfterPath]
 */
export function updateSeekPathLayer(map, hops, onClearMarkers, appendHopMarkers, onAfterPath) {
  if (!hops || hops.length < 2) {
    if (map.getLayer(SEEK_PATH_LAYER)) map.removeLayer(SEEK_PATH_LAYER)
    if (map.getSource(SEEK_PATH_SOURCE)) map.removeSource(SEEK_PATH_SOURCE)
    if (onClearMarkers) onClearMarkers()
    return
  }
  const coords = hops.map((hop) => [hop.lon, hop.lat])
  const pathGeoJson = {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: coords },
    properties: {},
  }
  if (map.getSource(SEEK_PATH_SOURCE)) {
    map.getSource(SEEK_PATH_SOURCE).setData(pathGeoJson)
  } else {
    map.addSource(SEEK_PATH_SOURCE, { type: 'geojson', data: pathGeoJson })
    map.addLayer(
      {
        id: SEEK_PATH_LAYER,
        type: 'line',
        source: SEEK_PATH_SOURCE,
        paint: {
          'line-color': '#fbbf24',
          'line-width': 3,
          'line-opacity': 0.85,
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      },
      SITES_CIRCLE,
    )
  }
  if (onClearMarkers) onClearMarkers()
  if (appendHopMarkers) appendHopMarkers()
  if (onAfterPath) onAfterPath()
}

/**
 * Apply seek candidate + line layers from a scan payload.
 * @param {maplibregl.Map} map
 * @param {{
 *   candidates?: import('geojson').FeatureCollection,
 *   lines?: import('geojson').FeatureCollection,
 * }} payload
 * @param {{
 *   filterLineFeatures: (features: import('geojson').Feature[]) => import('geojson').Feature[],
 *   filterCandidateFeatures: (features: import('geojson').Feature[]) => import('geojson').Feature[],
 *   candidatesForDisplay: (c: import('geojson').FeatureCollection) => import('geojson').FeatureCollection,
 *   onSiteSlugs?: (slugs: Set<string>) => void,
 *   onPathUpdate?: () => void,
 *   onGoalLine?: () => void,
 *   raiseSiteLayers?: () => void,
 * }} opts
 */
export function applySeekLayers(map, payload, opts) {
  if (!payload) return
  removeSeekCandidateLayers(map)

  const siteSlugs = new Set()
  for (const feature of payload.candidates?.features || []) {
    const props = feature?.properties || {}
    const v = props.rf_viable
    if (props.is_site && props.site_slug && (v === true || v === 'true')) {
      siteSlugs.add(String(props.site_slug))
    }
  }
  if (opts.onSiteSlugs) opts.onSiteSlugs(siteSlugs)

  const lines = payload.lines
  if (lines?.features?.length) {
    const filteredLines = opts.filterLineFeatures(lines.features)
    if (filteredLines.length) {
      const labeled = seekLinesGeoJsonWithLabels({
        ...lines,
        features: filteredLines,
      })
      map.addSource(SEEK_LINES_SOURCE, { type: 'geojson', data: labeled })
      map.addLayer(
        {
          id: SEEK_LINES_LAYER,
          type: 'line',
          source: SEEK_LINES_SOURCE,
          paint: {
            'line-color': [
              'case',
              ['boolean', ['get', 'is_goal'], false],
              '#22c55e',
              ['boolean', ['get', 'is_site'], false],
              DRAFT_MARKER_COLOR,
              ['case', ['get', 'rf_viable'], '#4a6cf7', '#94a3b8'],
            ],
            'line-width': [
              'case',
              ['boolean', ['get', 'is_goal'], false],
              3.5,
              ['boolean', ['get', 'is_site'], false],
              3,
              2.5,
            ],
            'line-opacity': 0.9,
            'line-dasharray': [
              'case',
              ['boolean', ['get', 'is_goal'], false],
              ['case', ['get', 'rf_viable'], ['literal', [1, 0]], ['literal', [2, 2]]],
              ['case', ['get', 'rf_viable'], ['literal', [1, 0]], ['literal', [2, 2]]],
            ],
          },
          layout: { 'line-cap': 'round', 'line-join': 'round' },
        },
        SITES_CIRCLE,
      )
      map.addLayer(
        linkLabelsLayerSpec(SEEK_LINES_LABELS_LAYER, SEEK_LINES_SOURCE, 'visible'),
        SITES_CIRCLE,
      )
    }
  }

  const candidates = opts.candidatesForDisplay({
    ...payload.candidates,
    features: opts.filterCandidateFeatures(payload.candidates?.features || []),
  })
  if (candidates?.features?.length) {
    map.addSource(SEEK_CANDIDATES_SOURCE, { type: 'geojson', data: candidates })
    map.addLayer(
      {
        id: SEEK_CANDIDATES_LAYER,
        type: 'circle',
        source: SEEK_CANDIDATES_SOURCE,
        paint: {
          'circle-radius': [
            'case',
            ['boolean', ['get', 'is_goal'], false],
            10,
            ['boolean', ['get', 'is_site'], false],
            7,
            7,
          ],
          'circle-color': [
            'case',
            ['boolean', ['get', 'is_goal'], false],
            '#22c55e',
            ['boolean', ['get', 'is_site'], false],
            '#4a6cf7',
            '#fb923c',
          ],
          'circle-stroke-color': [
            'case',
            ['boolean', ['get', 'is_site'], false],
            '#ffffff',
            '#1a1a1a',
          ],
          'circle-stroke-width': [
            'case',
            ['boolean', ['get', 'is_site'], false],
            2,
            1.5,
          ],
        },
      },
      SITES_CIRCLE,
    )
    map.addLayer(
      seekSiteCandidateLabelsLayerSpec(SEEK_CANDIDATES_LABELS_LAYER, SEEK_CANDIDATES_SOURCE),
      SITES_CIRCLE,
    )
  }

  if (opts.onPathUpdate) opts.onPathUpdate()
  if (opts.onGoalLine) opts.onGoalLine()
  if (opts.raiseSiteLayers) opts.raiseSiteLayers()
}
