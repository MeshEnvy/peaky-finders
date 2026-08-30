// @ts-check

import { createApp, computed, watch } from 'vue'
import { compareHuman, formatCoord } from '../geo.js'

/**
 * Vue goal-seek panel for #seek-panel.
 * MapLibre seek layers stay imperative in domains/seek.js.
 * @param {object} store
 * @param {object} appApi
 */
export function mountSeekPanel(store, appApi) {
  const panel = document.getElementById('seek-panel')
  if (!panel) return null

  panel.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'seek-panel-vue-root'
  panel.appendChild(mountPoint)

  const app = createApp({
    setup() {
      const startOptions = computed(() => {
        const filters = store.ui.tagFilters
        const mode = store.ui.tagFilterMode
        const list = store.sites.list.filter((site) => {
          if (store.sites.tagFilterBypass.has(site.slug)) return true
          if (!filters.size) return true
          const tags = site.tags || []
          if (mode === 'or') return [...filters].some((t) => tags.includes(t))
          return [...filters].every((t) => tags.includes(t))
        })
        return [...list].sort((a, b) => compareHuman(a.name, b.name))
      })

      const goalLabel = computed(() => {
        const state = store.seek.state
        const lat =
          state?.goalLat != null ? state.goalLat : store.seek.pendingGoalLat
        const lon =
          state?.goalLon != null ? state.goalLon : store.seek.pendingGoalLon
        if (lat == null || lon == null) return 'Not set'
        return `${formatCoord(lat)}, ${formatCoord(lon)}`
      })

      const canUndo = computed(() => {
        const s = store.seek.state
        return Boolean(s?.running && Array.isArray(s.hops) && s.hops.length > 1)
      })

      const canRedo = computed(() => {
        const s = store.seek.state
        return Boolean(s?.running && Array.isArray(s.redoStack) && s.redoStack.length)
      })

      const startLocked = computed(
        () => Boolean(store.seek.state?.running) || store.seek.scanning
      )

      const canRecalc = computed(() => {
        if (store.seek.scanning) return false
        const s = store.seek.state
        if (s?.complete) return false
        const start = store.seek.startSlug || s?.startSlug
        const hasGoal =
          (s?.goalLat != null && s?.goalLon != null) ||
          (store.seek.pendingGoalLat != null && store.seek.pendingGoalLon != null)
        return Boolean(start && hasGoal)
      })

      const canConvert = computed(() => {
        const s = store.seek.state
        if (!s?.running || !Array.isArray(s.hops)) return false
        return s.hops.some((hop) => hop && !hop.site_slug)
      })

      function onStartChange(ev) {
        const slug = ev.target?.value || ''
        store.seek.startSlug = slug
        appApi.onSeekStartChange?.(slug)
      }

      function closePanel() {
        appApi.toggleSeekPanel?.(false)
      }

      function toggleGoalPlacement() {
        if (!store.ui.seekPanelOpen || store.seek.scanning) return
        appApi.setSeekGoalPlacementMode?.(!store.seek.goalPlacementMode)
      }

      return {
        store,
        startOptions,
        goalLabel,
        canUndo,
        canRedo,
        startLocked,
        canRecalc,
        canConvert,
        onStartChange,
        closePanel,
        toggleGoalPlacement,
        undo: () => appApi.undoSeekHop?.(),
        redo: () => appApi.redoSeekHop?.(),
        reset: () => appApi.resetSeekRun?.(),
        convert: () => appApi.openConvertModal?.(),
        recalculate: () => {
          if (!canRecalc.value) return
          if (!store.seek.state?.running) {
            appApi.startSeekRun?.(store.seek.startSlug)
          }
          void appApi.refreshSeekCandidates?.()
        },
      }
    },
    template: `
      <div>
        <div class="seek-panel__header">
          <h2 class="seek-panel__title">Goal seek</h2>
          <wa-button appearance="plain" size="s" type="button" title="Close" aria-label="Close" @click="closePanel">
            <wa-icon name="xmark" label="Close"></wa-icon>
          </wa-button>
        </div>
        <div class="seek-panel__body">
          <label class="seek-panel__field">
            <span class="site-panel__label">Start</span>
            <select
              class="pf-mono"
              :value="store.seek.startSlug"
              :disabled="startLocked"
              @change="onStartChange"
            >
              <option v-if="!startOptions.length" value="">No sites match tags</option>
              <option v-for="site in startOptions" :key="site.slug" :value="site.slug">
                {{ site.name }}
              </option>
            </select>
          </label>
          <div class="seek-panel__field">
            <span class="site-panel__label">Goal</span>
            <div class="seek-panel__goal-row">
              <p class="seek-panel__goal-coords pf-mono pf-muted">{{ goalLabel }}</p>
              <wa-button
                appearance="outlined"
                size="s"
                type="button"
                title="Click map to set goal"
                aria-label="Set goal on map"
                :aria-pressed="store.seek.goalPlacementMode ? 'true' : 'false'"
                :class="{ active: store.seek.goalPlacementMode }"
                :disabled="!store.ui.seekPanelOpen || store.seek.scanning || undefined"
                @click="toggleGoalPlacement"
              >
                <wa-icon name="crosshairs" label="Set goal"></wa-icon>
              </wa-button>
            </div>
            <p class="seek-panel__hint pf-muted wa-caption">Set goal, then click the map to fix lat/lng</p>
          </div>
          <div class="seek-panel__actions">
            <div class="seek-panel__history-actions">
              <wa-button appearance="plain" size="s" type="button" title="Undo hop" aria-label="Undo hop" :disabled="!canUndo || undefined" @click="undo">
                <wa-icon name="arrow-rotate-left" label="Undo hop"></wa-icon>
              </wa-button>
              <wa-button appearance="plain" size="s" type="button" title="Redo hop" aria-label="Redo hop" :disabled="!canRedo || undefined" @click="redo">
                <wa-icon name="arrow-rotate-right" label="Redo hop"></wa-icon>
              </wa-button>
            </div>
            <wa-button appearance="outlined" size="s" type="button" @click="reset">Reset</wa-button>
            <wa-button
              appearance="outlined"
              size="s"
              type="button"
              title="Create preset sites from coordinate hops in the saved path"
              :disabled="!canConvert || store.seek.scanning || undefined"
              @click="convert"
            >
              Convert to sites
            </wa-button>
            <button
              class="seek-panel__recalc"
              type="button"
              title="Recalculate peak candidates for the current hop and map view"
              :disabled="!canRecalc"
              @click="recalculate"
            >
              Recalculate
            </button>
          </div>
          <p id="seek-status" class="seek-panel__status pf-muted wa-caption" :hidden="store.seek.scanning">
            {{ store.seek.statusText }}
          </p>
          <div v-show="store.seek.progress.visible" class="seek-panel__progress">
            <div
              class="seek-panel__progress-track"
              role="progressbar"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="store.seek.progress.indeterminate ? 0 : (store.seek.progress.pct ?? 0)"
              :aria-valuetext="store.seek.progress.detail"
            >
              <div
                class="seek-panel__progress-bar"
                :class="{ 'seek-panel__progress-bar--indeterminate': store.seek.progress.indeterminate }"
                :style="store.seek.progress.indeterminate ? {} : { width: (store.seek.progress.pct ?? 0) + '%' }"
              ></div>
            </div>
            <p class="seek-panel__progress-detail pf-muted wa-caption">{{ store.seek.progress.detail }}</p>
          </div>
        </div>
      </div>
    `,
  })
  app.mount(mountPoint)

  const convertModal = document.getElementById('seek-convert-sites-modal')
  const convertPrefix = document.getElementById('seek-convert-name-prefix')
  const convertHopCount = document.getElementById('seek-convert-hop-count')
  const convertError = document.getElementById('seek-convert-sites-error')
  const convertSave = document.getElementById('seek-convert-save')
  const convertTagInput = document.getElementById('seek-convert-tag-input')
  const convertTagsEl = document.getElementById('seek-convert-tags')
  watch(
    () => store.seek.convertModal,
    (modal) => {
      if (!convertModal || !modal) return
      convertModal.open = !!modal.open
      if (convertPrefix) convertPrefix.value = modal.namePrefix || 'Relay'
      if (convertTagInput) convertTagInput.value = modal.tagInput || ''
      if (convertError) {
        convertError.textContent = modal.error || ''
        convertError.hidden = !modal.error
      }
      if (convertHopCount) {
        convertHopCount.textContent = appApi.seekConvertHopSummary?.() || ''
      }
      if (convertSave) convertSave.disabled = !!modal.saving
      if (convertTagsEl) {
        convertTagsEl.innerHTML = ''
        for (const tag of modal.tags || []) {
          const chip = document.createElement('button')
          chip.type = 'button'
          chip.className = 'site-tag site-tag--toggle is-selected'
          chip.textContent = tag
          chip.addEventListener('click', () => {
            store.seek.convertModal.tags = modal.tags.filter((t) => t !== tag)
          })
          convertTagsEl.appendChild(chip)
        }
      }
    },
    { deep: true },
  )
  convertPrefix?.addEventListener('input', () => {
    if (store.seek.convertModal) store.seek.convertModal.namePrefix = convertPrefix.value
  })
  convertTagInput?.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return
    ev.preventDefault()
    const tag = convertTagInput.value.trim()
    if (!tag || !store.seek.convertModal) return
    if (!store.seek.convertModal.tags.includes(tag)) {
      store.seek.convertModal.tags = [...store.seek.convertModal.tags, tag]
    }
    convertTagInput.value = ''
    store.seek.convertModal.tagInput = ''
  })
  convertSave?.addEventListener('click', () => {
    void appApi.convertSeekPathToSites?.()
  })
  convertModal?.addEventListener('wa-after-hide', () => {
    if (store.seek.convertModal?.open) appApi.closeSeekConvertModal?.()
  })

  return { mountPoint, app }
}
