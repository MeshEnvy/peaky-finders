import {
  PITCH_TERRAIN_ON,
  SEEK_PEAK_BIN_MIN_M,
  SEEK_PEAK_BIN_MAX_M,
  SEEK_PEAK_BINS_ACROSS_VIEWPORT,
  SEEK_WEDGE_NEAR_DEG,
  SEEK_WEDGE_FAR_DEG,
} from './constants.js'

export function compareHuman(left, right) {
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: 'base',
  })
}

export function kmToDegreeDeltas(latDeg, km) {
  const m = km * 1000
  const latDelta = m / 111_320
  const lonDelta = m / (111_320 * Math.cos((latDeg * Math.PI) / 180))
  return { latDelta, lonDelta }
}

export function lngLatBoundsFromPoints(points) {
  let west = Infinity
  let east = -Infinity
  let south = Infinity
  let north = -Infinity
  for (const ll of points) {
    if (!ll || !Number.isFinite(ll.lng) || !Number.isFinite(ll.lat)) continue
    if (Math.abs(ll.lat) > 90 || Math.abs(ll.lng) > 180) continue
    west = Math.min(west, ll.lng)
    east = Math.max(east, ll.lng)
    south = Math.min(south, ll.lat)
    north = Math.max(north, ll.lat)
  }
  if (!Number.isFinite(west)) return null
  return {
    getWest: () => west,
    getEast: () => east,
    getSouth: () => south,
    getNorth: () => north,
  }
}

export function padMapBounds(bounds, minSpanM) {
  if (!bounds || minSpanM <= 0) return bounds
  const centerLat = (bounds.getNorth() + bounds.getSouth()) / 2
  const latRad = (centerLat * Math.PI) / 180
  const minLatDelta = minSpanM / 111320
  const minLonDelta = minSpanM / (111320 * Math.max(1e-6, Math.cos(latRad)))
  let west = bounds.getWest()
  let east = bounds.getEast()
  let south = bounds.getSouth()
  let north = bounds.getNorth()
  if (east - west < minLonDelta) {
    const cx = (east + west) / 2
    west = cx - minLonDelta / 2
    east = cx + minLonDelta / 2
  }
  if (north - south < minLatDelta) {
    const cy = (north + south) / 2
    south = cy - minLatDelta / 2
    north = cy + minLatDelta / 2
  }
  return {
    getWest: () => west,
    getEast: () => east,
    getSouth: () => south,
    getNorth: () => north,
  }
}

/** @param {number} lon @param {number} lat */
export function coordsUsableForMarker(lon, lat) {
  return (
    Number.isFinite(lon) &&
    Number.isFinite(lat) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180
  )
}

export function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export function slugifyName(name) {
  let base = String(name || '')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/[\s_]+/g, '-')
    .toLowerCase()
    .replace(/^-+|-+$/g, '')
  return base || 'site'
}

