// @ts-check

import { createApp, computed, watch } from 'vue'
import { LAND_OVERLAYS, landOverlayLayerKey } from '../map/land-overlays.js'
import {
  landSourceDisplayTitles,
  landLayerShortLabel,
  landLayerDisplayName,
  landRuleSidebarText,
  landLayerRoleBadgeSpec,
  landLayerHasLabelField,
} from '../stores/land-sidebar-view.js'
import {
  landLayerKey,
  isLandLayerEffectivelyVisible,
  isLandOverlayVisible,
  isLandSourceEffectivelyVisible,
} from '../stores/land.js'

/**
 * Vue land sidebar for #entity-panel-land-list.
 * Map sync goes through appApi (imperative MapLibre in domains/land.js).
 * @param {object} store
 * @param {object} appApi
 */
export function mountLandPanel(store, appApi) {
  const listEl = document.getElementById('entity-panel-land-list')
  const countEl = document.getElementById('entity-panel-land-count')
  if (!listEl) return null

  listEl.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'land-panel-vue-root'
  listEl.appendChild(mountPoint)

  const app = createApp({
    setup() {
      const revision = computed(() => store.land.panelRevision)

      const sourceCount = computed(() => {
        revision.value
        return store.land.sources?.length || 0
      })

      const layerCount = computed(() => {
        revision.value
        return store.land.layerRows?.length || 0
      })

      const folders = computed(() => {
        revision.value
        return store.land.sidebar?.folders || []
      })

      const unfiledSources = computed(() => {
        revision.value
        return store.land.sidebar?.unfiledSources || []
      })

      const displayTitles = computed(() => {
        revision.value
        return landSourceDisplayTitles(store.land.sources || [])
      })

      const layersBySource = computed(() => {
        revision.value
        /** @type {Map<string, object[]>} */
        const map = new Map()
        for (const row of store.land.layerRows || []) {
          const list = map.get(row.sourceId) || []
          const spec = row.spec || row.layer || {}
          list.push({
            ...row,
            layerKey: row.layerKey || row.key,
            spec,
          })
          map.set(row.sourceId, list)
        }
        return map
      })

      function layersForSource(sourceId) {
        return layersBySource.value.get(sourceId) || []
      }

      function layerKeysForSource(sourceId) {
        return layersForSource(sourceId).map((row) => row.layerKey)
      }

      function sourceTitle(sourceId) {
        return displayTitles.value.get(sourceId) || sourceId
      }

      function layerVisible(sourceId, layerKey) {
        revision.value
        return isLandLayerEffectivelyVisible(store, sourceId, layerKey)
      }

      function sourceVisible(sourceId) {
        revision.value
        return isLandSourceEffectivelyVisible(store, sourceId, layerKeysForSource(sourceId))
      }

      function overlayVisible(overlayId) {
        revision.value
        return isLandOverlayVisible(store, overlayId)
      }

      function folderCollapsed(folderId) {
        revision.value
        return store.land.foldersCollapsed.get(folderId) === true
      }

      function folderVisible(folderId) {
        revision.value
        const folder = (store.land.sidebar?.folders || []).find((f) => f.id === folderId)
        if (!folder?.sources?.length) return false
        return folder.sources.every((sid) => sourceVisible(sid))
      }

      function rowLoading(sourceId, layerKey) {
        revision.value
        return store.land.loadingKeys?.has(landLayerKey(sourceId, layerKey)) === true
      }

      function overlayLoading(overlayId) {
        revision.value
        return store.land.loadingKeys?.has(landOverlayLayerKey(overlayId)) === true
      }

      function hasLabels(spec) {
        return landLayerHasLabelField(spec)
      }

      function labelsVisible(sourceId, layerKey) {
        revision.value
        const key = landLayerKey(sourceId, layerKey)
        if (!store.land.labelsVisible.has(key)) return true
        return store.land.labelsVisible.get(key) === true
      }

      function sourceHasAnyLabels(sourceId) {
        return layersForSource(sourceId).some((row) => hasLabels(row.spec))
      }

      function sourceLabelsVisible(sourceId) {
        const labeled = layersForSource(sourceId).filter((row) => hasLabels(row.spec))
        if (!labeled.length) return false
        return labeled.every((row) => labelsVisible(sourceId, row.layerKey))
      }

      function folderHasAnyLabels(folderId) {
        const folder = (store.land.sidebar?.folders || []).find((f) => f.id === folderId)
        if (!folder) return false
        return folder.sources.some((sid) => sourceHasAnyLabels(sid))
      }

      function folderLabelsVisible(folderId) {
        const folder = (store.land.sidebar?.folders || []).find((f) => f.id === folderId)
        if (!folder) return false
        const labeled = []
        for (const sid of folder.sources) {
          for (const row of layersForSource(sid)) {
            if (hasLabels(row.spec)) labeled.push({ sid, key: row.layerKey })
          }
        }
        if (!labeled.length) return false
        return labeled.every(({ sid, key }) => labelsVisible(sid, key))
      }

      function roleBadge(role) {
        return landLayerRoleBadgeSpec(role)
      }

      function swatchStyle(spec) {
        const style = spec?.style
        let color = '#64748b'
        let opacity = 0.55
        if (style && typeof style === 'object' && 'color' in style) {
          color = String(style.color || color)
          if (typeof style.opacity === 'number') opacity = style.opacity
        } else if (style && typeof style === 'object') {
          const first = Object.values(style)[0]
          if (first && typeof first === 'object' && first.color) {
            color = String(first.color)
            if (typeof first.opacity === 'number') opacity = first.opacity
          }
        }
        return {
          backgroundColor: color,
          opacity: String(Math.max(0.45, opacity)),
        }
      }

      function overlaySwatch(spec) {
        return {
          backgroundColor: spec.color,
          opacity: String(Math.max(0.45, spec.opacity)),
        }
      }

      function toggleFolderCollapse(folderId) {
        appApi.setLandFolderCollapsed?.(folderId, !folderCollapsed(folderId))
      }

      function toggleFolderEye(folderId) {
        appApi.setLandFolderVisible?.(folderId, !folderVisible(folderId))
      }

      function toggleFolderLabels(folderId) {
        if (!folderHasAnyLabels(folderId)) return
        appApi.setLandFolderLabelsVisible?.(folderId, !folderLabelsVisible(folderId))
      }

      function toggleSource(sourceId) {
        appApi.toggleLandSourceLayers?.(sourceId)
      }

      function toggleSourceLabels(sourceId) {
        if (!sourceHasAnyLabels(sourceId)) return
        const next = !sourceLabelsVisible(sourceId)
        for (const row of layersForSource(sourceId)) {
          if (!hasLabels(row.spec)) continue
          appApi.setLandLayerLabelsVisible?.(sourceId, row.layerKey, next)
        }
      }

      function toggleLayer(sourceId, layerKey) {
        appApi.toggleLandLayerVisible?.(sourceId, layerKey)
      }

      function toggleLayerLabels(sourceId, layerKey) {
        const next = !labelsVisible(sourceId, layerKey)
        appApi.setLandLayerLabelsVisible?.(sourceId, layerKey, next)
      }

      function toggleOverlay(overlayId) {
        appApi.toggleLandOverlayVisible?.(overlayId)
      }

      function editSource(sourceId) {
        appApi.openLandSourceEditor?.(sourceId)
      }

      function removeSource(sourceId) {
        appApi.deleteLandSource?.(sourceId)
      }

      function renameFolder(folderId) {
        appApi.renameLandFolder?.(folderId)
      }

      function removeFolder(folderId) {
        appApi.deleteLandFolder?.(folderId)
      }

      return {
        LAND_OVERLAYS,
        sourceCount,
        layerCount,
        folders,
        unfiledSources,
        sourceTitle,
        layersForSource,
        layerVisible,
        sourceVisible,
        overlayVisible,
        folderCollapsed,
        folderVisible,
        rowLoading,
        overlayLoading,
        hasLabels,
        labelsVisible,
        sourceHasAnyLabels,
        sourceLabelsVisible,
        folderHasAnyLabels,
        folderLabelsVisible,
        roleBadge,
        swatchStyle,
        overlaySwatch,
        toggleFolderCollapse,
        toggleFolderEye,
        toggleFolderLabels,
        toggleSource,
        toggleSourceLabels,
        toggleLayer,
        toggleLayerLabels,
        toggleOverlay,
        editSource,
        removeSource,
        renameFolder,
        removeFolder,
        landLayerShortLabel,
        landLayerDisplayName,
        landRuleSidebarText,
      }
    },
    template: `
      <div class="land-panel-vue">
        <div class="entity-panel__land-overlays">
          <div class="entity-panel__land-overlays-header">Overlays</div>
          <div
            v-for="spec in LAND_OVERLAYS"
            :key="spec.id"
            class="entity-panel__row entity-panel__row--land entity-panel__land-layer entity-panel__land-overlay-row"
            :class="{ 'entity-panel__row--hidden': !overlayVisible(spec.id), 'entity-panel__row--land-loading': overlayLoading(spec.id) }"
            :data-land-key="'_overlays/' + spec.id"
            @click="toggleOverlay(spec.id)"
          >
            <span class="entity-panel__land-layer-swatch" :style="overlaySwatch(spec)"></span>
            <div class="entity-panel__main entity-panel__main--land-compact">
              <div class="entity-panel__land-source-title-row">
                <span
                  v-if="roleBadge(spec.role)"
                  :class="roleBadge(spec.role).className"
                  :title="roleBadge(spec.role).title"
                >{{ roleBadge(spec.role).label }}</span>
                <div class="entity-panel__name entity-panel__name--land-compact" :title="spec.title">{{ spec.label }}</div>
              </div>
              <div class="entity-panel__land-layer-summary" :title="spec.title">{{ spec.summary }}</div>
            </div>
            <div class="entity-panel__controls entity-panel__controls--land-compact">
              <span class="entity-panel__land-spinner-slot" aria-hidden="true">
                <span class="entity-panel__land-row-spinner pin-load-spinner" :hidden="!overlayLoading(spec.id)"></span>
              </span>
              <button
                type="button"
                class="entity-panel__action"
                :class="{ 'entity-panel__action--active': overlayVisible(spec.id) }"
                :title="overlayVisible(spec.id) ? 'Hide overlay on map' : 'Show overlay on map'"
                :aria-label="overlayVisible(spec.id) ? 'Hide overlay on map' : 'Show overlay on map'"
                :aria-pressed="overlayVisible(spec.id) ? 'true' : 'false'"
                @click.stop="toggleOverlay(spec.id)"
              >
                <wa-icon :name="overlayVisible(spec.id) ? 'eye' : 'eye-slash'" :label="overlayVisible(spec.id) ? 'Hide overlay on map' : 'Show overlay on map'"></wa-icon>
              </button>
            </div>
          </div>
        </div>

        <div v-if="!sourceCount" class="entity-panel__empty">Import land sources from data/.</div>

        <div v-for="folder in folders" :key="folder.id" class="entity-panel__land-folder">
          <div
            class="entity-panel__land-folder-header entity-panel__land-drop-row"
            data-land-drag-kind="folder"
            :data-land-drag-id="folder.id"
          >
            <button
              type="button"
              class="entity-panel__land-folder-collapse"
              :title="folderCollapsed(folder.id) ? 'Expand folder' : 'Collapse folder'"
              :aria-label="folderCollapsed(folder.id) ? 'Expand folder' : 'Collapse folder'"
              @click="toggleFolderCollapse(folder.id)"
            >
              <wa-icon :name="folderCollapsed(folder.id) ? 'chevron-right' : 'chevron-down'" label="Toggle folder"></wa-icon>
            </button>
            <div class="entity-panel__land-folder-title" :title="folder.label">{{ folder.label }}</div>
            <div class="entity-panel__land-source-actions">
              <button
                type="button"
                class="entity-panel__action"
                :class="{ 'entity-panel__action--active': folderVisible(folder.id) }"
                :title="folderVisible(folder.id) ? 'Hide all layers in folder' : 'Show all layers in folder'"
                :aria-pressed="folderVisible(folder.id) ? 'true' : 'false'"
                @click="toggleFolderEye(folder.id)"
              >
                <wa-icon :name="folderVisible(folder.id) ? 'eye' : 'eye-slash'" label="Folder visibility"></wa-icon>
              </button>
              <button
                type="button"
                class="entity-panel__action entity-panel__land-label-toggle"
                :class="{ 'entity-panel__action--active': folderLabelsVisible(folder.id) }"
                :disabled="!folderHasAnyLabels(folder.id)"
                :title="folderHasAnyLabels(folder.id) ? (folderLabelsVisible(folder.id) ? 'Hide all labels in folder' : 'Show all labels in folder') : 'No label field'"
                @click="toggleFolderLabels(folder.id)"
              >
                <wa-icon name="font" label="Folder labels"></wa-icon>
              </button>
              <button type="button" class="entity-panel__action entity-panel__action--manage" title="Rename folder" @click="renameFolder(folder.id)">
                <wa-icon name="pen" label="Rename folder"></wa-icon>
              </button>
              <button type="button" class="entity-panel__action entity-panel__action--danger entity-panel__action--manage" title="Delete folder" @click="removeFolder(folder.id)">
                <wa-icon name="trash" label="Delete folder"></wa-icon>
              </button>
            </div>
          </div>
          <div class="entity-panel__land-folder-body" :hidden="folderCollapsed(folder.id)">
            <div
              v-for="sourceId in folder.sources"
              :key="sourceId"
              class="entity-panel__land-source-group"
            >
              <div
                class="entity-panel__land-source entity-panel__land-source-draggable entity-panel__land-drop-row"
                :class="{ 'entity-panel__row--hidden': !sourceVisible(sourceId) }"
                data-land-drag-kind="source"
                :data-land-drag-id="sourceId"
                :data-land-folder-id="folder.id"
              >
                <div class="entity-panel__land-source-title-col">
                  <div class="entity-panel__land-source-title-row">
                    <div class="entity-panel__land-source-title" :title="sourceTitle(sourceId)">{{ sourceTitle(sourceId) }}</div>
                  </div>
                </div>
                <div class="entity-panel__land-source-actions">
                  <button
                    type="button"
                    class="entity-panel__action"
                    :class="{ 'entity-panel__action--active': sourceVisible(sourceId) }"
                    :title="sourceVisible(sourceId) ? 'Hide all layers on map' : 'Show all layers on map'"
                    :aria-pressed="sourceVisible(sourceId) ? 'true' : 'false'"
                    @click="toggleSource(sourceId)"
                  >
                    <wa-icon :name="sourceVisible(sourceId) ? 'eye' : 'eye-slash'" label="Source visibility"></wa-icon>
                  </button>
                  <button
                    type="button"
                    class="entity-panel__action entity-panel__land-label-toggle"
                    :class="{ 'entity-panel__action--active': sourceLabelsVisible(sourceId) }"
                    :disabled="!sourceHasAnyLabels(sourceId)"
                    :title="sourceHasAnyLabels(sourceId) ? (sourceLabelsVisible(sourceId) ? 'Hide all labels' : 'Show all labels') : 'No label field'"
                    @click="toggleSourceLabels(sourceId)"
                  >
                    <wa-icon name="font" label="Source labels"></wa-icon>
                  </button>
                  <button type="button" class="entity-panel__action entity-panel__action--manage" title="Edit source" @click="editSource(sourceId)">
                    <wa-icon name="pen" label="Edit source"></wa-icon>
                  </button>
                  <button type="button" class="entity-panel__action entity-panel__action--danger entity-panel__action--manage" title="Remove source" @click="removeSource(sourceId)">
                    <wa-icon name="trash" label="Remove source"></wa-icon>
                  </button>
                </div>
              </div>
              <div v-if="layersForSource(sourceId).length" class="entity-panel__land-layers">
                <div
                  v-for="row in layersForSource(sourceId)"
                  :key="row.layerKey"
                  class="entity-panel__row entity-panel__row--land entity-panel__land-layer"
                  :class="{ 'entity-panel__row--hidden': !layerVisible(sourceId, row.layerKey), 'entity-panel__row--land-loading': rowLoading(sourceId, row.layerKey) }"
                  :data-land-key="sourceId + '/' + row.layerKey"
                  @click="toggleLayer(sourceId, row.layerKey)"
                >
                  <span class="entity-panel__land-layer-swatch" :style="swatchStyle(row.spec)"></span>
                  <div class="entity-panel__main entity-panel__main--land-compact">
                    <div class="entity-panel__land-source-title-row">
                      <span
                        v-if="roleBadge(row.spec?.role)"
                        :class="roleBadge(row.spec?.role).className"
                        :title="roleBadge(row.spec?.role).title"
                      >{{ roleBadge(row.spec?.role).label }}</span>
                      <div
                        class="entity-panel__name entity-panel__name--land-compact"
                        :title="landLayerDisplayName(row.spec)"
                      >{{ landLayerShortLabel(row.spec) }}</div>
                    </div>
                    <div class="entity-panel__land-layer-summary" :title="landRuleSidebarText(row.spec)">{{ landRuleSidebarText(row.spec) }}</div>
                  </div>
                  <div class="entity-panel__controls entity-panel__controls--land-compact">
                    <span class="entity-panel__land-spinner-slot" aria-hidden="true">
                      <span class="entity-panel__land-row-spinner pin-load-spinner" :hidden="!rowLoading(sourceId, row.layerKey)"></span>
                    </span>
                    <button
                      type="button"
                      class="entity-panel__action"
                      :class="{ 'entity-panel__action--active': layerVisible(sourceId, row.layerKey) }"
                      :title="layerVisible(sourceId, row.layerKey) ? 'Hide layer on map' : 'Show layer on map'"
                      :aria-pressed="layerVisible(sourceId, row.layerKey) ? 'true' : 'false'"
                      @click.stop="toggleLayer(sourceId, row.layerKey)"
                    >
                      <wa-icon :name="layerVisible(sourceId, row.layerKey) ? 'eye' : 'eye-slash'" label="Layer visibility"></wa-icon>
                    </button>
                    <button
                      type="button"
                      class="entity-panel__action entity-panel__land-label-toggle"
                      :class="{ 'entity-panel__action--active': hasLabels(row.spec) && labelsVisible(sourceId, row.layerKey) }"
                      :disabled="!hasLabels(row.spec) || !layerVisible(sourceId, row.layerKey)"
                      :title="!hasLabels(row.spec) ? 'No label field' : (labelsVisible(sourceId, row.layerKey) ? 'Hide labels' : 'Show labels')"
                      @click.stop="toggleLayerLabels(sourceId, row.layerKey)"
                    >
                      <wa-icon name="font" label="Layer labels"></wa-icon>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="unfiledSources.length || folders.length"
          class="entity-panel__land-unfiled"
        >
          <div
            v-if="folders.length"
            class="entity-panel__land-unfiled-header entity-panel__land-drop-row"
            data-land-drag-kind="unfiled"
            data-land-drag-id="unfiled"
          >Unfiled</div>
          <div
            v-for="sourceId in unfiledSources"
            :key="'u-' + sourceId"
            class="entity-panel__land-source-group"
          >
            <div
              class="entity-panel__land-source entity-panel__land-source-draggable entity-panel__land-drop-row"
              :class="{ 'entity-panel__row--hidden': !sourceVisible(sourceId) }"
              data-land-drag-kind="source"
              :data-land-drag-id="sourceId"
            >
              <div class="entity-panel__land-source-title-col">
                <div class="entity-panel__land-source-title-row">
                  <div class="entity-panel__land-source-title" :title="sourceTitle(sourceId)">{{ sourceTitle(sourceId) }}</div>
                </div>
              </div>
              <div class="entity-panel__land-source-actions">
                <button
                  type="button"
                  class="entity-panel__action"
                  :class="{ 'entity-panel__action--active': sourceVisible(sourceId) }"
                  :title="sourceVisible(sourceId) ? 'Hide all layers on map' : 'Show all layers on map'"
                  :aria-pressed="sourceVisible(sourceId) ? 'true' : 'false'"
                  @click="toggleSource(sourceId)"
                >
                  <wa-icon :name="sourceVisible(sourceId) ? 'eye' : 'eye-slash'" label="Source visibility"></wa-icon>
                </button>
                <button
                  type="button"
                  class="entity-panel__action entity-panel__land-label-toggle"
                  :class="{ 'entity-panel__action--active': sourceLabelsVisible(sourceId) }"
                  :disabled="!sourceHasAnyLabels(sourceId)"
                  :title="sourceHasAnyLabels(sourceId) ? (sourceLabelsVisible(sourceId) ? 'Hide all labels' : 'Show all labels') : 'No label field'"
                  @click="toggleSourceLabels(sourceId)"
                >
                  <wa-icon name="font" label="Source labels"></wa-icon>
                </button>
                <button type="button" class="entity-panel__action entity-panel__action--manage" title="Edit source" @click="editSource(sourceId)">
                  <wa-icon name="pen" label="Edit source"></wa-icon>
                </button>
                <button type="button" class="entity-panel__action entity-panel__action--danger entity-panel__action--manage" title="Remove source" @click="removeSource(sourceId)">
                  <wa-icon name="trash" label="Remove source"></wa-icon>
                </button>
              </div>
            </div>
            <div v-if="layersForSource(sourceId).length" class="entity-panel__land-layers">
              <div
                v-for="row in layersForSource(sourceId)"
                :key="row.layerKey"
                class="entity-panel__row entity-panel__row--land entity-panel__land-layer"
                :class="{ 'entity-panel__row--hidden': !layerVisible(sourceId, row.layerKey), 'entity-panel__row--land-loading': rowLoading(sourceId, row.layerKey) }"
                :data-land-key="sourceId + '/' + row.layerKey"
                @click="toggleLayer(sourceId, row.layerKey)"
              >
                <span class="entity-panel__land-layer-swatch" :style="swatchStyle(row.spec)"></span>
                <div class="entity-panel__main entity-panel__main--land-compact">
                  <div class="entity-panel__land-source-title-row">
                    <span
                      v-if="roleBadge(row.spec?.role)"
                      :class="roleBadge(row.spec?.role).className"
                      :title="roleBadge(row.spec?.role).title"
                    >{{ roleBadge(row.spec?.role).label }}</span>
                    <div
                      class="entity-panel__name entity-panel__name--land-compact"
                      :title="landLayerDisplayName(row.spec)"
                    >{{ landLayerShortLabel(row.spec) }}</div>
                  </div>
                  <div class="entity-panel__land-layer-summary" :title="landRuleSidebarText(row.spec)">{{ landRuleSidebarText(row.spec) }}</div>
                </div>
                <div class="entity-panel__controls entity-panel__controls--land-compact">
                  <span class="entity-panel__land-spinner-slot" aria-hidden="true">
                    <span class="entity-panel__land-row-spinner pin-load-spinner" :hidden="!rowLoading(sourceId, row.layerKey)"></span>
                  </span>
                  <button
                    type="button"
                    class="entity-panel__action"
                    :class="{ 'entity-panel__action--active': layerVisible(sourceId, row.layerKey) }"
                    :title="layerVisible(sourceId, row.layerKey) ? 'Hide layer on map' : 'Show layer on map'"
                    :aria-pressed="layerVisible(sourceId, row.layerKey) ? 'true' : 'false'"
                    @click.stop="toggleLayer(sourceId, row.layerKey)"
                  >
                    <wa-icon :name="layerVisible(sourceId, row.layerKey) ? 'eye' : 'eye-slash'" label="Layer visibility"></wa-icon>
                  </button>
                  <button
                    type="button"
                    class="entity-panel__action entity-panel__land-label-toggle"
                    :class="{ 'entity-panel__action--active': hasLabels(row.spec) && labelsVisible(sourceId, row.layerKey) }"
                    :disabled="!hasLabels(row.spec) || !layerVisible(sourceId, row.layerKey)"
                    :title="!hasLabels(row.spec) ? 'No label field' : (labelsVisible(sourceId, row.layerKey) ? 'Hide labels' : 'Show labels')"
                    @click.stop="toggleLayerLabels(sourceId, row.layerKey)"
                  >
                    <wa-icon name="font" label="Layer labels"></wa-icon>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `,
  })

  app.mount(mountPoint)

  const folderModalEl = document.getElementById('land-folder-modal')
  const folderNameEl = document.getElementById('land-folder-name')
  const folderErrorEl = document.getElementById('land-folder-error')
  const folderSaveEl = document.getElementById('land-folder-save')
  watch(
    () => store.land.folderModal,
    (modal) => {
      if (!folderModalEl || !modal) return
      folderModalEl.label = modal.mode === 'rename' ? 'Rename folder' : 'New folder'
      if (folderNameEl) folderNameEl.value = modal.name || ''
      if (folderErrorEl) {
        folderErrorEl.textContent = modal.error || ''
        folderErrorEl.hidden = !modal.error
      }
      folderModalEl.open = !!modal.open
      if (modal.open) {
        requestAnimationFrame(() => {
          folderNameEl?.focus()
          if (modal.mode === 'rename') folderNameEl?.select()
        })
      }
    },
    { deep: true },
  )
  folderSaveEl?.addEventListener('click', () => {
    if (folderNameEl && store.land.folderModal) {
      store.land.folderModal.name = folderNameEl.value
    }
    appApi.saveLandFolderModal?.()
  })
  folderNameEl?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return
    ev.preventDefault()
    if (store.land.folderModal) store.land.folderModal.name = folderNameEl.value
    appApi.saveLandFolderModal?.()
  })
  folderModalEl?.addEventListener('wa-after-hide', () => {
    if (store.land.folderModal?.open) appApi.closeLandFolderModal?.()
  })

  if (countEl) {
    const countMount = document.createElement('span')
    countEl.textContent = ''
    countEl.appendChild(countMount)
    createApp({
      setup() {
        const text = computed(() => {
          store.land.panelRevision
          const sc = store.land.sources?.length || 0
          const lc = store.land.layerRows?.length || 0
          if (!sc) return 'No land sources'
          return `${sc} source${sc === 1 ? '' : 's'}, ${lc} layer${lc === 1 ? '' : 's'}`
        })
        return { text }
      },
      template: `<span>{{ text }}</span>`,
    }).mount(countMount)
  }

  return { mountPoint }
}
