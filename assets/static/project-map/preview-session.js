// @ts-check

/**
 * One id for the three pin requests (links, viewshed, route).
 * Moving or dismissing the pin aborts the browser fetches and tells the server to drop that id.
 */

let projectSlug = ''
/** @type {AbortController|null} */
let controller = null
let id = 0

/** @param {string} slug */
export function initPreviewSession(slug) {
  projectSlug = slug || ''
}

export function beginPreview() {
  controller?.abort()
  id += 1
  controller = new AbortController()
  return currentPreview()
}

export function cancelPreview() {
  controller?.abort()
  controller = null
  id += 1
  if (!projectSlug) return
  const cancelId = id
  void fetch(`/api/p/${encodeURIComponent(projectSlug)}/preview/cancel?preview=${cancelId}`, {
    method: 'POST',
  }).catch(() => {})
}

export function currentPreview() {
  return { id, signal: controller?.signal }
}
