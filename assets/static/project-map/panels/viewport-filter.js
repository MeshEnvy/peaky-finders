// @ts-check

/** @type {HTMLInputElement[]} */
const viewportFilterInputs = []

/**
 * Wire an "In view" checkbox to store.ui.filterByViewport; keep siblings in sync.
 * @param {HTMLInputElement|null} el
 * @param {object} store
 * @param {() => void} [onChange]
 */
export function mountViewportFilterCheckbox(el, store, onChange) {
  if (!el) return
  el.checked = !!store.ui.filterByViewport
  if (!viewportFilterInputs.includes(el)) viewportFilterInputs.push(el)
  el.addEventListener('change', () => {
    store.ui.filterByViewport = el.checked
    syncViewportFilterCheckboxes(el.checked)
    onChange?.()
  })
}

/** @param {boolean} checked */
export function syncViewportFilterCheckboxes(checked) {
  for (const el of viewportFilterInputs) {
    el.checked = checked
  }
}
