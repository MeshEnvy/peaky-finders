// @ts-check

/**
 * Render toggle chips into a container. Used by add-site / import / convert modals.
 * @param {HTMLElement|null} container
 * @param {{ tags: string[], selected: string[], onToggle: (tag: string) => void }} opts
 */
export function renderToggleTagChips(container, { tags, selected, onToggle }) {
  if (!container) return
  container.innerHTML = ''
  const selectedSet = new Set(selected)
  const shown = [...new Set([...(tags || []), ...selected])].sort((a, b) =>
    a.localeCompare(b),
  )
  for (const tag of shown) {
    const chip = document.createElement('button')
    chip.type = 'button'
    chip.className = selectedSet.has(tag)
      ? 'site-tag site-tag--toggle is-selected'
      : 'site-tag site-tag--toggle'
    chip.textContent = tag
    chip.setAttribute('aria-pressed', selectedSet.has(tag) ? 'true' : 'false')
    chip.title = selectedSet.has(tag) ? `Remove tag ${tag}` : `Add tag ${tag}`
    chip.addEventListener('click', () => onToggle(tag))
    container.appendChild(chip)
  }
}

/**
 * Fill a datalist with unused project tags.
 * @param {HTMLDataListElement|null} list
 * @param {string[]} allTags
 * @param {string[]} selected
 */
export function syncTagSuggestions(list, allTags, selected) {
  if (!list) return
  list.innerHTML = ''
  const taken = new Set(selected)
  for (const tag of allTags) {
    if (taken.has(tag)) continue
    const opt = document.createElement('option')
    opt.value = tag
    list.appendChild(opt)
  }
}
