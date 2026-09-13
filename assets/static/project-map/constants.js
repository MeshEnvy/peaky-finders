/** Layer/source IDs and static defaults for the project map. */

export const TERRAIN_SOURCE = 'terrain-dem'
export const TERRAIN_HILLSHADE = 'terrain-hillshade'
export const BASEMAP_REFERENCE_SOURCE = 'basemap-reference'
export const BASEMAP_REFERENCE_LAYER = 'basemap-reference'
export const PEAKS_SOURCE = 'eligible-peaks'
export const PEAKS_ACCESS_SOURCE = 'eligible-peaks-access'
export const PEAKS_ACCESS_LINE = 'eligible-peaks-access-line'
export const PEAKS_ROAD_SOURCE = 'eligible-peaks-road'
export const PEAKS_ROAD_CIRCLE = 'eligible-peaks-road-circle'
export const PEAKS_JEEP_SOURCE = 'eligible-peaks-jeep'
export const PEAKS_JEEP_LINE = 'eligible-peaks-jeep-line'
export const PEAKS_PAVED_SOURCE = 'eligible-peaks-paved'
export const PEAKS_PAVED_CIRCLE = 'eligible-peaks-paved-circle'
export const PEAKS_CURSOR_SOURCE = 'eligible-peaks-cursor'
export const PEAKS_CURSOR_CIRCLE = 'eligible-peaks-cursor-circle'
export const PEAKS_RING_CIRCLE = 'eligible-peaks-difficulty-ring'
export const PEAKS_SYMBOL = 'eligible-peaks-symbol'
export const PEAKS_ICON_ID = 'peaky-logo-pin'
export const PEAKS_ICON_URL = '/favicon-96x96.png'
export const SITES_ACCESS_SOURCE = 'sites-access'
export const SITES_ACCESS_LINE = 'sites-access-line'
export const SITES_JEEP_SOURCE = 'sites-jeep'
export const SITES_JEEP_LINE = 'sites-jeep-line'
export const SITES_PAVED_SOURCE = 'sites-paved'
export const SITES_PAVED_CIRCLE = 'sites-paved-circle'
export const SITES_PARK_SOURCE = 'sites-park'
export const SITES_PARK_CIRCLE = 'sites-park-circle'
export const SITES_SOURCE = 'sites'
export const SITES_CIRCLE = 'sites-circle'
export const SITES_LABELS = 'sites-labels'
export const SITES_SELECTED = 'sites-selected'
export const LINKS_SOURCE = 'site-links'
export const LINKS_LAYER = 'site-links-line'
export const LINKS_LABELS_LAYER = 'site-links-label'
export const DRAFT_LINKS_SOURCE = 'draft-site-links'
export const DRAFT_LINKS_LAYER = 'draft-site-links-line'
export const DRAFT_LINKS_LABELS_LAYER = 'draft-site-links-label'
export const LINK_SOLVER_PEAKS_SOURCE = 'link-solver-peaks'
export const LINK_SOLVER_PEAKS_LAYER = 'link-solver-peaks-circle'
export const LINK_SOLVER_LINES_SOURCE = 'link-solver-lines'
export const LINK_SOLVER_LINES_LAYER = 'link-solver-lines-line'
export const LINK_SOLVER_LINES_LABELS_LAYER = 'link-solver-lines-label'
export const LINK_SOLVER_VIEWSHED_PREFIX = '_linksolver_'
export const ALTERNATES_CANDIDATES_SOURCE = 'alternates-candidates'
export const ALTERNATES_CANDIDATES_LAYER = 'alternates-candidates-circle'
export const ALTERNATES_CANDIDATES_LABELS_LAYER = 'alternates-candidates-label'
export const ALTERNATES_LINES_SOURCE = 'alternates-candidate-lines'
export const ALTERNATES_LINES_LAYER = 'alternates-candidate-lines-line'
export const ALTERNATES_LINES_LABELS_LAYER = 'alternates-candidate-lines-label'
export const FORTIFY_CANDIDATES_SOURCE = 'fortify-candidates'
export const FORTIFY_CANDIDATES_LAYER = 'fortify-candidates-circle'
export const FORTIFY_CANDIDATES_LABELS_LAYER = 'fortify-candidates-label'
export const FORTIFY_LINES_SOURCE = 'fortify-candidate-lines'
export const FORTIFY_LINES_LAYER = 'fortify-candidate-lines-line'
export const FORTIFY_LINES_LABELS_LAYER = 'fortify-candidate-lines-label'

