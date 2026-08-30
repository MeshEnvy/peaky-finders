// @ts-check

/** @param {object} store @param {object|null} planState */
export function setSeekState(store, planState) {
  store.seek.state = planState
}

/** @param {object} store @param {boolean} scanning */
export function setSeekScanning(store, scanning) {
  store.seek.scanning = scanning
}

/** @param {object} store @param {boolean} open */
export function setSeekPanelOpen(store, open) {
  store.ui.seekPanelOpen = !!open
  store.seek.panelOpen = !!open
}

/** @param {object} store @param {boolean} active */
export function setSeekGoalPlacementMode(store, active) {
  store.seek.goalPlacementMode = !!active
}

/** @param {object} store @param {number|null} lat @param {number|null} lon */
export function setSeekPendingGoal(store, lat, lon) {
  store.seek.pendingGoalLat = lat
  store.seek.pendingGoalLon = lon
}

/** @param {object} store @param {string} slug */
export function setSeekStartSlug(store, slug) {
  store.seek.startSlug = slug || ''
}

/** @param {object} store @param {boolean} running */
export function setSeekRunning(store, running) {
  store.seek.running = !!running
}

/** @param {object} store @param {string} text */
export function setSeekStatusText(store, text) {
  store.seek.statusText = text || ''
}

const SEEK_PROGRESS_FALLBACK = {
  eligible_land: 'Building eligible land…',
  trim: 'Trimming to hop range and view…',
  dem: 'Loading Skadi DEM…',
  peak_scan: 'Scanning linkable peaks…',
  peak_links: 'Scanning linkable peaks…',
  rf: 'Checking goal and site links…',
  viewshed: 'Warming viewshed…',
  starting: 'Starting peak scan…',
}

/**
 * @param {object} store
 * @param {object|null} prog
 * @param {number} [scanStartedAt]
 */
export function updateSeekProgress(store, prog, scanStartedAt = 0) {
  const phase = prog?.phase || 'starting'
  const done = Number(prog?.done) || 0
  const total = Number(prog?.total) || 0
  let detail = prog?.detail || SEEK_PROGRESS_FALLBACK[phase] || 'Working…'
  const elapsed =
    scanStartedAt > 0 ? Math.floor((Date.now() - scanStartedAt) / 1000) : 0
  if (elapsed > 0) detail = `${detail} (${elapsed}s)`
  const indeterminate = !(total > 0)
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null
  store.seek.progress = {
    visible: store.seek.scanning,
    phase,
    done,
    total,
    detail,
    pct,
    indeterminate,
  }
}

/** @param {object} store */
export function clearSeekProgress(store) {
  store.seek.progress = {
    visible: false,
    phase: '',
    done: 0,
    total: 0,
    detail: '',
    pct: null,
    indeterminate: true,
  }
}

/** @param {object} store @param {boolean} scanning */
export function setSeekScanningWithProgress(store, scanning) {
  setSeekScanning(store, scanning)
  if (scanning) {
    store.seek.progress.visible = true
  } else {
    clearSeekProgress(store)
  }
}

/** Bump fetch epoch (invalidates in-flight seek candidate requests). @param {object} store */
export function bumpSeekFetchEpoch(store) {
  store.seek.fetchEpoch = (store.seek.fetchEpoch || 0) + 1
  return store.seek.fetchEpoch
}

/** Abort any in-flight seek fetch AbortController on the store. @param {object} store */
export function abortSeekFetch(store) {
  const ac = store.seek.fetchAbort
  store.seek.fetchAbort = null
  if (ac) ac.abort()
}

/**
 * Abort prior fetch, bump epoch, attach a fresh AbortController.
 * @param {object} store
 * @returns {{ epoch: number, signal: AbortSignal }}
 */
export function beginSeekFetchEpoch(store) {
  abortSeekFetch(store)
  const epoch = bumpSeekFetchEpoch(store)
  const ac = new AbortController()
  store.seek.fetchAbort = ac
  return { epoch, signal: ac.signal }
}

/** @param {object} store @param {number} epoch */
export function isSeekFetchCurrent(store, epoch) {
  return epoch === store.seek.fetchEpoch
}

/**
 * Invalidate in-flight seek work (abort + bump epoch). Does not clear timers.
 * @param {object} store
 */
export function invalidateSeekFetchEpoch(store) {
  abortSeekFetch(store)
  bumpSeekFetchEpoch(store)
}
