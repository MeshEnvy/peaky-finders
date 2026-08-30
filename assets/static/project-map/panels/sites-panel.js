// @ts-check

import { createApp, computed, watch } from 'vue'
import {
  sidebarSites as sidebarSitesFromStore,
  sidebarTags as sidebarTagsFromStore,
  viewportSites as viewportSitesFromStore,
} from '../stores/sites.js'

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountSitesPanel(store, appApi) {
  const countEl = document.getElementById('entity-panel-sites-count')
  const filterVisibleEl = document.getElementById('entity-panel-filter-visible')
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

  const app = createApp({
    setup() {
      const allTags = computed(() => sidebarTagsFromStore(store, mapOpts()))

      const scopedSites = computed(() => viewportSitesFromStore(store, mapOpts()))

      const sidebarSites = computed(() => sidebarSitesFromStore(store, mapOpts()))

      watch(
        [sidebarSites, scopedSites, () => store.ui.tagFilters.size],
        () => {
          if (!countEl) return
          const universe = scopedSites.value.length
          const shown = sidebarSites.value.length
          if (!universe && !shown) {
            countEl.textContent = store.sites.list.length ? 'No matching sites.' : ''
            return
          }
          if (shown < universe) {
            countEl.textContent = `${shown} of ${universe}`
          } else {
            countEl.textContent = `${shown} site${shown === 1 ? '' : 's'}`
          }
        },
        { immediate: true }
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

      return { store, sidebarSites, selectSite: (slug) => appApi.selectSite?.(slug) }
    },
    template: `
      <div class="entity-panel__list-items">
        <div v-if="!sidebarSites.length" class="entity-panel__empty">
          {{ store.sites.list.length ? 'No matching sites.' : 'No sites yet.' }}
        </div>
        <button v-for="site in sidebarSites" :key="site.slug" type="button"
          class="entity-panel__row"
          :class="{ 'entity-panel__row--selected': store.ui.selectedSlug === site.slug }"
          @click="selectSite(site.slug)">
          <span class="entity-panel__name">{{ site.name }}</span>
        </button>
      </div>
    `,
  })
  listApp.mount(listMount)

  if (filterVisibleEl) {
    filterVisibleEl.checked = store.ui.filterByViewport
    filterVisibleEl.addEventListener('change', () => {
      store.ui.filterByViewport = filterVisibleEl.checked
      appApi.onViewportFilterChange?.()
    })
  }

  const bulkTagBtn = document.getElementById('entity-panel-bulk-tag')
  const addSiteBtn = document.getElementById('entity-panel-add-site')
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
  bulkTagBtn?.addEventListener('click', () => appApi.openBulkTagModal?.())
  addSiteBtn?.addEventListener('click', () => appApi.openAddSiteModal?.())
  importBtn?.addEventListener('click', () => appApi.openImportSitesModal?.())

  return { mountPoint, listMount }
}
