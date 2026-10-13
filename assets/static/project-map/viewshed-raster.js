import {
  SKADI_DEM_SPACING_M,
  VIEWSHED_RASTER_MIN,
  VIEWSHED_RASTER_MAX,
} from './constants.js'

export function demNativeRasterDimension(radiusKm) {
  const px = Math.ceil((radiusKm * 1000) / SKADI_DEM_SPACING_M)
  return Math.max(VIEWSHED_RASTER_MIN, Math.min(VIEWSHED_RASTER_MAX, px))
}

/** @param {number} radiusKm */
export function formatViewshedRadiusLabel(radiusKm) {
  const km = Math.round(Number(radiusKm) || 50)
  const px = demNativeRasterDimension(km)
  return `${km} km · ${px} px`
}
