// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import { DRAFT_VIEWSHED_SLUG } from '../constants.js'
import { formatCoord, formatElevLabel } from '../geo.js'
import { osmRoadsProgressLabel } from '../stores/access.js'
import { allProjectTags } from '../stores/sites.js'
import { apiFetch } from '../api/client.js'
import * as apiUrls from '../api/urls.js'
import { AccessProfilesPanel } from './access-profiles.js'
import { TagPicker } from './tag-picker.js'

/**
 * Vue site detail sheet for #site-panel (in-place selected site + create draft).
 * @param {object} store
 * @param {object} appApi
 */
export function mountSiteSheet(store, appApi) {
  const panel = document.getElementById('site-panel')
  if (!panel) return null

  const body = panel.querySelector('.site-panel__body') || panel
  body.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'site-sheet-vue-root'
  body.appendChild(mountPoint)

  const app = createApp({
    components: { AccessProfilesPanel, TagPicker },
    setup() {
      const inlineName = ref('')
      const inlineHeight = ref('')
      const createName = ref('')
      const createTags = ref([])
      const errorText = ref('')
      const access = ref(/** @type {object|null} */ (null))
      const accessLoading = ref(false)
      const accessError = ref('')
      const elevM = ref(/** @type {number|null} */ (null))
      const elevLoading = ref(false)
      let elevAbort = /** @type {AbortController|null} */ (null)
      let elevSeq = 0

      const projectTags = computed(() => allProjectTags(store))

      const selectedSite = computed(() => {
        const slug = store.ui.selectedSlug
        if (!slug) return null
        return store.sites.list.find((s) => s.slug === slug) || null
      })

      const panelVisible = computed(
        () => store.ui.createMode || !!store.ui.selectedSlug,
      )

      const mode = computed(() => (store.ui.createMode ? 'create' : 'selected'))

      const relocateActive = computed(
        () =>
          store.ui.relocateMode &&
          store.ui.relocateSlug === store.ui.selectedSlug,
      )

      const displayCoords = computed(() => {
        const site = selectedSite.value
        if (!site) return { lat: null, lon: null }
        if (relocateActive.value) {
          return {
            lat: store.ui.relocateLat ?? site.lat,
            lon: store.ui.relocateLon ?? site.lon,
          }
        }
        return { lat: site.lat, lon: site.lon }
      })

      const coordsLabel = computed(() => {
        const { lat, lon } = displayCoords.value
        if (lat == null || lon == null) return ''
        return `${formatCoord(lat)}, ${formatCoord(lon)}`
      })

      const siteTagsModel = computed({
        get: () => [...(selectedSite.value?.tags || [])],
        set: (tags) => {
          void patchTags(tags)
        },
      })

      function accessFromStore(slug) {
        const row = store.access?.bySlug?.[slug]
        if (!row) return null
        if (row.hike?.profile || row.jeep?.profile || row.hike || row.jeep) return row
        return null
      }

      async function loadSiteAccess(site) {
        if (!site?.slug) {
          access.value = null
          accessLoading.value = false
          accessError.value = ''
          return
        }
        const cached = accessFromStore(site.slug)
        if (cached) {
          access.value = cached
          accessError.value = ''
          accessLoading.value = false
          void appApi.ensurePlaceAccessProfiles?.(
            site.slug,
            Number(site.lat),
            Number(site.lon),
            site,
            { isActive: () => store.ui.selectedSlug === site.slug },
          )
          return
        }
        access.value = null
        accessLoading.value = true
        accessError.value = ''
        try {
          const lat = Number(site.lat)
          const lon = Number(site.lon)
          if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            throw new Error('Missing site coordinates')
          }
          await appApi.ensurePlaceAccessProfiles?.(
            site.slug,
            lat,
            lon,
            site,
            { isActive: () => store.ui.selectedSlug === site.slug },
          )
          if (store.ui.selectedSlug !== site.slug) return
          const row = store.access?.bySlug?.[site.slug]
          if (row && (row.hike || row.jeep || row.trail)) {
            access.value = row
          } else if (!accessFromStore(site.slug)) {
            throw new Error('Access profiles missing')
          }
        } catch (err) {
          access.value = null
          accessError.value = String(err?.message || err)
        } finally {
          accessLoading.value = false
        }
      }

      watch(
        () => [
          store.ui.selectedSlug,
          store.ui.createMode,
          selectedSite.value?.lat,
          selectedSite.value?.lon,
        ],
        () => {
          if (store.ui.createMode) return
          const slug = store.ui.selectedSlug
          if (!slug) {
            access.value = null
            return
          }
          const site = store.sites.list.find((s) => s.slug === slug)
          void loadSiteAccess(site)
        },
        { immediate: true },
      )

      watch(
        () => [
          store.ui.createMode,
          store.access?.bySlug?.[DRAFT_VIEWSHED_SLUG],
          store.access?.draftLoading,
          store.access?.osm?.status,
          store.access?.osm?.step,
        ],
        () => {
          if (!store.ui.createMode) return
          const row = store.access?.bySlug?.[DRAFT_VIEWSHED_SLUG] || null
          access.value = row
          accessError.value = ''
          const osmBusy = store.access?.osm?.status === 'parsing'
          accessLoading.value = (!!store.access?.draftLoading || osmBusy) && !row
        },
      )

      watch(
        () => {
          const slug = store.ui.selectedSlug
          return slug ? store.access?.bySlug?.[slug] : null
        },
        (row) => {
          if (store.ui.createMode) return
          if (!row) return
          if (row.hike || row.jeep || row.trail) {
            access.value = row
            accessLoading.value = false
            accessError.value = ''
          }
        },
      )

      watch(
        () => store.ui.sheetError,
        (msg) => {
          if (msg) errorText.value = msg
        },
      )

      watch(
        () => selectedSite.value?.slug,
        () => {
          const site = selectedSite.value
          inlineName.value = site?.name || ''
          inlineHeight.value =
            site?.height_m != null && Number.isFinite(Number(site.height_m))
              ? String(site.height_m)
              : ''
          errorText.value = ''
          store.ui.sheetError = ''
        },
        { immediate: true },
      )

      watch(
        () => [store.ui.relocateLat, store.ui.relocateLon, store.ui.relocateMode],
        () => {
          const { lat, lon } = displayCoords.value
          if (lat != null && lon != null) void loadElev(lat, lon)
        },
      )

      watch(
        () => store.ui.createMode,
        (active) => {
          if (!active) return
          createName.value = ''
          createTags.value = []
          errorText.value = ''
        },
      )

      watch(panelVisible, (visible) => {
        panel.hidden = !visible
      })

      const accessLoadingLabel = computed(() => {
        const osm = store.access?.osm
        const roads = osmRoadsProgressLabel(osm)
        if (roads) return `${roads}…`
        return 'Loading access profile…'
      })

      const accessHike = computed(() => access.value?.hike || null)
      const accessJeep = computed(() => access.value?.jeep || null)
      const accessTrail = computed(() => access.value?.trail || null)
      const accessOptions = computed(() => {
        const opts = access.value?.options
        return Array.isArray(opts) ? opts : []
      })
      const accessBudgetRoute = computed(() =>
        accessOptions.value.length ? null : access.value?.budget_route || null,
      )
      const accessBudget = computed(() => access.value?.budget || null)
      const accessEligible = computed(() => access.value?.eligible ?? true)
      const hikeStartLabel = computed(() => (accessTrail.value ? 'Leave trail' : 'Park'))
      const difficultyConfig = computed(() => store.peaks.rules?.difficulty || null)

      /** @param {{ lat: number, lon: number }} pt */
      function onAccessPointClick(pt) {
        appApi.flyToPeakProfilePoint?.(pt.lat, pt.lon)
      }

      function closePanel() {
        appApi.deselectSite?.()
      }

      async function deleteSelected() {
        errorText.value = ''
        try {
          await appApi.deleteSelectedSite?.()
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Delete failed.'
        }
      }

      function cancelCreate() {
        appApi.cancelCreate?.()
      }

      async function saveCreate() {
        errorText.value = ''
        try {
          await appApi.saveCreate?.({
            name: createName.value.trim(),
            tags: [...createTags.value],
          })
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Save failed.'
        }
      }

      function toggleAccess() {
        const slug = store.ui.selectedSlug
        if (!slug) return
        appApi.toggleAccessForSelected?.()
      }

      /** @param {string} optionId */
      async function selectAccessOption(optionId) {
        errorText.value = ''
        const slug = store.ui.createMode ? DRAFT_VIEWSHED_SLUG : store.ui.selectedSlug
        if (!slug) return
        try {
          await appApi.selectSiteAccessOption?.(slug, optionId)
          access.value = store.access?.bySlug?.[slug] || access.value
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Could not save access route.'
        }
      }

      /** @param {number|null|undefined} m */
      function formatAccessKm(m) {
        const n = Number(m)
        if (!Number.isFinite(n)) return '—'
        return n >= 1000 ? `${(n / 1000).toFixed(1)} km` : `${Math.round(n)} m`
      }

      function copyCoords() {
        const [lat, lon] = sheetCoords()
        if (!Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))) return
        void appApi.copyCoordPair?.(lat, lon)
      }

      function copySlug() {
        const slug = selectedSite.value?.slug
        if (!slug) return
        void navigator.clipboard?.writeText(slug)
      }

      function toggleRelocate() {
        errorText.value = ''
        store.ui.sheetError = ''
        appApi.toggleRelocate?.()
      }

      /** @param {string[]} tags */
      async function patchTags(tags) {
        const site = selectedSite.value
        if (!site) return
        const prev = [...(site.tags || [])]
        try {
          await appApi.patchSiteField?.(site.slug, { tags: [...tags] })
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Could not update tags.'
          site.tags = prev
        }
      }

      async function commitName() {
        const site = selectedSite.value
        if (!site) return
        const next = inlineName.value.trim()
        if (!next) {
          inlineName.value = site.name
          errorText.value = 'Name is required.'
          return
        }
        if (next === site.name) return
        const prev = site.name
        try {
          await appApi.patchSiteField?.(site.slug, { name: next })
        } catch (err) {
          inlineName.value = prev
          errorText.value = err instanceof Error ? err.message : 'Could not update name.'
        }
      }

      async function commitHeight() {
        const site = selectedSite.value
        if (!site) return
        const raw = inlineHeight.value.trim()
        const prev =
          site.height_m != null && Number.isFinite(Number(site.height_m))
            ? String(site.height_m)
            : ''
        let body = /** @type {{ height_m: number|null }} */ ({ height_m: null })
        if (raw) {
          const heightM = Number(raw)
          if (!Number.isFinite(heightM) || heightM < 1) {
            inlineHeight.value = prev
            errorText.value = 'Antenna height must be at least 1 m.'
            return
          }
          body = { height_m: heightM }
        }
        const prevNum = site.height_m
        if (raw && Number(raw) === Number(prevNum)) return
        if (!raw && (prevNum == null || prevNum === '')) return
        try {
          await appApi.patchSiteField?.(site.slug, body)
        } catch (err) {
          inlineHeight.value = prev
          errorText.value = err instanceof Error ? err.message : 'Could not update height.'
        }
      }

      const accessActive = computed(() =>
        store.ui.selectedSlug ? !!appApi.isAccessVisible?.(store.ui.selectedSlug) : false,
      )

      const canDelete = computed(() => store.sites.list.length > 1)

      const elevLabel = computed(() => (elevLoading.value ? '…' : formatElevLabel(elevM.value)))
      const hikeEndElevM = computed(() => (Number.isFinite(elevM.value) ? elevM.value : undefined))

      function sheetCoords() {
        if (store.ui.createMode) {
          return [store.ui.createLat, store.ui.createLon]
        }
        const { lat, lon } = displayCoords.value
        return [lat, lon]
      }

      async function loadElev(lat, lon) {
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
          elevM.value = null
          elevLoading.value = false
          return
        }
        const site = !store.ui.createMode ? selectedSite.value : null
        if (site && Number.isFinite(Number(site.elev_m)) && site.lat === lat && site.lon === lon) {
          elevM.value = Number(site.elev_m)
          elevLoading.value = false
          return
        }
        elevAbort?.abort()
        elevAbort = new AbortController()
        const seq = ++elevSeq
        elevLoading.value = true
        try {
          const row = await apiFetch(apiUrls.placeElevApiUrl(store.projectSlug, lat, lon), {
            signal: elevAbort.signal,
          })
          if (seq !== elevSeq) return
          const next = Number(row?.elev_m)
          elevM.value = Number.isFinite(next) ? next : null
          if (site && site.lat === lat && site.lon === lon) site.elev_m = elevM.value
        } catch (err) {
          if (err?.name === 'AbortError' || seq !== elevSeq) return
          elevM.value = null
        } finally {
          if (seq === elevSeq) elevLoading.value = false
        }
      }

      watch(
        () => [...sheetCoords(), store.ui.createMode, store.ui.selectedSlug],
        ([lat, lon]) => {
          void loadElev(lat, lon)
        },
        { immediate: true },
      )

      const createCoordsLabel = computed(() => {
        const lat = store.ui.createLat
        const lon = store.ui.createLon
        if (lat == null || lon == null) return ''
        return `${formatCoord(lat)}, ${formatCoord(lon)}`
      })

      const peerLinks = computed(() => appApi.getSelectedSiteLinks?.() || [])
      const canFindAlternates = computed(() => peerLinks.value.length > 0)
      const alternatesForThisSite = computed(
        () =>
          store.alternates.active &&
          store.alternates.siteSlug === store.ui.selectedSlug,
      )
      const alternatesBusy = computed(
        () => alternatesForThisSite.value && store.alternates.scanning,
      )
      const alternatesStatus = computed(() =>
        alternatesForThisSite.value ? store.alternates.statusText : '',
      )
      const canAddAlternateSite = computed(
        () =>
          alternatesForThisSite.value &&
          !!store.alternates.selectedCandidateId &&
          !(store.alternates.payload?.candidates?.features || []).find(
            (f) =>
              f?.properties?.candidate_id === store.alternates.selectedCandidateId &&
              f?.properties?.is_site,
          ),
      )

      async function toggleAlternates() {
        errorText.value = ''
        if (alternatesForThisSite.value) {
          appApi.clearAlternates?.()
          return
        }
        const slug = store.ui.selectedSlug
        if (!slug) return
        try {
          await appApi.findAlternatesForSite?.(slug)
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Alternates failed.'
        }
      }

      async function addAlternateAsSite() {
        errorText.value = ''
        try {
          await appApi.addSelectedAlternateAsSite?.()
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Add site failed.'
        }
      }

      return {
        store,
        appApi,
        mode,
        selectedSite,
        inlineName,
        inlineHeight,
        createName,
        createTags,
        projectTags,
        siteTagsModel,
        errorText,
        access,
        accessLoading,
        accessLoadingLabel,
        accessError,
        accessHike,
        accessJeep,
        accessTrail,
        accessBudgetRoute,
        accessOptions,
        accessBudget,
        accessEligible,
        selectAccessOption,
        formatAccessKm,
        hikeStartLabel,
        difficultyConfig,
        onAccessPointClick,
        accessActive,
        canDelete,
        createCoordsLabel,
        coordsLabel,
        elevLabel,
        hikeEndElevM,
        peerLinks,
        canFindAlternates,
        alternatesForThisSite,
        alternatesBusy,
        alternatesStatus,
        canAddAlternateSite,
        relocateActive,
        toggleAlternates,
        addAlternateAsSite,
        closePanel,
        deleteSelected,
        cancelCreate,
        saveCreate,
        toggleAccess,
        copyCoords,
        copySlug,
        toggleRelocate,
        commitName,
        commitHeight,
        formatCoord,
      }
    },
    template: `
      <div class="site-sheet">
        <div v-if="mode === 'selected' && selectedSite" class="site-panel__view">
          <div class="site-panel__header site-panel__header--edit">
            <input
              id="site-sheet-name"
              v-model="inlineName"
              type="text"
              class="site-panel__title-input"
              required
              aria-label="Site name"
              @blur="commitName"
              @keydown.enter.prevent="($event.target).blur()"
            >
            <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="closePanel">×</button>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Slug</span>
            <div class="site-panel__inline-row">
              <p class="site-panel__slug pf-mono">{{ selectedSite.slug }}</p>
              <button type="button" class="site-panel__action" title="Copy slug" aria-label="Copy slug" @click="copySlug">
                <wa-icon name="copy" label="Copy slug"></wa-icon>
              </button>
            </div>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Tags</span>
            <TagPicker
              v-model="siteTagsModel"
              mode="simple"
              :suggestions="projectTags"
            />
          </div>
          <div class="site-panel__facts">
            <div class="site-panel__fact site-panel__fact--wide site-panel__coords-block">
              <div class="site-panel__coords-head">
                <span class="site-panel__label">Coordinates</span>
                <div class="site-panel__actions-inline">
                  <button type="button" class="site-panel__action" title="Copy lat, lon" aria-label="Copy lat, lon" @click="copyCoords">
                    <wa-icon name="copy" label="Copy lat, lon"></wa-icon>
                  </button>
                  <button
                    type="button"
                    class="site-panel__action"
                    :class="{ 'site-panel__action--active': relocateActive }"
                    title="Click map to move pin"
                    aria-label="Relocate on map"
                    @click="toggleRelocate"
                  >
                    <wa-icon name="crosshairs" label="Relocate on map"></wa-icon>
                  </button>
                </div>
              </div>
              <p class="site-panel__value site-panel__coords-value pf-mono">{{ coordsLabel }}</p>
              <p v-if="relocateActive" class="site-panel__hint">Click the map to place the pin. Escape cancels.</p>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Elevation</span>
              <p class="site-panel__value">{{ elevLabel }}</p>
            </div>
            <div class="site-panel__fact">
              <label class="site-panel__label" for="site-sheet-height">Height (m)</label>
              <input
                id="site-sheet-height"
                v-model="inlineHeight"
                type="number"
                step="0.1"
                min="1"
                class="site-panel__value-input pf-mono"
                @blur="commitHeight"
                @keydown.enter.prevent="($event.target).blur()"
              >
            </div>
          </div>
          <div v-if="selectedSite.description" class="site-panel__section">
            <span class="site-panel__label">Description</span>
            <p class="site-panel__value">{{ selectedSite.description }}</p>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Access</span>
            <AccessProfilesPanel
              :hike="accessHike"
              :jeep="accessJeep"
              :trail="accessTrail"
              :loading="accessLoading"
              :loading-label="accessLoadingLabel"
              :error="accessError"
              :hike-end-elev-m="hikeEndElevM"
              :hike-start-label="hikeStartLabel"
              :difficulty-config="difficultyConfig"
              hike-end-label="Site"
              empty-text="No mapped OSM road"
              :budget-route="accessBudgetRoute"
              :budget="accessBudget"
              :eligible="accessEligible"
              @point-click="onAccessPointClick"
            />
            <ul v-if="accessOptions.length" class="site-access-options">
              <li v-for="opt in accessOptions" :key="opt.id">
                <button
                  type="button"
                  class="site-access-options__row"
                  :class="{ 'site-access-options__row--active': opt.selected }"
                  @click="selectAccessOption(opt.id)"
                >
                  <span class="site-access-options__metrics pf-mono">
                    Jeep {{ formatAccessKm(opt.jeep_m) }}, hike {{ formatAccessKm(opt.hike_m) }}
                  </span>
                  <span v-if="opt.recommended" class="site-access-options__tag">recommended</span>
                  <span v-if="opt.passes_budget" class="site-access-options__tag">fits budget</span>
                  <span v-if="String(opt.id || '').startsWith('draft-')" class="site-access-options__tag pf-muted">custom draft</span>
                </button>
              </li>
            </ul>
          </div>
          <div v-if="peerLinks.length" class="site-panel__section">
            <span class="site-panel__label">{{ peerLinks.length === 1 ? '1 link' : peerLinks.length + ' links' }}</span>
            <div class="site-panel__link-list">
              <button v-for="peer in peerLinks" :key="peer.slug" type="button"
                class="site-panel__link" @click="appApi.selectSite(peer.slug)">
                <span class="site-panel__link-name">{{ peer.name }}</span>
              </button>
            </div>
          </div>
          <wa-callout v-if="errorText" variant="danger">{{ errorText }}</wa-callout>
          <p v-if="alternatesStatus" class="site-panel__value pf-muted">{{ alternatesStatus }}</p>
          <div class="site-panel__footer">
            <div class="site-panel__footer-row">
              <button type="button"
                class="site-panel__footer-btn"
                :class="{ 'site-panel__footer-btn--active': accessActive }"
                @click="toggleAccess">Access</button>
              <button type="button"
                class="site-panel__footer-btn"
                :class="{ 'site-panel__footer-btn--active': alternatesForThisSite && !alternatesBusy }"
                :disabled="!canFindAlternates || alternatesBusy"
                :title="canFindAlternates ? (alternatesForThisSite ? 'Clear alternate dots' : 'Find alternate placements') : 'Needs at least one RF link to a map-visible neighbor'"
                @click="toggleAlternates">
                {{ alternatesBusy ? 'Finding…' : alternatesForThisSite ? 'Clear' : 'Alternates' }}
              </button>
              <button v-if="canAddAlternateSite" type="button"
                class="site-panel__footer-btn site-panel__footer-btn--brand"
                @click="addAlternateAsSite">Add as site</button>
            </div>
            <div class="site-panel__footer-row site-panel__footer-row--manage">
              <button type="button" class="site-panel__delete-btn"
                :disabled="!canDelete"
                :title="canDelete ? 'Delete this site' : 'Cannot delete the last site'"
                @click="deleteSelected">Delete</button>
            </div>
          </div>
        </div>

        <div v-else-if="mode === 'create'" class="site-panel__create">
          <div class="site-panel__header">
            <h2 class="site-panel__title">New site</h2>
            <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="cancelCreate">×</button>
          </div>
          <div class="site-panel__section">
            <label class="site-panel__label" for="site-sheet-create-name">Name</label>
            <input id="site-sheet-create-name" v-model="createName" type="text" required>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Tags</span>
            <TagPicker
              v-model="createTags"
              mode="simple"
              :suggestions="projectTags"
            />
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Coordinates</span>
            <div v-if="createCoordsLabel" class="site-panel__inline-row">
              <p class="site-panel__value site-panel__coords-value pf-mono">{{ createCoordsLabel }}</p>
              <button type="button" class="site-panel__action" title="Copy lat, lon" aria-label="Copy lat, lon" @click="copyCoords">
                <wa-icon name="copy" label="Copy lat, lon"></wa-icon>
              </button>
            </div>
            <p v-else class="site-panel__value">Pick a location on the map</p>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Elevation</span>
            <p class="site-panel__value">{{ createCoordsLabel ? elevLabel : '—' }}</p>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Access</span>
            <AccessProfilesPanel
              :hike="accessHike"
              :jeep="accessJeep"
              :trail="accessTrail"
              :loading="accessLoading"
              :loading-label="accessLoadingLabel"
              :error="accessError"
              :hike-end-elev-m="hikeEndElevM"
              :hike-start-label="hikeStartLabel"
              :difficulty-config="difficultyConfig"
              hike-end-label="Site"
              empty-text="No mapped OSM road"
              :budget-route="accessBudgetRoute"
              :budget="accessBudget"
              :eligible="accessEligible"
              @point-click="onAccessPointClick"
            />
            <ul v-if="accessOptions.length" class="site-access-options">
              <li v-for="opt in accessOptions" :key="opt.id">
                <button
                  type="button"
                  class="site-access-options__row"
                  :class="{ 'site-access-options__row--active': opt.selected }"
                  @click="selectAccessOption(opt.id)"
                >
                  <span class="site-access-options__metrics pf-mono">
                    Jeep {{ formatAccessKm(opt.jeep_m) }}, hike {{ formatAccessKm(opt.hike_m) }}
                  </span>
                  <span v-if="opt.recommended" class="site-access-options__tag">recommended</span>
                  <span v-if="opt.passes_budget" class="site-access-options__tag">fits budget</span>
                  <span v-if="String(opt.id || '').startsWith('draft-')" class="site-access-options__tag pf-muted">custom draft</span>
                </button>
              </li>
            </ul>
          </div>
          <wa-callout v-if="errorText" variant="danger">{{ errorText }}</wa-callout>
          <div class="site-panel__create-actions">
            <button type="button" class="site-panel__save-btn" @click="saveCreate">Save</button>
            <button type="button" class="site-panel__cancel-btn" @click="cancelCreate">Cancel</button>
          </div>
        </div>
      </div>
    `,
  })
  app.mount(mountPoint)
  return { mountPoint, app }
}
