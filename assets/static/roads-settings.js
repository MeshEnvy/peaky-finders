'use strict'

import { fetchRoadsBook, saveRoadsBook } from './project-map/api/roads-book.js'

const ROLES = ['paved', 'jeep', 'hiking', 'other', 'private']

/** @param {string} slug */
function roadsApiUrl(slug) {
  return `/api/p/${encodeURIComponent(slug)}/roads`
}

function ruleSummary(row) {
  if (row.coords?.length) {
    const id = row.id || '(auto id)'
    return `custom ${id} · ${row.coords.length} vertices`
  }
  const m = row.match || {}
  const parts = []
  if (m.id) parts.push(`id=${m.id}`)
  if (m.kind) parts.push(`kind=${m.kind}`)
  if (m.surface) parts.push(`surface=${m.surface}`)
  if (m.grade) parts.push(`grade=${m.grade}`)
  if (m.access) parts.push(`access=${m.access}`)
  return parts.length ? parts.join(' · ') : '(empty match)'
}

/**
 * @param {HTMLElement} root
 * @param {object} book
 */
function renderRulesTable(root, book) {
  const tbody = root.querySelector('#home-roads-rules-body')
  if (!tbody) return
  tbody.innerHTML = ''
  const rules = book.rules || []
  rules.forEach((row, index) => {
    const tr = document.createElement('tr')
    const isCustom = Boolean(row.coords?.length)
    const matchCells = isCustom
      ? `<td colspan="5" class="pf-mono">coords</td>`
      : `<td><input class="home-roads-match" data-field="id" data-index="${index}" value="${row.match?.id || ''}" /></td>
         <td><input class="home-roads-match" data-field="kind" data-index="${index}" value="${row.match?.kind || ''}" /></td>
         <td><input class="home-roads-match" data-field="surface" data-index="${index}" value="${row.match?.surface || ''}" /></td>
         <td><input class="home-roads-match" data-field="grade" data-index="${index}" value="${row.match?.grade || ''}" /></td>
         <td><input class="home-roads-match" data-field="access" data-index="${index}" value="${row.match?.access || ''}" /></td>`
    const roleOpts = ROLES.map(
      (r) => `<option value="${r}"${r === row.role ? ' selected' : ''}>${r}</option>`,
    ).join('')
    tr.innerHTML = `${matchCells}
      <td><select class="home-roads-rule-role" data-index="${index}">${roleOpts}</select></td>
      <td class="pf-muted">${ruleSummary(row)}</td>
      <td>
        <wa-button class="home-roads-rule-move" appearance="plain" size="s" data-index="${index}" data-dir="-1" type="button">Up</wa-button>
        <wa-button class="home-roads-rule-move" appearance="plain" size="s" data-index="${index}" data-dir="1" type="button">Down</wa-button>
        <wa-button class="home-roads-rule-delete" appearance="plain" size="s" data-index="${index}" type="button">Delete</wa-button>
      </td>`
    tbody.appendChild(tr)
  })
}

function collectBookFromDom(root, base) {
  const book = structuredClone(base)
  const rules = book.rules || []
  root.querySelectorAll('.home-roads-match').forEach((el) => {
    const index = Number(el.dataset.index)
    const field = el.dataset.field
    if (!Number.isFinite(index) || !field || !rules[index]) return
    if (!rules[index].match) rules[index].match = {}
    rules[index].match[field] = el.value.trim() || undefined
  })
  root.querySelectorAll('.home-roads-rule-role').forEach((el) => {
    const index = Number(el.dataset.index)
    if (!Number.isFinite(index) || !rules[index]) return
    rules[index].role = el.value
  })
  book.rules = rules
  return book
}

const panelState = { root: null, slug: null, book: null, bound: false }

async function panelLoad() {
  if (!panelState.root || !panelState.slug) return
  panelState.book = await fetchRoadsBook(panelState.slug)
  renderRulesTable(panelState.root, panelState.book)
}

async function panelSave() {
  if (!panelState.root || !panelState.slug || !panelState.book) return
  const book = collectBookFromDom(panelState.root, panelState.book)
  panelState.book = await saveRoadsBook(panelState.slug, book)
  renderRulesTable(panelState.root, panelState.book)
  window.dispatchEvent(new CustomEvent('peaky-roads-book-changed'))
}

function bindPanelOnce(root) {
  if (panelState.bound) return
  panelState.bound = true
  root.querySelector('#home-roads-save')?.addEventListener('click', () => {
    void panelSave()
  })
  root.querySelector('#home-roads-rules-body')?.addEventListener('click', (ev) => {
    const move = ev.target.closest('.home-roads-rule-move')
    if (move && panelState.book) {
      const index = Number(move.dataset.index)
      const dir = Number(move.dataset.dir)
      const rules = panelState.book.rules || []
      const next = index + dir
      if (Number.isFinite(index) && next >= 0 && next < rules.length) {
        const [row] = rules.splice(index, 1)
        rules.splice(next, 0, row)
        panelState.book.rules = rules
        renderRulesTable(root, panelState.book)
      }
      return
    }
    const btn = ev.target.closest('.home-roads-rule-delete')
    if (!btn || !panelState.book) return
    const index = Number(btn.dataset.index)
    if (!Number.isFinite(index)) return
    panelState.book.rules = (panelState.book.rules || []).filter((_, i) => i !== index)
    renderRulesTable(root, panelState.book)
  })
  root.querySelector('#home-roads-add-match')?.addEventListener('click', () => {
    if (!panelState.book) return
    if (!Array.isArray(panelState.book.rules)) panelState.book.rules = []
    panelState.book.rules.push({ match: { kind: 'track' }, role: 'jeep' })
    renderRulesTable(root, panelState.book)
  })
}

export function bindRoadsSettingsUi(_showError) {}

/** @param {string} slug @param {(msg: string) => void} showError */
export async function loadRoadsSettings(slug, showError) {
  const root = document.getElementById('home-settings-roads-panel')
  if (!root) return
  panelState.root = root
  panelState.slug = slug
  bindPanelOnce(root)
  try {
    await panelLoad()
  } catch (err) {
    showError(err.message || String(err))
  }
}

/** @param {(msg: string) => void} showError */
export async function saveRoadsSettings(showError) {
  try {
    await panelSave()
  } catch (err) {
    showError(err.message || String(err))
  }
}

export function initRoadsSettings(root, slug) {
  panelState.root = root
  panelState.slug = slug
  bindPanelOnce(root)
  void panelLoad()
}
