// @ts-check

import { createApp, computed, watch } from 'vue'
import {
  sidebarSites as sidebarSitesFromStore,
  sidebarTags as sidebarTagsFromStore,
  isSiteMapVisible,
  viewportSites as viewportSitesFromStore,
} from '../stores/sites.js'
import { mountViewportFilterCheckbox } from './viewport-filter.js'

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountSitesPanel(store, appApi) {
  const countEl = document.getElementById('entity-panel-sites-count')
  const filterVisibleEl = document.getElementById('entity-panel-filter-visible-sites')
  const tagFiltersEl = document.getElementById('entity-panel-tag-filters')
  const listEl = document.getElementById('entity-panel-sites-list')
  if (!tagFiltersEl || !listEl) return null

  const viewportLabel = filterVisibleEl?.closest('label')
  if (
    viewportLabel &&
    tagFiltersEl.parentElement &&
    !viewportLabel.parentElement?.classList.contains('entity-panel__sites-scope')
  ) {
    const bar = document.createElement('div')
    bar.className = 'entity-panel__sites-scope'
    tagFiltersEl.before(bar)
    bar.appendChild(viewportLabel)
  }

  tagFiltersEl.innerHTML = ''
  listEl.innerHTML = ''

  const mountPoint = document.createElement('div')
  mountPoint.className = 'sites-panel-vue-root'
  tagFiltersEl.appendChild(mountPoint)

  const listMount = document.createElement('div')
  listMount.className = 'sites-panel-vue-list'
  listEl.appendChild(listMount)

  const mapOpts = () => ({
    map: appApi.getMap?.(),
    mapReady: store.ui.mapReady,
    epoch: store.ui.viewportEpoch,
  })

  mountViewportFilterCheckbox(filterVisibleEl, store, () => {
    appApi.onViewportFilterChange?.()
  })

  const app = createApp({
    setup() {
      const allTags = computed(() => sidebarTagsFromStore(store, mapOpts()))

      const scopedSites = computed(() => viewportSitesFromStore(store, mapOpts()))

      const sidebarSites = computed(() => sidebarSitesFromStore(store, mapOpts()))

      const visibleCount = computed(() => {
        void store.sites.revision
        return sidebarSites.value.filter((site) => isSiteMapVisible(store, site.slug)).length
      })

      watch(
        [sidebarSites, visibleCount, scopedSites],
        () => {
          if (!countEl) return
          const total = sidebarSites.value.length
          const shown = visibleCount.value
          if (!total) {
            countEl.textContent = store.sites.list.length ? 'No sites in view.' : ''
            return
          }
          if (shown < total) {
            countEl.textContent = `${shown} of ${total} visible`
          } else {
            countEl.textContent = `${total} site${total === 1 ? '' : 's'}`
          }
        },
        { immediate: true },
      )

      function toggleTagFilter(tag) {
        if (store.ui.tagFilters.has(tag)) store.ui.tagFilters.delete(tag)
        else store.ui.tagFilters.add(tag)
        appApi.onTagFilterChange?.()
      }

      function setTagFilterMode(mode) {
        store.ui.tagFilterMode = mode
        appApi.onTagFilterChange?.()
      }

      function selectSite(slug) {
        appApi.selectSite?.(slug)
      }

      return {
        store,
        allTags,
        sidebarSites,
        toggleTagFilter,
        setTagFilterMode,
        selectSite,
      }
    },
    template: `
      <div>
        <div v-if="allTags.length" class="entity-panel__tag-filters-inner" role="toolbar">
          <div class="entity-panel__tag-filter-mode" role="group">
            <button type="button" class="entity-panel__tag-filter"
              :class="{ 'entity-panel__tag-filter--active': store.ui.tagFilterMode === 'and' }"
              @click="setTagFilterMode('and')">intersect</button>
            <button type="button" class="entity-panel__tag-filter"
              :class="{ 'entity-panel__tag-filter--active': store.ui.tagFilterMode === 'or' }"
              @click="setTagFilterMode('or')">union</button>
          </div>
          <button v-for="tag in allTags" :key="tag" type="button"
            class="entity-panel__tag-filter"
            :class="{ 'entity-panel__tag-filter--active': store.ui.tagFilters.has(tag) }"
            @click="toggleTagFilter(tag)">{{ tag }}</button>
        </div>
      </div>
    `,
  })
  app.mount(mountPoint)

  const listApp = createApp({
    setup() {
      const sidebarSites = computed(() => sidebarSitesFromStore(store, mapOpts()))

      function siteVisible(slug) {
        void store.sites.revision
        return isSiteMapVisible(store, slug)
      }

      function toggleVisible(slug) {
        appApi.toggleSiteMapVisible?.(slug)
      }

      function selectSite(slug) {
        appApi.selectSite?.(slug)
      }

      return { store, sidebarSites, siteVisible, toggleVisible, selectSite }
    },
    template: `
      <div class="entity-panel__list-items">
        <div v-if="!sidebarSites.length" class="entity-panel__empty">
          {{ store.sites.list.length ? 'No sites in view.' : 'No sites yet.' }}
        </div>
        <div
          v-for="site in sidebarSites"
          :key="site.slug"
          class="entity-panel__row entity-panel__row--site"
          :class="{
            'entity-panel__row--hidden': !siteVisible(site.slug),
            'entity-panel__row--selected': store.ui.selectedSlug === site.slug,
          }"
        >
          <button type="button" class="entity-panel__main entity-panel__main--site" @click="selectSite(site.slug)">
            <span class="entity-panel__name">{{ site.name }}</span>
          </button>
          <div class="entity-panel__controls entity-panel__controls--site">
            <button
              type="button"
              class="entity-panel__action"
              :class="{ 'entity-panel__action--active': siteVisible(site.slug) }"
              :title="siteVisible(site.slug) ? 'Hide site on map' : 'Show site on map'"
              :aria-label="siteVisible(site.slug) ? 'Hide site on map' : 'Show site on map'"
              @click.stop="toggleVisible(site.slug)"
            >
              <wa-icon
                :name="siteVisible(site.slug) ? 'eye' : 'eye-slash'"
                :label="siteVisible(site.slug) ? 'Hide site on map' : 'Show site on map'"
              ></wa-icon>
            </button>
          </div>
        </div>
      </div>
    `,
  })
  listApp.mount(listMount)

  const bulkTagBtn = document.getElementById('entity-panel-bulk-tag')
  const addSiteBtn = document.getElementById('entity-panel-add-site')
  const exportBtn = document.getElementById('entity-panel-export-sites')
  const importBtn = document.getElementById('entity-panel-import-sites')
  watch(
    () => sidebarSitesFromStore(store, mapOpts()).length,
    (count) => {
      if (bulkTagBtn) bulkTagBtn.disabled = !store.ui.mapReady || count === 0
    },
    { immediate: true },
  )
  watch(
    () => store.ui.mapReady,
    () => {
      if (bulkTagBtn) {
        bulkTagBtn.disabled =
          !store.ui.mapReady || sidebarSitesFromStore(store, mapOpts()).length === 0
      }
    },
  )
  function syncExportButton() {
    if (!exportBtn) return
    const count = appApi.viewportExportableSites?.()?.length ?? 0
    exportBtn.disabled = !store.ui.mapReady || count === 0
  }

  watch(
    () => [
      store.ui.mapReady,
      store.ui.viewportEpoch,
      store.sites.revision,
      store.ui.tagFilters.size,
      store.ui.tagFilterMode,
      store.sites.hidden.size,
    ],
    () => syncExportButton(),
    { immediate: true },
  )

  bulkTagBtn?.addEventListener('click', () => appApi.openBulkTagModal?.())
  addSiteBtn?.addEventListener('click', () => appApi.openAddSiteModal?.())
  exportBtn?.addEventListener('click', () => {
    try {
      appApi.exportViewportSitesKml?.()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Export failed'
      window.alert(message)
    }
  })
  importBtn?.addEventListener('click', () => appApi.openImportSitesModal?.())

  return { mountPoint, listMount }
}
