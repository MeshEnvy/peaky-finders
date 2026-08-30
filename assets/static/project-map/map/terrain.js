// @ts-check

import {
  PITCH_TERRAIN_ON,
  PITCH_TERRAIN_OFF,
  TERRAIN_SOURCE,
  TERRAIN_HILLSHADE,
} from '../constants.js'
import { terrainDemSourceSpec, usesSkadiAnalysisDem } from './basemap.js'

/** @param {maplibregl.Map} map @param {string} basemapKey */
export function ensureTerrainSource(map, basemapKey) {
  if (map.getSource(TERRAIN_SOURCE)) {
    const spec = terrainDemSourceSpec(basemapKey)
    const src = map.getSource(TERRAIN_SOURCE)
    if (src && typeof src.setTiles === 'function') {
      src.setTiles(spec.tiles)
    }
    return
  }
  map.addSource(TERRAIN_SOURCE, terrainDemSourceSpec(basemapKey))
}

/** @param {maplibregl.Map} map @param {string} basemapKey */
function ensureHillshadeLayer(map, basemapKey) {
  if (map.getLayer(TERRAIN_HILLSHADE)) return
  ensureTerrainSource(map, basemapKey)
  map.addLayer(
    {
      id: TERRAIN_HILLSHADE,
      type: 'hillshade',
      source: TERRAIN_SOURCE,
      paint: {
        'hillshade-exaggeration': 0.45,
        'hillshade-shadow-color': '#3d4654',
        'hillshade-highlight-color': '#f8fafc',
        'hillshade-accent-color': '#94a3b8',
      },
    },
    'basemap'
  )
}

/** @param {maplibregl.Map} map */
function removeHillshadeLayer(map) {
  if (map.getLayer(TERRAIN_HILLSHADE)) map.removeLayer(TERRAIN_HILLSHADE)
}

/** @param {maplibregl.Map} map */
function removeTerrainSource(map) {
  removeHillshadeLayer(map)
  if (map.getSource(TERRAIN_SOURCE)) map.removeSource(TERRAIN_SOURCE)
}

/** @param {string} basemapKey */
function basemapRasterOpacityForTerrain(basemapKey) {
  if (usesSkadiAnalysisDem(basemapKey)) return 0
  return 0.9
}

/**
 * @param {maplibregl.Map} map
 * @param {{ basemapKey: string, onRaiseLayers?: () => void }} opts
 */
export function showTerrainOverlays(map, { basemapKey, onRaiseLayers }) {
  ensureTerrainSource(map, basemapKey)
  ensureHillshadeLayer(map, basemapKey)
  map.setTerrain({ source: TERRAIN_SOURCE, exaggeration: 1.35 })
  if (map.getLayer('basemap')) {
    map.setPaintProperty('basemap', 'raster-opacity', basemapRasterOpacityForTerrain(basemapKey))
  }
  if (typeof onRaiseLayers === 'function') onRaiseLayers()
}

/** @param {maplibregl.Map} map */
export function hideTerrainOverlays(map) {
  map.setTerrain(null)
  removeTerrainSource(map)
  if (map.getLayer('basemap')) {
    map.setPaintProperty('basemap', 'raster-opacity', 1)
  }
}

/**
 * @param {maplibregl.Map} map
 * @param {{ mapReady: boolean, terrainActive: boolean, basemapKey: string, onRaiseLayers?: () => void }} opts
 * @returns {boolean} next terrainActive
 */
export function syncTerrainFromPitch(map, { mapReady, terrainActive, basemapKey, onRaiseLayers }) {
  if (!mapReady) return terrainActive
  const pitch = map.getPitch()
  if (!terrainActive && pitch >= PITCH_TERRAIN_ON) {
    showTerrainOverlays(map, { basemapKey, onRaiseLayers })
    return true
  }
  if (terrainActive && pitch <= PITCH_TERRAIN_OFF) {
    hideTerrainOverlays(map)
    return false
  }
  return terrainActive
}
