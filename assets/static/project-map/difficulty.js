// @ts-check

/**
 * Runtime access difficulty (easy / medium / difficult / extreme).
 * Keep thresholds in sync with `crates/peaky-preset/src/difficulty.rs`.
 * Project overrides may live at `peaks/_meta.yaml` → `rules.difficulty` (optional).
 */

/** @typedef {{
 *   extremeMinGainM?: number,
 *   extremeMinAvgGradePct?: number,
 *   difficultMinGainM?: number,
 *   difficultMinAvgGradePct?: number,
 *   mediumMinMaxGradePct?: number,
 *   mediumMinAvgGradePct?: number,
 *   mediumMinGainM?: number,
 * }} HikeDifficultyConfig */

/** @type {Required<HikeDifficultyConfig>} */
export const DEFAULT_HIKE_DIFFICULTY = {
  extremeMinGainM: 250,
  extremeMinAvgGradePct: 18,
  difficultMinGainM: 80,
  difficultMinAvgGradePct: 15,
  mediumMinMaxGradePct: 15,
  mediumMinAvgGradePct: 8,
  mediumMinGainM: 40,
}

/** @typedef {{ hike?: Partial<HikeDifficultyConfig> }} DifficultyConfig */

/** @param {Record<string, unknown>|undefined} hike */
function hikeConfigFromRules(hike) {
  if (!hike) return {}
  return {
    extremeMinGainM: hike.extreme_min_gain_m ?? hike.extremeMinGainM,
    extremeMinAvgGradePct: hike.extreme_min_avg_grade_pct ?? hike.extremeMinAvgGradePct,
    difficultMinGainM: hike.difficult_min_gain_m ?? hike.difficultMinGainM,
    difficultMinAvgGradePct: hike.difficult_min_avg_grade_pct ?? hike.difficultMinAvgGradePct,
    mediumMinMaxGradePct: hike.medium_min_max_grade_pct ?? hike.mediumMinMaxGradePct,
    mediumMinAvgGradePct: hike.medium_min_avg_grade_pct ?? hike.mediumMinAvgGradePct,
    mediumMinGainM: hike.medium_min_gain_m ?? hike.mediumMinGainM,
  }
}

/** @param {DifficultyConfig|null|undefined} config */
export function mergeDifficultyConfig(config) {
  const hike = hikeConfigFromRules(config?.hike)
  return {
    hike: {
      ...DEFAULT_HIKE_DIFFICULTY,
      ...Object.fromEntries(Object.entries(hike).filter(([, v]) => v != null)),
    },
  }
}

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

/**
 * @param {number} maxGradePct
 * @param {number} avgGradePct
 * @param {number} gainM
 * @param {Required<HikeDifficultyConfig>} cfg
 */
export function hikeDifficultyFromGrades(maxGradePct, avgGradePct, gainM, cfg) {
  if (gainM >= cfg.extremeMinGainM && avgGradePct >= cfg.extremeMinAvgGradePct) return 'extreme'
  if (gainM >= cfg.difficultMinGainM && avgGradePct >= cfg.difficultMinAvgGradePct) return 'difficult'
  if (
    maxGradePct >= cfg.mediumMinMaxGradePct ||
    avgGradePct >= cfg.mediumMinAvgGradePct ||
    gainM >= cfg.mediumMinGainM
  ) {
    return 'medium'
  }
  return 'easy'
}

/** Keep in sync with `jeep_road_rank` in difficulty.rs. */
export function jeepRoadRank(highway, tracktype) {
  const hw = String(highway || '')
  const tt = tracktype || ''
  if (['motorway', 'trunk', 'primary', 'secondary', 'tertiary'].includes(hw)) return 0
  if (hw === 'residential' || hw === 'unclassified') {
    if (tt === 'grade5') return 3
    if (tt === 'grade4' || tt === 'grade3') return 2
    if (tt === 'grade1') return 0
    return 1
  }
  if (hw === 'service') return 1
  if (hw === 'track') {
    if (tt === 'grade5') return 3
    if (tt === 'grade4' || tt === 'grade3') return 2
    return 1
  }
  return 1
}

