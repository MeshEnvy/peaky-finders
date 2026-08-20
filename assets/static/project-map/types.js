// @ts-check

/**
 * @typedef {Object} PeakySite
 * @property {string} slug
 * @property {string} name
 * @property {number} lat
 * @property {number} lon
 * @property {string[]} [tags]
 */

/**
 * @typedef {Object} PeakyProject
 * @property {string} slug
 * @property {PeakySite[]} sites
 * @property {Record<string, unknown>} simulation
 * @property {Record<string, unknown>} [land]
 * @property {Record<string, unknown>} [seek]
 */

/**
 * Shared mutable runtime for map modules.
 * @typedef {Object} MapContext
 * @property {PeakyProject} config
 * @property {string} projectSlug
 * @property {import('maplibre-gl').Map} map
 * @property {boolean} mapReady
 * @property {PeakySite[]} sites
 * @property {() => void} reloadViewshedsForSimChange
 * @property {(radiusKm: number, quality: number) => boolean} setViewshedSimulation
 * @property {Record<string, Function>} [key]
 */

export {}
