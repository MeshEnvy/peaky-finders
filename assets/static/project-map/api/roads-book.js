// @ts-check

import { roadsBookApiUrl } from './urls.js'

/** @typedef {'paved'|'jeep'|'hiking'|'other'|'private'} RoadRole */

/**
 * @param {string} projectSlug
 * @returns {Promise<{ sources?: object, rules: object[] }>}
 */
export async function fetchRoadsBook(projectSlug) {
  const resp = await fetch(roadsBookApiUrl(projectSlug))
  const payload = await resp.json().catch(() => null)
  if (!resp.ok) {
    const msg = payload?.error || resp.statusText || 'Failed to load roads'
    throw new Error(msg)
  }
  return payload
}

/**
 * @param {string} projectSlug
 * @param {object} book
 */
export async function saveRoadsBook(projectSlug, book) {
  const resp = await fetch(roadsBookApiUrl(projectSlug), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(book),
  })
  const payload = await resp.json().catch(() => null)
  if (!resp.ok) {
    const msg = payload?.error || resp.statusText || 'Failed to save roads'
    throw new Error(msg)
  }
  return payload
}

/** @param {object} props @returns {string|null} */
export function segmentIdFromProps(props) {
  if (props?.id) return String(props.id)
  const osmId = props?.osm_id
  if (osmId != null && osmId !== '') return `osm:${osmId}`
  return null
}

/** @param {object} props */
export function overrideKeyFromProps(props) {
  const id = segmentIdFromProps(props)
  if (id) return { kind: 'id', id }
  return null
}

/**
 * @param {object} book
 * @param {{ kind: 'id', id: string }} key
 * @param {RoadRole} role
 */
export function upsertOverride(book, key, role) {
  if (!Array.isArray(book.rules)) book.rules = []
  const rules = book.rules.filter((row) => {
    const mid = row.match?.id
    return !(mid && mid === key.id)
  })
  rules.push({ match: { id: key.id }, role })
  book.rules = rules
}

/**
 * @param {object} book
 * @param {string} id
 * @param {RoadRole} role
 * @param {[number, number][]} coords lat/lon pairs
 */
export function appendCustomRoad(book, id, role, coords) {
  if (!Array.isArray(book.rules)) book.rules = []
  book.rules.push({
    id,
    role,
    coords: coords.map(([lat, lon]) => [lat, lon]),
  })
}
