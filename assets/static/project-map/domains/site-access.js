// @ts-check

import * as apiUrls from '../api/urls.js'
import { ensureSiteAccessLayers, setSiteAccessLayerData } from '../map/site-access-layers.js'
import { isViewshedVisible as isViewshedVisibleInStore } from '../stores/viewshed.js'

/**
 * Site access alongside viewshed warm: store rows, paint jeep/hike layers.
 * Same show gate as viewsheds: not map-hidden and viewshed visible.
 * @param {object} opts
 * @param {object} opts.store
 * @param {string} opts.projectSlug
 * @param {() => maplibregl.Map} opts.getMap
 * @param {() => boolean} opts.getMapReady
 * @param {() => object[]} opts.getSites
 * @param {(slug: string) => boolean} [opts.isSiteMapHidden]
 * @param {(slug: string) => boolean} [opts.isViewshedVisible]
 */
export function createSiteAccessDomain(opts) {
  const { store, projectSlug, getMap, getMapReady, getSites } = opts

  /** @type {Map<string, Promise<void>>} */
  const inflight = new Map()

  if (!store.access) {
    store.access = { bySlug: {} }
  }

  function isSiteMapHidden(slug) {
    return opts.isSiteMapHidden?.(slug) === true
  }

  function isViewshedVisible(slug) {
    if (opts.isViewshedVisible) return opts.isViewshedVisible(slug)
    return isViewshedVisibleInStore(store, slug)
  }

  /** Same gate as viewshed paint / schedule. */
  function siteAccessShowing(slug) {
    if (!slug) return false
    return !isSiteMapHidden(slug) && isViewshedVisible(slug)
  }

  function accessPlacesForMap() {
    return Object.values(store.access.bySlug).filter(
      (row) => row?.slug && siteAccessShowing(row.slug),
    )
  }

  function refreshLayers() {
    if (!getMapReady()) return
    const map = getMap()
    ensureSiteAccessLayers(map)
    setSiteAccessLayerData(map, accessPlacesForMap())
  }

  /**
   * @param {string} slug
   * @param {object} access
   * @param {object} [site]
   */
  function ingestAccess(slug, access, site) {
    if (!slug || !access || access.status === 'error' || access.status === 'missing') {
      return
    }
    if (!siteAccessShowing(slug)) {
      // Keep selected-sheet cache if needed, but do not paint off-map sites.
      if (store.ui.selectedSlug !== slug) return
    }
    const base = site || getSites().find((s) => s.slug === slug) || {}
    store.access.bySlug[slug] = {
      slug,
      name: base.name || slug,
      lat: Number.isFinite(base.lat) ? base.lat : access.lat,
      lon: Number.isFinite(base.lon) ? base.lon : access.lon,
      road_lat: access.road_lat,
      road_lon: access.road_lon,
      paved_lat: access.paved_lat,
      paved_lon: access.paved_lon,
      hike_m: access.hike_m,
      jeep_m: access.jeep_m,
      hike: access.hike || null,
      jeep: access.jeep || null,
    }
    if (store.ui.selectedSlug === slug) {
      store.access.selected = store.access.bySlug[slug]
    }
    refreshLayers()
  }

  function handleAccessEvent(data) {
    if (!data?.slug) return
    if (data.status === 'error' || data.status === 'missing') return
    if (!siteAccessShowing(data.slug) && store.ui.selectedSlug !== data.slug) return
    ingestAccess(data.slug, data)
  }

  /**
   * Fetch access for a site (cache-first GET; warm=1 so server computes if missing).
   * Only for sites that would show a viewshed.
   * @param {object} site
   */
  function ensureSiteAccess(site) {
    if (!site?.slug) return
    if (!siteAccessShowing(site.slug)) return
    if (store.access.bySlug[site.slug]?.hike || store.access.bySlug[site.slug]?.jeep) {
      refreshLayers()
      return
    }
    if (inflight.has(site.slug)) return
    const task = (async () => {
      try {
        if (!getMapReady()) return
        if (!siteAccessShowing(site.slug)) return
        ensureSiteAccessLayers(getMap())
        const resp = await fetch(
          apiUrls.placeAccessApiUrl(projectSlug, site.slug, {
            warm: true,
            lat: site.lat,
            lon: site.lon,
          }),
        )
        if (!resp.ok) return
        const access = await resp.json()
        ingestAccess(site.slug, access, site)
      } catch (err) {
        console.warn('site access load failed', site.slug, err)
      } finally {
        inflight.delete(site.slug)
      }
    })()
    inflight.set(site.slug, task)
  }

  function ensureSitesAccess(sites) {
    for (const site of sites || []) ensureSiteAccess(site)
  }

  return {
    ensureSiteAccess,
    ensureSitesAccess,
    handleAccessEvent,
    refreshLayers,
    ingestAccess,
    siteAccessShowing,
  }
}