/**
 * Governing OSM class for the jeep badge (highway + tracktype + distance).
 * Prefers serve-computed `osm_*` fields; falls back to `segments`.
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
  const total = segs.reduce((sum, s) => sum + s.dist_m, 0)
  const minLen = Math.max(total * 0.02, 40)
  const kept = segs.filter((s) => total < 80 || s.dist_m >= minLen)
  if (!kept.length) return null
  let worst = 0
  for (const s of kept) worst = Math.max(worst, jeepRoadRank(s.highway, s.tracktype))
  const distByClass = new Map()
  for (const s of kept) {
    if (jeepRoadRank(s.highway, s.tracktype) !== worst) continue
    const key = `${s.highway || ''}\t${s.tracktype || ''}`
    distByClass.set(key, (distByClass.get(key) || 0) + Math.max(Number(s.dist_m) || 0, 0))
  }
  let best = null
  for (const [key, dist] of distByClass) {
    if (!best || dist > best.dist_m) {
      const [highway, tracktype] = key.split('\t')
      best = { highway, tracktype, dist_m: dist }
    }
  }
  return best
}

/**
 * @param {object|null|undefined} hike
 * @param {DifficultyConfig|null|undefined} config
 */
export function deriveHikeDifficulty(hike, config) {
  if (!hike) return ''
  const cfg = mergeDifficultyConfig(config).hike
  const maxG = Number(hike.max_grade_pct)
  const avgG = Number(hike.avg_grade_pct)
  const gainM = Number(hike.gain_m)
  if (!Number.isFinite(maxG) && !Number.isFinite(avgG) && !Number.isFinite(gainM)) return ''
  return hikeDifficultyFromGrades(
    Number.isFinite(maxG) ? maxG : 0,
    Number.isFinite(avgG) ? avgG : 0,
    Number.isFinite(gainM) ? gainM : 0,
    cfg,
  )
}

/**
 * @param {object|null|undefined} jeep
 * @param {DifficultyConfig|null|undefined} [_config]
 */
export function deriveJeepDifficulty(jeep, _config) {
  if (!jeep) return ''
  const cls = jeepOsmClass(jeep)
  if (!cls?.highway) return ''
  return difficultyLabel(jeepRoadRank(cls.highway, cls.tracktype))
}

/**
 * Derive from thin peak/catalog facts (no profile blob).
 * @param {object|null|undefined} peak
 * @param {DifficultyConfig|null|undefined} config
 */
export function derivePeakHikeDifficulty(peak, config) {
  if (!peak) return ''
  const cfg = mergeDifficultyConfig(config).hike
  if (
    Number.isFinite(peak.hike_gain_m) &&
    Number.isFinite(peak.hike_avg_grade_pct) &&
    Number.isFinite(peak.hike_max_grade_pct)
  ) {
    return hikeDifficultyFromGrades(
      peak.hike_max_grade_pct,
      peak.hike_avg_grade_pct,
      peak.hike_gain_m,
      cfg,
    )
  }
  if (peak.hike) return deriveHikeDifficulty(peak.hike, config)
  if (Number.isFinite(peak.hike_m) && Number.isFinite(peak.max_slope_deg)) {
    const maxGrade = Math.tan((peak.max_slope_deg * Math.PI) / 180) * 100
    return hikeDifficultyFromGrades(maxGrade, 0, 0, cfg)
  }
  return ''
}

/**
 * @param {object|null|undefined} peak
 * @param {DifficultyConfig|null|undefined} config
 */
export function derivePeakJeepDifficulty(peak, config) {
  if (!peak) return ''
  if (peak.jeep_highway) {
    return difficultyLabel(jeepRoadRank(peak.jeep_highway, peak.jeep_tracktype || ''))
  }
  if (peak.jeep) return deriveJeepDifficulty(peak.jeep, config)
  return ''
}

/**
 * Worse of hike vs jeep for pin rings and map styling.
 * @param {object|null|undefined} peak
 * @param {DifficultyConfig|null|undefined} config
 */
export function accessDifficulty(peak, config) {
  if (!peak) return ''
  const hike = derivePeakHikeDifficulty(peak, config)
  const jeep = derivePeakJeepDifficulty(peak, config)
  if (!hike && !jeep) return ''
  if (!hike) return jeep
  if (!jeep) return hike
  return worseDifficulty(hike, jeep)
}
