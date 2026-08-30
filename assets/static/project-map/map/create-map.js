// @ts-check

import { basemapStyle } from './basemap.js'

/**
 * Create the main project MapLibre map.
 * @param {object} opts
 * @param {string} opts.container
 * @param {string} opts.basemapKey
 * @param {{ center: number[], zoom: number, bearing?: number, pitch?: number }|null} [opts.savedMapState]
 */
export function createProjectMap({ container, basemapKey, savedMapState }) {
  return new maplibregl.Map({
    container,
    style: basemapStyle(basemapKey),
    center: savedMapState ? savedMapState.center : [-98.35, 39.5],
    zoom: savedMapState ? savedMapState.zoom : 4,
    maxPitch: 85,
    bearing: savedMapState ? savedMapState.bearing || 0 : 0,
    pitch: savedMapState ? savedMapState.pitch || 0 : 0,
    maxParallelImageRequests: 64,
    attributionControl: { compact: true },
  })
}
