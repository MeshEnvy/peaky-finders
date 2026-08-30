// @ts-check

import { SEEK_CANDIDATES_LAYER, SITES_CIRCLE, SITES_LABELS } from '../constants.js'

const LONG_PRESS_MS = 500
const LONG_PRESS_MOVE_PX = 12
const SITE_LAYER_IDS = [SITES_CIRCLE, SITES_LABELS]

/**
 * Map pointer routing: hover cursor, click/select, create, edit move, seek.
 * @param {object} ctx
 */
export function createMapInteractionsDomain(ctx) {
  const {
    store,
    getMap,
    getMapReady,
    getSeek,
    getSiteBySlug,
    syncMapCursor,
    setEditDraftCoords,
    onEditCoordsChanged,
    setAddPlacementMode,
    selectSite,
    deselectSite,
    openCreatePanel,
    beginCreateAtMapPoint,
  } = ctx

  let longPressTimer = null
  let longPressStart = null
  let installed = false

  function seek() {
    return getSeek?.()
  }

  function clearLongPressTimer() {
    if (longPressTimer) {
      clearTimeout(longPressTimer)
      longPressTimer = null
    }
    longPressStart = null
  }

  function lngLatFromClientPoint(clientX, clientY) {
    const map = getMap()
    const rect = map.getCanvas().getBoundingClientRect()
    return map.unproject([clientX - rect.left, clientY - rect.top])
  }

  function wireMapLongPress() {
    const canvas = getMap().getCanvas()
    canvas.addEventListener(
      'touchstart',
      (ev) => {
        if (store.ui.editMode || ev.touches.length !== 1) return
        const touch = ev.touches[0]
        longPressStart = { x: touch.clientX, y: touch.clientY }
        clearLongPressTimer()
        longPressTimer = setTimeout(() => {
          longPressTimer = null
          if (!longPressStart || !getMapReady()) return
          const { x, y } = longPressStart
          longPressStart = null
          const lngLat = lngLatFromClientPoint(x, y)
          beginCreateAtMapPoint(lngLat.lat, lngLat.lng)
        }, LONG_PRESS_MS)
      },
      { passive: true },
    )
    canvas.addEventListener(
      'touchmove',
      (ev) => {
        if (!longPressStart || !longPressTimer || ev.touches.length !== 1) return
        const touch = ev.touches[0]
        const dx = touch.clientX - longPressStart.x
        const dy = touch.clientY - longPressStart.y
        if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_PX) clearLongPressTimer()
      },
      { passive: true },
    )
    canvas.addEventListener('touchend', clearLongPressTimer)
    canvas.addEventListener('touchcancel', clearLongPressTimer)
  }

  function install() {
    if (installed) return
    installed = true
    const map = getMap()
    map.on('mousemove', (ev) => {
      if (store.ui.addPlacementMode || store.ui.editMode || store?.seek?.goalPlacementMode) {
        map.getCanvas().style.cursor = 'crosshair'
        return
      }
      const seekDomain = seek()
      if (seekDomain?.seekSessionActive()) {
        if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
          const seekFeats = map.queryRenderedFeatures(ev.point, {
            layers: [SEEK_CANDIDATES_LAYER],
          })
          if (seekFeats.length) {
            map.getCanvas().style.cursor = 'pointer'
            return
          }
        }
        const siteFeats = map.queryRenderedFeatures(ev.point, {
          layers: SITE_LAYER_IDS,
        })
        if (siteFeats.length) {
          const slug = siteFeats[0].properties?.slug
          if (slug && seekDomain.seekSiteCandidateSlugs().has(slug)) {
            map.getCanvas().style.cursor = 'pointer'
            return
          }
        }
      }
      syncMapCursor?.()
    })
    for (const layerId of SITE_LAYER_IDS) {
      map.on('mouseenter', layerId, () => {
        if (store.ui.addPlacementMode || store.ui.editMode || store?.seek?.goalPlacementMode) {
          map.getCanvas().style.cursor = 'crosshair'
          return
        }
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', layerId, () => {
        syncMapCursor?.()
      })
    }
    map.on('contextmenu', (ev) => {
      if (store.ui.editMode) return
      ev.preventDefault()
      beginCreateAtMapPoint(ev.lngLat.lat, ev.lngLat.lng)
    })
    map.on('click', (ev) => {
      if (store.ui.editMode) {
        setEditDraftCoords(ev.lngLat.lat, ev.lngLat.lng)
        onEditCoordsChanged()
        return
      }
      const seekDomain = seek()
      if (store?.seek?.goalPlacementMode && store?.ui?.seekPanelOpen) {
        seekDomain?.setSeekGoalAt(ev.lngLat.lat, ev.lngLat.lng)
        return
      }
      if (seekDomain?.seekSessionActive()) {
        if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
          const seekFeats = map.queryRenderedFeatures(ev.point, {
            layers: [SEEK_CANDIDATES_LAYER],
          })
          if (seekFeats.length) {
            const props = seekFeats[0].properties || {}
            if (!props.is_goal) {
              seekDomain.commitSeekCandidate(seekFeats[0])
            }
            return
          }
        }
        const siteSeekFeats = map.queryRenderedFeatures(ev.point, {
          layers: SITE_LAYER_IDS,
        })
        if (siteSeekFeats.length) {
          const slug = siteSeekFeats[0].properties?.slug
          if (slug && seekDomain.seekSiteCandidateSlugs().has(slug)) {
            const site = getSiteBySlug(slug)
            if (site) {
              seekDomain.commitSeekCandidate({
                geometry: { type: 'Point', coordinates: [site.lon, site.lat] },
                properties: {
                  is_site: true,
                  site_slug: slug,
                  site_name: site.name,
                  elev_m: site.height_m ?? null,
                },
              })
              return
            }
          }
        }
      }
      const feats = map.queryRenderedFeatures(ev.point, {
        layers: SITE_LAYER_IDS,
      })
      if (feats.length) {
        const slug = feats[0].properties && feats[0].properties.slug
        if (slug) {
          ev.preventDefault()
          if (store.ui.addPlacementMode) setAddPlacementMode(null)
          selectSite(slug)
        }
        return
      }
      if (store.ui.addPlacementMode) {
        openCreatePanel(ev.lngLat.lat, ev.lngLat.lng)
        return
      }
      deselectSite()
    })
    wireMapLongPress()
  }

  return { install }
}
