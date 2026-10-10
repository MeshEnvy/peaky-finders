// @ts-check

import { roadsBookApiUrl } from './urls.js'

/** @typedef {'paved'|'jeep'|'hiking'|'other'} RoadRole */

/**
 * @param {string} projectSlug
 * @returns {Promise<{ classes: object[], overrides: object[], customs: object[] }>}
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

/** @param {object} props @returns {number|null} */
export function osmIdFromProps(props) {
  const raw = props?.osm_id
  if (raw == null || raw === '') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

/** @param {object} props */
export function overrideKeyFromProps(props) {
  const osmId = osmIdFromProps(props)
  if (osmId != null) return { kind: 'osm', osm_id: osmId }
  if (props?.source === 'gtlf' && props?.gtlf_layer && props?.coord_hash) {
    return {
      kind: 'gtlf',
      layer: String(props.gtlf_layer),
      coord_hash: String(props.coord_hash),
    }
  }
  return null
}

/**
 * @param {object} book
 * @param {object} key from overrideKeyFromProps expanded
 * @param {RoadRole} role
 */
export function upsertOverride(book, key, role) {
  const overrides = Array.isArray(book.overrides) ? [...book.overrides] : []
  const filtered = overrides.filter((ov) => {
    if (key.kind === 'osm') return Number(ov.osm_id) !== Number(key.osm_id)
    if (ov.gtlf) {
      return !(ov.gtlf.layer === key.layer && ov.gtlf.coord_hash === key.coord_hash)
    }
    return true
  })
  if (key.kind === 'osm') {
    filtered.push({ osm_id: key.osm_id, role })
  } else {
    filtered.push({ gtlf: { layer: key.layer, coord_hash: key.coord_hash }, role })
  }
  book.overrides = filtered
}

/**
 * @param {object} book
 * @param {string} id
 * @param {RoadRole} role
 * @param {[number, number][]} coords lat/lon pairs
 */
export function appendCustomRoad(book, id, role, coords) {
  const customs = Array.isArray(book.customs) ? [...book.customs] : []
  customs.push({
    id,
    role,
    coords: coords.map(([lat, lon]) => [lat, lon]),
  })
  book.customs = customs
}
