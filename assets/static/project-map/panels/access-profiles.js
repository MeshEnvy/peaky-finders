// @ts-check

import { computed } from 'vue'

/** Grade → fill color (flat → steep), onX-style elevation map. */
const GRADE_COLORS = [
  '#22c55e',
  '#84cc16',
  '#eab308',
  '#f97316',
  '#ef4444',
  '#dc2626',
  '#991b1b',
]

const CHART = { w: 300, h: 112, padX: 12, padTop: 22, padBottom: 22 }

/** Match Rust `access::LEG_MIN_M` — hide zero-length jeep/hike legs. */
const LEG_MIN_M = 5

/** @param {object|null|undefined} leg */
function hasAccessLeg(leg) {
  return leg != null && Number.isFinite(leg.horiz_m) && leg.horiz_m > LEG_MIN_M
}

/** Keep chart segments wide enough to show grade colors (long jeep routes). */
const CHART_MAX_DRAW_POINTS = 96

/** @param {number} gradePct */
export function gradeColor(gradePct) {
  if (gradePct < 5) return GRADE_COLORS[0]
  if (gradePct < 10) return GRADE_COLORS[1]
  if (gradePct < 15) return GRADE_COLORS[2]
  if (gradePct < 20) return GRADE_COLORS[3]
  if (gradePct < 25) return GRADE_COLORS[4]
  if (gradePct < 30) return GRADE_COLORS[5]
  return GRADE_COLORS[6]
}

/**
 * Road → destination order, optional snap of last elev.
 * @param {object[]|undefined} profile
 * @param {number|undefined} endElevM
 */
export function prepareDisplayProfile(profile, endElevM) {
  if (!profile?.length) return []
  let pts = profile.map((p) => ({ ...p }))
  if (pts.length >= 2 && pts[0].dist_m > pts[pts.length - 1].dist_m) {
    const maxDist = Math.max(...pts.map((p) => p.dist_m))
    pts = pts
      .slice()
      .reverse()
      .map((p) => ({ ...p, dist_m: maxDist - p.dist_m }))
  }
  if (Number.isFinite(endElevM)) {
    const last = pts.length - 1
    pts[last] = { ...pts[last], elev_m: endElevM }
  }
  return pts
}

/**
 * @param {object[]} profile
 * @param {number} maxPoints
 */
export function downsampleProfile(profile, maxPoints = CHART_MAX_DRAW_POINTS) {
  if (profile.length <= maxPoints) return profile
  const out = [profile[0]]
  const last = profile.length - 1
  for (let i = 1; i < maxPoints - 1; i += 1) {
    const idx = Math.round((i / (maxPoints - 1)) * last)
    out.push(profile[idx])
  }
  out.push(profile[last])
  return out
}

/** @param {number} elevM */
export function formatElevLabel(elevM) {
  if (!Number.isFinite(elevM)) return '—'
  return `${Math.round(elevM).toLocaleString()} m`
}

