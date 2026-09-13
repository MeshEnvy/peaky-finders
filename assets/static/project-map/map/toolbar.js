// @ts-check

import { VIEWSHED_OPACITY_DEFAULT } from '../constants.js'

/** @param {string} name @param {string} label */
function mapToolIcon(name, label) {
  return `<wa-icon name="${name}" label="${label}"></wa-icon>`
}

/**
 * Install basemap / link solver / sites / opacity / settings controls into a MapLibre nav group.
 * @param {HTMLElement} navGroup
 * @param {{ viewshedOpacity?: number }} [opts]
 */
export function installMapToolbar(navGroup, { viewshedOpacity = VIEWSHED_OPACITY_DEFAULT } = {}) {
  const basemapDropdown = document.createElement('wa-dropdown')
  basemapDropdown.className = 'map-toolbar-dropdown'
  basemapDropdown.placement = 'bottom-end'

  const basemapBtn = document.createElement('button')
  basemapBtn.type = 'button'
  basemapBtn.slot = 'trigger'
  basemapBtn.id = 'map-tool-basemap'
  basemapBtn.className = 'map-toolbar-tool'
  basemapBtn.setAttribute('aria-label', 'Base map')
  basemapBtn.title = 'Base map'
  basemapBtn.innerHTML = mapToolIcon('layer-group', 'Base map')

  for (const [key, label] of [
    ['street', 'Street'],
    ['topo', 'USGS Topo'],
    ['skadi', 'Skadi relief (analysis DEM)'],
    ['satellite', 'Satellite'],
  ]) {
    const item = document.createElement('wa-dropdown-item')
    item.setAttribute('data-basemap', key)
    item.value = key
    item.textContent = label
    basemapDropdown.appendChild(item)
  }
  basemapDropdown.insertBefore(basemapBtn, basemapDropdown.firstChild)

  const linkSolverBtn = document.createElement('button')
  linkSolverBtn.type = 'button'
  linkSolverBtn.id = 'map-tool-link-solver'
  linkSolverBtn.className = 'map-toolbar-tool'
  linkSolverBtn.setAttribute('aria-pressed', 'false')
  linkSolverBtn.setAttribute('aria-controls', 'link-solver-panel')
  linkSolverBtn.setAttribute('aria-label', 'Link solver')
  linkSolverBtn.title = 'Link solver'
  linkSolverBtn.innerHTML = mapToolIcon('route', 'Link solver')

  const sitesBtn = document.createElement('button')
  sitesBtn.type = 'button'
  sitesBtn.id = 'map-tool-sites'
  sitesBtn.className = 'map-toolbar-tool'
  sitesBtn.setAttribute('aria-pressed', 'false')
  sitesBtn.setAttribute('aria-controls', 'entity-panel')
  sitesBtn.setAttribute('aria-label', 'Sites')
  sitesBtn.title = 'Sites'
  sitesBtn.innerHTML = mapToolIcon('tower-broadcast', 'Sites')

  const settingsBtn = document.createElement('button')
  settingsBtn.type = 'button'
  settingsBtn.id = 'home-settings-open'
  settingsBtn.className = 'map-toolbar-tool'
  settingsBtn.setAttribute('aria-label', 'Settings')
  settingsBtn.title = 'Settings'
  settingsBtn.innerHTML = mapToolIcon('gear', 'Settings')

  const opacityDropdown = document.createElement('wa-dropdown')
  opacityDropdown.className = 'map-toolbar-dropdown'
  opacityDropdown.placement = 'bottom-end'

  const opacityBtn = document.createElement('button')
  opacityBtn.type = 'button'
  opacityBtn.slot = 'trigger'
  opacityBtn.id = 'map-tool-opacity'
  opacityBtn.className = 'map-toolbar-tool'
  opacityBtn.setAttribute('aria-label', 'Viewshed opacity')
  opacityBtn.title = 'Viewshed opacity'
  opacityBtn.innerHTML = mapToolIcon('droplet', 'Viewshed opacity')

  const opacityMenu = document.createElement('div')
  opacityMenu.id = 'map-opacity-menu'
  opacityMenu.className = 'map-opacity-menu'

  const opacityLabel = document.createElement('label')
  opacityLabel.className = 'pf-label'
  opacityLabel.htmlFor = 'viewshed-opacity'
  opacityLabel.textContent = 'Viewshed opacity'

  const opacitySlider = document.createElement('input')
  opacitySlider.type = 'range'
  opacitySlider.id = 'viewshed-opacity'
  opacitySlider.className = 'pf-range'
  opacitySlider.min = '0'
  opacitySlider.max = '100'
  opacitySlider.value = String(Math.round(viewshedOpacity * 100))
  opacitySlider.title = 'Viewshed opacity'

  opacityMenu.appendChild(opacityLabel)
  opacityMenu.appendChild(opacitySlider)
  opacityDropdown.appendChild(opacityBtn)
  opacityDropdown.appendChild(opacityMenu)

  for (const el of [basemapDropdown, linkSolverBtn, sitesBtn, opacityDropdown, settingsBtn]) {
    navGroup.appendChild(el)
  }

  return {
    mapBasemapMenu: basemapDropdown,
    mapToolLinkSolver: linkSolverBtn,
    mapToolSites: sitesBtn,
    viewshedOpacityInput: opacitySlider,
    mapToolSettings: settingsBtn,
  }
}
