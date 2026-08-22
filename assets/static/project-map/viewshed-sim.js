import {
  VIEWSHED_PREVIEW_QUALITY,
  VIEWSHED_QUALITY_MIN,
  VIEWSHED_QUALITY_MAX,
} from './constants.js'

export function clampRadiusKm(km, minKm, maxKm) {
  return Math.max(minKm, Math.min(maxKm, Number(km)))
}

export function clampViewshedQuality(quality) {
  return Math.max(
    VIEWSHED_QUALITY_MIN,
    Math.min(VIEWSHED_QUALITY_MAX, Math.round(Number(quality))),
  )
}

export function buildViewshedSimQueryParams(radiusKm, quality) {
  const params = new URLSearchParams()
  params.set('radius_km', String(radiusKm))
  params.set('quality', String(quality))
  return params
}

export function buildViewshedPreviewSimQueryParams(radiusKm) {
  return buildViewshedSimQueryParams(radiusKm, VIEWSHED_PREVIEW_QUALITY)
}
