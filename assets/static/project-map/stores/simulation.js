// @ts-check

import {
  VIEWSHED_PREVIEW_QUALITY,
  VIEWSHED_QUALITY_MIN,
  VIEWSHED_QUALITY_MAX,
} from '../constants.js'

/**
 * Simulation radius/quality live on `store.simulation` (see stores/root.js).
 *
 * Settings UI stays in `assets/static/home-settings.js`. On the project map it
 * mutates via `window.PEAKY_MAP.setViewshedSimulation` / `reloadViewshedsForSimChange`
 * and may read `window.PEAKY_STORE.simulation` for the gear tooltip
 * (`radiusKm` / `quality`, or legacy `radius_km` / `viewshed_quality`).
 */

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

/**
 * @param {object} store
 * @param {number} radiusKm
 * @param {number} quality
 * @returns {boolean} whether radius or quality changed
 */
export function setSimulation(store, radiusKm, quality) {
  const nextRadius = clampRadiusKm(radiusKm, store.simulation.radiusMin, store.simulation.radiusMax)
  const nextQuality = clampViewshedQuality(quality)
  const changed =
    nextRadius !== store.simulation.radiusKm || nextQuality !== store.simulation.quality
  store.simulation.radiusKm = nextRadius
  store.simulation.quality = nextQuality
  if (typeof window.PEAKY_HOME_SETTINGS?.updateGearSummary === 'function') {
    window.PEAKY_HOME_SETTINGS.updateGearSummary()
  }
  return changed
}
