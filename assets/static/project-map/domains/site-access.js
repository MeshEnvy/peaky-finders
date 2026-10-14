// @ts-check

import * as apiUrls from '../api/urls.js'
import { DRAFT_VIEWSHED_SLUG, isEphemeralPlaceSlug } from '../constants.js'
import { currentPreview } from '../preview-session.js'
import { coordsMatchPair } from '../geo.js'
import { ensureSiteAccessLayers, setSiteAccessLayerData } from '../map/site-access-layers.js'
import {
  activeAccessPreviewSlug,
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

  /** @param {object} opt */
  function routeFieldsFromAccessPayload(opt) {
    return {
      road_lat: opt.road_lat,
      road_lon: opt.road_lon,
      paved_lat: opt.paved_lat,
      paved_lon: opt.paved_lon,
      hike_m: opt.hike_m,
      jeep_m: opt.jeep_m,
      trail_m: opt.trail_m,
      trail_lat: opt.trail_lat,
      trail_lon: opt.trail_lon,
      hike: opt.hike || null,
      jeep: opt.jeep || null,
      trail: opt.trail || null,
      budget: opt.budget || null,
      hike_difficulty: opt.hike_difficulty,
      jeep_difficulty: opt.jeep_difficulty,
      access_difficulty: opt.access_difficulty,
      max_slope_deg: opt.max_slope_deg,
    }
  }

  /**
   * @param {object} row
   * @param {string|null|undefined} choiceId
   */
  function rowWithAccessChoice(row, choiceId) {
    const options = row.options
    if (!Array.isArray(options) || options.length === 0) return row
    const id = choiceId || row.access_choice
    const opt =
      (id && options.find((o) => o.id === id)) ||
      options.find((o) => o.recommended) ||
      options[0]
    if (!opt) return row
    return {
      ...row,
      ...routeFieldsFromAccessPayload(opt),
      access_choice: opt.id,
      options: options.map((o) => ({ ...o, selected: o.id === opt.id })),
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
    const hasOptions = Array.isArray(access.options) && access.options.length > 0
    const choiceId = isEphemeralPlaceSlug(slug)
      ? store.access.draftAccessChoice
      : base.access_choice ?? access.access_choice
    let row = {
      slug,
      name: base.name || slug,
      lat: Number.isFinite(base.lat) ? base.lat : access.lat,
      lon: Number.isFinite(base.lon) ? base.lon : access.lon,
      eligible: access.eligible ?? true,
      budget: access.budget || null,
      budget_route: hasOptions ? null : access.budget_route || null,
      options: hasOptions ? access.options : null,
      access_choice: choiceId ?? access.access_choice ?? null,
      road_lat: access.road_lat,
      road_lon: access.road_lon,
      paved_lat: access.paved_lat,
      paved_lon: access.paved_lon,
      hike_m: access.hike_m,
      jeep_m: access.jeep_m,
      trail_m: access.trail_m,
      trail_lat: access.trail_lat,
      trail_lon: access.trail_lon,
      hike: access.hike || null,
      jeep: access.jeep || null,
      trail: access.trail || null,
    }
    row = rowWithAccessChoice(row, choiceId)
    store.access.bySlug[slug] = row
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

  function handleOsmEvent(data) {
    if (!data || typeof data !== 'object') return
    store.access.osm = {
      status: data.status || 'idle',
      phase: data.phase || '',
      step: Number(data.step) || 0,
      total: Number(data.total) || 4,
      fraction: Number(data.fraction) || 0,
    }
    opts.updatePinOverlays?.()
    const phase = String(data.phase || '').toLowerCase()
    const ready =
      phase === 'ready' ||
      (data.status === 'ready' && Number(data.fraction) >= 1) ||
      Number(data.fraction) >= 1
    if (ready && store.access.pendingRewarmAfterRoads) {
      store.access.pendingRewarmAfterRoads = false
      void rewarmAfterRoadsBookChange()
    }
  }

  function accessRewarmTargets() {
    /** @type {Map<string, { slug: string, lat: number, lon: number, name?: string }>} */
    const out = new Map()
    const add = (slug, lat, lon, name) => {
      if (!slug || !Number.isFinite(lat) || !Number.isFinite(lon)) return
      if (!siteAccessShowing(slug)) return
      out.set(slug, { slug, lat, lon, name: name || slug })
    }
    for (const row of Object.values(store.access.bySlug || {})) {
      if (!row?.slug) continue
      add(row.slug, Number(row.lat), Number(row.lon), row.name)
    }
    const selected = store.ui?.selectedSlug
    if (selected) {
      const site = getSites().find((s) => s.slug === selected)
      if (site) add(site.slug, Number(site.lat), Number(site.lon), site.name)
    }
    const preview = activeAccessPreviewSlug(store)
    if (preview) {
      const row = store.access.bySlug[preview]
      if (row) add(preview, Number(row.lat), Number(row.lon), row.name)
    }
    return [...out.values()]
  }

  /** Drop cached routes and POST warm after ``roads.yaml`` changes (custom roads, overrides). */
  async function rewarmAfterRoadsBookChange() {
    store.access.pendingRewarmAfterRoads = true
    const osm = store.access.osm
    const osmBusy =
      osm &&
      osm.status !== 'idle' &&
      osm.status !== 'ready' &&
      Number(osm.fraction) < 1 &&
      String(osm.phase || '').toLowerCase() !== 'ready'

    const jobs = []
    if (!osmBusy) {
      for (const target of accessRewarmTargets()) {
        const { slug, lat, lon, name } = target
        const meta =
          getSites().find((s) => s.slug === slug) || {
            slug,
            lat,
            lon,
            name: name || slug,
          }
        jobs.push(
          ensurePlaceAccessProfiles(slug, lat, lon, meta, {
            forceRewarm: true,
            isActive: () => siteAccessShowing(slug),
          }),
        )
      }
    }

    if (
      !osmBusy &&
      store.ui.createMode &&
      Number.isFinite(store.ui.createLat) &&
      Number.isFinite(store.ui.createLon)
    ) {
      const lat = store.ui.createLat
      const lon = store.ui.createLon
      store.access.draftLoading = true
      opts.updatePinOverlays?.()
      jobs.push(
        ensurePlaceAccessProfiles(
          DRAFT_VIEWSHED_SLUG,
          lat,
          lon,
          { slug: DRAFT_VIEWSHED_SLUG, name: 'New site', lat, lon },
          {
            forceRewarm: true,
            isActive: () =>
              !!store.ui.createMode && store.ui.createLat === lat && store.ui.createLon === lon,
            onReady: () => {
              if (store.ui.createLat === lat && store.ui.createLon === lon) {
                store.access.draftLoading = false
                opts.updatePinOverlays?.()
              }
            },
          },
        ).finally(() => {
          if (store.ui.createLat === lat && store.ui.createLon === lon) {
            store.access.draftLoading = false
            opts.updatePinOverlays?.()
          }
        }),
      )
    }

    if (jobs.length) {
      store.access.pendingRewarmAfterRoads = false
      await Promise.all(jobs)
    } else if (!osmBusy) {
      store.access.pendingRewarmAfterRoads = false
    }
  }

  /**
   * Fetch access for a site. GET reads the probe cache only; POST /warm computes a miss.
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

  function siteNeedsAccessOptions(slug) {
    return !!slug && !isEphemeralPlaceSlug(slug)
  }

  /** Booked sites need a non-empty ``options`` list, not just jeep/hike profiles. */
  function accessPayloadComplete(slug, access) {
    if (!accessHasRouteProfiles(access)) return false
    if (siteNeedsAccessOptions(slug)) {
      return Array.isArray(access.options) && access.options.length > 0
    }
    return true
  }

  function accessRowComplete(slug, row, lat, lon) {
    if (!accessRowMatchesCoords(row, lat, lon)) return false
    if (siteNeedsAccessOptions(slug)) {
      return Array.isArray(row.options) && row.options.length > 0
    }
    return true
  }

  /** @param {object|null|undefined} row @param {number} lat @param {number} lon */
  function accessRowMatchesCoords(row, lat, lon) {
    if (!accessRowLoaded(row)) return false
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return true
    return coordsMatchPair(Number(row.lat), Number(row.lon), lat, lon)
  }

  function clearPlaceAccess(slug) {
    if (!slug) return
    delete store.access.bySlug[slug]
    placeInflight.delete(slug)
    if (store.access.selected?.slug === slug) store.access.selected = null
    refreshLayers()
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

    if (opts.forceRewarm) {
      delete store.access.bySlug[slug]
      placeInflight.delete(slug)
    }

    const cached = store.access.bySlug[slug]
    if (!opts.forceRewarm && accessRowComplete(slug, cached, lat, lon)) {
      if (!opts.prefetchOnly && active()) opts.onReady?.()
      return
    }
    // Keep jeep/hike on screen while the route list is still warming.
    const keepProfiles = accessHasRouteProfiles(cached)

    if (placeInflight.has(slug)) {
      await placeInflight.get(slug)
      if (
        accessRowComplete(slug, store.access.bySlug[slug], lat, lon) ||
        (keepProfiles && accessHasRouteProfiles(store.access.bySlug[slug]))
      ) {
        if (!opts.prefetchOnly && active()) opts.onReady?.()
        return
      }
    }

    const preview = currentPreview()

    async function postWarmAccess() {
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
      const warmResp = await fetch(
        apiUrls.placeAccessWarmApiUrl(projectSlug, slug, {
          lat,
          lon,
          preview: preview.id || undefined,
        }),
        { method: 'POST', signal: preview.signal },
      )
      if (!warmResp.ok) {
        console.warn('place access warm failed', slug, warmResp.status)
        return
      }
      const warmed = await warmResp.json()
      if (warmed?.status === 'cancelled') return
      if (active() || opts.prefetchOnly) {
        ingestAccess(slug, warmed, meta)
      }
      if (!opts.prefetchOnly && active()) opts.onReady?.()
    }

    const task = (async () => {
      try {
        if (!active() && !opts.prefetchOnly) return

        if (opts.forceRewarm) {
          await postWarmAccess()
          return
        }

        if (!isEphemeralPlaceSlug(slug)) {
          const resp = await fetch(
            apiUrls.placeAccessApiUrl(projectSlug, slug, {
              lat,
              lon,
              preview: preview.id || undefined,
            }),
            { signal: preview.signal },
          )
          if (resp.ok) {
            const access = await resp.json()
            if (access?.status === 'cancelled') return
            if (active() || opts.prefetchOnly) {
              ingestAccess(slug, access, meta)
            }
            if (accessHasRouteProfiles(access)) {
              if (!opts.prefetchOnly && active()) opts.onReady?.()
            }
            if (accessPayloadComplete(slug, access)) {
              return
            }
            if (siteNeedsAccessOptions(slug) && accessHasRouteProfiles(access)) {
              await postWarmAccess()
              return
            }
          }
        }

        await postWarmAccess()
      } catch (err) {
        if (err?.name === 'AbortError') return
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
    const lat = Number(site.lat)
    const lon = Number(site.lon)
    if (accessRowComplete(site.slug, store.access.bySlug[site.slug], lat, lon)) {
      refreshLayers()
      return
    }
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    void ensurePlaceAccessProfiles(site.slug, lat, lon, site, {
      isActive: () => siteAccessShowing(site.slug),
    })
  }

  function ensureSitesAccess(sites) {
    for (const site of sites || []) ensureSiteAccess(site)
  }

  function ensureAccessForVisibleSites() {
    for (const site of getSites()) {
      if (!siteAccessShowing(site.slug)) continue
      const lat = Number(site.lat)
      const lon = Number(site.lon)
      if (accessRowComplete(site.slug, store.access.bySlug[site.slug], lat, lon)) continue
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

  function clearDraftAccess() {
    delete store.access.bySlug[DRAFT_VIEWSHED_SLUG]
    placeInflight.delete(DRAFT_VIEWSHED_SLUG)
    store.access.draftLoading = false
    store.access.draftAccessChoice = null
    refreshLayers()
  }

  /**
   * @param {string} slug
   * @param {string} optionId
   */
  async function selectAccessOption(slug, optionId) {
    const row = store.access.bySlug[slug]
    if (!row || !optionId) return
    if (isEphemeralPlaceSlug(slug)) {
      store.access.draftAccessChoice = optionId
      store.access.bySlug[slug] = rowWithAccessChoice({ ...row, access_choice: optionId }, optionId)
      if (store.access.selected?.slug === slug) {
        store.access.selected = store.access.bySlug[slug]
      }
      refreshLayers()
      return
    }
    await opts.patchSiteField?.(slug, { access_choice: optionId })
    store.access.bySlug[slug] = rowWithAccessChoice({ ...row, access_choice: optionId }, optionId)
    if (store.access.selected?.slug === slug) {
      store.access.selected = store.access.bySlug[slug]
    }
    refreshLayers()
  }

  /**
   * Place-access preview at a draft pin (no peak catalog gates).
   * @param {number} lat
   * @param {number} lon
   */
  function ensureDraftAccess(lat, lon) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    const existing = store.access.bySlug[DRAFT_VIEWSHED_SLUG]
    if (
      existing &&
      existing.lat === lat &&
      existing.lon === lon &&
      accessHasRouteProfiles(existing)
    ) {
      store.access.draftLoading = false
      refreshLayers()
      return
    }
    delete store.access.bySlug[DRAFT_VIEWSHED_SLUG]
    placeInflight.delete(DRAFT_VIEWSHED_SLUG)
    store.access.draftAccessChoice = null
    store.access.draftLoading = true
    opts.updatePinOverlays?.()
    void ensurePlaceAccessProfiles(
      DRAFT_VIEWSHED_SLUG,
      lat,
      lon,
      { slug: DRAFT_VIEWSHED_SLUG, name: 'New site', lat, lon },
      {
        isActive: () =>
          !!store.ui.createMode && store.ui.createLat === lat && store.ui.createLon === lon,
        onReady: () => {
          if (store.ui.createLat === lat && store.ui.createLon === lon) {
            store.access.draftLoading = false
            opts.updatePinOverlays?.()
          }
        },
      },
    ).finally(() => {
      if (store.ui.createLat === lat && store.ui.createLon === lon) {
        store.access.draftLoading = false
        opts.updatePinOverlays?.()
      }
    })
  }

  return {
    ensureSiteAccess,
    ensureDraftAccess,
    clearDraftAccess,
    clearPlaceAccess,
    ensurePlaceAccessProfiles,
    ensureSitesAccess,
    ensureAccessForVisibleSites,
    handleAccessEvent,
    handleOsmEvent,
    refreshLayers,
    ingestAccess,
    accessHasRouteProfiles,
    siteAccessShowing,
    isAccessVisible,
    setAccessVisible,
    applyAccessVisibilityForSite,
    selectAccessOption,
    rewarmAfterRoadsBookChange,
  }
}
