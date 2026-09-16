// @ts-check

import * as apiUrls from '../api/urls.js'
import { ensureSiteAccessLayers, setSiteAccessLayerData } from '../map/site-access-layers.js'
import {
  isAccessVisible as isAccessVisibleInStore,
  setAccessVisible as setAccessVisibleInStore,
  siteAccessShouldShow,
} from '../stores/access.js'

/**
 * Site jeep/hike access overlay: independent of viewshed visibility.
 * @param {object} opts
 * @param {object} opts.store
 * @param {string} opts.projectSlug
 * @param {() => maplibregl.Map} opts.getMap
 * @param {() => boolean} opts.getMapReady
 * @param {() => object[]} opts.getSites
 * @param {(slug: string) => boolean} [opts.isSiteMapHidden]
 * @param {(slug: string) => boolean} [opts.isAccessVisible]
 * @param {() => void} [opts.raiseSiteLayers]
 */
export function createSiteAccessDomain(opts) {
  const { store, projectSlug, getMap, getMapReady, getSites, raiseSiteLayers } = opts

  /** @type {Map<string, Promise<void>>} */
  const inflight = new Map()
  /** @type {Map<string, Promise<void>>} */
  const placeInflight = new Map()

  if (!store.access) {
    store.access = { bySlug: {}, visible: new Map() }
  }
  if (!store.access.visible) {
    store.access.visible = new Map()
  }

  function isSiteMapHidden(slug) {
    return opts.isSiteMapHidden?.(slug) === true
  }

  function isAccessVisible(slug) {
    if (opts.isAccessVisible) return opts.isAccessVisible(slug)
    return isAccessVisibleInStore(store, slug)
  }

  function siteAccessShowing(slug) {
    return siteAccessShouldShow(store, slug, isSiteMapHidden(slug))
  }

  function accessPlacesForMap() {
    return Object.values(store.access.bySlug).filter(
      (row) => row?.slug && siteAccessShowing(row.slug),
    )
  }

  function refreshLayers() {
    if (!getMapReady()) return
    const map = getMap()
    if (!map) return
    try {
      ensureSiteAccessLayers(map)
      setSiteAccessLayerData(map, accessPlacesForMap())
      raiseSiteLayers?.()
    } catch (err) {
      console.warn('site access layers failed', err)
    }
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
    if (siteAccessShowing(slug)) {
      refreshLayers()
    }
  }

  function handleAccessEvent(data) {
    if (!data?.slug) return
    if (data.status === 'error' || data.status === 'missing') return
    ingestAccess(data.slug, data)
  }

  /**
   * Fetch access for a site (cache-first GET; warm=1 so server computes if missing).
   * @param {object} site
   */
  function accessHasRouteProfiles(row) {
    if (!row) return false
    const hikeProfile = row.hike?.profile
    const jeepProfile = row.jeep?.profile
    return (
      (Array.isArray(hikeProfile) && hikeProfile.length >= 2) ||
      (Array.isArray(jeepProfile) && jeepProfile.length >= 2)
    )
  }

  function accessRowLoaded(row) {
    return accessHasRouteProfiles(row)
  }

  /**
   * Cache-first access load for peaks / solver previews. GET disk cache, then POST warm.
   * @param {string} slug
   * @param {number} lat
   * @param {number} lon
   * @param {object} [siteMeta]
   * @param {{ prefetchOnly?: boolean, isActive?: () => boolean, onReady?: () => void }} [opts]
   */
  async function ensurePlaceAccessProfiles(slug, lat, lon, siteMeta, opts = {}) {
    if (!slug) return
    const active = () => (opts.isActive ? opts.isActive() : true)
    const meta = siteMeta || { slug, lat, lon, name: slug }

    const cached = store.access.bySlug[slug]
    if (accessHasRouteProfiles(cached)) {
      if (!opts.prefetchOnly && active()) opts.onReady?.()
      return
    }

    if (placeInflight.has(slug)) {
      await placeInflight.get(slug)
      if (accessHasRouteProfiles(store.access.bySlug[slug])) {
        if (!opts.prefetchOnly && active()) opts.onReady?.()
      }
      return
    }

    const task = (async () => {
      try {
        if (!active() && !opts.prefetchOnly) return

        const resp = await fetch(
          apiUrls.placeAccessApiUrl(projectSlug, slug, { lat, lon }),
        )
        if (resp.ok) {
          const access = await resp.json()
          if (active() || opts.prefetchOnly) {
            ingestAccess(slug, access, meta)
          }
          if (accessHasRouteProfiles(access)) {
            if (!opts.prefetchOnly && active()) opts.onReady?.()
            return
          }
        }

        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
        const warmResp = await fetch(
          apiUrls.placeAccessWarmApiUrl(projectSlug, slug, { lat, lon }),
          { method: 'POST' },
        )
        if (!warmResp.ok) return
        const warmed = await warmResp.json()
        if (active() || opts.prefetchOnly) {
          ingestAccess(slug, warmed, meta)
        }
        if (!opts.prefetchOnly && active()) opts.onReady?.()
      } catch (err) {
        console.warn('place access load failed', slug, err)
      } finally {
        placeInflight.delete(slug)
      }
    })()

    placeInflight.set(slug, task)
    await task
  }

  function ensureSiteAccess(site) {
    if (!site?.slug) return
    if (!siteAccessShowing(site.slug)) return
    if (accessRowLoaded(store.access.bySlug[site.slug])) {
      refreshLayers()
      return
    }
    if (inflight.has(site.slug)) return
    const task = (async () => {
      try {
        if (!siteAccessShowing(site.slug)) return
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

  function ensureAccessForVisibleSites() {
    for (const site of getSites()) {
      if (!siteAccessShowing(site.slug)) continue
      ensureSiteAccess(site)
    }
  }

  function setAccessVisible(slug, visible) {
    setAccessVisibleInStore(store, slug, visible)
    if (!visible) {
      refreshLayers()
      return
    }
    const site = getSites().find((s) => s.slug === slug)
    if (site) ensureSiteAccess(site)
    else refreshLayers()
  }

  function applyAccessVisibilityForSite(slug) {
    refreshLayers()
    if (siteAccessShowing(slug)) {
      const site = getSites().find((s) => s.slug === slug)
      if (site) ensureSiteAccess(site)
    }
  }

  return {
    ensureSiteAccess,
    ensurePlaceAccessProfiles,
    ensureSitesAccess,
    ensureAccessForVisibleSites,
    handleAccessEvent,
    refreshLayers,
    ingestAccess,
    accessHasRouteProfiles,
    siteAccessShowing,
    isAccessVisible,
    setAccessVisible,
    applyAccessVisibilityForSite,
  }
}
