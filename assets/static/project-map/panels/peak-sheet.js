// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import * as apiUrls from '../api/urls.js'

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

/** @param {number} gradePct */
function gradeColor(gradePct) {
  if (gradePct < 5) return GRADE_COLORS[0]
  if (gradePct < 10) return GRADE_COLORS[1]
  if (gradePct < 15) return GRADE_COLORS[2]
  if (gradePct < 20) return GRADE_COLORS[3]
  if (gradePct < 25) return GRADE_COLORS[4]
  if (gradePct < 30) return GRADE_COLORS[5]
  return GRADE_COLORS[6]
}

/**
 * Road → summit order, snap summit to catalog elevation when provided.
 * @param {object[]|undefined} profile
 * @param {number|undefined} peakElevM
 */
function prepareDisplayProfile(profile, peakElevM) {
  if (!profile?.length) return []
  let pts = profile.map((p) => ({ ...p }))
  if (pts.length >= 2 && pts[0].dist_m > pts[pts.length - 1].dist_m) {
    const maxDist = Math.max(...pts.map((p) => p.dist_m))
    pts = pts
      .slice()
      .reverse()
      .map((p) => ({ ...p, dist_m: maxDist - p.dist_m }))
  }
  if (Number.isFinite(peakElevM)) {
    const last = pts.length - 1
    pts[last] = { ...pts[last], elev_m: peakElevM }
  }
  return pts
}

/** Keep chart segments wide enough to show grade colors (long jeep routes). */
const CHART_MAX_DRAW_POINTS = 96

/**
 * @param {object[]} profile
 * @param {number} maxPoints
 */
