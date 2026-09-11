// @ts-check

import { mountSitesPanel } from './sites-panel.js'
import { mountSiteSheet } from './site-sheet.js'
import { mountPeakSheet } from './peak-sheet.js'
import { mountLandPanel } from './land-panel.js'
import { mountSeekPanel } from './seek-panel.js'
import { mountBulkTagModal } from './bulk-tag-modal.js'
import { mountAddSiteModal } from './add-site-modal.js'
import { mountImportSitesModal } from './import-sites-modal.js'

/**
 * Mount Vue panel roots. MapLibre stays imperative in domains/app.js adapters.
 * @param {object} store
 * @param {object} appApi
 */
export function mountPanels(store, appApi) {
  const errors = []
  function run(name, fn) {
    try {
      fn()
    } catch (err) {
      errors.push({ name, err: String(err), stack: err?.stack || '' })
      console.error(`[peaky] mount ${name} failed`, err)
    }
  }

  run('sites', () => mountSitesPanel(store, appApi))
  run('land', () => mountLandPanel(store, appApi))
  run('seek', () => mountSeekPanel(store, appApi))
  run('site-sheet', () => mountSiteSheet(store, appApi))
  run('peak-sheet', () => mountPeakSheet(store, appApi))
  run('bulk-tag', () => mountBulkTagModal(store, appApi))
  run('add-site', () => mountAddSiteModal(store, appApi))
  run('import-sites', () => mountImportSitesModal(store, appApi))

  if (typeof window !== 'undefined') window.__PEAKY_MOUNT_ERRORS = errors
}
