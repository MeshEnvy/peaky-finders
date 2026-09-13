// @ts-check

import { createApp, computed, watch } from 'vue'
import { compareHuman } from '../geo.js'
import { mountFloatingPanel } from './floating-panel.js'
import { linkSolverPanelPosKey } from '../constants.js'

/**
 * @param {object} store
 * @param {object} appApi
 */
export function mountLinkSolverPanel(store, appApi) {
  const panel = document.getElementById('link-solver-panel')
  if (!panel) return null
  const shell = document.querySelector('.map-shell')
  const header = panel.querySelector('.link-solver-panel__header')
  if (!shell || !header) return null

  const body = panel.querySelector('.link-solver-panel__body') || panel
  body.innerHTML = ''
  const mountPoint = document.createElement('div')
  mountPoint.className = 'link-solver-panel__vue'
  body.appendChild(mountPoint)

  const floating = mountFloatingPanel({
    panelEl: panel,
    headerEl: header,
    shellEl: shell,
    posKey: linkSolverPanelPosKey(store.projectSlug),
    getPos: () => store.linkSolver.pos,
    setPos: (pos) => {
      store.linkSolver.pos = pos
    },
    onCollapsedToggle: (collapsed) => {
      store.linkSolver.collapsed = collapsed
      panel.classList.toggle('floating-panel--collapsed', collapsed)
    },
  })

  watch(
    () => store.linkSolver.collapsed,
    (collapsed) => {
      floating.toggleCollapsed(!!collapsed)
      panel.classList.toggle('floating-panel--collapsed', !!collapsed)
    },
    { immediate: true },
  )

  watch(
    () => store.linkSolver.panelOpen,
    (open) => {
      if (open) floating.syncPosition()
    },
  )

  const app = createApp({
    setup() {
      const sortedSites = computed(() => {
        void store.ui.viewportEpoch
        void store.sites.revision
        void store.ui.tagFilters?.size
        const visible = appApi.viewportExportableSites?.() || []
        const bySlug = new Map(visible.map((site) => [site.slug, site]))
        for (const slug of [store.linkSolver.a, store.linkSolver.b]) {
          if (!slug || bySlug.has(slug)) continue
          const site = store.sites.list.find((s) => s.slug === slug)
          if (site) bySlug.set(slug, site)
        }
        return [...bySlug.values()].sort((a, b) => compareHuman(a.name, b.name))
      })

      const siteBChoices = computed(() =>
        sortedSites.value.filter((site) => site.slug !== store.linkSolver.a),
      )

      const routes = computed(() => appApi.displayLinkSolverRoutes?.() || [])

      const canSolve = computed(
        () =>
          !!store.linkSolver.a &&
          !!store.linkSolver.b &&
          store.linkSolver.a !== store.linkSolver.b &&
          !store.linkSolver.alreadyLinked &&
          !store.linkSolver.scanning,
      )

      function peakChain(route) {
        const peaks = route?.peaks
        if (!Array.isArray(peaks) || !peaks.length) return '—'
        return peaks.map((p) => p.name || p.peak_slug || '?').join(' → ')
      }

      function worstAccess(route) {
        const accessMap = appApi.linkSolverPeakAccess?.() || new Map()
        let worst = null
        for (const peak of route?.peaks || []) {
          const slug = peak.peak_slug || peak.slug
          const val = slug ? accessMap.get(String(slug)) : null
          if (val && (!worst || val > worst)) worst = val
        }
        return worst || '—'
      }

      function onSiteAChange(ev) {
        store.linkSolver.a = ev.target.value || null
        if (store.linkSolver.b === store.linkSolver.a) store.linkSolver.b = null
        appApi.checkLinkSolverAlreadyLinked?.()
      }

      function onSiteBChange(ev) {
        store.linkSolver.b = ev.target.value || null
        appApi.checkLinkSolverAlreadyLinked?.()
      }

      watch(
        () => [store.linkSolver.a, store.linkSolver.b],
        () => appApi.checkLinkSolverAlreadyLinked?.(),
      )

      return {
        store,
        sortedSites,
        siteBChoices,
        routes,
        canSolve,
        peakChain,
        worstAccess,
        onSiteAChange,
        onSiteBChange,
        solve: () => appApi.solveLinkPair?.(store.linkSolver.a, store.linkSolver.b),
        selectRoute: (routeId) => appApi.selectLinkSolverRoute?.(routeId),
        moreLikeThis: (routeId) => appApi.moreLikeLinkSolverRoute?.(routeId),
        loadMore: () => appApi.loadMoreLinkSolverRoutes?.(),
        accept: () => appApi.acceptLinkSolverRoute?.(),
        close: () => appApi.toggleLinkSolverPanel?.(false),
        toggleCollapse: () => {
          store.linkSolver.collapsed = !store.linkSolver.collapsed
        },
      }
    },
    template: `
      <div class="link-solver-panel__content" :class="{ 'link-solver-panel__content--collapsed': store.linkSolver.collapsed }">
        <label class="link-solver-panel__field">
          <span class="site-panel__label">Site A</span>
          <select class="pf-mono" :value="store.linkSolver.a || ''" @change="onSiteAChange">
            <option value="">Choose site…</option>
            <option v-for="site in sortedSites" :key="site.slug" :value="site.slug">{{ site.name }}</option>
          </select>
        </label>
        <label class="link-solver-panel__field">
          <span class="site-panel__label">Site B</span>
          <select class="pf-mono" :value="store.linkSolver.b || ''" @change="onSiteBChange">
            <option value="">Choose site…</option>
            <option v-for="site in siteBChoices" :key="site.slug" :value="site.slug">{{ site.name }}</option>
          </select>
        </label>
        <wa-callout v-if="store.linkSolver.alreadyLinked" variant="warning" size="small">
          These sites are already linked in the mesh.
        </wa-callout>
        <div class="link-solver-panel__actions">
          <wa-button variant="brand" size="s" type="button" :disabled="!canSolve" @click="solve">
            {{ store.linkSolver.scanning ? 'Scanning…' : 'Solve' }}
          </wa-button>
        </div>
        <p v-if="store.linkSolver.statusText" class="link-solver-panel__status pf-muted wa-caption">
          {{ store.linkSolver.statusText }}
        </p>
        <ul v-if="routes.length && !store.linkSolver.collapsed" class="link-solver-panel__routes">
          <li
            v-for="route in routes"
            :key="route.route_id"
            class="link-solver-panel__route"
            :class="{ 'link-solver-panel__route--selected': store.linkSolver.selectedRouteId === route.route_id }"
            @click="selectRoute(route.route_id)"
          >
            <div class="link-solver-panel__route-head">
              <span class="link-solver-panel__route-title">
                {{ route.hops }} hop{{ route.hops === 1 ? '' : 's' }}
                · {{ route.bottleneck_db != null ? route.bottleneck_db.toFixed(1) : '?' }} dB
                · {{ route.total_km != null ? route.total_km.toFixed(1) : '?' }} km
              </span>
              <span v-if="route.unique" class="link-solver-panel__badge">unique</span>
            </div>
            <p class="link-solver-panel__route-peaks pf-mono pf-muted">{{ peakChain(route) }}</p>
            <p class="link-solver-panel__route-access pf-muted wa-caption">Access: {{ worstAccess(route) }}</p>
            <wa-button
              appearance="plain"
              size="s"
              type="button"
              class="link-solver-panel__like-btn"
              @click.stop="moreLikeThis(route.route_id)"
            >
              More like this
            </wa-button>
          </li>
        </ul>
        <div v-if="routes.length && !store.linkSolver.collapsed" class="link-solver-panel__footer">
          <wa-button appearance="outlined" size="s" type="button" :disabled="store.linkSolver.scanning" @click="loadMore">
            Load more
          </wa-button>
          <wa-button
            variant="brand"
            size="s"
            type="button"
            :disabled="!store.linkSolver.selectedRouteId || store.linkSolver.accepting"
            @click="accept"
          >
            {{ store.linkSolver.accepting ? 'Accepting…' : 'Accept route' }}
          </wa-button>
        </div>
      </div>
    `,
  })

  app.mount(mountPoint)

  panel.querySelector('.link-solver-panel__collapse')?.addEventListener('click', () => {
    store.linkSolver.collapsed = !store.linkSolver.collapsed
  })
  panel.querySelector('.link-solver-panel__close')?.addEventListener('click', () => {
    appApi.toggleLinkSolverPanel?.(false)
  })

  return app
}
