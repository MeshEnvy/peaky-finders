// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import * as apiUrls from '../api/urls.js'
import { difficultyClass } from './access-profiles.js'

/** @param {string} a @param {string} b */
function canonicalLinkPair(a, b) {
  return a <= b ? { a, b } : { a: b, b: a }
}

/** @param {string|null|undefined} label */
function titleCaseDifficulty(label) {
  const s = String(label || '').trim()
  if (!s) return '—'
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountLinkSheet(store, appApi) {
  const panel = document.getElementById('link-panel')
  if (!panel) return null

  const body = panel.querySelector('.link-panel__body') || panel
  body.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'link-sheet-vue-root'
  body.appendChild(mountPoint)

  const app = createApp({
    setup() {
      const pairDetail = ref(/** @type {object|null} */ (null))
      const loading = ref(false)
      const detailError = ref('')
      const actionError = ref('')

      const selectedLink = computed(() => store.ui.selectedLink)

      const panelVisible = computed(() => !!selectedLink.value?.a && !!selectedLink.value?.b)

      const canonical = computed(() => {
        const sel = selectedLink.value
        if (!sel?.a || !sel?.b) return null
        return canonicalLinkPair(sel.a, sel.b)
      })

      const meshFeature = computed(() => {
        const canon = canonical.value
        if (!canon) return null
        return appApi.findSiteLinkFeature?.(canon.a, canon.b) || null
      })

      const endpointA = computed(() => {
        const canon = canonical.value
        if (!canon) return null
        return store.sites.list.find((s) => s.slug === canon.a) || { slug: canon.a, name: canon.a }
      })

      const endpointB = computed(() => {
        const canon = canonical.value
        if (!canon) return null
        return store.sites.list.find((s) => s.slug === canon.b) || { slug: canon.b, name: canon.b }
      })

      const distanceKm = computed(() => {
        const props = meshFeature.value?.properties || {}
        if (props.distance_km != null) return Number(props.distance_km)
        if (pairDetail.value?.distance_km != null) return Number(pairDetail.value.distance_km)
        return null
      })

      const strength = computed(() => {
        const props = meshFeature.value?.properties || {}
        if (props.strength) return String(props.strength)
        if (pairDetail.value?.strength) return String(pairDetail.value.strength)
        return 'unknown'
      })

      const marginDb = computed(() => {
        if (pairDetail.value?.margin_db != null) return Number(pairDetail.value.margin_db)
        return null
      })

      const hopKm = computed(() => {
        const props = meshFeature.value?.properties || {}
        if (props.hop_range_km != null) return Number(props.hop_range_km)
        if (pairDetail.value?.hop_range_km != null) return Number(pairDetail.value.hop_range_km)
        return store.simulation?.radiusKm ?? null
      })

      const fortifyBusy = computed(() => !!store.fortify?.scanning)
      const fortifyActive = computed(() => !!store.fortify?.active)
      const fortifyStatus = computed(() => store.fortify?.statusText || '')
      const fortifyForThisLink = computed(() => {
        const canon = canonical.value
        if (!canon || !store.fortify?.active) return false
        const fa = store.fortify.linkA
        const fb = store.fortify.linkB
        if (!fa || !fb) return false
        const other = canonicalLinkPair(fa, fb)
        return other.a === canon.a && other.b === canon.b
      })

      const fortifyCandidates = computed(() => {
        if (!fortifyForThisLink.value) return []
        const features = store.fortify?.payload?.candidates?.features
        if (!Array.isArray(features)) return []
        return features.map((feature, index) => {
          const props = feature?.properties || {}
          return {
            id: props.candidate_id || `c${index}`,
            rank: index + 1,
            name: props.name || props.peak_slug || props.slug || `Candidate ${index + 1}`,
            elevM: props.elev_m != null ? Number(props.elev_m) : null,
            marginDb: props.margin_db != null ? Number(props.margin_db) : null,
            splitFromAPct:
              props.split_from_a_pct != null ? Number(props.split_from_a_pct) : null,
            legAKm: props.leg_a_km != null ? Number(props.leg_a_km) : null,
            legBKm: props.leg_b_km != null ? Number(props.leg_b_km) : null,
            splitImbalanceKm:
              props.split_imbalance_km != null ? Number(props.split_imbalance_km) : null,
            accessDifficulty: props.access_difficulty || null,
            hikeDifficulty: props.hike_difficulty || null,
            jeepDifficulty: props.jeep_difficulty || null,
            hikeM: props.hike_m != null ? Number(props.hike_m) : null,
            jeepM: props.jeep_m != null ? Number(props.jeep_m) : null,
          }
        })
      })

      const selectedCandidateId = computed(() => store.fortify?.selectedCandidateId || null)

      async function loadPairDetail(a, b) {
        if (!a || !b) {
          pairDetail.value = null
          return
        }
        loading.value = true
        detailError.value = ''
        try {
          const resp = await fetch(apiUrls.linkPairApiUrl(store.projectSlug, a, b))
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
          pairDetail.value = await resp.json()
        } catch (err) {
          detailError.value = String(err?.message || err)
          pairDetail.value = null
        } finally {
          loading.value = false
        }
      }

      watch(
        () => [selectedLink.value?.a, selectedLink.value?.b],
        ([a, b]) => {
          actionError.value = ''
          if (a && b) {
            const canon = canonicalLinkPair(a, b)
            void loadPairDetail(canon.a, canon.b)
          } else {
            pairDetail.value = null
          }
        },
        { immediate: true },
      )

      watch(panelVisible, (visible) => {
        panel.hidden = !visible
        if (visible) appApi.syncMapViewport?.()
      })

      function closePanel() {
        appApi.deselectLink?.()
      }

      function jumpToSite(slug) {
        if (slug) appApi.selectSite?.(slug)
      }

      async function startFortify() {
        actionError.value = ''
        const canon = canonical.value
        if (!canon) return
        try {
          await appApi.fortifyLink?.(canon.a, canon.b)
        } catch (err) {
          actionError.value = String(err?.message || err)
        }
      }

      function clearFortify() {
        actionError.value = ''
        appApi.clearFortify?.()
      }

      async function addAsSite() {
        actionError.value = ''
        try {
          await appApi.addSelectedFortifyAsSite?.()
        } catch (err) {
          actionError.value = String(err?.message || err)
        }
      }

      function selectCandidate(id) {
        appApi.selectFortifyCandidateById?.(id)
      }

      function strengthLabel(val) {
        if (val === 'strong') return 'Mutual (strong)'
        if (val === 'weak') return 'One-way (weak)'
        return val
      }

      function splitQuality(pct) {
        if (pct == null || !Number.isFinite(pct)) return ''
        const dev = Math.abs(pct - 50)
        if (dev <= 5) return 'Balanced'
        if (dev <= 15) return 'Fair split'
        return 'Off-center'
      }

      function splitFromLabel(row) {
        if (row.splitFromAPct == null) return '—'
        const bPct = Math.max(0, Math.min(100, 100 - row.splitFromAPct))
        return `${row.splitFromAPct}% / ${bPct}%`
      }

      function legLines(row) {
        if (row.legAKm == null || row.legBKm == null) return []
        return [
          {
            key: 'a',
            short: 'A',
            name: endpointA.value?.name || 'A',
            km: row.legAKm,
          },
          {
            key: 'b',
            short: 'B',
            name: endpointB.value?.name || 'B',
            km: row.legBKm,
          },
        ]
      }

      function rfSummary(row) {
        const bits = []
        if (row.marginDb != null) bits.push(`${row.marginDb.toFixed(1)} dB`)
        if (row.elevM != null) bits.push(`${Math.round(row.elevM)} m`)
        return bits.length ? bits.join(' · ') : '—'
      }

      function primaryDifficulty(row) {
        return String(
          row.accessDifficulty || row.hikeDifficulty || row.jeepDifficulty || 'unknown',
        ).toLowerCase()
      }

      function accessLines(row) {
        const lines = []
        if (row.hikeM != null) lines.push(`${Math.round(row.hikeM)} m hike`)
        if (row.jeepM != null) lines.push(`${(row.jeepM / 1000).toFixed(1)} km jeep`)
        return lines
      }

      function difficultyCardClass(row) {
        const d = primaryDifficulty(row)
        if (d === 'easy' || d === 'medium' || d === 'difficult' || d === 'extreme') {
          return `fortify-candidate--diff-${d}`
        }
        return 'fortify-candidate--diff-unknown'
      }

      return {
        panelVisible,
        endpointA,
        endpointB,
        distanceKm,
        strength,
        strengthLabel,
        marginDb,
        hopKm,
        loading,
        detailError,
        actionError,
        fortifyBusy,
        fortifyActive,
        fortifyForThisLink,
        fortifyStatus,
        fortifyCandidates,
        selectedCandidateId,
        closePanel,
        jumpToSite,
        startFortify,
        clearFortify,
        addAsSite,
        selectCandidate,
        splitFromLabel,
        splitQuality,
        legLines,
        rfSummary,
        primaryDifficulty,
        accessLines,
        difficultyClass,
        difficultyCardClass,
        titleCaseDifficulty,
      }
    },
    template: `
      <div v-if="panelVisible" class="link-sheet">
        <div class="site-panel__header">
          <h2 class="site-panel__title">RF link</h2>
          <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="closePanel">×</button>
        </div>
        <div class="site-panel__section">
          <span class="site-panel__label">Endpoints</span>
          <div class="site-panel__link-list">
            <button type="button" class="site-panel__link" @click="jumpToSite(endpointA?.slug)">
              <span class="site-panel__link-name">{{ endpointA?.name }}</span>
            </button>
            <button type="button" class="site-panel__link" @click="jumpToSite(endpointB?.slug)">
              <span class="site-panel__link-name">{{ endpointB?.name }}</span>
            </button>
          </div>
        </div>
        <div class="site-panel__facts">
          <div class="site-panel__fact">
            <span class="site-panel__label">Distance</span>
            <p class="site-panel__value">{{ distanceKm != null ? distanceKm.toFixed(1) + ' km' : '—' }}</p>
          </div>
          <div class="site-panel__fact">
            <span class="site-panel__label">Strength</span>
            <p class="site-panel__value">{{ strengthLabel(strength) }}</p>
          </div>
          <div class="site-panel__fact">
            <span class="site-panel__label">Margin</span>
            <p class="site-panel__value">{{ loading ? '…' : marginDb != null ? marginDb.toFixed(1) + ' dB' : '—' }}</p>
          </div>
          <div class="site-panel__fact">
            <span class="site-panel__label">Hop radius</span>
            <p class="site-panel__value">{{ hopKm != null ? hopKm + ' km' : '—' }}</p>
          </div>
        </div>
        <p v-if="detailError" class="site-panel__value pf-muted">{{ detailError }}</p>
        <p v-if="fortifyForThisLink && fortifyStatus" class="site-panel__value pf-muted">{{ fortifyStatus }}</p>
        <div v-if="fortifyForThisLink && fortifyCandidates.length" class="site-panel__section site-panel__section--fortify">
          <span class="site-panel__label">Fortify candidates ({{ fortifyCandidates.length }})</span>
          <div class="fortify-candidate-list">
            <button
              v-for="row in fortifyCandidates"
              :key="row.id"
              type="button"
              class="fortify-candidate"
              :class="[
                difficultyCardClass(row),
                { 'fortify-candidate--selected': selectedCandidateId === row.id },
              ]"
              @click="selectCandidate(row.id)"
            >
              <div class="fortify-candidate__head">
                <span
                  class="fortify-candidate__ring"
                  :class="'fortify-candidate__ring--' + primaryDifficulty(row)"
                  aria-hidden="true"
                ></span>
                <div class="fortify-candidate__head-main">
                  <div class="fortify-candidate__title">
                    <span class="fortify-candidate__rank">{{ row.rank }}.</span>
                    <span class="fortify-candidate__name">{{ row.name }}</span>
                  </div>
                  <span
                    v-if="primaryDifficulty(row) !== 'unknown'"
                    class="peak-difficulty fortify-candidate__diff-badge"
                    :class="difficultyClass(primaryDifficulty(row))"
                  >{{ titleCaseDifficulty(primaryDifficulty(row)) }}</span>
                </div>
              </div>
              <div class="fortify-candidate__details">
                <div class="fortify-candidate__detail">
                  <span class="fortify-candidate__detail-label">RF</span>
                  <div class="fortify-candidate__detail-value">{{ rfSummary(row) }}</div>
                </div>
                <div class="fortify-candidate__detail">
                  <span class="fortify-candidate__detail-label">Split</span>
                  <div class="fortify-candidate__detail-value">
                    <div>{{ splitFromLabel(row) }}</div>
                    <span
                      v-if="row.splitFromAPct != null"
                      class="fortify-candidate__split-badge"
                    >{{ splitQuality(row.splitFromAPct) }}</span>
                  </div>
                </div>
                <div v-if="legLines(row).length" class="fortify-candidate__detail">
                  <span class="fortify-candidate__detail-label">Legs</span>
                  <div class="fortify-candidate__detail-value">
                    <div
                      v-for="leg in legLines(row)"
                      :key="leg.key"
                      class="fortify-candidate__leg"
                      :title="leg.name"
                    >
                      <span class="fortify-candidate__leg-tag">{{ leg.short }}</span>
                      <span class="fortify-candidate__leg-name">{{ leg.name }}</span>
                      <span class="fortify-candidate__leg-km">{{ leg.km.toFixed(1) }} km</span>
                    </div>
                  </div>
                </div>
                <div v-if="accessLines(row).length" class="fortify-candidate__detail">
                  <span class="fortify-candidate__detail-label">Route</span>
                  <div class="fortify-candidate__detail-value">
                    <div v-for="line in accessLines(row)" :key="line">{{ line }}</div>
                  </div>
                </div>
              </div>
            </button>
          </div>
        </div>
        <wa-callout v-if="actionError" variant="danger">{{ actionError }}</wa-callout>
        <div class="site-panel__footer">
          <div class="site-panel__footer-row">
            <button
              type="button"
              class="site-panel__footer-btn"
              :class="{ 'site-panel__footer-btn--active': fortifyForThisLink && !fortifyBusy }"
              :disabled="fortifyBusy"
              @click="fortifyForThisLink ? clearFortify() : startFortify()"
            >
              {{ fortifyForThisLink ? 'Clear Fortify' : 'Fortify' }}
            </button>
            <button
              v-if="fortifyForThisLink && selectedCandidateId"
              type="button"
              class="site-panel__footer-btn site-panel__footer-btn--brand"
              @click="addAsSite"
            >
              Add as site
            </button>
          </div>
        </div>
      </div>
    `,
  })

  app.mount(mountPoint)
  return app
}
