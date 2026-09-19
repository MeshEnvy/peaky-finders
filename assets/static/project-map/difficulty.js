// @ts-check

/**
 * Access difficulty labels. Scan stamps `hike_difficulty` / `jeep_difficulty` on peak rows;
 * serve list JSON includes those plus `access_difficulty` (worse of the two).
 */

const DIFFICULTY_RANK = { easy: 0, medium: 1, difficult: 2, extreme: 3 }

/** @param {string|null|undefined} label */
export function difficultyRank(label) {
  return DIFFICULTY_RANK[String(label || '').toLowerCase()] ?? -1
}

/** @param {number} rank */
export function difficultyLabel(rank) {
  if (rank >= 3) return 'extreme'
  if (rank >= 2) return 'difficult'
  if (rank >= 1) return 'medium'
  return 'easy'
}

/** @param {string|null|undefined} a @param {string|null|undefined} b */
export function worseDifficulty(a, b) {
  return difficultyLabel(Math.max(difficultyRank(a), difficultyRank(b)))
}

/** @param {string|null|undefined} d */
export function difficultyClass(d) {
  const dMap = {
    easy: 'peak-difficulty--easy',
    medium: 'peak-difficulty--medium',
    difficult: 'peak-difficulty--difficult',
    extreme: 'peak-difficulty--extreme',
  }
  return dMap[String(d || '').toLowerCase()] || 'peak-difficulty--medium'
}

/** @param {object|null|undefined} hike */
export function deriveHikeDifficulty(hike, _config) {
  if (!hike) return ''
  if (hike.difficulty) return String(hike.difficulty)
  return ''
}

/** @param {object|null|undefined} jeep */
export function deriveJeepDifficulty(jeep, _config) {
  if (!jeep) return ''
  if (jeep.difficulty) return String(jeep.difficulty)
  return ''
}

/**
 * Governing OSM class for the jeep badge (highway + tracktype + distance).
 * @param {object|null|undefined} jeep
 */
export function jeepOsmClass(jeep) {
  if (!jeep) return null
  if (jeep.osm_highway) {
    return {
      highway: String(jeep.osm_highway),
      tracktype: jeep.osm_tracktype ? String(jeep.osm_tracktype) : '',
      dist_m: Number(jeep.osm_class_m),
    }
  }
  const raw = Array.isArray(jeep.segments) ? jeep.segments : []
  if (!raw.length) return null
  const segs = []
  for (const s of raw) {
    const dist = Math.max(Number(s.dist_m) || 0, 0)
    if (!dist) continue
    const last = segs[segs.length - 1]
    const highway = s.highway || ''
    const tracktype = s.tracktype || ''
    if (last && last.highway === highway && last.tracktype === tracktype) {
      last.dist_m += dist
      continue
    }
    segs.push({ highway, tracktype, dist_m: dist })
  }
  if (!segs.length) return null
  let best = segs[0]
  for (const s of segs) {
    if (s.dist_m > best.dist_m) best = s
  }
  return best
}

/**
 * Worse of hike vs jeep for pin rings and map styling.
 * Prefers stamped list-row fields from scan; legacy rows may omit labels.
 * @param {object|null|undefined} peak
 * @param {object|null|undefined} [_config]
 */
export function accessDifficulty(peak, _config) {
  if (!peak) return ''
  if (peak.access_difficulty) return String(peak.access_difficulty)
  const hike = peak.hike_difficulty ? String(peak.hike_difficulty) : ''
  const jeep = peak.jeep_difficulty ? String(peak.jeep_difficulty) : ''
  if (!hike && !jeep) return ''
  if (!hike) return jeep
  if (!jeep) return hike
  return worseDifficulty(hike, jeep)
}
