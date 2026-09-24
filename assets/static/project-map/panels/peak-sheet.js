// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import { AccessProfilesPanel } from './access-profiles.js'

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
    components: { AccessProfilesPanel },
    setup() {
      const hikeDetail = ref(/** @type {object|null} */ (null))
      const loading = ref(false)
      const accessError = ref('')
      const actionError = ref('')

      const selectedPeak = computed(() => {
        const slug = store.ui.selectedPeakSlug
        if (!slug) return null
        return store.peaks.list.find((p) => p.slug === slug) || null
      })

      const panelVisible = computed(() => !!store.ui.selectedPeakSlug)

      const hike = computed(() => hikeDetail.value?.hike || selectedPeak.value?.hike || null)
      const jeep = computed(() => hikeDetail.value?.jeep || selectedPeak.value?.jeep || null)
      const trail = computed(() => hikeDetail.value?.trail || selectedPeak.value?.trail || null)
      const hikeStartLabel = computed(() => (trail.value ? 'Leave trail' : 'Park'))

      const title = computed(() => {
        const peak = selectedPeak.value
        const hikeName = hikeDetail.value?.name
        if (hikeName) return hikeName
        if (peak?.name) return peak.name
        if (Number.isFinite(peak?.elev_m)) {
          return `${Math.round(peak.elev_m).toLocaleString()} m peak`
        }
        return peak?.slug || 'Peak'
      })

      /** @param {object|null|undefined} peak @param {string} slug @param {object} row */
      function hikeDetailFromAccess(peak, slug, row) {
        return {
          ...(peak || {}),
          slug,
          name: row?.name || peak?.name,
          hike: row?.hike || null,
          jeep: row?.jeep || null,
          trail: row?.trail || null,
          road_lat: row?.road_lat,
          road_lon: row?.road_lon,
          trail_lat: row?.trail_lat,
          trail_lon: row?.trail_lon,
          paved_lat: row?.paved_lat,
          paved_lon: row?.paved_lon,
          eligible: row?.eligible,
          budget: row?.budget || null,
          budget_route: row?.budget_route || null,
        }
      }

      /** @param {object|null|undefined} row */
      function accessHasProfiles(row) {
        if (appApi.accessHasRouteProfiles?.(row)) return true
        const hikeProfile = row?.hike?.profile
        const jeepProfile = row?.jeep?.profile
        return (
          (Array.isArray(hikeProfile) && hikeProfile.length >= 2) ||
          (Array.isArray(jeepProfile) && jeepProfile.length >= 2)
        )
      }

      async function loadHike(slug) {
        if (!slug) {
          hikeDetail.value = null
          return
        }
        const peak = store.peaks.list.find((p) => p.slug === slug)
        const cached = store.access?.bySlug?.[slug]
        if (accessHasProfiles(cached)) {
          hikeDetail.value = hikeDetailFromAccess(peak, slug, cached)
          accessError.value = ''
          loading.value = false
          return
        }
        if (accessHasProfiles(peak)) {
          hikeDetail.value = hikeDetailFromAccess(peak, slug, peak)
          accessError.value = ''
          loading.value = false
          return
        }
        const lat = Number(peak?.lat)
        const lon = Number(peak?.lon)
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
          hikeDetail.value = null
          accessError.value = 'Missing peak coordinates'
          loading.value = false
          return
        }
        loading.value = true
        accessError.value = ''
        try {
          await appApi.ensurePlaceAccessProfiles?.(
            slug,
            lat,
            lon,
            { slug, name: peak?.name || slug, lat, lon },
            { isActive: () => store.ui.selectedPeakSlug === slug },
          )
          if (store.ui.selectedPeakSlug !== slug) return
          const row = store.access?.bySlug?.[slug]
          if (!accessHasProfiles(row)) throw new Error('Access profiles missing')
          hikeDetail.value = hikeDetailFromAccess(peak, slug, row)
        } catch (err) {
          hikeDetail.value = null
          accessError.value = String(err?.message || err)
        } finally {
          loading.value = false
        }
      }

      async function addPeakAsSite() {
        const peak = selectedPeak.value
        if (!peak || !appApi.sitesDomain?.createSite) return
        actionError.value = ''
        try {
          const body = {
            name: peak.name || title.value,
            lat: peak.lat,
            lon: peak.lon,
            preferred_slug: peak.slug,
          }
          const { site } = await appApi.sitesDomain.createSite(body)
          if (site?.slug) {
            appApi.applySavedSiteToMap?.(site, peak.lat, peak.lon)
            appApi.deselectPeak?.()
            appApi.selectSite?.(site.slug)
          }
        } catch (err) {
          actionError.value = String(err?.message || err)
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
      })

      function closePanel() {
        appApi.deselectPeak?.()
      }

      /** @param {{ lat: number, lon: number }} pt */
      function onAccessPointClick(pt) {
        appApi.flyToPeakProfilePoint?.(pt.lat, pt.lon)
      }

      const difficultyConfig = computed(() => store.peaks.rules?.difficulty || null)
      const budgetRoute = computed(() => hikeDetail.value?.budget_route || null)
      const budget = computed(() => hikeDetail.value?.budget || null)
      const eligible = computed(() => hikeDetail.value?.eligible ?? true)

      return {
        panelVisible,
        selectedPeak,
        hike,
        jeep,
        trail,
        hikeStartLabel,
        loading,
        accessError,
        actionError,
        title,
        difficultyConfig,
        budgetRoute,
        budget,
        eligible,
        closePanel,
        onAccessPointClick,
        addPeakAsSite,
      }
    },
    template: `
      <div v-if="panelVisible" class="peak-sheet">
        <div class="site-panel__header">
          <h2 class="site-panel__title">{{ title }}</h2>
          <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="closePanel">×</button>
        </div>
        <p v-if="selectedPeak?.slug" class="site-panel__slug">{{ selectedPeak.slug }}</p>
        <div class="site-panel__footer-row" style="margin-bottom: 0.75rem">
          <button type="button" class="site-panel__footer-btn site-panel__footer-btn--brand" @click="addPeakAsSite">
            Add as site
          </button>
        </div>
        <wa-callout v-if="actionError" variant="danger">{{ actionError }}</wa-callout>
        <AccessProfilesPanel
          :hike="hike"
          :jeep="jeep"
          :trail="trail"
          :loading="loading"
          :error="accessError"
          :hike-end-elev-m="selectedPeak?.elev_m"
          :hike-start-label="hikeStartLabel"
          :difficulty-config="difficultyConfig"
          hike-end-label="Summit"
          empty-text="No mapped OSM road"
          :budget-route="budgetRoute"
          :budget="budget"
          :eligible="eligible"
          @point-click="onAccessPointClick"
        />
      </div>
    `,
  })

  app.mount(mountPoint)
  return app
}