/** @param {string} name @param {Set<string>} takenSlugs */
export function previewSlugForName(name, takenSlugs) {
  const base = slugifyName(name)
  const taken = takenSlugs
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

export function formatCoord(n) {
  return Number(n).toFixed(6)
}

export function parseCoordPairFromText(text) {
  const trimmed = String(text || '').trim()
  if (!trimmed) return null
  const parts = trimmed.split(/[,\s]+/).filter(Boolean)
  if (parts.length < 2) return null
  const lat = Number.parseFloat(parts[0])
  const lon = Number.parseFloat(parts[1])
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  return { lat, lon }
}

export function coordsMatchPair(lat1, lon1, lat2, lon2) {
  return Math.abs(lat1 - lat2) < 1e-5 && Math.abs(lon1 - lon2) < 1e-5
}

export function coordSeparationM(lat1, lon1, lat2, lon2) {
  const r = 6371000
  const phi1 = (lat1 * Math.PI) / 180
  const phi2 = (lat2 * Math.PI) / 180
  const dPhi = ((lat2 - lat1) * Math.PI) / 180
  const dLambda = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dPhi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2
  return 2 * r * Math.asin(Math.sqrt(a))
}

export function haversineMeters(lat1, lon1, lat2, lon2) {
  const earthRadiusM = 6371000
  const toRad = (deg) => (deg * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * earthRadiusM * Math.asin(Math.sqrt(a))
}

export function bearingDeg(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180
  const toDeg = (rad) => (rad * 180) / Math.PI
  const phi1 = toRad(lat1)
  const phi2 = toRad(lat2)
  const dLon = toRad(lon2 - lon1)
  const y = Math.sin(dLon) * Math.cos(phi2)
  const x =
    Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

export function destinationPointLatLon(lat, lon, bearingDegVal, distanceM) {
  const r = 6371000
  const brng = (bearingDegVal * Math.PI) / 180
  const lat1 = (lat * Math.PI) / 180
  const lon1 = (lon * Math.PI) / 180
  const ang = distanceM / r
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(ang) + Math.cos(lat1) * Math.sin(ang) * Math.cos(brng),
  )
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(brng) * Math.sin(ang) * Math.cos(lat1),
      Math.cos(ang) - Math.sin(lat1) * Math.sin(lat2),
    )
  return [(lat2 * 180) / Math.PI, (lon2 * 180) / Math.PI]
}

/** @param {number} hopM @param {number} hopRadiusM */
export function seekWedgeHalfAngleDeg(hopM, hopRadiusM) {
  if (hopRadiusM <= 0) return SEEK_WEDGE_FAR_DEG
  const t = Math.min(1, Math.max(0, hopM / hopRadiusM))
  return SEEK_WEDGE_NEAR_DEG + t * (SEEK_WEDGE_FAR_DEG - SEEK_WEDGE_NEAR_DEG)
}

/** @param {{ lat: number, lon: number }} from @param {{ lat: number, lon: number }} goal @param {number} hopRadiusM */
export function buildSeekWedgeFeature(from, goal, hopRadiusM) {
  const goalBearing = bearingDeg(from.lat, from.lon, goal.lat, goal.lon)
  const steps = 36
  const ring = [[from.lon, from.lat]]
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps
    const d = t * hopRadiusM
    const half = seekWedgeHalfAngleDeg(d, hopRadiusM)
    const [lat, lon] = destinationPointLatLon(from.lat, from.lon, goalBearing - half, d)
    ring.push([lon, lat])
  }
  for (let i = steps; i >= 0; i -= 1) {
    const t = i / steps
    const d = t * hopRadiusM
    const half = seekWedgeHalfAngleDeg(d, hopRadiusM)
    const [lat, lon] = destinationPointLatLon(from.lat, from.lon, goalBearing + half, d)
    ring.push([lon, lat])
  }
  ring.push([from.lon, from.lat])
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring] },
    properties: { kind: 'seek-wedge' },
  }
}

/** @param {{ lat: number, lon: number }} from @param {{ lat: number, lon: number }} goal */
export function buildSeekGoalLineFeature(from, goal) {
  const distanceKm = haversineMeters(from.lat, from.lon, goal.lat, goal.lon) / 1000
  return {
    type: 'Feature',
    geometry: {
      type: 'LineString',
      coordinates: [
        [from.lon, from.lat],
        [goal.lon, goal.lat],
      ],
    },
    properties: {
      distance_km: Math.round(distanceKm * 10) / 10,
      bearing_deg: Math.round(bearingDeg(from.lat, from.lon, goal.lat, goal.lon)),
      kind: 'goal',
    },
  }
}

/**
 * @param {import('maplibre-gl').Map} mapInstance
 * @param {boolean} mapReady
 */
export function isMapTiltedView(mapInstance, mapReady) {
  return Boolean(mapInstance && mapReady && mapInstance.getPitch() >= PITCH_TERRAIN_ON)
}

