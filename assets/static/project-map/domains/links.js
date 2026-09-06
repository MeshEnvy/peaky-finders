// @ts-check

import * as apiUrls from '../api/urls.js'
import { SITES_CIRCLE } from '../constants.js'
import { compareHuman } from '../geo.js'
import {
  applyLinksMeshGeoJson,
  clearLinksMesh,
  linksGeoJsonWithLabels,
  setLinksLayerVisibility,
} from '../map/links-layers.js'
import { bumpWarmPriorities as bumpWarmPrioritiesApi } from '../stores/links.js'

const WARM_PRIORITY_INTERACTIVE = 0
const WARM_PRIORITY_VIEWPORT = 10
const WARM_VIEWPORT_SLUG_CAP = 48

/**
 * @param {object} opts
 * @param {object} [opts.store]
 * @param {maplibregl.Map} opts.map
 * @param {string} opts.projectSlug
 * @param {() => boolean} opts.getMapReady
 * @param {() => object[]} opts.getSites
 * @param {() => string|null} opts.getSelectedSlug
 * @param {(slug: string) => boolean} opts.isSiteMapHidden
 * @param {(slug: string) => boolean} opts.isViewshedVisible
 * @param {(site: object) => boolean} opts.siteVisibleInMap
 * @param {() => boolean} opts.isMapTiltedView
 * @param {(geojson: import('geojson').FeatureCollection|null|undefined) => import('geojson').FeatureCollection|null|undefined} opts.filterSiteLinksGeoJson
 * @param {() => void} opts.raiseSiteLayers
 * @param {() => boolean} opts.getShowSiteLinks
 * @param {(slug: string) => void} opts.markSiteOutboundLinksReady
 * @param {(slug: string) => boolean} opts.sitePinSpinning
 * @param {(slug: string) => boolean} opts.isSiteOutboundLinksReady
 * @param {() => void} opts.renderSelectedPanel
 * @param {(slug: string) => object|undefined} opts.getSiteBySlug
 * @param {() => string[]} [opts.getVisibleSiteSlugs]
 */