function downsampleProfile(profile, maxPoints = CHART_MAX_DRAW_POINTS) {
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

/** @param {object[]} profile */
function buildElevationChart(profile) {
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
 * Map an SVG click to the nearest profile point (works for dense jeep charts).
 * @param {MouseEvent} event
 * @param {object|null} chart
 */
function profilePointAtChartClick(event, chart) {
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

/** @param {number} elevM */
function formatElevLabel(elevM) {
  if (!Number.isFinite(elevM)) return '—'
  return `${Math.round(elevM * 3.28084).toLocaleString()} ft`
}

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountPeakSheet(store, appApi) {
  const panel = document.getElementById('peak-panel')
  if (!panel) return null

  const body = panel.querySelector('.peak-panel__body') || panel
  body.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'peak-sheet-vue-root'
  body.appendChild(mountPoint)

  const app = createApp({
    setup() {
      const hikeDetail = ref(/** @type {object|null} */ (null))
      const loading = ref(false)
      const errorText = ref('')

      const selectedPeak = computed(() => {
        const slug = store.ui.selectedPeakSlug
        if (!slug) return null
        return store.peaks.list.find((p) => p.slug === slug) || null
      })

      const panelVisible = computed(() => !!store.ui.selectedPeakSlug)

      const hike = computed(() => hikeDetail.value?.hike || selectedPeak.value?.hike || null)
      const jeep = computed(() => hikeDetail.value?.jeep || selectedPeak.value?.jeep || null)

      const displayProfile = computed(() =>
        prepareDisplayProfile(hike.value?.profile, selectedPeak.value?.elev_m),
      )

      const jeepDisplayProfile = computed(() => prepareDisplayProfile(jeep.value?.profile))

      const elevationChart = computed(() => buildElevationChart(displayProfile.value))
      const jeepElevationChart = computed(() => buildElevationChart(jeepDisplayProfile.value))

      const title = computed(() => {
        const peak = selectedPeak.value
        const hikeName = hikeDetail.value?.name
        if (hikeName) return hikeName
        if (peak?.name) return peak.name
        if (Number.isFinite(peak?.elev_m)) {
          return `${Math.round(peak.elev_m * 3.28084).toLocaleString()} ft peak`
        }
        return peak?.slug || 'Peak'
      })

      async function loadHike(slug) {
        if (!slug) {
          hikeDetail.value = null
          return
        }
        const peak = store.peaks.list.find((p) => p.slug === slug)
        if (peak?.hike) {
          hikeDetail.value = { ...peak, hike: peak.hike, jeep: peak.jeep || null }
          errorText.value = ''
          loading.value = false
          return
        }
        loading.value = true
        errorText.value = ''
        try {
          const resp = await fetch(apiUrls.peakHikeApiUrl(store.projectSlug, slug))
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
          hikeDetail.value = await resp.json()
        } catch (err) {
          hikeDetail.value = null
          errorText.value = String(err?.message || err)
        } finally {
          loading.value = false
        }
      }

      watch(
        () => store.ui.selectedPeakSlug,
        (slug) => {
          void loadHike(slug)
        },
        { immediate: true },
      )

      watch(panelVisible, (visible) => {
        panel.hidden = !visible
        if (visible) appApi.syncMapViewport?.()
      })

      function closePanel() {
        appApi.deselectPeak?.()
      }

      /** @param {MouseEvent} event @param {object|null} chart */
      function onChartClick(event, chart) {
        const pt = profilePointAtChartClick(event, chart)
        if (!pt) return
        appApi.flyToPeakProfilePoint?.(pt.lat, pt.lon)
      }

      function formatDist(m) {
        if (!Number.isFinite(m)) return '—'
        if (m >= 1000) return `${(m / 1000).toFixed(2)} km`
        const yd = m * 1.09361
        if (yd >= 1760) return `${(yd / 1760).toFixed(2)} mi`
        return `${Math.round(yd)} yd`
      }

      function formatGainLoss(m) {
        if (!Number.isFinite(m) || m < 0.5) return '0 ft'
        return `${Math.round(m * 3.28084)} ft`
      }

      function formatGradePct(pct) {
        if (!Number.isFinite(pct)) return '—'
        return `${pct.toFixed(1)}%`
      }

      function difficultyClass(d) {
        const dMap = {
          easy: 'peak-difficulty--easy',
          medium: 'peak-difficulty--medium',
          difficult: 'peak-difficulty--difficult',
          extreme: 'peak-difficulty--extreme',
        }
        return dMap[String(d || '').toLowerCase()] || 'peak-difficulty--medium'
      }

      const netDownhillNote = computed(() => {
        const h = hike.value
        if (!h || !Number.isFinite(h.loss_m) || !Number.isFinite(h.gain_m)) return ''
        if (h.loss_m <= h.gain_m + 0.3) return ''
        const netFt = Math.round((h.loss_m - h.gain_m) * 3.28084)
        if (netFt < 1) return ''
        return `Road pull-off is ${netFt} ft higher than the summit on this bump.`
      })

      return {
        panelVisible,
        selectedPeak,
        hike,
        jeep,
        elevationChart,
        jeepElevationChart,
        loading,
        errorText,
        title,
        closePanel,
        formatDist,
        formatGainLoss,
        formatGradePct,
        difficultyClass,
        netDownhillNote,
        onChartClick,
      }
    },
    template: `
      <div v-if="panelVisible" class="peak-sheet">
        <div class="site-panel__header">
          <h2 class="site-panel__title">{{ title }}</h2>
          <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="closePanel">×</button>
        </div>
        <p v-if="selectedPeak?.slug" class="site-panel__slug">{{ selectedPeak.slug }}</p>
        <wa-callout v-if="errorText" variant="danger">{{ errorText }}</wa-callout>
        <p v-else-if="loading" class="pf-muted">Loading hike profile…</p>
        <template v-else-if="hike">
          <div class="peak-sheet__rating">
            <span class="peak-difficulty" :class="difficultyClass(hike.difficulty)">{{ hike.difficulty }}</span>
            <span class="pf-muted">overall</span>
          </div>
          <wa-callout v-if="netDownhillNote" variant="neutral" class="peak-sheet__note">{{ netDownhillNote }}</wa-callout>
          <div class="site-panel__facts">
            <div class="site-panel__fact">
              <span class="site-panel__label">Hike distance</span>
              <p class="site-panel__value">{{ formatDist(hike.horiz_m) }}</p>
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
                aria-label="Elevation profile colored by grade, road to summit. Click to pan the map to that spot."
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
                >Road</text>
                <text
                  :x="elevationChart.end.x"
                  :y="elevationChart.baseline + 14"
                  text-anchor="end"
                  class="peak-elev-map__axis-label"
                >Summit</text>
              </svg>
            </div>
          </div>
          <template v-if="jeep">
            <div class="site-panel__section peak-sheet__jeep-header">
              <span class="site-panel__label">Jeep access</span>
              <div class="peak-sheet__rating">
                <span class="peak-difficulty" :class="difficultyClass(jeep.difficulty)">{{ jeep.difficulty }}</span>
                <span class="pf-muted">drive</span>
              </div>
            </div>
            <div class="site-panel__facts">
              <div class="site-panel__fact">
                <span class="site-panel__label">Jeep distance</span>
                <p class="site-panel__value">{{ formatDist(jeep.horiz_m) }}</p>
              </div>
              <div class="site-panel__fact">
                <span class="site-panel__label">Elevation gain</span>
                <p class="site-panel__value">{{ formatGainLoss(jeep.gain_m) }}</p>
              </div>
              <div class="site-panel__fact">
                <span class="site-panel__label">Max grade</span>
                <p class="site-panel__value">{{ formatGradePct(jeep.max_grade_pct) }}</p>
              </div>
              <div class="site-panel__fact">
                <span class="site-panel__label">Avg grade</span>
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
      </div>
    `,
  })

  app.mount(mountPoint)
  return app
}
