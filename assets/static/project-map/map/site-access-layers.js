// @ts-check

import {
  SITES_ACCESS_LINE,
  SITES_ACCESS_SOURCE,
  SITES_JEEP_LINE,
  SITES_JEEP_SOURCE,
  SITES_PARK_CIRCLE,
  SITES_PARK_SOURCE,
  SITES_PAVED_CIRCLE,
  SITES_PAVED_SOURCE,
  SITES_CIRCLE,
} from '../constants.js'
import {
  peaksAccessGeoJson,
  peaksJeepGeoJson,
  peaksPavedGeoJson,
  peaksRoadGeoJson,
} from './peaks-layers.js'

/**
 * @param {maplibregl.Map} map
 * @param {string} beforeId
 */
export function ensureSiteAccessLayers(map, beforeId = SITES_CIRCLE) {
  if (!map.getSource(SITES_JEEP_SOURCE)) {
    map.addSource(SITES_JEEP_SOURCE, { type: 'geojson', data: peaksJeepGeoJson([]) })
  }
  if (!map.getSource(SITES_ACCESS_SOURCE)) {
    map.addSource(SITES_ACCESS_SOURCE, { type: 'geojson', data: peaksAccessGeoJson([]) })
  }
  if (!map.getSource(SITES_PAVED_SOURCE)) {
    map.addSource(SITES_PAVED_SOURCE, { type: 'geojson', data: peaksPavedGeoJson([]) })
  }
  if (!map.getSource(SITES_PARK_SOURCE)) {
    map.addSource(SITES_PARK_SOURCE, { type: 'geojson', data: peaksRoadGeoJson([]) })
  }
  if (!map.getLayer(SITES_JEEP_LINE)) {
    map.addLayer(
      {
        id: SITES_JEEP_LINE,
        type: 'line',
        source: SITES_JEEP_SOURCE,
        paint: {
          'line-color': '#ea580c',
          'line-width': 3.5,
          'line-opacity': 0.85,
        },
      },
      beforeId,
    )
  } else {
    map.setPaintProperty(SITES_JEEP_LINE, 'line-color', '#ea580c')
  }
  if (!map.getLayer(SITES_ACCESS_LINE)) {
    map.addLayer(
      {
        id: SITES_ACCESS_LINE,
        type: 'line',
        source: SITES_ACCESS_SOURCE,
        paint: {
          'line-color': '#22c55e',
          'line-width': 3,
          'line-opacity': 0.9,
          'line-dasharray': [1.5, 1.2],
        },
      },
      beforeId,
    )
  }
  if (!map.getLayer(SITES_PAVED_CIRCLE)) {
    map.addLayer(
      {
        id: SITES_PAVED_CIRCLE,
        type: 'circle',
        source: SITES_PAVED_SOURCE,
        paint: {
          'circle-radius': 5,
          'circle-color': '#c2410c',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#fff7ed',
        },
      },
      beforeId,
    )
  } else {
    map.setPaintProperty(SITES_PAVED_CIRCLE, 'circle-color', '#c2410c')
    map.setPaintProperty(SITES_PAVED_CIRCLE, 'circle-stroke-color', '#fff7ed')
  }
  if (!map.getLayer(SITES_PARK_CIRCLE)) {
    map.addLayer(
      {
        id: SITES_PARK_CIRCLE,
        type: 'circle',
        source: SITES_PARK_SOURCE,
        paint: {
          'circle-radius': 5,
          'circle-color': '#eab308',
          'circle-stroke-width': 1.5,
          'circle-stroke-color': '#fefce8',
        },
      },
      beforeId,
    )
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {object[]} places  // peak-shaped access rows with slug/lat/lon/hike/jeep/…
 */
export function setSiteAccessLayerData(map, places) {
  if (map.getSource(SITES_JEEP_SOURCE)) {
    map.getSource(SITES_JEEP_SOURCE).setData(peaksJeepGeoJson(places))
  }
  if (map.getSource(SITES_ACCESS_SOURCE)) {
    map.getSource(SITES_ACCESS_SOURCE).setData(peaksAccessGeoJson(places))
  }
  if (map.getSource(SITES_PAVED_SOURCE)) {
    map.getSource(SITES_PAVED_SOURCE).setData(peaksPavedGeoJson(places))
  }
  if (map.getSource(SITES_PARK_SOURCE)) {
    map.getSource(SITES_PARK_SOURCE).setData(peaksRoadGeoJson(places))
  }
}
