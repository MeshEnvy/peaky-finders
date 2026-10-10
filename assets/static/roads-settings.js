'use strict'

import { fetchRoadsBook, saveRoadsBook } from './project-map/api/roads-book.js'

const ROLES = ['paved', 'jeep', 'hiking', 'other']

/** @param {string} slug */
function roadsApiUrl(slug) {
  return `/api/p/${encodeURIComponent(slug)}/roads`
}

function formatClassLabel(row) {
  const parts = [row.highway]
  if (row.tracktype) parts.push(`tracktype=${row.tracktype}`)
  if (row.surface === 'paved_surfaces') parts.push('surface=paved*')
  else if (row.surface && typeof row.surface === 'object') {
    if (row.surface.exact) parts.push(`surface=${row.surface.exact}`)
    else if (row.surface.Exact) parts.push(`surface=${row.surface.Exact}`)
  }
  return parts.join(' · ')
}

function roleSelect(id, value, defaultRole) {
  const opts = ROLES.map(
    (r) => `<option value="${r}"${r === value ? ' selected' : ''}>${r}</option>`,
  ).join('')
  const reset =
    value !== defaultRole
      ? `<wa-button class="home-roads-reset" appearance="plain" size="s" data-class-id="${id}" type="button" title="Reset to default"><wa-icon name="arrow-rotate-left"></wa-icon></wa-button>`
      : ''
  return `<select class="home-roads-role" data-class-id="${id}">${opts}</select>${reset}`
}

/**
 * @param {HTMLElement} root
 * @param {object} book
 */
function renderClassesTable(root, book) {
  const tbody = root.querySelector('#home-roads-classes-body')
  if (!tbody) return
  tbody.innerHTML = ''
  for (const row of book.classes || []) {
    const tr = document.createElement('tr')
    tr.innerHTML = `<td class="pf-mono">${row.id}</td><td>${formatClassLabel(row)}</td><td class="home-roads-role-cell">${roleSelect(row.id, row.role, row.default_role)}</td>`
    tbody.appendChild(tr)
  }
}

/**
 * @param {HTMLElement} root
 * @param {object} book
 */
function renderOverridesList(root, book) {
  const list = root.querySelector('#home-roads-overrides-list')
  if (!list) return
  list.innerHTML = ''
  const overrides = book.overrides || []
  if (!overrides.length) {
    list.innerHTML = '<p class="wa-caption pf-muted">No segment overrides yet. Use the All overlay popup on the map.</p>'
    return
  }
  for (let i = 0; i < overrides.length; i += 1) {
    const ov = overrides[i]
    const li = document.createElement('li')
    li.className = 'home-roads-list-item'
    let label = 'segment'
    if (ov.osm_id != null) label = `OSM way ${ov.osm_id}`
    else if (ov.gtlf) label = `GTLF layer ${ov.gtlf.layer}`
    li.innerHTML = `<span>${label}</span> <select class="home-roads-override-role" data-ov-index="${i}">${ROLES.map((r) => `<option value="${r}"${r === ov.role ? ' selected' : ''}>${r}</option>`).join('')}</select> <wa-button class="home-roads-override-delete" appearance="plain" size="s" data-ov-index="${i}" type="button">Delete</wa-button>`
    list.appendChild(li)
  }
}

/**
 * @param {HTMLElement} root
 * @param {object} book
 */
function renderCustomsList(root, book) {
  const list = root.querySelector('#home-roads-customs-list')
  if (!list) return
  list.innerHTML = ''
  const customs = book.customs || []
  if (!customs.length) {
    list.innerHTML = '<p class="wa-caption pf-muted">No custom roads. Use Draw road on the access network menu.</p>'
    return
  }
  for (let i = 0; i < customs.length; i += 1) {
    const c = customs[i]
    const li = document.createElement('li')
    li.className = 'home-roads-list-item'
    const verts = (c.coords || []).length
    li.innerHTML = `<span class="pf-mono">${c.id}</span> <span>${verts} pts</span> <select class="home-roads-custom-role" data-custom-index="${i}">${ROLES.map((r) => `<option value="${r}"${r === c.role ? ' selected' : ''}>${r}</option>`).join('')}</select> <wa-button class="home-roads-custom-delete" appearance="plain" size="s" data-custom-index="${i}" type="button">Delete</wa-button>`
    list.appendChild(li)
  }
}

/** @type {{ book: object|null, slug: string }} */
const state = { book: null, slug: '' }

function readBookFromDom() {
  if (!state.book) return null
  const book = structuredClone(state.book)
  document.querySelectorAll('.home-roads-role').forEach((el) => {
    const id = el.getAttribute('data-class-id')
    const row = book.classes?.find((r) => r.id === id)
    if (row) row.role = el.value
  })
  document.querySelectorAll('.home-roads-override-role').forEach((el) => {
    const i = Number(el.getAttribute('data-ov-index'))
    if (book.overrides?.[i]) book.overrides[i].role = el.value
  })
  document.querySelectorAll('.home-roads-custom-role').forEach((el) => {
    const i = Number(el.getAttribute('data-custom-index'))
    if (book.customs?.[i]) book.customs[i].role = el.value
  })
  return book
}

/**
 * @param {string} slug
 * @param {(msg: string) => void} showError
 */
export async function loadRoadsSettings(slug, showError) {
  state.slug = slug
  const panel = document.getElementById('home-settings-roads-panel')
  if (!panel || !slug) return
  try {
    state.book = await fetchRoadsBook(slug)
    renderClassesTable(panel, state.book)
    renderOverridesList(panel, state.book)
    renderCustomsList(panel, state.book)
  } catch (err) {
    showError(err.message || String(err))
  }
}

/** @param {(msg: string) => void} showError */
export async function saveRoadsSettings(showError) {
  if (!state.slug || !state.book) return
  const book = readBookFromDom()
  if (!book) return
  try {
    state.book = await saveRoadsBook(state.slug, book)
    const panel = document.getElementById('home-settings-roads-panel')
    if (panel) {
      renderClassesTable(panel, state.book)
      renderOverridesList(panel, state.book)
      renderCustomsList(panel, state.book)
    }
    window.dispatchEvent(new CustomEvent('peaky-roads-book-changed'))
  } catch (err) {
    showError(err.message || String(err))
    throw err
  }
}

export function bindRoadsSettingsUi(showError) {
  const panel = document.getElementById('home-settings-roads-panel')
  if (!panel) return

  panel.addEventListener('click', (ev) => {
    const t = ev.target
    if (!(t instanceof Element)) return
    const reset = t.closest('.home-roads-reset')
    if (reset && state.book) {
      const id = reset.getAttribute('data-class-id')
      const row = state.book.classes?.find((r) => r.id === id)
      if (row) {
        row.role = row.default_role
        renderClassesTable(panel, state.book)
      }
      return
    }
    const delOv = t.closest('.home-roads-override-delete')
    if (delOv && state.book) {
      const i = Number(delOv.getAttribute('data-ov-index'))
      state.book.overrides = (state.book.overrides || []).filter((_, idx) => idx !== i)
      renderOverridesList(panel, state.book)
      return
    }
    const delCustom = t.closest('.home-roads-custom-delete')
    if (delCustom && state.book) {
      const i = Number(delCustom.getAttribute('data-custom-index'))
      state.book.customs = (state.book.customs || []).filter((_, idx) => idx !== i)
      renderCustomsList(panel, state.book)
    }
  })
}

export { roadsApiUrl }
