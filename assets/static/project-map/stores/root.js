// @ts-check

import { reactive } from 'vue'
import {
  mapStateKey,
  seekStateKey,
  seekRedoKey,
  viewshedRadiusBounds,
  VIEWSHED_OPACITY_DEFAULT,
} from '../constants.js'
import { normalizeSiteFromApi } from './sites.js'

export { normalizeSiteFromApi } from './sites.js'

/**
 * @param {Record<string, unknown>} config
 * @param {Record<string, unknown>|null} savedMapState
 */
export function createRootStore(config, savedMapState) {
  const projectSlug = String(config.slug || '')
  const simDefaults = /** @type {Record<string, unknown>} */ (config.simulation || {})
  const { min: radiusMin, max: radiusMax } = viewshedRadiusBounds(simDefaults)
  const defaultRadiusKm = Number(simDefaults.radius_km) || 25
  const defaultQuality = Number(simDefaults.viewshed_quality) || 3

  const store = reactive({
    projectSlug,
    simulation: {
      radiusKm: defaultRadiusKm,
      quality: defaultQuality,
      radiusMin,
      radiusMax,
      defaults: simDefaults,
    },
    sites: {
      list: [],
      hidden: new Set(),
      tagFilterBypass: new Set(),
    },
    ui: {
      mapReady: false,
      restoring: true,
      selectedSlug: null,
      entityPanelOpen: savedMapState?.entityPanelOpen === true,
      entityPanelTab: savedMapState?.entityPanelTab === 'land' ? 'land' : 'sites',
      filterByViewport: savedMapState?.filterByViewport === true,
      viewportEpoch: 0,
      tagFilters: new Set(Array.isArray(savedMapState?.tagFilters) ? savedMapState.tagFilters : []),
      tagFilterMode: savedMapState?.tagFilterMode === 'or' ? 'or' : 'and',
      showSiteLinks: savedMapState?.showLinks ?? true,
      viewshedOpacity: Number(savedMapState?.viewshedOpacity) || VIEWSHED_OPACITY_DEFAULT,
      basemapKey: savedMapState?.basemap || 'street',
      terrainActive: false,
      createMode: false,
      createLat: null,
      createLon: null,
      editMode: false,
      editSlug: null,
      editLat: null,
      editLon: null,
      addPlacementMode: null,
      seekPanelOpen: false,
    },
    mapState: {
      saveTimer: null,
      keys: {
        map: mapStateKey(projectSlug),
        seek: seekStateKey(projectSlug),
        seekRedo: seekRedoKey(projectSlug),
      },
      saved: savedMapState,
    },
    viewshed: {
      visible: new Map(),
      loading: new Set(),
      pendingEpoch: new Map(),
      loadEpoch: 0,
      ready: new Set(),
      outboundLinksReady: new Set(),
      pinProgress: new Map(),
      opacity: Number(savedMapState?.viewshedOpacity) || VIEWSHED_OPACITY_DEFAULT,
    },
    links: {
      payload: null,
    },
    land: {
      sources: [],
      sidebar: { folders: [], unfiledSources: [] },
      aoiDigest: 'none',
      overlayDigest: 'none',
      visible: new Map(),
      sourceBatchVisible: new Map(),
      labelsVisible: new Map(),
      foldersCollapsed: new Map(),
      layerOrder: [],
      layerRows: [],
      panelRevision: 0,
      loadingKeys: new Set(),
      folderModal: {
        open: false,
        mode: 'create',
        folderId: null,
        name: '',
        error: '',
      },
    },
    modals: {
      bulkTag: {
        open: false,
        error: '',
        saving: false,
        tagInput: '',
        pending: {},
      },
      addSite: {
        open: false,
        name: '',
        coords: '',
        tags: [],
        tagInput: '',
        error: '',
        saving: false,
      },
      importSites: {
        open: false,
        error: '',
        status: 'Choose a file with Point placemarks.',
        tags: ['imported'],
        tagInput: '',
        points: [],
        skipped: 0,
        busy: false,
        saving: false,
        filterVisible: true,
        selectedIndex: -1,
        hasPayload: false,
        fileName: '',
        previewHidden: true,
      },
    },
    seek: {
      running: false,
      scanning: false,
      goalPlacementMode: false,
      panelOpen: false,
      pendingGoalLat: null,
      pendingGoalLon: null,
      startSlug: '',
      state: null,
      statusText: '',
      fetchEpoch: 0,
      fetchAbort: null,
      progress: {
        visible: false,
        phase: '',
        done: 0,
        total: 0,
        detail: '',
        pct: null,
        indeterminate: true,
      },
      convertModal: {
        open: false,
        namePrefix: 'Relay',
        tags: [],
        tagInput: '',
        error: '',
        saving: false,
      },
    },
    config: {
      ...config,
      seek: config.seek || {},
      land: config.land || {},
    },
  })

  if (savedMapState?.viewshedVisible && typeof savedMapState.viewshedVisible === 'object') {
    for (const [slug, visible] of Object.entries(savedMapState.viewshedVisible)) {
      store.viewshed.visible.set(slug, visible !== false)
    }
  }
  if (savedMapState?.hiddenSites && Array.isArray(savedMapState.hiddenSites)) {
    for (const slug of savedMapState.hiddenSites) store.sites.hidden.add(String(slug))
  }
  if (savedMapState?.landVisible) {
    for (const [k, v] of Object.entries(savedMapState.landVisible)) {
      store.land.visible.set(k, !!v)
    }
  }
  if (savedMapState?.landSourceBatchVisible) {
    for (const [k, v] of Object.entries(savedMapState.landSourceBatchVisible)) {
      store.land.sourceBatchVisible.set(k, !!v)
    }
  }
  if (savedMapState?.landLabelsVisible) {
    for (const [k, v] of Object.entries(savedMapState.landLabelsVisible)) {
      store.land.labelsVisible.set(k, !!v)
    }
  }
  if (savedMapState?.landFoldersCollapsed) {
    for (const [k, v] of Object.entries(savedMapState.landFoldersCollapsed)) {
      store.land.foldersCollapsed.set(k, !!v)
    }
  }
  if (Array.isArray(savedMapState?.landLayerOrder)) {
    store.land.layerOrder = [...savedMapState.landLayerOrder]
  }

  return store
}

