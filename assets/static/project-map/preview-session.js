// @ts-check

/**
 * One id for the three pin requests (links, viewshed, route).
 * Each placement gets a millisecond timestamp, so a reloaded page is newer
 * than the previous one instead of restarting a counter at 1.
 */

let projectSlug = ''
/** @type {AbortController|null} */
let controller = null
let id = 0

/** @param {string} slug */
export function initPreviewSession(slug) {
  projectSlug = slug || ''
}

function mintPreviewId() {
  const now = Date.now()
  id = now > id ? now : id + 1
  return id
}

export function beginPreview() {
  controller?.abort()
  mintPreviewId()
  controller = new AbortController()
  return currentPreview()
}

export function cancelPreview() {
  const cancelId = id
  controller?.abort()
  controller = null
  if (!projectSlug || !cancelId) return
  void fetch(`/api/p/${encodeURIComponent(projectSlug)}/preview/cancel?preview=${cancelId}`, {
    method: 'POST',
  }).catch(() => {})
}

export function currentPreview() {
  return { id, signal: controller?.signal }
}
