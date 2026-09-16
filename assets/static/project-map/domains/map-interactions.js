// @ts-check

import {
  ALTERNATES_CANDIDATES_LAYER,
  FORTIFY_CANDIDATES_LAYER,
  LINK_SOLVER_PEAKS_LAYER,
  LINKS_LAYER,
  PEAKS_RING_CIRCLE,
  PEAKS_SYMBOL,
  SITES_CIRCLE,
  SITES_LABELS,
} from '../constants.js'

const LONG_PRESS_MS = 500
const LONG_PRESS_MOVE_PX = 12
const SITE_HIT_PAD_PX = 12
const SITE_LAYER_IDS = [SITES_CIRCLE, SITES_LABELS]

/** @param {maplibregl.Map} map @param {{ x: number, y: number }} point */
function siteFeaturesAtPoint(map, point) {
  const pad = SITE_HIT_PAD_PX
  return map.queryRenderedFeatures(
    [
      [point.x - pad, point.y - pad],
      [point.x + pad, point.y + pad],
    ],
    { layers: SITE_LAYER_IDS },
  )
}

/**
 * Map pointer routing: hover cursor, click/select, create, edit move.
 * @param {object} ctx
 */
export function createMapInteractionsDomain(ctx) {
  const {
    store,
    getMap,
    getMapReady,
    getLinkSolver,
    getAlternates,
    getFortify,
    getLinks,
    syncMapCursor,
    setEditDraftCoords,
    onEditCoordsChanged,
    setAddPlacementMode,
    selectSite,
    deselectSite,
    selectPeak,
    deselectPeak,
    selectLink,
    deselectLink,
    openCreatePanel,
    beginCreateAtMapPoint,
  } = ctx

  let longPressTimer = null
  let longPressStart = null
  let installed = false

  function linkSolver() {
    return getLinkSolver?.()
  }

  function alternates() {
    return getAlternates?.()
  }

  function fortify() {
    return getFortify?.()
  }

  function links() {
    return getLinks?.()
  }

  function trySelectLinkSolverAtPoint(point) {
    const map = getMap()
    if (!store?.linkSolver?.panelOpen || !map.getLayer(LINK_SOLVER_PEAKS_LAYER)) {
      return false
    }
    const feats = map.queryRenderedFeatures(point, {
      layers: [LINK_SOLVER_PEAKS_LAYER],
    })
    if (!feats.length) return false
    linkSolver()?.selectLinkSolverPeak?.(feats[0])
    return true
  }

  function trySelectFortifyAtPoint(point) {
    const map = getMap()
    if (!store?.fortify?.active || !map.getLayer(FORTIFY_CANDIDATES_LAYER)) {
      return false
    }
    const feats = map.queryRenderedFeatures(point, {
      layers: [FORTIFY_CANDIDATES_LAYER],
    })
    if (!feats.length) return false
    fortify()?.selectFortifyCandidate?.(feats[0])
    return true
  }

  function trySelectAlternateAtPoint(point) {
    const map = getMap()
    if (!store?.alternates?.active || !map.getLayer(ALTERNATES_CANDIDATES_LAYER)) {
      return false
    }
    const altFeats = map.queryRenderedFeatures(point, {
      layers: [ALTERNATES_CANDIDATES_LAYER],
    })
    if (!altFeats.length) return false
    alternates()?.selectAlternateCandidate?.(altFeats[0])
    return true
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
      if (store.ui.addPlacementMode || store.ui.editMode) {
        map.getCanvas().style.cursor = 'crosshair'
        return
      }
      if (store?.fortify?.active && map.getLayer(FORTIFY_CANDIDATES_LAYER)) {
        const fortifyFeats = map.queryRenderedFeatures(ev.point, {
          layers: [FORTIFY_CANDIDATES_LAYER],
        })
        if (fortifyFeats.length) {
          map.getCanvas().style.cursor = 'pointer'
          return
        }
      }
      if (store?.alternates?.active && map.getLayer(ALTERNATES_CANDIDATES_LAYER)) {
        const altFeats = map.queryRenderedFeatures(ev.point, {
          layers: [ALTERNATES_CANDIDATES_LAYER],
        })
        if (altFeats.length) {
          map.getCanvas().style.cursor = 'pointer'
          return
        }
      }
      if (store?.linkSolver?.panelOpen && map.getLayer(LINK_SOLVER_PEAKS_LAYER)) {
        const hopFeats = map.queryRenderedFeatures(ev.point, {
          layers: [LINK_SOLVER_PEAKS_LAYER],
        })
        if (hopFeats.length) {
          map.getCanvas().style.cursor = 'pointer'
          return
        }
      }
      if (siteFeaturesAtPoint(map, ev.point).length) {
        map.getCanvas().style.cursor = 'pointer'
        return
      }
      if (map.getLayer(LINKS_LAYER)) {
        const linkFeats = map.queryRenderedFeatures(ev.point, { layers: [LINKS_LAYER] })
        if (linkFeats.length) {
          map.getCanvas().style.cursor = 'pointer'
          return
        }
      }
      syncMapCursor?.()
    })
    for (const peakLayer of [PEAKS_SYMBOL, PEAKS_RING_CIRCLE]) {
      map.on('mouseenter', peakLayer, () => {
        if (!map.getLayer(peakLayer)) return
        if (store.ui.addPlacementMode || store.ui.editMode) {
          map.getCanvas().style.cursor = 'crosshair'
          return
        }
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', peakLayer, () => {
        syncMapCursor?.()
      })
    }
    map.on('mouseenter', LINKS_LAYER, () => {
      if (store.ui.addPlacementMode || store.ui.editMode) {
        map.getCanvas().style.cursor = 'crosshair'
        return
      }
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', LINKS_LAYER, () => {
      syncMapCursor?.()
    })
    for (const layerId of SITE_LAYER_IDS) {
      map.on('mouseenter', layerId, () => {
        if (store.ui.addPlacementMode || store.ui.editMode) {
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
      if (trySelectLinkSolverAtPoint(ev.point)) return
      if (trySelectFortifyAtPoint(ev.point)) return
      if (trySelectAlternateAtPoint(ev.point)) return
      const siteFeats = siteFeaturesAtPoint(map, ev.point)
      if (siteFeats.length) {
        const slug = siteFeats[0].properties && siteFeats[0].properties.slug
        if (slug) {
          ev.preventDefault()
          if (store.ui.addPlacementMode) setAddPlacementMode(null)
          selectSite(slug)
        }
        return
      }
      const peakLayers = [PEAKS_SYMBOL, PEAKS_RING_CIRCLE].filter((id) => map.getLayer(id))
      if (peakLayers.length) {
        const peakFeats = map.queryRenderedFeatures(ev.point, { layers: peakLayers })
        if (peakFeats.length) {
          const slug = peakFeats[0].properties?.slug
          if (slug) {
            ev.preventDefault()
            if (store.ui.addPlacementMode) setAddPlacementMode(null)
            selectPeak?.(slug)
            return
          }
        }
      }
      const linkFeature = links()?.linkFeatureAtPoint?.(ev)
      if (linkFeature) {
        const props = linkFeature.properties || {}
        if (props.a && props.b) {
          ev.preventDefault()
          if (store.ui.addPlacementMode) setAddPlacementMode(null)
          selectLink?.(props.a, props.b)
          return
        }
      }
      if (store.ui.addPlacementMode) {
        openCreatePanel(ev.lngLat.lat, ev.lngLat.lng)
        return
      }
      if (store?.alternates?.active || store?.fortify?.active) return
      deselectPeak?.()
      deselectSite()
      deselectLink?.()
    })
    wireMapLongPress()
  }

  return { install }
}
