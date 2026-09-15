// @ts-check

/** @param {object} store @param {string} slug */
export function isPeakMapVisible(store, slug) {
  return !store.peaks.hidden.has(slug)
}

/** @param {object} store */
export function visiblePeaksForMap(store) {
  return store.peaks.list.filter((peak) => isPeakMapVisible(store, peak.slug))
}

/** @param {object} store */
export function sortedPeaksList(store) {
  return [...store.peaks.list].sort((a, b) =>
    String(a.name || a.slug).localeCompare(String(b.name || b.slug)),
  )
}

/** @param {object} store */
export function hiddenPeakSlugs(store) {
  return [...store.peaks.hidden]
}
