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

/** @param {object[]} profile */
function buildElevationChart(profile) {
  if (!profile?.length) return null
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
  for (let i = 0; i < profile.length - 1; i += 1) {
    const a = profile[i]
    const b = profile[i + 1]
    const p0 = xy(a)
    const p1 = xy(b)
    segments.push({
      path: `M${p0.x.toFixed(1)},${baseline} L${p0.x.toFixed(1)},${p0.y.toFixed(1)} L${p1.x.toFixed(1)},${p1.y.toFixed(1)} L${p1.x.toFixed(1)},${baseline} Z`,
      color: gradeColor(segmentGrade(a, b)),
    })
    if (i === 0) outline.push(`M${p0.x.toFixed(1)},${p0.y.toFixed(1)}`)
    outline.push(`L${p1.x.toFixed(1)},${p1.y.toFixed(1)}`)
  }

  const start = xy(profile[0])
  const end = xy(profile[profile.length - 1])
  const gridLines = [0.25, 0.5, 0.75].map((t) => ({
    y: CHART.padTop + plotH * t,
  }))

  return {
    segments,
    outline: outline.join(' '),
    gridLines,
    start: {
      x: start.x,
      y: start.y,
      elev: formatElevLabel(profile[0].elev_m),
    },
    end: {
      x: end.x,
      y: end.y,
      elev: formatElevLabel(profile[profile.length - 1].elev_m),
    },
    baseline,
    width: CHART.w,
    height: CHART.h,
  }
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

      const hike = computed(() => hikeDetail.value?.hike || null)

      const displayProfile = computed(() =>
        prepareDisplayProfile(hike.value?.profile, selectedPeak.value?.elev_m),
      )

      const elevationChart = computed(() => buildElevationChart(displayProfile.value))

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
          hikeDetail.value = { ...peak, hike: peak.hike }
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

      function gradeBarColor(minGradePct) {
        return gradeColor(Number(minGradePct) || 0)
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
        elevationChart,
        loading,
        errorText,
        title,
        closePanel,
        formatDist,
        formatGainLoss,
        formatGradePct,
        difficultyClass,
        gradeBarColor,
        netDownhillNote,
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
                class="peak-elev-map__chart"
                :viewBox="'0 0 ' + elevationChart.width + ' ' + elevationChart.height"
                role="img"
                aria-label="Elevation profile colored by grade, road to summit"
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
          <div v-if="hike.histogram?.length" class="site-panel__section peak-grade-breakdown">
            <span class="site-panel__label">Grade breakdown</span>
            <div class="peak-grade-breakdown__list">
              <div v-for="(bucket, i) in hike.histogram" :key="i" class="peak-grade-breakdown__row">
                <span class="peak-grade-breakdown__label">{{ bucket.label }}</span>
                <div class="peak-grade-breakdown__bar-wrap">
                  <div
                    class="peak-grade-breakdown__bar"
                    :style="{ width: bucket.pct_of_route + '%', background: gradeBarColor(bucket.min_grade_pct) }"
                  ></div>
                </div>
                <span class="peak-grade-breakdown__pct">{{ Math.round(bucket.pct_of_route) }}%</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    `,
  })

  app.mount(mountPoint)
  return app
}
