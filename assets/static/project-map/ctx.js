// @ts-check

/** @typedef {import('./types.js').MapContext} MapContext */

/**
 * Shared map context — populated during boot for cross-module facades.
 * @returns {MapContext}
 */
export function createMapContext() {
  return /** @type {MapContext} */ ({})
}
