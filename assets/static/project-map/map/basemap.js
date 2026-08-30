// @ts-check

import { MAP_GLYPHS_URL } from '../constants.js'

/** @type {Record<string, { label: string, tiles: string[], maxzoom: number, analysisDem?: boolean, referenceTiles?: string[] }>} */
export const BASEMAPS = {
  street: {
    label: 'Street',
    tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    maxzoom: 19,
  },
  topo: {
    label: 'USGS Topo',
    tiles: [
      'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
    ],
    maxzoom: 16,
  },
  skadi: {
    label: 'Skadi relief',
    tiles: ['/api/dem/hillshade/{z}/{x}/{y}'],
    maxzoom: 13,
    analysisDem: true,
  },
  satellite: {
    label: 'Satellite',
    tiles: [
      'https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    ],
    maxzoom: 19,
    referenceTiles: [
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
    ],
  },
}

/** @param {string} key */
export function basemapStyle(key) {
  const bm = BASEMAPS[key] || BASEMAPS.street
  /** @type {object[]} */
  const layers = []
  if (bm.analysisDem) {
    layers.push({
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#3d4654' },
    })
  }
  layers.push({ id: 'basemap', type: 'raster', source: 'basemap' })
  return {
    version: 8,
    glyphs: MAP_GLYPHS_URL,
    sources: {
      basemap: {
        type: 'raster',
        tiles: bm.tiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      },
    },
    layers,
  }
}

/** @param {string} basemapKey */
export function usesSkadiAnalysisDem(basemapKey) {
  return Boolean(BASEMAPS[basemapKey]?.analysisDem)
}

/** @param {string} basemapKey */
export function terrainDemSourceSpec(basemapKey) {
  if (usesSkadiAnalysisDem(basemapKey)) {
    return {
      type: 'raster-dem',
      tiles: ['/api/dem/terrarium/{z}/{x}/{y}'],
      tileSize: 256,
      maxzoom: 13,
      encoding: 'terrarium',
    }
  }
  return {
    type: 'raster-dem',
    tiles: ['https://elevation-tiles-prod.s3.amazonaws.com/v2/terrarium/{z}/{x}/{y}.png'],
    tileSize: 256,
    maxzoom: 15,
    encoding: 'terrarium',
  }
}