export function createLinksDomain(opts) {
  const {
    store,
    map,
    projectSlug,
    getMapReady,
    getSites,
    isSiteMapHidden,
    isViewshedVisible,
    siteVisibleInMap,
    isMapTiltedView,
    filterSiteLinksGeoJson,
    raiseSiteLayers,
    getShowSiteLinks,
    markSiteOutboundLinksReady,
    sitePinSpinning,
    isSiteOutboundLinksReady,
    renderSelectedPanel,
    getSiteBySlug,
    getVisibleSiteSlugs,
  } = opts

  const singleSiteLinksInflight = new Set()
  let warmPrioritiesTimer = null

  function getPayload() {
    return store.links.payload
  }

  function warmPrioritySlugsInViewport() {
    const slugs = []
    for (const site of getSites()) {
      if (isSiteMapHidden(site.slug) || !isViewshedVisible(site.slug)) continue
      if (siteVisibleInMap(site)) slugs.push(site.slug)
      if (slugs.length >= WARM_VIEWPORT_SLUG_CAP) break
    }
    return slugs
  }

  function bumpWarmPriorities(slugs, priority) {
    const list = Array.isArray(slugs) ? slugs.filter(Boolean) : []
    if (!list.length) return
    void bumpWarmPrioritiesApi(projectSlug, list, priority).catch(() => {
      /* background warm is best-effort */
    })
  }

  function syncWarmPriorities() {
    if (!getMapReady()) return
    const viewport = warmPrioritySlugsInViewport()
    const slugs = new Set(viewport)
    const selectedSlug = store.ui.selectedSlug
    if (selectedSlug && isViewshedVisible(selectedSlug)) slugs.add(selectedSlug)
    if (!slugs.size) return
    if (selectedSlug && slugs.has(selectedSlug)) {
      void bumpWarmPriorities([selectedSlug], WARM_PRIORITY_INTERACTIVE)
      slugs.delete(selectedSlug)
    }
    if (slugs.size) {
      void bumpWarmPriorities([...slugs], WARM_PRIORITY_VIEWPORT)
    }
  }

  function scheduleWarmPrioritiesSync() {
    if (warmPrioritiesTimer) clearTimeout(warmPrioritiesTimer)
    warmPrioritiesTimer = setTimeout(() => {
      warmPrioritiesTimer = null
      syncWarmPriorities()
    }, 300)
  }

  function onMapMoveEndForWarmPriorities() {
    if (isMapTiltedView()) return
    scheduleWarmPrioritiesSync()
  }

  function setSiteLinksVisible(visible) {
    if (!getMapReady()) return
    setLinksLayerVisibility(map, visible)
  }

  function addSiteLinksLayer(geojson) {
    const filtered = filterSiteLinksGeoJson(geojson)
    if (!filtered || !filtered.features || !filtered.features.length) {
      clearLinksMesh(map)
      raiseSiteLayers()
      return
    }
    const labeled = linksGeoJsonWithLabels(filtered)
    applyLinksMeshGeoJson(map, labeled, {
      visible: getShowSiteLinks(),
      beforeId: SITES_CIRCLE,
    })
    raiseSiteLayers()
  }

  function refreshFilteredLinks() {
    if (store.links.payload && store.links.payload.geojson) {
      addSiteLinksLayer(store.links.payload.geojson)
    }
  }

  function mergeSingleSiteLinks(slug, payload) {
    if (!payload) return
    const features = payload.geojson?.features
    if (!Array.isArray(features)) {
      if (payload.outbound_ready !== false) {
        markSiteOutboundLinksReady(slug)
      }
      return
    }
    const base =
      store.links.payload &&
      store.links.payload.geojson &&
      Array.isArray(store.links.payload.geojson.features)
        ? store.links.payload
        : {
            status: 'pending',
            links: [],
            geojson: { type: 'FeatureCollection', features: [] },
          }
    const untouched = (props) => props.a !== slug && props.b !== slug
    const mergedFeatures = base.geojson.features
      .filter((f) => untouched(f.properties || {}))
      .concat(features)
    const links = (Array.isArray(base.links) ? base.links : [])
      .filter((r) => untouched(r || {}))
      .concat(Array.isArray(payload.links) ? payload.links : [])
    store.links.payload = {
      ...base,
      links,
      geojson: { type: 'FeatureCollection', features: mergedFeatures },
    }
    addSiteLinksLayer(store.links.payload.geojson)
    if (payload.outbound_ready !== false) {
      markSiteOutboundLinksReady(slug)
    }
    const selectedSlug = store.ui.selectedSlug
    if (selectedSlug) renderSelectedPanel(getSiteBySlug(selectedSlug))
  }

  function applySiteLinksPayload(payload) {
    if (!payload) return
    if (payload.partial && payload.site) {
      mergeSingleSiteLinks(payload.site, payload)
      return
    }
    const prevFeatures = store.links.payload?.geojson?.features
    const prevCount = Array.isArray(prevFeatures) ? prevFeatures.length : 0
    const nextFeatures = payload.geojson?.features
    const nextCount = Array.isArray(nextFeatures) ? nextFeatures.length : 0
    if (
      payload.status === 'pending' &&
      store.links.payload &&
      store.links.payload.status === 'ready' &&
      store.links.payload.geojson &&
      prevCount > 0
    ) {
      return
    }
    if (
      payload.status === 'ready' &&
      store.links.payload?.status === 'ready' &&
      prevCount > 0 &&
      nextCount === 0
    ) {
      return
    }
    store.links.payload = payload
    if (payload.geojson) addSiteLinksLayer(payload.geojson)
    if (payload.status === 'ready' && !payload.partial) {
      for (const site of getSites()) {
        markSiteOutboundLinksReady(site.slug)
      }
    }
    const selectedSlug = store.ui.selectedSlug
    if (selectedSlug) renderSelectedPanel(getSiteBySlug(selectedSlug))
  }

  function mergeConvertedSeekLinks(payload) {
    const created = Array.isArray(payload?.created_slugs)
      ? payload.created_slugs.filter(Boolean)
      : []
    if (!created.length || !payload.geojson) return
    for (const slug of created) {
      const links = (Array.isArray(payload.links) ? payload.links : []).filter(
        (row) => row.a === slug || row.b === slug,
      )
      const features = (payload.geojson.features || []).filter((feature) => {
        const props = feature.properties || {}
        return props.a === slug || props.b === slug
      })
      mergeSingleSiteLinks(slug, {
        links,
        geojson: { type: 'FeatureCollection', features },
        outbound_ready: true,
      })
    }
  }

  async function loadSiteLinks() {
    try {
      const resp = await fetch(apiUrls.linksApiUrl(projectSlug))
      if (!resp.ok) {
        syncWarmPriorities()
        return
      }
      const payload = await resp.json()
      applySiteLinksPayload(payload)
      syncWarmPriorities()
    } catch (_) {
      syncWarmPriorities()
    }
  }

  function scheduleSingleSiteLinksRetry(slug) {
    if (!slug || isSiteOutboundLinksReady(slug)) return
    window.setTimeout(() => {
      if (sitePinSpinning(slug) && !isSiteOutboundLinksReady(slug)) {
        void loadSingleSiteLinks(slug)
      }
    }, 5000)
  }

  async function loadSingleSiteLinks(slug) {
    if (!slug || singleSiteLinksInflight.has(slug)) return
    singleSiteLinksInflight.add(slug)
    try {
      const resp = await fetch(apiUrls.siteLinksApiUrl(projectSlug, slug))
      if (!resp.ok) {
        scheduleSingleSiteLinksRetry(slug)
        return
      }
      const payload = await resp.json()
      mergeSingleSiteLinks(slug, payload)
    } catch (_) {
      scheduleSingleSiteLinksRetry(slug)
    } finally {
      singleSiteLinksInflight.delete(slug)
    }
  }

  function purgeSiteLinksForSlug(slug) {
    if (!slug || !store.links.payload) return
    const touches = (props) => props.a === slug || props.b === slug
    const geojson = store.links.payload.geojson
    if (!geojson || !Array.isArray(geojson.features)) {
      refreshFilteredLinks()
      return
    }
    store.links.payload = {
      ...store.links.payload,
      geojson: {
        type: 'FeatureCollection',
        features: geojson.features.filter((f) => !touches(f.properties || {})),
      },
      links: Array.isArray(store.links.payload.links)
        ? store.links.payload.links.filter((r) => !touches(r || {}))
        : store.links.payload.links,
    }
    addSiteLinksLayer(store.links.payload.geojson)
  }

  function linkedPeersForSite(slug) {
    if (!store.links.payload || !Array.isArray(store.links.payload.links)) return []
    const peers = []
    for (const row of store.links.payload.links) {
      if (!row.linked) continue
      if (row.a === slug) peers.push(row.b)
      else if (row.b === slug) peers.push(row.a)
    }
    return peers.sort()
  }

  function visibleSiteSlugSet() {
    if (typeof getVisibleSiteSlugs === 'function') {
      return new Set(getVisibleSiteSlugs())
    }
    return new Set(getSites().map((site) => site.slug).filter((slug) => !isSiteMapHidden(slug)))
  }

  function visibleLinkedPeersForSite(slug) {
    const visible = visibleSiteSlugSet()
    return linkedPeersForSite(slug).filter((peer) => visible.has(peer))
  }

  function selectedSiteLinks({ visibleOnly = false } = {}) {
    const slug = store.ui.selectedSlug
    if (!slug) return []
    const geojson = store.links.payload?.geojson
    const peerSlugs = visibleOnly ? visibleLinkedPeersForSite(slug) : linkedPeersForSite(slug)
    return peerSlugs
      .map((peerSlug) => {
        const peer = getSiteBySlug(peerSlug)
        let distanceKm = null
        if (geojson?.features) {
          const [a, b] = slug <= peerSlug ? [slug, peerSlug] : [peerSlug, slug]
          for (const feature of geojson.features) {
            const props = feature.properties || {}
            if (props.a === a && props.b === b) {
              distanceKm = props.distance_km
              break
            }
          }
        }
        return { slug: peerSlug, name: peer?.name || peerSlug, distanceKm }
      })
      .sort((left, right) => compareHuman(left.name, right.name))
  }

  function findSiteLinkFeature(slugA, slugB) {
    const features = store.links.payload?.geojson?.features
    if (!features) return null
    for (const feature of features) {
      const props = feature.properties || {}
      if (
        (props.a === slugA && props.b === slugB) ||
        (props.a === slugB && props.b === slugA)
      ) {
        return feature
      }
    }
    return null
  }

  async function ensureSiteLinksForSlug(slug) {
    if (!slug) return
    const hasLinks = (store.links.payload?.links || []).some(
      (row) => row.linked && (row.a === slug || row.b === slug),
    )
    if (hasLinks) return
    await loadSingleSiteLinks(slug)
  }

  return {
    getPayload,
    applySiteLinksPayload,
    mergeSingleSiteLinks,
    mergeConvertedSeekLinks,
    loadSiteLinks,
    loadSingleSiteLinks,
    addSiteLinksLayer,
    refreshFilteredLinks,
    setSiteLinksVisible,
    purgeSiteLinksForSlug,
    syncWarmPriorities,
    scheduleWarmPrioritiesSync,
    onMapMoveEndForWarmPriorities,
    bumpWarmPriorities,
    linkedPeersForSite,
    visibleLinkedPeersForSite,
    selectedSiteLinks,
    findSiteLinkFeature,
    ensureSiteLinksForSlug,
    WARM_PRIORITY_INTERACTIVE,
    WARM_PRIORITY_VIEWPORT,
  }
}