/** @param {object[]} profile */
export function buildElevationChart(profile) {
  if (!profile?.length) return null
  const drawProfile = downsampleProfile(profile)
  const maxDist = Math.max(...profile.map((p) => p.dist_m), 1)
  const elevs = profile.map((p) => p.elev_m)
  const rawMin = Math.min(...elevs)
  const rawMax = Math.max(...elevs)
  const mid = (rawMin + rawMax) / 2
  const minSpanM = 6.0
  const span = Math.max(rawMax - rawMin, minSpanM)
  const minE = mid - span / 2
  const maxE = mid + span / 2
  const rangeE = maxE - minE
  const plotH = CHART.h - CHART.padTop - CHART.padBottom
  const plotW = CHART.w - CHART.padX * 2
  const baseline = CHART.h - CHART.padBottom

  /** @param {object} p */
  function xy(p) {
    return {
      x: CHART.padX + (p.dist_m / maxDist) * plotW,
      y: CHART.padTop + plotH * (1 - (p.elev_m - minE) / rangeE),
    }
  }

  /** @param {object} a @param {object} b */
  function segmentGrade(a, b) {
    const horiz = Math.max(b.dist_m - a.dist_m, 0.001)
    return (Math.abs(b.elev_m - a.elev_m) / horiz) * 100
  }

  const segments = []
  const outline = []
  for (let i = 0; i < drawProfile.length - 1; i += 1) {
    const a = drawProfile[i]
    const b = drawProfile[i + 1]
    const p0 = xy(a)
    const p1 = xy(b)
    segments.push({
      path: `M${p0.x.toFixed(1)},${baseline} L${p0.x.toFixed(1)},${p0.y.toFixed(1)} L${p1.x.toFixed(1)},${p1.y.toFixed(1)} L${p1.x.toFixed(1)},${baseline} Z`,
      color: gradeColor(segmentGrade(a, b)),
      lat: b.lat,
      lon: b.lon,
    })
    if (i === 0) outline.push(`M${p0.x.toFixed(1)},${p0.y.toFixed(1)}`)
    outline.push(`L${p1.x.toFixed(1)},${p1.y.toFixed(1)}`)
  }

  const start = xy(drawProfile[0])
  const end = xy(drawProfile[drawProfile.length - 1])
  const gridLines = [0.25, 0.5, 0.75].map((t) => ({
    y: CHART.padTop + plotH * t,
  }))

  return {
    segments,
    outline: outline.join(' '),
    gridLines,
    profile,
    maxDist,
    padX: CHART.padX,
    plotW,
    start: {
      x: start.x,
      y: start.y,
      elev: formatElevLabel(drawProfile[0].elev_m),
    },
    end: {
      x: end.x,
      y: end.y,
      elev: formatElevLabel(drawProfile[drawProfile.length - 1].elev_m),
    },
    baseline,
    width: CHART.w,
    height: CHART.h,
  }
}

/**
 * @param {MouseEvent} event
 * @param {object|null} chart
 */
export function profilePointAtChartClick(event, chart) {
  if (!chart?.profile?.length) return null
  const svg = /** @type {SVGSVGElement|null} */ (event.currentTarget)
  if (!svg) return null
  const rect = svg.getBoundingClientRect()
  if (rect.width <= 0) return null
  const x = ((event.clientX - rect.left) / rect.width) * chart.width
  const t = Math.min(1, Math.max(0, (x - chart.padX) / chart.plotW))
  const targetDist = t * chart.maxDist
  let best = chart.profile[0]
  let bestDelta = Math.abs(best.dist_m - targetDist)
  for (let i = 1; i < chart.profile.length; i += 1) {
    const p = chart.profile[i]
    const delta = Math.abs(p.dist_m - targetDist)
    if (delta < bestDelta) {
      best = p
      bestDelta = delta
    }
  }
  if (!Number.isFinite(best.lat) || !Number.isFinite(best.lon)) return null
  return { lat: best.lat, lon: best.lon }
}

/** Horizontal access distance. Metric only. */
export function formatAccessDist(m) {
  if (!Number.isFinite(m)) return '—'
  if (m >= 1000) return `${(m / 1000).toFixed(2)} km`
  return `${Math.round(m)} m`
}

export function formatGainLoss(m) {
  if (!Number.isFinite(m) || m < 0.5) return '0 m'
  return `${Math.round(m)} m`
}

export function formatGradePct(pct) {
  if (!Number.isFinite(pct)) return '—'
  return `${pct.toFixed(1)}%`
}

export function difficultyClass(d) {
  const dMap = {
    easy: 'peak-difficulty--easy',
    medium: 'peak-difficulty--medium',
    difficult: 'peak-difficulty--difficult',
    extreme: 'peak-difficulty--extreme',
  }
  return dMap[String(d || '').toLowerCase()] || 'peak-difficulty--medium'
}

