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

/** @param {object} store @param {string} slug */
export function isSelectedLinkEndpoint(store, slug) {
  const link = store.ui?.selectedLink
  return Boolean(link && (link.a === slug || link.b === slug))
}

/**
 * Selected site always paints jeep/hike lines (even if tag-hidden).
 * RF link endpoints paint while the link sheet is open.
 * Access toggle pins them after deselect. Default is off.
 * @param {object} store
 * @param {string} slug
 * @param {boolean} [hidden]
 */
export function siteAccessShouldShow(store, slug, hidden = false) {
  if (!slug) return false
  if (store.ui?.selectedSlug === slug) return true
  if (store.fortify?.accessSlug === slug) return true
  if (isSelectedLinkEndpoint(store, slug)) return true
  if (hidden) return false
  return isAccessVisible(store, slug)
}
