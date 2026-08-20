import {
  SKADI_DEM_SPACING_M,
  VIEWSHED_QUALITY_MIN,
  VIEWSHED_QUALITY_MAX,
  VIEWSHED_RASTER_MIN,
  VIEWSHED_RASTER_MAX,
} from './constants.js'

export function demNativeRasterDimension(radiusKm) {
  const px = Math.ceil((radiusKm * 1000) / SKADI_DEM_SPACING_M)
  return Math.max(VIEWSHED_RASTER_MIN, Math.min(VIEWSHED_RASTER_MAX, px))
}

export function rasterUpgradeLadder(minPx, targetPx) {
  const min = Math.max(VIEWSHED_RASTER_MIN, Math.min(VIEWSHED_RASTER_MAX, minPx))
  const target = Math.max(min, Math.min(VIEWSHED_RASTER_MAX, targetPx))
  const ladder = [min]
  let cur = min
  while (cur < target) {
    const next = Math.min(cur * 2, target)
    if (next <= cur) break
    ladder.push(next)
    cur = next
  }
  return ladder
}

export function computeViewshedRaster(quality, radiusKm) {
  const q = Math.max(
    VIEWSHED_QUALITY_MIN,
    Math.min(VIEWSHED_QUALITY_MAX, Math.round(Number(quality) || VIEWSHED_QUALITY_MIN)),
  )
  if (q === 1) return VIEWSHED_RASTER_MIN
  const full = demNativeRasterDimension(radiusKm)
  if (q === 5 || full <= VIEWSHED_RASTER_MIN) return full
  const ladder = rasterUpgradeLadder(VIEWSHED_RASTER_MIN, full)
  const idx = Math.round(((q - 1) / (VIEWSHED_QUALITY_MAX - 1)) * (ladder.length - 1))
  return ladder[Math.min(idx, ladder.length - 1)]
}

/** @param {number} quality @param {number} radiusKm */
export function formatViewshedQualityLabel(quality, radiusKm) {
  const q = Math.round(Number(quality) || 3)
  const px = computeViewshedRaster(q, radiusKm)
  return `${q} · ${px} px`
}
