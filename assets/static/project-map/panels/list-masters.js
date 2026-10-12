// @ts-check

/**
 * Sync the list-header eye and viewshed buttons.
 * Active means every listed row is on. The eye never writes viewshed flags.
 * @param {{
 *   eye: HTMLElement|null,
 *   viewshed: HTMLElement|null,
 *   total: number,
 *   visible: number,
 *   viewsheds: number,
 *   pinNoun: string,
 * }} opts
 */
export function syncListMasters({ eye, viewshed, total, visible, viewsheds, pinNoun }) {
  const allVisible = total > 0 && visible === total
  const allViewsheds = total > 0 && viewsheds === total
  if (eye) {
    const title = allVisible ? `Hide ${pinNoun} on map` : `Show ${pinNoun} on map`
    eye.classList.toggle('entity-panel__action--active', allVisible)
    eye.disabled = total === 0
    eye.title = title
    eye.setAttribute('aria-label', title)
    const icon = eye.querySelector('wa-icon')
    if (icon) {
      const name = allVisible ? 'eye' : 'eye-slash'
      icon.name = name
      icon.setAttribute('name', name)
      icon.label = title
      icon.setAttribute('label', title)
    }
  }
  if (viewshed) {
    const title = allViewsheds ? 'Hide viewsheds' : 'Show viewsheds'
    viewshed.classList.toggle('entity-panel__action--active', allViewsheds)
    viewshed.disabled = total === 0
    viewshed.title = title
    viewshed.setAttribute('aria-label', title)
  }
}
