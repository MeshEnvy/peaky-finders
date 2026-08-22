// @ts-check

import { initProjectMap } from './init.js'

/** @type {{ reloadViewshedsForSimChange: () => void, setViewshedSimulation: (radiusKm: number, quality: number) => boolean }} */
const api = initProjectMap()
window.PEAKY_MAP = api
