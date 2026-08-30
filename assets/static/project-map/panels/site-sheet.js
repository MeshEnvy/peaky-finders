// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import { formatCoord } from '../geo.js'

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
    setup() {
      const editName = ref('')
      const editLat = ref('')
      const editLon = ref('')
      const editHeight = ref('')
      const createName = ref('')
      const createTags = ref('')
      const errorText = ref('')

      const selectedSite = computed(() => {
        const slug = store.ui.selectedSlug
        if (!slug) return null
        return store.sites.list.find((s) => s.slug === slug) || null
      })

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
          createTags.value = ''
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

      function cancelEdit() {
        appApi.cancelEdit?.()
      }

      function cancelCreate() {
        appApi.cancelCreate?.()
      }

      async function saveEdit() {
        errorText.value = ''
        try {
          await appApi.saveEdit?.({
            name: editName.value.trim(),
            lat: editLat.value.trim(),
            lon: editLon.value.trim(),
            height_m: editHeight.value.trim(),
          })
        } catch (err) {
          errorText.value = err instanceof Error ? err.message : 'Save failed.'
        }
      }

      async function saveCreate() {
        errorText.value = ''
        try {
          await appApi.saveCreate?.({
            name: createName.value.trim(),
            tags: createTags.value
              .split(',')
              .map((t) => t.trim())
              .filter(Boolean),
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

      function copyCoords() {
        const site = selectedSite.value
        if (!site) return
        void appApi.copyCoordPair?.(site.lat, site.lon)
      }

      const viewshedActive = computed(() =>
        store.ui.selectedSlug ? !!appApi.isViewshedVisible?.(store.ui.selectedSlug) : false
      )

      const heightLabel = computed(() => {
        const site = selectedSite.value
        if (!site) return ''
        return appApi.formatSiteHeight?.(site) || ''
      })

      const peerLinks = computed(() => appApi.getSelectedSiteLinks?.() || [])

      return {
        store,
        appApi,
        mode,
        selectedSite,
        editName,
        editLat,
        editLon,
        editHeight,
        createName,
        createTags,
        errorText,
        viewshedActive,
        createCoordsLabel,
        heightLabel,
        peerLinks,
        closePanel,
        openEdit,
        cancelEdit,
        cancelCreate,
        saveEdit,
        saveCreate,
        toggleViewshed,
        copyCoords,
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
          <div v-if="peerLinks.length" class="site-panel__section">
            <span class="site-panel__label">{{ peerLinks.length === 1 ? '1 link' : peerLinks.length + ' links' }}</span>
            <div class="site-panel__link-list">
              <button v-for="peer in peerLinks" :key="peer.slug" type="button"
                class="site-panel__link" @click="appApi.selectSite(peer.slug)">
                <span class="site-panel__link-name">{{ peer.name }}</span>
              </button>
            </div>
          </div>
          <div class="site-panel__footer">
            <button type="button"
              class="site-panel__action site-panel__viewshed-toggle"
              :class="{ 'site-panel__action--active': viewshedActive }"
              @click="toggleViewshed">Viewshed</button>
            <button type="button" class="site-panel__edit-btn" @click="openEdit">Edit</button>
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
          <wa-callout v-if="errorText" variant="danger">{{ errorText }}</wa-callout>
          <div class="site-panel__create-actions">
            <button type="button" class="site-panel__save-btn" @click="saveEdit">Save</button>
            <button type="button" class="site-panel__cancel-btn" @click="cancelEdit">Cancel</button>
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
            <label class="site-panel__label" for="site-sheet-create-tags">Tags (comma-separated)</label>
            <input id="site-sheet-create-tags" v-model="createTags" type="text" placeholder="tag1, tag2">
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
