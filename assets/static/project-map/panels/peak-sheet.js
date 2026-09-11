// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import * as apiUrls from '../api/urls.js'
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
        if (peak?.hike?.profile || peak?.jeep?.profile) {
          hikeDetail.value = { ...peak, hike: peak.hike, jeep: peak.jeep || null }
          accessError.value = ''
          loading.value = false
          return
        }
        loading.value = true
        accessError.value = ''
        try {
          const resp = await fetch(apiUrls.placeAccessApiUrl(store.projectSlug, slug))
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
          const access = await resp.json()
          hikeDetail.value = {
            ...(peak || {}),
            slug,
            name: peak?.name,
            hike: access.hike || null,
            jeep: access.jeep || null,
            road_lat: access.road_lat,
            road_lon: access.road_lon,
            paved_lat: access.paved_lat,
            paved_lon: access.paved_lon,
          }
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
        if (visible) appApi.syncMapViewport?.()
      })

      function closePanel() {
        appApi.deselectPeak?.()
      }

      /** @param {{ lat: number, lon: number }} pt */
      function onAccessPointClick(pt) {
        appApi.flyToPeakProfilePoint?.(pt.lat, pt.lon)
      }

      return {
        panelVisible,
        selectedPeak,
        hike,
        jeep,
        loading,
        accessError,
        actionError,
        title,
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
          :loading="loading"
          :error="accessError"
          :hike-end-elev-m="selectedPeak?.elev_m"
          hike-end-label="Summit"
          empty-text="No access route yet"
          @point-click="onAccessPointClick"
        />
      </div>
    `,
  })

  app.mount(mountPoint)
  return app
}
