// @ts-check

import { createApp, computed, watch } from 'vue'
import { hiddenPeakSlugs, isPeakMapVisible, sortedPeaksList } from '../stores/peaks.js'

/**
 * Vue peaks sidebar for #entity-panel-peaks-list.
 * @param {object} store
 * @param {object} appApi
 */
export function mountPeaksPanel(store, appApi) {
  const countEl = document.getElementById('entity-panel-peaks-count')
  const listEl = document.getElementById('entity-panel-peaks-list')
  if (!listEl) return null

  listEl.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'peaks-panel-vue-root'
  listEl.appendChild(mountPoint)

  const app = createApp({
    setup() {
      const revision = computed(() => store.peaks.panelRevision)

      const peaks = computed(() => {
        revision.value
        return sortedPeaksList(store)
      })

      const visibleCount = computed(() => {
        revision.value
        return peaks.value.filter((peak) => isPeakMapVisible(store, peak.slug)).length
      })

      watch(
        [peaks, visibleCount],
        () => {
          if (!countEl) return
          const total = peaks.value.length
          const shown = visibleCount.value
          if (!total) {
            countEl.textContent = ''
            return
          }
          if (shown < total) {
            countEl.textContent = `${shown} of ${total} visible`
          } else {
            countEl.textContent = `${total} peak${total === 1 ? '' : 's'}`
          }
        },
        { immediate: true },
      )

      function peakVisible(slug) {
        revision.value
        return isPeakMapVisible(store, slug)
      }

      function toggleVisible(slug) {
        appApi.togglePeakMapVisible?.(slug)
      }

      function showInView(peak) {
        appApi.showPeakInView?.(peak)
      }

      function selectPeak(slug) {
        appApi.selectPeak?.(slug)
      }

      function showAll() {
        appApi.showAllPeaks?.()
      }

      function hideAll() {
        appApi.hideAllPeaks?.()
      }

      return {
        store,
        peaks,
        peakVisible,
        toggleVisible,
        showInView,
        selectPeak,
        showAll,
        hideAll,
      }
    },
    template: `
      <div class="entity-panel__list-items">
        <div v-if="!peaks.length" class="entity-panel__empty">No peaks in catalog yet.</div>
        <div
          v-for="peak in peaks"
          :key="peak.slug"
          class="entity-panel__row entity-panel__row--peak"
          :class="{
            'entity-panel__row--hidden': !peakVisible(peak.slug),
            'entity-panel__row--selected': store.ui.selectedPeakSlug === peak.slug,
          }"
        >
          <button type="button" class="entity-panel__main entity-panel__main--peak" @click="selectPeak(peak.slug)">
            <span class="entity-panel__name">{{ peak.name || peak.slug }}</span>
          </button>
          <div class="entity-panel__controls entity-panel__controls--peak">
            <button
              type="button"
              class="entity-panel__action"
              title="Show in view"
              aria-label="Show in view"
              @click.stop="showInView(peak)"
            >
              <wa-icon name="crosshairs" label="Show in view"></wa-icon>
            </button>
            <button
              type="button"
              class="entity-panel__action"
              :class="{ 'entity-panel__action--active': peakVisible(peak.slug) }"
              :title="peakVisible(peak.slug) ? 'Hide peak on map' : 'Show peak on map'"
              :aria-label="peakVisible(peak.slug) ? 'Hide peak on map' : 'Show peak on map'"
              @click.stop="toggleVisible(peak.slug)"
            >
              <wa-icon
                :name="peakVisible(peak.slug) ? 'eye' : 'eye-slash'"
                :label="peakVisible(peak.slug) ? 'Hide peak on map' : 'Show peak on map'"
              ></wa-icon>
            </button>
          </div>
        </div>
      </div>
    `,
  })
  app.mount(mountPoint)

  document.getElementById('entity-panel-peaks-show-all')?.addEventListener('click', () => {
    appApi.showAllPeaks?.()
  })
  document.getElementById('entity-panel-peaks-hide-all')?.addEventListener('click', () => {
    appApi.hideAllPeaks?.()
  })

  return { mountPoint }
}

export { hiddenPeakSlugs }
