// @ts-check

/**
 * Viewshed domain store helpers. Runtime state lives on root store.viewshed.
 * SSE commits should update store.viewshed then call map/viewshed-layers adapters.
 */

/** @param {object} store @param {string} slug @param {object} partial */
export function setPinProgress(store, slug, partial) {
  const prev = store.viewshed.pinProgress.get(slug) || {}
  store.viewshed.pinProgress.set(slug, { ...prev, ...partial })
}

/** @param {object} store @param {string} slug */
export function clearPinProgress(store, slug) {
  store.viewshed.pinProgress.delete(slug)
}

/** @param {object} store @param {string} slug */
export function addViewshedLoading(store, slug) {
  store.viewshed.loading.add(slug)
}

/** @param {object} store @param {string} slug */
export function clearViewshedLoading(store, slug) {
  store.viewshed.loading.delete(slug)
}

/** @param {object} store @param {string} slug @param {number} epoch */
export function setPendingEpoch(store, slug, epoch) {
  store.viewshed.pendingEpoch.set(slug, epoch)
}

/** @param {object} store @param {string} slug */
export function clearPendingEpoch(store, slug) {
  store.viewshed.pendingEpoch.delete(slug)
}

/** @param {object} store @returns {number} */
export function bumpLoadEpoch(store) {
  store.viewshed.loadEpoch += 1
  return store.viewshed.loadEpoch
}

/** @param {object} store @param {string} slug */
export function markViewshedReady(store, slug) {
  store.viewshed.ready.add(slug)
  store.viewshed.loading.delete(slug)
}

/** @param {object} store @param {string} slug */
export function clearViewshedReady(store, slug) {
  store.viewshed.ready.delete(slug)
}

/** @param {object} store @param {string} slug */
export function markOutboundLinksReady(store, slug) {
  store.viewshed.outboundLinksReady.add(slug)
}

/** @param {object} store @param {string} slug */
export function clearOutboundLinksReady(store, slug) {
  store.viewshed.outboundLinksReady.delete(slug)
}

/**
 * Clear ready + outbound-links-ready + pin progress for a site (sim reload / re-warm).
 * @param {object} store
 * @param {string} slug
 */
export function resetSiteProgress(store, slug) {
  store.viewshed.ready.delete(slug)
  store.viewshed.outboundLinksReady.delete(slug)
  clearPinProgress(store, slug)
}

/** @param {object} store @param {string} slug @param {boolean} visible */
export function setViewshedVisible(store, slug, visible) {
  store.viewshed.visible.set(slug, visible)
}

/** @param {object} store @param {string} slug */
export function isViewshedVisible(store, slug) {
  return store.viewshed.visible.get(slug) !== false
}

/**
 * Drop pending/loading/pin for a slug (failed load or superseded).
 * @param {object} store
 * @param {string} slug
 */
export function clearViewshedLoadingState(store, slug) {
  store.viewshed.pendingEpoch.delete(slug)
  store.viewshed.loading.delete(slug)
  clearPinProgress(store, slug)
}
