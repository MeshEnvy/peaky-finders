// @ts-check

/** Built-in land overlays computed from AOI / include / exclude. */

export const LAND_OVERLAY_SOURCE_ID = '_overlays'

export const LAND_OVERLAYS = [
  {
    id: 'eligible',
    key: 'eligible',
    role: 'eligible',
    label: 'Eligible',
    summary: 'Include − exclude, clipped to AOI',
    title: 'Land seek can use: (include − exclude) ∩ AOI',
    color: '#10b981',
    opacity: 0.38,
  },
]

export function landOverlaySpec(id) {
  return LAND_OVERLAYS.find((row) => row.id === id) || null
}

export function landOverlayLayerKey(id) {
  return `${LAND_OVERLAY_SOURCE_ID}/${id}`
}