/**
 * @param {import('maplibre-gl').Map} mapInstance
 */
export function mapOverheadEquivalentBounds(mapInstance) {
  const center = mapInstance.getCenter()
  const zoom = mapInstance.getZoom()
  const canvas = mapInstance.getCanvas()
  const w = Math.max(1, canvas.clientWidth)
  const h = Math.max(1, canvas.clientHeight)
  const latRad = (center.lat * Math.PI) / 180
  const worldSize = 512 * 2 ** zoom
  const metersPerPixel = (40_075_016.686 * Math.cos(latRad)) / worldSize
  const halfWidthM = (w / 2) * metersPerPixel
  const halfHeightM = (h / 2) * metersPerPixel
  const latDelta = halfHeightM / 111_320
  const lonDelta = halfWidthM / (111_320 * Math.max(1e-6, Math.cos(latRad)))
  const west = center.lng - lonDelta
  const east = center.lng + lonDelta
  const south = center.lat - latDelta
  const north = center.lat + latDelta
  return {
    getWest: () => west,
    getEast: () => east,
    getSouth: () => south,
    getNorth: () => north,
  }
}

/**
 * @param {import('maplibre-gl').Map} mapInstance
 * @param {boolean} mapReady
 */
export function mapDataViewportBounds(mapInstance, mapReady) {
  if (isMapTiltedView(mapInstance, mapReady)) return mapOverheadEquivalentBounds(mapInstance)
  return mapInstance.getBounds()
}

/**
 * @param {import('maplibre-gl').Map} mapInstance
 * @param {boolean} mapReady
 */
export function mapSeekScanBounds(mapInstance, mapReady) {
  if (!mapInstance || !mapReady) return mapDataViewportBounds(mapInstance, mapReady)
  if (!isMapTiltedView(mapInstance, mapReady)) return mapInstance.getBounds()
  const canvas = mapInstance.getCanvas()
  const w = Math.max(1, canvas.clientWidth)
  const h = Math.max(1, canvas.clientHeight)
  const yMin = h * 0.1
  const cols = 7
  const rows = 7
  const points = []
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const x = ((col + 0.5) / cols) * w
      const y = yMin + ((row + 0.5) / rows) * (h - yMin)
      points.push(mapInstance.unproject([x, y]))
    }
  }
  const bounds = lngLatBoundsFromPoints(points)
  if (bounds) return bounds
  return mapInstance.getBounds()
}

/** @param {{ getNorth: () => number, getSouth: () => number, getEast: () => number, getWest: () => number }} bounds */
export function seekPeakBinSizeMForBounds(bounds) {
  const centerLat = (bounds.getNorth() + bounds.getSouth()) / 2
  const lngSpan = Math.abs(bounds.getEast() - bounds.getWest())
  const metersPerDegLng = 111320 * Math.cos((centerLat * Math.PI) / 180)
  const viewportWidthM = lngSpan * metersPerDegLng
  const raw = viewportWidthM / SEEK_PEAK_BINS_ACROSS_VIEWPORT
  return Math.round(Math.max(SEEK_PEAK_BIN_MIN_M, Math.min(SEEK_PEAK_BIN_MAX_M, raw)))
}

/** @param {import('maplibre-gl').Map} mapInstance @param {boolean} mapReady */
export function seekScanBoundsForRequest(mapInstance, mapReady) {
  const raw = mapSeekScanBounds(mapInstance, mapReady)
  const binM = seekPeakBinSizeMForBounds(raw)
  return padMapBounds(raw, Math.max(binM * 4, SEEK_PEAK_BIN_MIN_M * 2))
}

/** @param {number} km @param {number} min @param {number} max */
export function clampRadiusKm(km, min, max) {
  return Math.max(min, Math.min(max, Number(km)))
}

/** @param {number} quality */
export function clampViewshedQuality(quality) {
  return Math.max(1, Math.min(5, Math.round(Number(quality))))
}
