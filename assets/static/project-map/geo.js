import {
  SEEK_PEAK_BIN_MIN_M,
  SEEK_PEAK_BIN_MAX_M,
  SEEK_PEAK_BINS_ACROSS_VIEWPORT,
} from './constants.js'

export function compareHuman(left, right) {
  return String(left).localeCompare(String(right), undefined, {
    numeric: true,
    sensitivity: 'base',
  })
}

export function copyCoordPair(lat, lon) {
  return navigator.clipboard.writeText(`${formatCoord(lat)}, ${formatCoord(lon)}`).catch(() => {})
}

export function formatAntennaHeightM(heightM) {
  if (heightM == null || !Number.isFinite(Number(heightM))) return null
  return `${Number(heightM).toLocaleString(undefined, { maximumFractionDigits: 1 })} m`
}

export function formatSiteHeight(site, defaultTxHeightM) {
  const explicit = formatAntennaHeightM(site.height_m)
  if (explicit) return explicit
  return `default (${defaultTxHeightM} m)`
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

export function seekPeakBinSizeMForBounds(bounds) {
  const centerLat = (bounds.getNorth() + bounds.getSouth()) / 2
  const lngSpan = Math.abs(bounds.getEast() - bounds.getWest())
  const metersPerDegLng = 111320 * Math.cos((centerLat * Math.PI) / 180)
  const viewportWidthM = lngSpan * metersPerDegLng
  const raw = viewportWidthM / SEEK_PEAK_BINS_ACROSS_VIEWPORT
  return Math.round(
    Math.max(SEEK_PEAK_BIN_MIN_M, Math.min(SEEK_PEAK_BIN_MAX_M, raw)),
  )
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
    Math.cos(phi1) * Math.sin(phi2) -
    Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLon)
  return (toDeg(Math.atan2(y, x)) + 360) % 360
}

export function progressLensHalfAngleDeg(hopM, goalDistM) {
  if (goalDistM <= 1 || hopM >= 2 * goalDistM) return 180
  return (Math.acos(Math.min(1, Math.max(0, hopM / (2 * goalDistM)))) * 180) / Math.PI
}

function signedBearingDelta(fromDeg, toDeg) {
  let d = (toDeg - fromDeg) % 360
  if (d > 180) d -= 360
  else if (d < -180) d += 360
  return d
}

function appendBearingArc(ring, centerLat, centerLon, radiusM, fromBearing, toBearing, steps) {
  const delta = signedBearingDelta(fromBearing, toBearing)
  for (let i = 1; i <= steps; i += 1) {
    const t = i / steps
    const bearing = fromBearing + delta * t
    const [lat, lon] = destinationPointLatLon(centerLat, centerLon, bearing, radiusM)
    ring.push([lon, lat])
  }
}

export function destinationPointLatLon(lat, lon, bearingDegVal, distanceM) {
  const r = 6371000
  const brng = (bearingDegVal * Math.PI) / 180
  const lat1 = (lat * Math.PI) / 180
  const lon1 = (lon * Math.PI) / 180
  const ang = distanceM / r
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(ang) +
      Math.cos(lat1) * Math.sin(ang) * Math.cos(brng),
  )
  const lon2 =
    lon1 +
    Math.atan2(
      Math.sin(brng) * Math.sin(ang) * Math.cos(lat1),
      Math.cos(ang) - Math.sin(lat1) * Math.sin(lat2),
    )
  return [(lat2 * 180) / Math.PI, (lon2 * 180) / Math.PI]
}

export function buildSeekLensFeature(from, goal, hopRadiusM) {
  const goalDist = haversineMeters(from.lat, from.lon, goal.lat, goal.lon)
  const goalBearing = bearingDeg(from.lat, from.lon, goal.lat, goal.lon)
  const half = progressLensHalfAngleDeg(hopRadiusM, goalDist)
  const steps = 32
  const ring = [[from.lon, from.lat]]
  for (let i = 0; i <= steps; i += 1) {
    const t = i / steps
    const bearing = goalBearing - half + t * (2 * half)
    const [lat, lon] = destinationPointLatLon(from.lat, from.lon, bearing, hopRadiusM)
    ring.push([lon, lat])
  }
  const [plusLat, plusLon] = destinationPointLatLon(
    from.lat,
    from.lon,
    goalBearing + half,
    hopRadiusM,
  )
  const [minusLat, minusLon] = destinationPointLatLon(
    from.lat,
    from.lon,
    goalBearing - half,
    hopRadiusM,
  )
  const bPlus = bearingDeg(goal.lat, goal.lon, plusLat, plusLon)
  const bMinus = bearingDeg(goal.lat, goal.lon, minusLat, minusLon)
  const bNear = bearingDeg(goal.lat, goal.lon, from.lat, from.lon)
  appendBearingArc(ring, goal.lat, goal.lon, goalDist, bPlus, bNear, steps)
  appendBearingArc(ring, goal.lat, goal.lon, goalDist, bNear, bMinus, steps)
  ring.push([from.lon, from.lat])
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring] },
    properties: { kind: 'seek-lens' },
  }
}

export function buildSeekGoalLineFeature(from, goal) {
  const distanceKm =
    haversineMeters(from.lat, from.lon, goal.lat, goal.lon) / 1000
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
  const dphi = ((lat2 - lat1) * Math.PI) / 180
  const dlambda = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dphi / 2) ** 2 +
    Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlambda / 2) ** 2
  return 2 * r * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

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

/** @param {Iterable<string>} takenSlugs */
export function uniqueSlugFromName(name, takenSlugs) {
  const base = slugifyName(name)
  const taken = new Set(takenSlugs)
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

export function normalizeTagInput(raw) {
  return String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

export function slugifyLandFolderId(label, folders) {
  const base =
    String(label)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '') || 'folder'
  const existing = new Set(folders.map((folder) => folder.id))
  if (!existing.has(base)) return base
  let n = 2
  while (existing.has(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}
