// @ts-check

import { watch } from 'vue'
import { allProjectTags, bulkTagInitialCounts, computeBulkTagOps } from '../stores/sites.js'
import { syncTagSuggestions } from './tag-chips.js'

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
  const form = document.getElementById('bulk-tag-add-form')
  const input = document.getElementById('bulk-tag-add-input')
  const suggestions = document.getElementById('bulk-tag-add-suggestions')
  const save = document.getElementById('bulk-tag-save')

  function listed() {
    return appApi.listedSidebarSites?.() || []
  }

  function chipTags(listedSites) {
    const counts = bulkTagInitialCounts(listedSites)
    return [...new Set([...allProjectTags(store), ...Object.keys(counts), ...Object.keys(store.modals.bulkTag.pending || {})])].sort(
      (a, b) => a.localeCompare(b),
    )
  }

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
      const known = chipTags(sites)
      const pending = modal.pending || {}
      const counts = bulkTagInitialCounts(sites)
      const total = sites.length
      if (tagsEl) {
        tagsEl.innerHTML = ''
        for (const tag of known) {
          let visual = 'none'
          if (pending[tag] === 'all') visual = 'full'
          else if (pending[tag] === 'none') visual = 'none'
          else {
            const count = counts[tag] || 0
            if (count >= total && total) visual = 'full'
            else if (count > 0) visual = 'partial'
          }
          const chip = document.createElement('button')
          chip.type = 'button'
          chip.className = 'site-tag site-tag--toggle'
          if (visual === 'full') chip.classList.add('is-selected')
          if (visual === 'partial') chip.classList.add('is-partial')
          if (visual === 'partial') {
            const icon = document.createElement('span')
            icon.className = 'site-tag__partial-icon'
            icon.setAttribute('aria-hidden', 'true')
            icon.textContent = '◐'
            chip.appendChild(icon)
          }
          const label = document.createElement('span')
          label.textContent = tag
          chip.appendChild(label)
          chip.setAttribute(
            'aria-pressed',
            visual === 'full' ? 'true' : visual === 'partial' ? 'mixed' : 'false',
          )
          chip.title =
            visual === 'full'
              ? `Remove ${tag} from all sites in view`
              : `Add ${tag} to all sites in view`
          chip.addEventListener('click', () => appApi.toggleBulkTag?.(tag))
          tagsEl.appendChild(chip)
        }
      }
      if (input) input.value = modal.tagInput || ''
      syncTagSuggestions(suggestions, allProjectTags(store), [])
      if (save) {
        const { addTags, removeTags } = computeBulkTagOps(
          sites.map((s) => s.slug),
          pending,
          counts,
        )
        save.disabled = !!modal.saving || !sites.length || (!addTags.length && !removeTags.length)
      }
    },
    { deep: true },
  )

  input?.addEventListener('input', () => {
    if (store.modals.bulkTag) store.modals.bulkTag.tagInput = input.value
  })
  form?.addEventListener('submit', (ev) => {
    ev.preventDefault()
    appApi.addBulkTagFromInput?.()
  })
  save?.addEventListener('click', () => {
    void appApi.saveBulkTagModal?.()
  })
  dialog.addEventListener('wa-after-hide', () => {
    if (store.modals.bulkTag?.open) appApi.closeBulkTagModal?.()
  })

  return { dialog }
}
