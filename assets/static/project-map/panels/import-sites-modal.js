// @ts-check

import { watch } from 'vue'
import { allProjectTags } from '../stores/sites.js'
import { formatCoord } from '../geo.js'
import { renderToggleTagChips, syncTagSuggestions } from './tag-chips.js'

/**
 * Drive #import-sites-modal from store.modals.importSites.
 * Preview MapLibre stays in domains/site-modals.js.
 * @param {object} store
 * @param {object} appApi
 */
export function mountImportSitesModal(store, appApi) {
  const dialog = document.getElementById('import-sites-modal')
  if (!dialog) return null

  const fileEl = document.getElementById('import-sites-file')
  const statusEl = document.getElementById('import-sites-status')
  const previewField = document.getElementById('import-sites-preview-field')
  const tagsEl = document.getElementById('import-sites-tags')
  const form = document.getElementById('import-sites-tag-form')
  const input = document.getElementById('import-sites-tag-input')
  const suggestions = document.getElementById('import-sites-tag-suggestions')
  const errorEl = document.getElementById('import-sites-error')
  const save = document.getElementById('import-sites-save')
  const listCount = document.getElementById('import-sites-list-count')
  const selectAll = document.getElementById('import-sites-select-all')
  const clearAll = document.getElementById('import-sites-clear-all')
  const filterVisible = document.getElementById('import-sites-filter-visible')
  const pointList = document.getElementById('import-sites-point-list')

  function renderPointList(modal) {
    if (!pointList) return
    const scrollTop = pointList.scrollTop
    pointList.innerHTML = ''
    const points = modal.points || []
    const total = points.length
    if (!total) {
      if (listCount) listCount.textContent = ''
      return
    }
    let shown = 0
    for (let index = 0; index < points.length; index++) {
      const point = points[index]
      if (modal.filterVisible && !appApi.importPointVisible?.(point)) continue
      shown += 1
      const row = document.createElement('div')
      row.className = 'import-sites-point-row'
      row.setAttribute('role', 'listitem')
      if (index === modal.selectedIndex) row.classList.add('import-sites-point-row--selected')
      if (point.ignored) row.classList.add('import-sites-point-row--ignored')
      if (point.duplicate) row.classList.add('import-sites-point-row--duplicate')

      const main = document.createElement('button')
      main.type = 'button'
      main.className = 'import-sites-point-row__main'
      const name = document.createElement('span')
      name.className = 'import-sites-point-row__name'
      name.textContent = point.name || `Point ${index + 1}`
      const meta = document.createElement('span')
      meta.className = 'import-sites-point-row__meta'
      let metaText = `${formatCoord(point.lat)}, ${formatCoord(point.lon)}`
      if (point.duplicate && point.duplicateName) {
        metaText += ` · near ${point.duplicateName} (${point.duplicateDistM} m)`
      }
      meta.textContent = metaText
      main.appendChild(name)
      main.appendChild(meta)
      main.addEventListener('click', () => appApi.focusImportPreviewPoint?.(index))

      const importLabel = document.createElement('label')
      importLabel.className = 'import-sites-point-row__import pf-check'
      const importCheck = document.createElement('input')
      importCheck.type = 'checkbox'
      importCheck.checked = !point.ignored
      importCheck.setAttribute(
        'aria-label',
        `Import ${point.name || `point ${index + 1}`}`,
      )
      importCheck.addEventListener('click', (ev) => ev.stopPropagation())
      importCheck.addEventListener('change', () => {
        appApi.setImportPointIgnored?.(index, !importCheck.checked)
      })
      importLabel.appendChild(importCheck)
      row.appendChild(main)
      row.appendChild(importLabel)
      pointList.appendChild(row)
    }
    if (!shown) {
      const empty = document.createElement('p')
      empty.className = 'import-sites-point-list__empty'
      empty.textContent = modal.filterVisible
        ? 'No points in the current map view — pan or zoom out.'
        : 'No points to show.'
      pointList.appendChild(empty)
    }
    const toImport = points.filter((p) => !p.ignored).length
    if (listCount) {
      let text = ''
      if (modal.filterVisible && shown < total) text = `${shown} of ${total} visible`
      else text = `${total} point${total === 1 ? '' : 's'}`
      if (toImport < total) text += ` · ${toImport} to import`
      listCount.textContent = text
    }
    pointList.scrollTop = scrollTop
  }

  watch(
    () => store.modals.importSites,
    (modal) => {
      if (!modal) return
      dialog.open = !!modal.open
      if (statusEl) statusEl.textContent = modal.status || ''
      if (previewField) previewField.hidden = !!modal.previewHidden
      if (errorEl) {
        errorEl.textContent = modal.error || ''
        errorEl.hidden = !modal.error
      }
      if (input && input !== document.activeElement) input.value = modal.tagInput || ''
      if (filterVisible) filterVisible.checked = !!modal.filterVisible
      const ready =
        (appApi.importablePoints?.() || []).length > 0 &&
        (appApi.effectiveImportTags?.() || []).length > 0 &&
        !!modal.hasPayload &&
        !modal.busy
      if (save) save.disabled = !!modal.saving || !ready
      const enabled = (modal.points || []).length > 0 && !modal.busy
      if (selectAll) selectAll.disabled = !enabled
      if (clearAll) clearAll.disabled = !enabled
      renderToggleTagChips(tagsEl, {
        tags: allProjectTags(store),
        selected: modal.tags || [],
        onToggle: (tag) => appApi.toggleImportTag?.(tag),
      })
      syncTagSuggestions(suggestions, allProjectTags(store), modal.tags || [])
      renderPointList(modal)
    },
    { deep: true },
  )

  fileEl?.addEventListener('change', () => {
    const file = fileEl.files && fileEl.files[0]
    if (file) void appApi.previewImportFile?.(file)
  })
  input?.addEventListener('input', () => {
    if (store.modals.importSites) store.modals.importSites.tagInput = input.value
  })
  form?.addEventListener('submit', (ev) => {
    ev.preventDefault()
    appApi.addImportTagFromInput?.()
  })
  save?.addEventListener('click', () => {
    void appApi.saveImportSitesModal?.()
  })
  filterVisible?.addEventListener('change', () => {
    if (store.modals.importSites) {
      store.modals.importSites.filterVisible = !!filterVisible.checked
    }
  })
  selectAll?.addEventListener('click', () => appApi.setAllImportPointsIgnored?.(false))
  clearAll?.addEventListener('click', () => appApi.setAllImportPointsIgnored?.(true))
  dialog.addEventListener('wa-after-show', () => {
    appApi.resizeImportPreviewMap?.()
    if (fileEl) requestAnimationFrame(() => fileEl.focus())
  })
  dialog.addEventListener('wa-after-hide', () => {
    if (store.modals.importSites?.open) appApi.closeImportSitesModal?.()
    appApi.resetImportSitesModal?.()
    if (fileEl) fileEl.value = ''
  })

  return { dialog }
}
