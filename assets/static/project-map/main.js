// @ts-check

import { initProjectMap } from './legacy.js'

/** @type {import('./types.js').MapContext & { reloadViewshedsForSimChange: () => void, setViewshedSimulation: (radiusKm: number, quality: number) => boolean }} */
const api = initProjectMap()
window.PEAKY_MAP = api