/** @param {ReturnType<typeof createRootStore>} store */
export function initSitesFromConfig(store) {
  const raw = /** @type {unknown[]} */ (store.config.sites || [])
  store.sites.list = raw.map(normalizeSiteFromApi).filter(Boolean)
}

/** @param {ReturnType<typeof createRootStore>} store */
export function initLandFromConfig(store) {
  const land = /** @type {Record<string, unknown>} */ (store.config.land || {})
  const sources = Array.isArray(land.sources) ? [...land.sources] : []
  store.land.sources = sources
  store.land.aoiDigest =
    typeof land.aoiDigest === 'string' ? land.aoiDigest : 'none'
  store.land.overlayDigest =
    typeof land.overlayDigest === 'string' ? land.overlayDigest : 'none'
  const sidebar = land.sidebar && typeof land.sidebar === 'object' ? land.sidebar : {}
  store.land.sidebar = {
    folders: Array.isArray(sidebar.folders)
      ? sidebar.folders.map((folder) => ({
          id: folder.id,
          label: folder.label,
          sources: [...(folder.sources || [])],
        }))
      : [],
    unfiledSources: Array.isArray(sidebar.unfiledSources)
      ? [...sidebar.unfiledSources]
      : sources.map((s) => s.id).filter(Boolean),
  }
  /** @type {object[]} */
  const rows = []
  for (const src of sources) {
    const layers = Array.isArray(src.layers) ? src.layers : []
    for (const layer of layers) {
      const key =
        typeof layer === 'string'
          ? layer
          : layer?.key || layer?.name || layer?.id
      if (!key) continue
      const spec = typeof layer === 'object' && layer ? layer : { key }
      rows.push({
        sourceId: src.id,
        label: src.label || src.id,
        path: src.path,
        layerKey: String(key),
        spec,
      })
    }
  }
  store.land.layerRows = rows
  store.land.panelRevision = (store.land.panelRevision || 0) + 1
}
