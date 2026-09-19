// @ts-check

import { watch } from 'vue'
import {
  allProjectTags,
  bulkTagInitialCounts,
  bulkTagListTags,
  bulkTagVisualState,
  computeBulkTagOps,
} from '../stores/sites.js'
import { mountTagPicker } from './tag-picker.js'

/**
 * Drive #bulk-tag-modal from store.modals.bulkTag.
 * @param {object} store
 * @param {object} appApi
 */
export function mountBulkTagModal(store, appApi) {
  const dialog = document.getElementById('bulk-tag-modal')
  if (!dialog) return null

  const errorEl = document.getElementById('bulk-tag-error')
  const statusEl = document.getElementById('bulk-tag-status')
  const tagsEl = document.getElementById('bulk-tag-tags')
  const save = document.getElementById('bulk-tag-save')

  function listed() {
    return appApi.listedSidebarSites?.() || []
  }

  function chipTags(listedSites) {
    const counts = bulkTagInitialCounts(listedSites)
    return bulkTagListTags(store.modals.bulkTag.pending || {}, counts)
  }

  function bulkStates(listedSites, modal) {
    const counts = bulkTagInitialCounts(listedSites)
    const total = listedSites.length
    const pending = modal.pending || {}
    /** @type {Record<string, string>} */
    const states = {}
    for (const tag of chipTags(listedSites)) {
      states[tag] = bulkTagVisualState(tag, total, pending, counts)
    }
    return states
  }

  mountTagPicker(
    tagsEl,
    () => {
      const sites = listed()
      return {
        mode: 'bulk',
        states: bulkStates(sites, store.modals.bulkTag || {}),
        suggestions: allProjectTags(store),
      }
    },
    {
      onSetState: (tag, visual) => appApi.setBulkTagState?.(tag, visual),
    },
  )

  watch(
    () => store.modals.bulkTag,
    (modal) => {
      if (!modal) return
      dialog.open = !!modal.open
      if (errorEl) {
        errorEl.textContent = modal.error || ''
        errorEl.hidden = !modal.error
      }
      const sites = listed()
      if (statusEl) statusEl.textContent = appApi.bulkTagStatusText?.(sites) || ''
      const counts = bulkTagInitialCounts(sites)
      if (save) {
        const { addTags, removeTags } = computeBulkTagOps(
          sites.map((s) => s.slug),
          modal.pending,
          counts,
        )
        save.disabled = !!modal.saving || !sites.length || (!addTags.length && !removeTags.length)
      }
    },
    { deep: true },
  )

  save?.addEventListener('click', () => {
    void appApi.saveBulkTagModal?.()
  })
  dialog.addEventListener('wa-after-hide', () => {
    if (store.modals.bulkTag?.open) appApi.closeBulkTagModal?.()
  })

  return { dialog }
}
