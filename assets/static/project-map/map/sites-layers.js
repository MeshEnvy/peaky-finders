// @ts-check

import {
  SITES_SOURCE,
  SITES_CIRCLE,
  SITES_LABELS,
  SITES_SELECTED,
  MAP_LABEL_FONT,
} from '../constants.js'

/** @param {object[]} sites */
export function sitesGeoJson(sites) {
  return {
    type: 'FeatureCollection',
    features: sites
      .filter((site) => Number.isFinite(site.lat) && Number.isFinite(site.lon))
      .map((site) => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [site.lon, site.lat] },
        properties: { name: site.name, slug: site.slug },
      })),
  }
}

/** @param {maplibregl.Map} map */
export function ensureSiteLayers(map) {
  if (map.getSource(SITES_SOURCE)) return
  map.addSource(SITES_SOURCE, { type: 'geojson', data: sitesGeoJson([]) })
  map.addLayer({
    id: SITES_CIRCLE,
    type: 'circle',
    source: SITES_SOURCE,
    paint: {
      'circle-radius': 7,
      'circle-color': '#4a6cf7',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff',
    },
  })
  map.addLayer({
    id: SITES_LABELS,
    type: 'symbol',
    source: SITES_SOURCE,
    layout: {
      'text-field': ['get', 'name'],
      'text-size': 12,
      'text-offset': [0, -1.4],
      'text-anchor': 'bottom',
      'text-font': MAP_LABEL_FONT,
      'text-allow-overlap': true,
    },
    paint: {
      'text-color': '#e8eaed',
      'text-halo-color': '#1a1a1a',
      'text-halo-width': 2,
    },
  })
  map.addLayer({
    id: SITES_SELECTED,
    type: 'circle',
    source: SITES_SOURCE,
    filter: ['==', ['get', 'slug'], ''],
    paint: {
      'circle-radius': 11,
      'circle-color': '#4a6cf7',
      'circle-stroke-width': 3,
      'circle-stroke-color': '#fbbf24',
      'circle-opacity': 0.35,
    },
  })
}

/** @param {maplibregl.Map} map @param {import('geojson').FeatureCollection} data */
export function setSitesSourceData(map, data) {
  if (!map.getSource(SITES_SOURCE)) return
  map.getSource(SITES_SOURCE).setData(data)
}
