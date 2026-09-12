// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import { formatCoord, normalizeTagInput } from '../geo.js'
import { allProjectTags, tagChipChoices, toggleTag } from '../stores/sites.js'
import * as apiUrls from '../api/urls.js'
import { AccessProfilesPanel } from './access-profiles.js'

/**
 * Vue site detail sheet for #site-panel (view / edit / create).
 * Map-side effects stay on appApi; persistence uses sitesDomain where wired.
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
    components: { AccessProfilesPanel },
    setup() {
      const editName = ref('')
      const editLat = ref('')
      const editLon = ref('')
      const editHeight = ref('')
      const editTags = ref([])
      const editTagInput = ref('')
      const createName = ref('')
      const createTags = ref([])
      const createTagInput = ref('')
      const errorText = ref('')
      const access = ref(/** @type {object|null} */ (null))
      const accessLoading = ref(false)
      const accessError = ref('')

      const projectTags = computed(() => allProjectTags(store))
      const editTagChoices = computed(() => tagChipChoices(projectTags.value, editTags.value))
      const createTagChoices = computed(() => tagChipChoices(projectTags.value, createTags.value))
      const editTagSuggestions = computed(() =>
        projectTags.value.filter((tag) => !editTags.value.includes(tag)),
      )
      const createTagSuggestions = computed(() =>
        projectTags.value.filter((tag) => !createTags.value.includes(tag)),
      )

      const selectedSite = computed(() => {
        const slug = store.ui.selectedSlug
        if (!slug) return null
        return store.sites.list.find((s) => s.slug === slug) || null
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
          accessError.value = ''
          return
        }
        const cached = accessFromStore(site.slug)
        if (cached) {
          access.value = cached
          accessError.value = ''
          accessLoading.value = false
          appApi.ensureSiteAccess?.(site)
          return
        }
        accessLoading.value = true
        accessError.value = ''
        try {
          const resp = await fetch(
            apiUrls.placeAccessApiUrl(store.projectSlug, site.slug, {
              warm: true,
              lat: site.lat,
              lon: site.lon,
            }),
          )
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
          const row = await resp.json()
          access.value = row
          appApi.ingestSiteAccess?.(site.slug, row, site)
        } catch (err) {
          access.value = null
          accessError.value = String(err?.message || err)
        } finally {
          accessLoading.value = false
        }
      }

      watch(
        () => [store.ui.selectedSlug, store.ui.createMode, store.ui.editMode],
        () => {
          const slug = store.ui.selectedSlug
          if (!slug || store.ui.createMode || store.ui.editMode) {
            access.value = null
            return
          }
          const site = store.sites.list.find((s) => s.slug === slug)
          void loadSiteAccess(site)
        },
        { immediate: true },
      )

      // Prefer live warm ingest over a prior fetch.
      watch(
        () => {
          const slug = store.ui.selectedSlug
          return slug ? store.access?.bySlug?.[slug] : null
        },
        (row) => {
          if (!row || store.ui.createMode || store.ui.editMode) return
          if (row.hike || row.jeep) {
            access.value = row
            accessLoading.value = false
            accessError.value = ''
          }
        },
      )

      const accessHike = computed(() => access.value?.hike || null)
      const accessJeep = computed(() => access.value?.jeep || null)

      /** @param {{ lat: number, lon: number }} pt */
      function onAccessPointClick(pt) {
        appApi.flyToPeakProfilePoint?.(pt.lat, pt.lon)
      }

      const panelVisible = computed(
        () => store.ui.createMode || store.ui.editMode || !!store.ui.selectedSlug
      )

      const mode = computed(() => {
        if (store.ui.createMode) return 'create'
        if (store.ui.editMode) return 'edit'
        return 'view'
      })

      watch(
        () => store.ui.editMode,
        (active) => {
          if (!active || !selectedSite.value) return
          const site = selectedSite.value
          editName.value = site.name
          const lat = store.ui.editLat ?? site.lat
          const lon = store.ui.editLon ?? site.lon
          editLat.value = formatCoord(lat)
          editLon.value = formatCoord(lon)
          editHeight.value =
            site.height_m != null && Number.isFinite(Number(site.height_m))
              ? String(site.height_m)
              : ''
          editTags.value = [...(site.tags || [])]
          editTagInput.value = ''
          errorText.value = ''
        },
        { immediate: true }
      )

      watch(
        () => [store.ui.editLat, store.ui.editLon],
        ([lat, lon]) => {
          if (lat == null || lon == null) return
          const nextLat = formatCoord(lat)
          const nextLon = formatCoord(lon)
          if (editLat.value !== nextLat) editLat.value = nextLat
          if (editLon.value !== nextLon) editLon.value = nextLon
        }
      )

      watch([editLat, editLon], () => {
        if (!store.ui.editMode) return
        const lat = Number.parseFloat(editLat.value)
        const lon = Number.parseFloat(editLon.value)
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
        if (
          store.ui.editLat != null &&
          store.ui.editLon != null &&
          formatCoord(store.ui.editLat) === editLat.value &&
          formatCoord(store.ui.editLon) === editLon.value
        ) {
          return
        }
        store.ui.editLat = lat
        store.ui.editLon = lon
        appApi.onEditCoordsChanged?.()
      })

      const createCoordsLabel = computed(() => {
        const lat = store.ui.createLat
        const lon = store.ui.createLon
        if (lat == null || lon == null) return ''
        return `${formatCoord(lat)}, ${formatCoord(lon)}`
      })

      watch(
        () => store.ui.createMode,
        (active) => {
          if (!active) return
          createName.value = ''
          createTags.value = []
          createTagInput.value = ''
          errorText.value = ''
        }
      )

      watch(panelVisible, (visible) => {
        panel.hidden = !visible
        if (visible) appApi.syncMapViewport?.()
      })

      function closePanel() {
        appApi.deselectSite?.()
      }

      function openEdit() {
        appApi.openEditPanel?.()
      }

      async function deleteSelected() {
        errorText.value = ''
        try {
          await appApi.deleteSelectedSite?.()
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Delete failed.'
        }
      }

      function cancelEdit() {
        appApi.cancelEdit?.()
      }

      function cancelCreate() {
        appApi.cancelCreate?.()
      }

      async function saveEdit() {
        errorText.value = ''
        try {
          const pending = normalizeTagInput(editTagInput.value)
          const tags = [...editTags.value]
          if (pending && !tags.includes(pending)) tags.push(pending)
          await appApi.saveEdit?.({
            name: editName.value.trim(),
            lat: editLat.value.trim(),
            lon: editLon.value.trim(),
            height_m: editHeight.value.trim(),
            tags,
          })
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Save failed.'
        }
      }

      async function saveCreate() {
        errorText.value = ''
        try {
          const pending = normalizeTagInput(createTagInput.value)
          const tags = [...createTags.value]
          if (pending && !tags.includes(pending)) tags.push(pending)
          await appApi.saveCreate?.({
            name: createName.value.trim(),
            tags,
          })
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Save failed.'
        }
      }

      function toggleViewshed() {
        const slug = store.ui.selectedSlug
        if (!slug) return
        appApi.toggleViewshedForSelected?.()
      }

      function toggleAccess() {
        const slug = store.ui.selectedSlug
        if (!slug) return
        appApi.toggleAccessForSelected?.()
      }

      function copyCoords() {
        const site = selectedSite.value
        if (!site) return
        void appApi.copyCoordPair?.(site.lat, site.lon)
      }

      function toggleEditTag(tag) {
        editTags.value = toggleTag(editTags.value, tag)
      }

      function addEditTagFromInput() {
        const tag = normalizeTagInput(editTagInput.value)
        editTagInput.value = ''
        if (!tag || editTags.value.includes(tag)) return
        editTags.value = [...editTags.value, tag]
      }

      function toggleCreateTag(tag) {
        createTags.value = toggleTag(createTags.value, tag)
      }

      function addCreateTagFromInput() {
        const tag = normalizeTagInput(createTagInput.value)
        createTagInput.value = ''
        if (!tag || createTags.value.includes(tag)) return
        createTags.value = [...createTags.value, tag]
      }

      const viewshedActive = computed(() =>
        store.ui.selectedSlug ? !!appApi.isViewshedVisible?.(store.ui.selectedSlug) : false
      )

      const accessActive = computed(() =>
        store.ui.selectedSlug ? !!appApi.isAccessVisible?.(store.ui.selectedSlug) : false
      )

      const canDelete = computed(() => store.sites.list.length > 1)

      const heightLabel = computed(() => {
        const site = selectedSite.value
        if (!site) return ''
        return appApi.formatSiteHeight?.(site) || ''
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
        editName,
        editLat,
        editLon,
        editHeight,
        editTags,
        editTagInput,
        editTagChoices,
        editTagSuggestions,
        createName,
        createTags,
        createTagInput,
        createTagChoices,
        createTagSuggestions,
        errorText,
        access,
        accessLoading,
        accessError,
        accessHike,
        accessJeep,
        onAccessPointClick,
        viewshedActive,
        accessActive,
        canDelete,
        createCoordsLabel,
        heightLabel,
        peerLinks,
        canFindAlternates,
        alternatesForThisSite,
        alternatesBusy,
        alternatesStatus,
        canAddAlternateSite,
        toggleAlternates,
        addAlternateAsSite,
        closePanel,
        openEdit,
        deleteSelected,
        cancelEdit,
        cancelCreate,
        saveEdit,
        saveCreate,
        toggleViewshed,
        toggleAccess,
        copyCoords,
        toggleEditTag,
        addEditTagFromInput,
        toggleCreateTag,
        addCreateTagFromInput,
        formatCoord,
      }
    },
    template: `
      <div class="site-sheet">
        <div v-if="mode === 'view' && selectedSite" class="site-panel__view">
          <div class="site-panel__header">
            <h2 class="site-panel__title">{{ selectedSite.name }}</h2>
            <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="closePanel">×</button>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Tags</span>
            <div class="site-tags">
              <span v-for="tag in (selectedSite.tags || [])" :key="tag" class="site-tag">{{ tag }}</span>
              <span v-if="!(selectedSite.tags || []).length" class="pf-muted">None</span>
            </div>
          </div>
          <div class="site-panel__facts">
            <div class="site-panel__fact site-panel__fact--wide">
              <span class="site-panel__label">Coordinates</span>
              <div class="site-panel__value-row">
                <p class="site-panel__value pf-mono">{{ formatCoord(selectedSite.lat) }}, {{ formatCoord(selectedSite.lon) }}</p>
                <button type="button" class="coord-action-btn" @click="copyCoords">Copy</button>
              </div>
            </div>
            <div class="site-panel__fact">
              <span class="site-panel__label">Antenna height</span>
              <p class="site-panel__value">{{ heightLabel }}</p>
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
              :loading="accessLoading"
              :error="accessError"
              hike-end-label="Site"
              empty-text="No access route yet"
              @point-click="onAccessPointClick"
            />
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
                :class="{ 'site-panel__footer-btn--active': viewshedActive }"
                @click="toggleViewshed">Viewshed</button>
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
              <button type="button" class="site-panel__footer-btn" @click="openEdit">Edit</button>
              <button type="button" class="site-panel__delete-btn"
                :disabled="!canDelete"
                :title="canDelete ? 'Delete this site' : 'Cannot delete the last site'"
                @click="deleteSelected">Delete</button>
            </div>
          </div>
        </div>

        <div v-else-if="mode === 'edit' && selectedSite" class="site-panel__edit">
          <div class="site-panel__header">
            <h2 class="site-panel__title">Edit</h2>
            <button type="button" class="site-panel__close" title="Close" aria-label="Close" @click="cancelEdit">×</button>
          </div>
          <div class="site-panel__section">
            <label class="site-panel__label" for="site-sheet-edit-name">Name</label>
            <input id="site-sheet-edit-name" v-model="editName" type="text" required>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Slug</span>
            <p class="site-panel__slug pf-mono">{{ selectedSite.slug }}</p>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Coordinates</span>
            <div class="site-panel__coords-grid">
              <label class="pf-label" for="site-sheet-edit-lat">Latitude</label>
              <input id="site-sheet-edit-lat" v-model="editLat" type="text" class="pf-mono" inputmode="decimal">
              <label class="pf-label" for="site-sheet-edit-lon">Longitude</label>
              <input id="site-sheet-edit-lon" v-model="editLon" type="text" class="pf-mono" inputmode="decimal">
            </div>
          </div>
          <div class="site-panel__section">
            <label class="site-panel__label" for="site-sheet-edit-height">Antenna height (m)</label>
            <input id="site-sheet-edit-height" v-model="editHeight" type="number" step="0.1" min="1" class="pf-mono">
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Tags</span>
            <div class="site-tags add-site-tags" role="group">
              <button v-for="tag in editTagChoices" :key="tag" type="button"
                class="site-tag site-tag--toggle"
                :class="{ 'is-selected': editTags.includes(tag) }"
                :aria-pressed="editTags.includes(tag) ? 'true' : 'false'"
                :title="editTags.includes(tag) ? 'Remove tag ' + tag : 'Add tag ' + tag"
                @click="toggleEditTag(tag)">{{ tag }}</button>
            </div>
            <form class="site-tag-add-form" @submit.prevent="addEditTagFromInput">
              <input id="site-sheet-edit-tag-input" v-model="editTagInput" type="text"
                list="site-sheet-edit-tag-suggestions" placeholder="add tag"
                autocomplete="off" maxlength="32" aria-label="Add tag">
              <datalist id="site-sheet-edit-tag-suggestions">
                <option v-for="tag in editTagSuggestions" :key="tag" :value="tag"></option>
              </datalist>
            </form>
          </div>
          <wa-callout v-if="errorText" variant="danger">{{ errorText }}</wa-callout>
          <div class="site-panel__create-actions">
            <button type="button" class="site-panel__save-btn" @click="saveEdit">Save</button>
            <button type="button" class="site-panel__cancel-btn" @click="cancelEdit">Cancel</button>
            <button type="button" class="site-panel__delete-btn"
              :disabled="!canDelete"
              :title="canDelete ? 'Delete this site' : 'Cannot delete the last site'"
              @click="deleteSelected">Delete</button>
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
            <div class="site-tags add-site-tags" role="group">
              <button v-for="tag in createTagChoices" :key="tag" type="button"
                class="site-tag site-tag--toggle"
                :class="{ 'is-selected': createTags.includes(tag) }"
                :aria-pressed="createTags.includes(tag) ? 'true' : 'false'"
                :title="createTags.includes(tag) ? 'Remove tag ' + tag : 'Add tag ' + tag"
                @click="toggleCreateTag(tag)">{{ tag }}</button>
            </div>
            <form class="site-tag-add-form" @submit.prevent="addCreateTagFromInput">
              <input id="site-sheet-create-tag-input" v-model="createTagInput" type="text"
                list="site-sheet-create-tag-suggestions" placeholder="add tag"
                autocomplete="off" maxlength="32" aria-label="Add tag">
              <datalist id="site-sheet-create-tag-suggestions">
                <option v-for="tag in createTagSuggestions" :key="tag" :value="tag"></option>
              </datalist>
            </form>
          </div>
          <div class="site-panel__section">
            <span class="site-panel__label">Coordinates</span>
            <p class="site-panel__value">{{ createCoordsLabel || 'Pick a location on the map' }}</p>
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
