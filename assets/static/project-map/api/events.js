// @ts-check

import { projectEventsUrl } from './urls.js'

/**
 * @param {string} projectSlug
 * @param {{ onHello?: () => void, onViewshed?: (data: object) => void, onLinks?: (data: object) => void, onAccess?: (data: object) => void }} handlers
 */
export function connectProjectEvents(projectSlug, handlers) {
  /** @type {EventSource|null} */
  let source = null

  function connect() {
    if (source) {
      source.close()
      source = null
    }
    source = new EventSource(projectEventsUrl(projectSlug))
    source.addEventListener('hello', () => {
      handlers.onHello?.()
    })
    source.addEventListener('viewshed', (ev) => {
      try {
        handlers.onViewshed?.(JSON.parse(ev.data))
      } catch {
        /* malformed */
      }
    })
    source.addEventListener('links', (ev) => {
      try {
        handlers.onLinks?.(JSON.parse(ev.data))
      } catch {
        /* malformed */
      }
    })
    source.addEventListener('access', (ev) => {
      try {
        handlers.onAccess?.(JSON.parse(ev.data))
      } catch {
        /* malformed */
      }
    })
  }

  connect()

  return {
    reconnect: connect,
    close() {
      if (source) source.close()
      source = null
    },
  }
}
