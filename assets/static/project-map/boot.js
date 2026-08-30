// @ts-check

import { createRootStore, initSitesFromConfig, initLandFromConfig } from './stores/root.js'
import { runApp } from './domains/app.js'
import { mountPanels } from './panels/mount.js'
import { loadMapState } from './map/map-state.js'

/** Boot Peaky map UI: Vue reactive store + domain app + panel mounts. */
export function bootProjectMap() {
  const config = window.PEAKY_PROJECT || {}
  const projectSlug = String(config.slug || '')
  const savedMapState = loadMapState(projectSlug)

  const store = createRootStore(config, savedMapState)
  initSitesFromConfig(store)
  initLandFromConfig(store)

  const appApi = runApp({
    store,
    onStoreSync() {
      /* Vue panels react to store mutations */
    },
  })

  window.PEAKY_STORE = store

  mountPanels(store, appApi)
  appApi.renderLandPanel?.()
  appApi.renderEntityPanel?.()

  window.PEAKY_MAP = {
    reloadViewshedsForSimChange: () => appApi.reloadViewshedsForSimChange(),
    setViewshedSimulation: (radiusKm, quality) => appApi.setViewshedSimulation(radiusKm, quality),
  }

  return appApi
}
