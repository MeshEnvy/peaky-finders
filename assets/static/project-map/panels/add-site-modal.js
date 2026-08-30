// @ts-check

import { watch } from 'vue'
import { allProjectTags } from '../stores/sites.js'
import { formatCoord, parseCoordPairFromText } from '../geo.js'
import { renderToggleTagChips, syncTagSuggestions } from './tag-chips.js'

/**
 * Drive #add-site-modal from store.modals.addSite.
 * @param {object} store
 * @param {object} appApi
 */
export function mountAddSiteModal(store, appApi) {
  const dialog = document.getElementById('add-site-modal')
  if (!dialog) return null

  const nameEl = document.getElementById('add-site-name')
  const coordsEl = document.getElementById('add-site-coords')
  const tagsEl = document.getElementById('add-site-tags')
  const form = document.getElementById('add-site-tag-form')
  const input = document.getElementById('add-site-tag-input')
  const suggestions = document.getElementById('add-site-tag-suggestions')
  const errorEl = document.getElementById('add-site-error')
  const save = document.getElementById('add-site-save')

  watch(
    () => store.modals.addSite,
    (modal) => {
      if (!modal) return
      dialog.open = !!modal.open
      if (nameEl && nameEl !== document.activeElement) nameEl.value = modal.name || ''
      if (coordsEl && coordsEl !== document.activeElement) coordsEl.value = modal.coords || ''
      if (input && input !== document.activeElement) input.value = modal.tagInput || ''
      if (errorEl) {
        errorEl.textContent = modal.error || ''
        errorEl.hidden = !modal.error
      }
      if (save) save.disabled = !!modal.saving
      renderToggleTagChips(tagsEl, {
        tags: allProjectTags(store),
        selected: modal.tags || [],
        onToggle: (tag) => appApi.toggleAddSiteTag?.(tag),
      })
      syncTagSuggestions(suggestions, allProjectTags(store), modal.tags || [])
    },
    { deep: true },
  )

  nameEl?.addEventListener('input', () => {
    if (store.modals.addSite) store.modals.addSite.name = nameEl.value
  })
  nameEl?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return
    ev.preventDefault()
    coordsEl?.focus()
  })
  coordsEl?.addEventListener('input', () => {
    if (store.modals.addSite) store.modals.addSite.coords = coordsEl.value
  })
  coordsEl?.addEventListener('paste', (ev) => {
    const text = ev.clipboardData?.getData('text') || ''
    const pair = parseCoordPairFromText(text)
    if (!pair) return
    ev.preventDefault()
    const value = `${formatCoord(pair.lat)}, ${formatCoord(pair.lon)}`
    coordsEl.value = value
    if (store.modals.addSite) {
      store.modals.addSite.coords = value
      store.modals.addSite.error = ''
    }
  })
  coordsEl?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return
    ev.preventDefault()
    void appApi.saveAddSiteModal?.()
  })
  input?.addEventListener('input', () => {
    if (store.modals.addSite) store.modals.addSite.tagInput = input.value
  })
  form?.addEventListener('submit', (ev) => {
    ev.preventDefault()
    appApi.addAddSiteTagFromInput?.()
  })
  save?.addEventListener('click', () => {
    void appApi.saveAddSiteModal?.()
  })
  dialog.addEventListener('wa-after-hide', () => {
    if (store.modals.addSite?.open) appApi.closeAddSiteModal?.()
  })
  watch(
    () => store.modals.addSite.open,
    (open) => {
      if (open) {
        requestAnimationFrame(() => nameEl?.focus())
      }
    },
  )

  return { dialog }
}
