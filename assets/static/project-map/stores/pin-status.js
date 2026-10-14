// @ts-check

/**
 * Per-pin job stack for draft placement progress. Each job id (access, osm, viewshed)
 * upserts in place; completing one (fraction >= 1) pops it and reverts display to the
 * newest remaining open job.
 */

/** @typedef {{ id: string, label: string, fraction: number, seq: number }} PinStatusJob */

/** @param {object} store */
function ensurePinStatus(store) {
  if (!store.pinStatus) {
    store.pinStatus = {
      /** @type {Map<string, Map<string, PinStatusJob>>} */
      jobsBySlug: new Map(),
      /** @type {Map<string, { label: string, fraction: number }|null>} */
      displayBySlug: new Map(),
      seq: 0,
    }
  }
  return store.pinStatus
}

/** @param {object} store @param {string} slug */
function jobsForSlug(store, slug) {
  const ps = ensurePinStatus(store)
  let map = ps.jobsBySlug.get(slug)
  if (!map) {
    map = new Map()
    ps.jobsBySlug.set(slug, map)
  }
  return map
}

/** @param {Map<string, PinStatusJob>} jobs */
function newestJob(jobs) {
  let best = null
  for (const job of jobs.values()) {
    if (!best || job.seq > best.seq) best = job
  }
  return best
}

/** @param {object} store @param {string} slug @param {Map<string, PinStatusJob>} jobs */
function syncDisplay(store, slug, jobs) {
  const ps = ensurePinStatus(store)
  const next = newestJob(jobs)
  if (next) {
    ps.displayBySlug.set(slug, { label: next.label, fraction: next.fraction })
  } else {
    ps.displayBySlug.delete(slug)
  }
}

/**
 * Record progress for a job on a pin. Latest note wins on screen. fraction >= 1 completes the job.
 * @param {object} store
 * @param {string} slug
 * @param {string} jobId
 * @param {string} label
 * @param {number} fraction
 * @returns {{ label: string, fraction: number }|null} current display after this note
 */
export function notePinStatusJob(store, slug, jobId, label, fraction) {
  if (!slug || !jobId) return null
  const ps = ensurePinStatus(store)
  const jobs = jobsForSlug(store, slug)
  const frac = Math.max(0, Math.min(1, Number(fraction) || 0))

  if (frac >= 1) {
    jobs.delete(jobId)
    syncDisplay(store, slug, jobs)
    if (jobs.size === 0) ps.jobsBySlug.delete(slug)
    return ps.displayBySlug.get(slug) ?? null
  }

  ps.seq += 1
  jobs.set(jobId, { id: jobId, label, fraction: frac, seq: ps.seq })
  const display = { label, fraction: frac }
  ps.displayBySlug.set(slug, display)
  return display
}

/**
 * @param {object} store
 * @param {string} slug
 * @returns {{ progressLabel: string, progressFraction: number }|null}
 */
export function pinStatusDisplay(store, slug) {
  const row = store.pinStatus?.displayBySlug?.get(slug)
  if (!row) return null
  return { progressLabel: row.label, progressFraction: row.fraction }
}

/** @param {object} store @param {string} slug */
export function clearPinStatusJobs(store, slug) {
  if (!store.pinStatus || !slug) return
  store.pinStatus.jobsBySlug.delete(slug)
  store.pinStatus.displayBySlug.delete(slug)
}
