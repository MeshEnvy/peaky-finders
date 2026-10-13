// @ts-check

/**
 * Simulation radius lives on `store.simulation` (see stores/root.js).
 *
 * Settings UI stays in `assets/static/home-settings.js`. On the project map it
 * mutates via `window.PEAKY_MAP.setViewshedSimulation` / `reloadViewshedsForSimChange`
 * and may read `window.PEAKY_STORE.simulation` for the gear tooltip.
 */

export function clampRadiusKm(km, minKm, maxKm) {
  return Math.max(minKm, Math.min(maxKm, Number(km)))
}

export function buildViewshedSimQueryParams(radiusKm) {
  const params = new URLSearchParams()
  params.set('radius_km', String(radiusKm))
  return params
}

/**
 * @param {object} store
 * @param {number} radiusKm
 * @returns {boolean} whether radius changed
 */
export function setSimulation(store, radiusKm) {
  const nextRadius = clampRadiusKm(radiusKm, store.simulation.radiusMin, store.simulation.radiusMax)
  const changed = nextRadius !== store.simulation.radiusKm
  store.simulation.radiusKm = nextRadius
  if (typeof window.PEAKY_HOME_SETTINGS?.updateGearSummary === 'function') {
    window.PEAKY_HOME_SETTINGS.updateGearSummary()
  }
  return changed
}