/** OSM rank used for the jeep badge. Keep in sync with `jeep_road_rank` in difficulty.rs. */
function osmRoadRank(highway, tracktype) {
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
  for (const s of kept) worst = Math.max(worst, osmRoadRank(s.highway, s.tracktype))
  const distByClass = new Map()
  for (const s of kept) {
    if (osmRoadRank(s.highway, s.tracktype) !== worst) continue
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

/** OSM tracktype / highway in plain language (wiki tracktype, jeep-operator voice). */
export function osmRoadClassHint(highway, tracktype) {
  const tt = String(tracktype || '').toLowerCase()
  if (tt === 'grade1') return 'Solid surface. Paved or heavily packed. Fine for most vehicles.'
  if (tt === 'grade2') return 'Mostly solid gravel or packed dirt. Occasional soft spots.'
  if (tt === 'grade3') return 'Mix of hard and soft. Ruts and loose rock. High clearance helps.'
  if (tt === 'grade4') return 'Mostly soft dirt or sand. Heavily rutted. Plan on 4WD.'
  if (tt === 'grade5') return 'Soft and barely a road. Deep ruts or sand. Specialized 4WD only.'
  const hw = String(highway || '').toLowerCase()
  if (['motorway', 'trunk', 'primary', 'secondary', 'tertiary'].includes(hw)) {
    return 'Paved highway.'
  }
  if (hw === 'residential') return 'Paved street.'
  if (hw === 'unclassified') return 'Minor public road, often paved.'
  if (hw === 'service') return 'Driveway or access road.'
  if (hw === 'track') return 'Unpaved two-track. Roughness not tagged.'
  return ''
}

/** @param {object|null|undefined} jeep */
export function formatOsmRoadClass(jeep) {
  const cls = jeepOsmClass(jeep)
  if (!cls?.highway) return ''
  const name = cls.tracktype ? `${cls.highway} ${cls.tracktype}` : cls.highway
  if (!Number.isFinite(cls.dist_m) || cls.dist_m <= 0) return name
  return `${name} (${formatAccessDist(cls.dist_m)})`
}

/**
 * Shared hike + jeep access stats and clickable elevation profiles.
 * Used by peak sheet and site sheet.
 */
export const AccessProfilesPanel = {
  name: 'AccessProfilesPanel',
  props: {
    hike: { type: Object, default: null },
    jeep: { type: Object, default: null },
    loading: { type: Boolean, default: false },
    error: { type: String, default: '' },
    /** Optional catalog elev for hike end snap (peaks). */
    hikeEndElevM: { type: Number, default: undefined },
    hikeStartLabel: { type: String, default: 'Road' },
    hikeEndLabel: { type: String, default: 'Summit' },
    emptyText: { type: String, default: 'No access route yet' },
  },
  emits: ['point-click'],
  setup(props, { emit }) {
    const elevationChart = computed(() =>
      buildElevationChart(prepareDisplayProfile(props.hike?.profile, props.hikeEndElevM)),
    )
    const jeepElevationChart = computed(() =>
      buildElevationChart(prepareDisplayProfile(props.jeep?.profile)),
    )

    const netDownhillNote = computed(() => {
      const h = props.hike
      if (!h || !Number.isFinite(h.loss_m) || !Number.isFinite(h.gain_m)) return ''
      if (h.loss_m <= h.gain_m + 0.3) return ''
      const netM = Math.round(h.loss_m - h.gain_m)
      if (netM < 1) return ''
      const dest = props.hikeEndLabel.toLowerCase()
      return `Road pull-off is ${netM} m higher than the ${dest} on this bump.`
    })

    /** @param {MouseEvent} event @param {object|null} chart */
    function onChartClick(event, chart) {
      const pt = profilePointAtChartClick(event, chart)
      if (!pt) return
      emit('point-click', pt)
    }

    const osmRoadClass = computed(() => formatOsmRoadClass(props.jeep))
    const osmRoadHint = computed(() => {
      const cls = jeepOsmClass(props.jeep)
      if (!cls?.highway) return ''
      return osmRoadClassHint(cls.highway, cls.tracktype)
    })
    const showHike = computed(() => hasAccessLeg(props.hike))
    const showJeep = computed(() => hasAccessLeg(props.jeep))

    return {
      elevationChart,
      jeepElevationChart,
      netDownhillNote,
      osmRoadClass,
      osmRoadHint,
      showHike,
      showJeep,
      onChartClick,
      formatAccessDist,
      formatGainLoss,
      formatGradePct,
      difficultyClass,
    }
  },
  template: `
    <div class="access-profiles">
      <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>
      <p v-else-if="loading" class="pf-muted">Loading access profile…</p>
      <template v-else-if="showHike || showJeep">
        <template v-if="showHike">
          <div class="peak-sheet__rating">
            <span class="peak-difficulty" :class="difficultyClass(hike.difficulty)">{{ hike.difficulty }}</span>
            <span class="pf-muted">hike</span>
          </div>
          <wa-callout v-if="netDownhillNote" variant="neutral" class="peak-sheet__note">{{ netDownhillNote }}</wa-callout>
          <div class="site-panel__facts">
            <div class="site-panel__fact">
              <span class="site-panel__label">Hike distance</span>
              <p class="site-panel__value">{{ formatAccessDist(hike.horiz_m) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Elevation gain</span>
              <p class="site-panel__value">{{ formatGainLoss(hike.gain_m) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Max grade</span>
              <p class="site-panel__value">{{ formatGradePct(hike.max_grade_pct) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Avg grade</span>
              <p class="site-panel__value">{{ formatGradePct(hike.avg_grade_pct) }}</p>
            </div>
          </div>
          <div v-if="elevationChart" class="site-panel__section peak-elev-map">
            <span class="site-panel__label">Elevation profile</span>
            <div class="peak-elev-map__wrap">
              <svg
                class="peak-elev-map__chart peak-elev-map__chart--interactive"
                :viewBox="'0 0 ' + elevationChart.width + ' ' + elevationChart.height"
                role="img"
                :aria-label="'Elevation profile colored by grade, ' + hikeStartLabel + ' to ' + hikeEndLabel + '. Click to pan the map to that spot.'"
                @click="onChartClick($event, elevationChart)"
              >
                <rect x="0" y="0" :width="elevationChart.width" :height="elevationChart.height" class="peak-elev-map__bg" />
                <line
                  v-for="(grid, i) in elevationChart.gridLines"
                  :key="'g' + i"
                  :x1="12"
                  :x2="elevationChart.width - 12"
                  :y1="grid.y"
                  :y2="grid.y"
                  class="peak-elev-map__grid"
                />
                <path
                  v-for="(seg, i) in elevationChart.segments"
                  :key="i"
                  :d="seg.path"
                  :fill="seg.color"
                  class="peak-elev-map__segment"
                />
                <path
                  v-if="elevationChart.outline"
                  :d="elevationChart.outline"
                  class="peak-elev-map__outline"
                />
                <circle :cx="elevationChart.start.x" :cy="elevationChart.start.y" r="3" class="peak-elev-map__dot peak-elev-map__dot--start" />
                <circle :cx="elevationChart.end.x" :cy="elevationChart.end.y" r="3" class="peak-elev-map__dot peak-elev-map__dot--end" />
                <text
                  :x="elevationChart.start.x"
                  :y="elevationChart.start.y - 7"
                  text-anchor="start"
                  class="peak-elev-map__elev-label"
                >{{ elevationChart.start.elev }}</text>
                <text
                  :x="elevationChart.end.x"
                  :y="elevationChart.end.y - 7"
                  text-anchor="end"
                  class="peak-elev-map__elev-label"
                >{{ elevationChart.end.elev }}</text>
                <text
                  :x="elevationChart.start.x"
                  :y="elevationChart.baseline + 14"
                  text-anchor="start"
                  class="peak-elev-map__axis-label"
                >{{ hikeStartLabel }}</text>
                <text
                  :x="elevationChart.end.x"
                  :y="elevationChart.baseline + 14"
                  text-anchor="end"
                  class="peak-elev-map__axis-label"
                >{{ hikeEndLabel }}</text>
              </svg>
            </div>
          </div>
        </template>
        <template v-if="showJeep">
          <div class="site-panel__section peak-sheet__jeep-header">
            <span class="site-panel__label">Jeep access</span>
            <div class="peak-sheet__rating">
              <span class="peak-difficulty" :class="difficultyClass(jeep.difficulty)">{{ jeep.difficulty }}</span>
              <span class="pf-muted">drive</span>
            </div>
          </div>
          <div class="site-panel__facts">
            <div v-if="osmRoadClass" class="site-panel__fact site-panel__fact--wide">
              <span class="site-panel__label">Road class</span>
              <p class="site-panel__value">{{ osmRoadClass }}</p>
              <p v-if="osmRoadHint" class="site-panel__hint">{{ osmRoadHint }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Jeep distance</span>
              <p class="site-panel__value">{{ formatAccessDist(jeep.horiz_m) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Elevation gain</span>
              <p class="site-panel__value">{{ formatGainLoss(jeep.gain_m) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Max slope</span>
              <p class="site-panel__value">{{ formatGradePct(jeep.max_grade_pct) }}</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Avg slope</span>
              <p class="site-panel__value">{{ formatGradePct(jeep.avg_grade_pct) }}</p>
            </div>
          </div>
          <div v-if="jeepElevationChart" class="site-panel__section peak-elev-map">
            <span class="site-panel__label">Jeep elevation profile</span>
            <div class="peak-elev-map__wrap">
              <svg
                class="peak-elev-map__chart peak-elev-map__chart--interactive"
                :viewBox="'0 0 ' + jeepElevationChart.width + ' ' + jeepElevationChart.height"
                role="img"
                aria-label="Jeep elevation profile colored by grade, paved to park. Click to pan the map to that spot."
                @click="onChartClick($event, jeepElevationChart)"
              >
                <rect x="0" y="0" :width="jeepElevationChart.width" :height="jeepElevationChart.height" class="peak-elev-map__bg" />
                <line
                  v-for="(grid, i) in jeepElevationChart.gridLines"
                  :key="'jg' + i"
                  :x1="12"
                  :x2="jeepElevationChart.width - 12"
                  :y1="grid.y"
                  :y2="grid.y"
                  class="peak-elev-map__grid"
                />
                <path
                  v-for="(seg, i) in jeepElevationChart.segments"
                  :key="'js' + i"
                  :d="seg.path"
                  :fill="seg.color"
                  class="peak-elev-map__segment"
                />
                <path
                  v-if="jeepElevationChart.outline"
                  :d="jeepElevationChart.outline"
                  class="peak-elev-map__outline"
                />
                <circle :cx="jeepElevationChart.start.x" :cy="jeepElevationChart.start.y" r="3" class="peak-elev-map__dot peak-elev-map__dot--paved" />
                <circle :cx="jeepElevationChart.end.x" :cy="jeepElevationChart.end.y" r="3" class="peak-elev-map__dot peak-elev-map__dot--park" />
                <text
                  :x="jeepElevationChart.start.x"
                  :y="jeepElevationChart.start.y - 7"
                  text-anchor="start"
                  class="peak-elev-map__elev-label"
                >{{ jeepElevationChart.start.elev }}</text>
                <text
                  :x="jeepElevationChart.end.x"
                  :y="jeepElevationChart.end.y - 7"
                  text-anchor="end"
                  class="peak-elev-map__elev-label"
                >{{ jeepElevationChart.end.elev }}</text>
                <text
                  :x="jeepElevationChart.start.x"
                  :y="jeepElevationChart.baseline + 14"
                  text-anchor="start"
                  class="peak-elev-map__axis-label"
                >Paved</text>
                <text
                  :x="jeepElevationChart.end.x"
                  :y="jeepElevationChart.baseline + 14"
                  text-anchor="end"
                  class="peak-elev-map__axis-label"
                >Park</text>
              </svg>
            </div>
          </div>
        </template>
      </template>
      <p v-else class="pf-muted">{{ emptyText }}</p>
    </div>
  `,
}