/** Top-of-stack paint order after a basemap or overlay change. Peaks stay under sites and RF. */
export const SITE_STACK_RAISE_IDS = [
  PEAKS_JEEP_LINE,
  PEAKS_ACCESS_LINE,
  PEAKS_PAVED_CIRCLE,
  PEAKS_ROAD_CIRCLE,
  PEAKS_RING_CIRCLE,
  PEAKS_SYMBOL,
  PEAKS_CURSOR_CIRCLE,
  DRAFT_LINKS_LAYER,
  DRAFT_LINKS_LABELS_LAYER,
  LINKS_LAYER,
  LINKS_LABELS_LAYER,
  ALTERNATES_LINES_LAYER,
  ALTERNATES_LINES_LABELS_LAYER,
  ALTERNATES_CANDIDATES_LAYER,
  ALTERNATES_CANDIDATES_LABELS_LAYER,
  FORTIFY_LINES_LAYER,
  FORTIFY_LINES_LABELS_LAYER,
  FORTIFY_CANDIDATES_LAYER,
  FORTIFY_CANDIDATES_LABELS_LAYER,
  LINK_SOLVER_LINES_LAYER,
  LINK_SOLVER_LINES_LABELS_LAYER,
  LINK_SOLVER_PEAKS_LAYER,
  SITES_CIRCLE,
  SITES_LABELS,
  SITES_SELECTED,
  SITES_JEEP_LINE,
  SITES_ACCESS_LINE,
  SITES_PAVED_CIRCLE,
  SITES_PARK_CIRCLE,
]
export const PEAK_BIN_MIN_M = 500
export const PEAK_BIN_MAX_M = 1500
export const PEAK_BINS_ACROSS_VIEWPORT = 20

export const LAND_DEFAULT_FILL_COLOR = '#4a6cf7'
export const LAND_DEFAULT_FILL_OPACITY = 0.48
export const LAND_DEFAULT_LINE_COLOR = '#1e40af'
export const LAND_LINE_WIDTH = 1.25
export const LAND_PREVIEW_LINE_WIDTH = 2.5

export const VIEWSHED_OPACITY_DEFAULT = 0.75
export const VIEWSHED_OVERLAY_BATCH = 4
export const DRAFT_VIEWSHED_SLUG = '_draft'
export const ALTERNATE_VIEWSHED_SLUG = '_alternate'
export const FORTIFY_VIEWSHED_SLUG = '_fortify'
export const PEAK_VIEWSHED_SLUG = '_peak'
export const VIEWSHED_PREVIEW_QUALITY = 1
export const SKADI_DEM_SPACING_M = 30
export const VIEWSHED_QUALITY_MIN = 1
export const VIEWSHED_QUALITY_MAX = 5
export const VIEWSHED_RASTER_MIN = 128
export const VIEWSHED_RASTER_MAX = 4096
export const COORD_PREFETCH_MS = 350
export const DRAFT_MARKER_COLOR = '#fbbf24'

export const PITCH_TERRAIN_ON = 12
export const PITCH_TERRAIN_OFF = 6
export const SITE_FIT_BUFFER_KM = 30
export const MAP_STATE_SAVE_MS = 400
export const MAP_GLYPHS_URL =
  'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf'
export const MAP_TEXT_FONT = ['Noto Sans Regular']
export const MAP_LABEL_FONT = ['Noto Sans Medium']

export const PIN_LOAD_MARKER_OFFSET = [0, 10]

/** @param {string} projectSlug */
export function mapStateKey(projectSlug) {
  return `peaky.map.v1.${projectSlug}`
}

/** @param {string} projectSlug */
export function linkSolverPanelPosKey(projectSlug) {
  return `peaky.linkSolverPanelPos.${projectSlug}`
}

/** @param {string} slug */
export function isPreviewViewshedSlug(slug) {
  return (
    slug === FORTIFY_VIEWSHED_SLUG ||
    slug === ALTERNATE_VIEWSHED_SLUG ||
    slug.startsWith(LINK_SOLVER_VIEWSHED_PREFIX)
  )
}

/**
 * @param {Record<string, unknown>} simDefaults
 */
export function viewshedRadiusBounds(simDefaults) {
  return {
    min: Number(simDefaults?.radius_km_min) || 1,
    max: Number(simDefaults?.radius_km_max) || 100,
  }
}

/** @param {string} slug */
export function viewshedSourceId(slug) {
  return `viewshed-${slug}`
}

/** @param {string} slug */
export function viewshedLayerId(slug) {
  return `viewshed-${slug}-raster`
}
