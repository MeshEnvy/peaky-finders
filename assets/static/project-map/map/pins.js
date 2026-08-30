// @ts-check

import { PIN_LOAD_MARKER_OFFSET } from '../constants.js'
import { coordsUsableForMarker } from '../geo.js'

/** @type {Map<string, maplibregl.Marker>} */
const pinLoadMarkers = new Map()

/** @type {{ map: maplibregl.Map, getMapReady: () => boolean, setMarkerLngLatSafe: (marker: maplibregl.Marker, lon: number, lat: number) => boolean } | null} */
let pinCtx = null

/**
 * @param {{ map: maplibregl.Map, getMapReady: () => boolean, setMarkerLngLatSafe: (marker: maplibregl.Marker, lon: number, lat: number) => boolean }} ctx
 */
export function initPinMarkers(ctx) {
  pinCtx = ctx
}

/** @param {string} slug @param {'overlay' | 'spinner'} [kind] */
function createPinLoadMarkerElement(slug, kind) {
  const el = document.createElement('div')
  el.setAttribute('data-slug', slug)
  if (kind === 'spinner') {
    el.className = 'pin-load-spinner-wrap'
    const spin = document.createElement('div')
    spin.className = 'pin-load-spinner'
    el.appendChild(spin)
    return el
  }
  el.className = 'pin-load-overlay'
  el.innerHTML =
    '<div class="pin-load-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100">' +
    '<div class="pin-load-progress__fill"></div></div>' +
    '<div class="pin-load-progress__label"></div>'
  return el
}

/** @param {string} slug @param {'overlay' | 'spinner'} [kind] */
export function ensurePinLoadMarker(slug, kind = 'overlay') {
  if (!pinCtx) throw new Error('initPinMarkers must be called first')
  let marker = pinLoadMarkers.get(slug)
  if (!marker) {
    const el = createPinLoadMarkerElement(slug, kind)
    marker = new maplibregl.Marker({
      element: el,
      anchor: kind === 'spinner' ? 'center' : 'top',
      offset: kind === 'spinner' ? [0, 0] : PIN_LOAD_MARKER_OFFSET,
    })
    pinLoadMarkers.set(slug, marker)
  }
  return marker
}

/** @param {string} slug */
export function hidePinLoadMarker(slug) {
  const marker = pinLoadMarkers.get(slug)
  if (!marker) return
  marker.remove()
  pinLoadMarkers.delete(slug)
}

/**
 * @param {string} slug
 * @param {number} lon
 * @param {number} lat
 * @param {{ progressLabel: string, progressFraction: number }} progress
 */
export function renderPinLoadOverlay(slug, lon, lat, progress) {
  if (!pinCtx) return
  const mapReady = pinCtx.getMapReady()
  if (!mapReady || !coordsUsableForMarker(lon, lat)) return
  const marker = ensurePinLoadMarker(slug)
  if (!pinCtx.setMarkerLngLatSafe(marker, lon, lat)) return
  const el = marker.getElement()
  const frac = progress.progressFraction
  const pct = Math.round(frac * 100)
  const fill = el.querySelector('.pin-load-progress__fill')
  const label = el.querySelector('.pin-load-progress__label')
  const bar = el.querySelector('.pin-load-progress')
  if (fill) fill.style.width = `${pct}%`
  if (label) label.textContent = progress.progressLabel
  if (bar) {
    bar.setAttribute('aria-valuenow', String(pct))
    bar.setAttribute('aria-label', `${slug} ${progress.progressLabel}`)
  }
  el.hidden = false
}

/** @param {Set<string>} activeSlugs */
export function hideInactivePinMarkers(activeSlugs) {
  for (const [slug] of pinLoadMarkers) {
    if (!activeSlugs.has(slug)) hidePinLoadMarker(slug)
  }
}

/** @returns {IterableIterator<string>} */
export function pinLoadMarkerSlugs() {
  return pinLoadMarkers.keys()
}
