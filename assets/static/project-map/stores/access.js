// @ts-check

/** @param {object} store @param {string} slug @param {boolean} visible */
export function setAccessVisible(store, slug, visible) {
  if (!store.access) store.access = { bySlug: {}, visible: new Map() }
  if (!store.access.visible) store.access.visible = new Map()
  store.access.visible.set(slug, visible)
}

/** Access button pin (opt-in). Unset slugs are off. */
export function isAccessVisible(store, slug) {
  return store.access?.visible?.get(slug) === true
}

/**
 * Selected site always paints jeep/hike lines (even if tag-hidden).
 * Access toggle pins them after deselect. Default is off.
 * @param {object} store
 * @param {string} slug
 * @param {boolean} [hidden]
 */
export function siteAccessShouldShow(store, slug, hidden = false) {
  if (!slug) return false
  if (store.ui?.selectedSlug === slug) return true
  if (hidden) return false
  return isAccessVisible(store, slug)
}
