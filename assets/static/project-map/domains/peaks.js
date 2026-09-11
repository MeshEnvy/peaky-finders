// @ts-check

import * as apiUrls from '../api/urls.js'
import { ensurePeaksLayers, setPeaksLayerData } from '../map/peaks-layers.js'

/**
 * @param {object} opts
 * @param {string} opts.projectSlug
 * @param {() => maplibregl.Map} opts.getMap
 * @param {() => boolean} opts.getMapReady
 */
export function createPeaksDomain(opts) {
  const { projectSlug, getMap, getMapReady } = opts

  async function loadPeaks() {
    if (!getMapReady()) return
    const map = getMap()
    try {
      const resp = await fetch(apiUrls.peaksApiUrl(projectSlug))
      if (!resp.ok) return
      const data = await resp.json()
      await ensurePeaksLayers(map)
      setPeaksLayerData(map, data.peaks || [])
    } catch (err) {
      console.warn('peaks: catalog load failed', err)
    }
  }

  return { loadPeaks }
}
