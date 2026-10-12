// @ts-check

import { createApp, computed, watch } from 'vue'
import {
  sidebarEmptyMessage,
  sidebarSites as sidebarSitesFromStore,
  sidebarTags as sidebarTagsFromStore,
  isSiteMapVisible,
  visibleSidebarSites as visibleSidebarSitesFromStore,
  viewportSites as viewportSitesFromStore,
} from '../stores/sites.js'
import { isViewshedVisible as isViewshedVisibleInStore } from '../stores/viewshed.js'
import { syncListMasters } from './list-masters.js'

/**
 * tagged | all on the site list header, between the count and the eye / v.
 * Moves the group when served HTML still has the old scope bar above the tags.
 * @param {HTMLElement|null} toolbar
 * @param {object} store
 * @param {() => void} [onChange]
 */
function mountListTagScope(toolbar, store, onChange) {
  if (!toolbar) return
  const pane = document.getElementById('entity-panel-sites-pane')
  let group = pane?.querySelector('.entity-panel__list-scope') || null
  if (!group) {
    group = document.createElement('div')
    group.className = 'entity-panel__list-scope'
    group.setAttribute('role', 'group')
    group.setAttribute('aria-label', 'Which sites appear in the list')
    for (const [scope, title] of [
      ['tagged', 'List sites on the map that match the selected tags'],
      ['all', 'List every site on the map'],
    ]) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'entity-panel__tag-filter'
      btn.dataset.listScope = scope
      btn.title = title
      btn.textContent = scope
      group.appendChild(btn)
    }
  }
  const masters = document.getElementById('entity-panel-sites-masters')
  const bulk = document.getElementById('entity-panel-bulk-tag')
  const before =
    masters && masters.parentElement === toolbar
      ? masters
      : bulk && bulk.parentElement === toolbar
        ? bulk
        : null
  if (before) toolbar.insertBefore(group, before)
  else if (group.parentElement !== toolbar) toolbar.appendChild(group)
  pane?.querySelector(':scope > .entity-panel__sites-scope')?.remove()

  const buttons = [...group.querySelectorAll('[data-list-scope]')]
  function sync() {
    const mode = store.ui.listTagScope === 'all' ? 'all' : 'tagged'
    for (const btn of buttons) {
      const active = btn.dataset.listScope === mode
      btn.classList.toggle('entity-panel__tag-filter--active', active)
      btn.setAttribute('aria-pressed', active ? 'true' : 'false')
    }
  }
  for (const btn of buttons) {
    btn.addEventListener('click', () => {
      const next = btn.dataset.listScope === 'all' ? 'all' : 'tagged'
      if (store.ui.listTagScope === next) return
      store.ui.listTagScope = next
      sync()
      onChange?.()
    })
  }
  sync()
}

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountSitesPanel(store, appApi) {
  const countEl = document.getElementById('entity-panel-sites-count')
  const tagFiltersEl = document.getElementById('entity-panel-tag-filters')
  const listEl = document.getElementById('entity-panel-sites-list')
  if (!tagFiltersEl || !listEl) return null

  document.getElementById('entity-panel-filter-visible-sites')?.closest('label')?.remove()

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

  const masters = ensureSiteMasterButtons()
  const toolbar = document.querySelector('#entity-panel-sites-pane .entity-panel__list-toolbar')
  mountListTagScope(toolbar, store, () => {
    appApi.onListTagScopeChange?.()
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

      const viewshedOnCount = computed(() => {
        void store.sites.revision
        return sidebarSites.value.filter((site) => isViewshedVisibleInStore(store, site.slug)).length
      })

      watch(
        [sidebarSites, visibleCount, scopedSites, viewshedOnCount],
        () => {
          const total = sidebarSites.value.length
          const shown = visibleCount.value
          if (countEl) {
            if (!total) {
              countEl.textContent = store.sites.list.length
                ? sidebarEmptyMessage(store, { scopedCount: scopedSites.value.length })
                : ''
            } else if (shown < total) {
              countEl.textContent = `${shown} of ${total} visible`
            } else {
              countEl.textContent = `${total} site${total === 1 ? '' : 's'}`
            }
          }
          syncListMasters({
            eye: document.getElementById('entity-panel-sites-toggle-visible'),
            viewshed: document.getElementById('entity-panel-sites-toggle-viewshed'),
            total,
            visible: shown,
            viewsheds: viewshedOnCount.value,
            pinNoun: 'sites',
          })
        },
        { immediate: true },
      )

      function toggleTagFilter(tag) {
        if (store.ui.tagFilters.has(tag)) store.ui.tagFilters.delete(tag)
        else store.ui.tagFilters.add(tag)
        appApi.onTagFilterChange?.()
      }

      function setTagFilterMode(mode) {
        if (store.ui.tagFilterMode === mode) return
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

      const emptyMessage = computed(() =>
        sidebarEmptyMessage(store, {
          scopedCount: viewportSitesFromStore(store, mapOpts()).length,
        }),
      )

      function siteVisible(slug) {
        void store.sites.revision
        return isSiteMapVisible(store, slug)
      }

      function toggleVisible(slug) {
        appApi.toggleSiteMapVisible?.(slug)
      }

      function viewshedVisible(slug) {
        return isViewshedVisibleInStore(store, slug)
      }

      function toggleViewshed(slug) {
        appApi.toggleViewshed?.(slug)
      }

      function showInView(site) {
        appApi.showSiteInView?.(site)
      }

      function selectSite(slug) {
        appApi.selectSite?.(slug)
      }

      return {
        store,
        sidebarSites,
        emptyMessage,
        siteVisible,
        toggleVisible,
        viewshedVisible,
        toggleViewshed,
        showInView,
        selectSite,
      }
    },
    template: `
      <div class="entity-panel__list-items">
        <div v-if="!sidebarSites.length" class="entity-panel__empty">
          {{ emptyMessage }}
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
              title="Show in view"
              aria-label="Show in view"
              @click.stop="showInView(site)"
            >
              <wa-icon name="crosshairs" label="Show in view"></wa-icon>
            </button>
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
            <button
              type="button"
              class="entity-panel__action entity-panel__action--letter"
              :class="{ 'entity-panel__action--active': viewshedVisible(site.slug) }"
              :title="viewshedVisible(site.slug) ? 'Hide viewshed' : 'Show viewshed'"
              :aria-label="viewshedVisible(site.slug) ? 'Hide viewshed' : 'Show viewshed'"
              @click.stop="toggleViewshed(site.slug)"
            >
              v
            </button>
          </div>
        </div>
      </div>
    `,
  })
  listApp.mount(listMount)

  masters?.querySelector('#entity-panel-sites-toggle-visible')?.addEventListener('click', () => {
    appApi.toggleListedSiteVisibility?.()
  })
  masters?.querySelector('#entity-panel-sites-toggle-viewshed')?.addEventListener('click', () => {
    appApi.toggleListedSiteViewsheds?.()
  })

  const bulkTagBtn = document.getElementById('entity-panel-bulk-tag')
  const addSiteBtn = document.getElementById('entity-panel-add-site')
  const exportBtn = document.getElementById('entity-panel-export-sites')
  const importBtn = document.getElementById('entity-panel-import-sites')
  watch(
    () => [
      store.ui.mapReady,
      store.sites.revision,
      store.ui.viewportEpoch,
      store.ui.tagFilters.size,
      store.ui.tagFilterMode,
      store.sites.hidden.size,
      visibleSidebarSitesFromStore(store, mapOpts()).length,
    ],
    () => {
      if (!bulkTagBtn) return
      const count = visibleSidebarSitesFromStore(store, mapOpts()).length
      bulkTagBtn.disabled = !store.ui.mapReady || count === 0
    },
    { immediate: true },
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
    void (async () => {
      try {
        await appApi.exportViewportSitesKml?.()
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Export failed'
        window.alert(message)
      }
    })()
  })
  importBtn?.addEventListener('click', () => appApi.openImportSitesModal?.())

  return { mountPoint, listMount }
}

function ensureSiteMasterButtons() {
  const toolbar = document.querySelector('#entity-panel-sites-pane .entity-panel__list-toolbar')
  if (!toolbar) return null
  let group = document.getElementById('entity-panel-sites-masters')
  if (!group) {
    group = document.createElement('div')
    group.id = 'entity-panel-sites-masters'
    group.className = 'entity-panel__controls entity-panel__controls--site'
    group.innerHTML = `
      <button type="button" id="entity-panel-sites-toggle-visible" class="entity-panel__action" title="Show sites on map" aria-label="Show sites on map">
        <wa-icon name="eye-slash" label="Show sites on map"></wa-icon>
      </button>
      <button type="button" id="entity-panel-sites-toggle-viewshed" class="entity-panel__action entity-panel__action--letter" title="Show viewsheds" aria-label="Show viewsheds">v</button>
    `
    const bulk = document.getElementById('entity-panel-bulk-tag')
    if (bulk && bulk.parentElement === toolbar) toolbar.insertBefore(group, bulk)
    else toolbar.appendChild(group)
  }
  return group
}
