/** Layer/source IDs and static defaults for the project map. */

export const TERRAIN_SOURCE = 'terrain-dem'
export const TERRAIN_HILLSHADE = 'terrain-hillshade'
export const BASEMAP_REFERENCE_SOURCE = 'basemap-reference'
export const BASEMAP_REFERENCE_LAYER = 'basemap-reference'
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
export const SEEK_CANDIDATES_SOURCE = 'seek-candidates'
export const SEEK_CANDIDATES_LAYER = 'seek-candidates-circle'
export const SEEK_CANDIDATES_LABELS_LAYER = 'seek-candidates-label'
export const SEEK_LINES_SOURCE = 'seek-candidate-lines'
export const SEEK_LINES_LAYER = 'seek-candidate-lines-line'
export const SEEK_LINES_LABELS_LAYER = 'seek-candidate-lines-label'
export const SEEK_PATH_SOURCE = 'seek-path'
export const SEEK_PATH_LAYER = 'seek-path-line'
export const SEEK_GOAL_LINE_SOURCE = 'seek-goal-line'
export const SEEK_GOAL_LINE_LAYER = 'seek-goal-line'
export const SEEK_WEDGE_SOURCE = 'seek-goal-wedge'
export const SEEK_WEDGE_FILL_LAYER = 'seek-goal-wedge-fill'
export const SEEK_WEDGE_OUTLINE_LAYER = 'seek-goal-wedge-outline'
export const SEEK_ANCILLARY_LINES_SOURCE = 'seek-ancillary-lines'
export const SEEK_ANCILLARY_LINES_LAYER = 'seek-ancillary-lines-line'
export const SEEK_ANCILLARY_LINES_LABELS_LAYER = 'seek-ancillary-lines-label'

/** Top-of-stack paint order after a basemap or overlay change. */
export const SITE_STACK_RAISE_IDS = [
  DRAFT_LINKS_LAYER,
  DRAFT_LINKS_LABELS_LAYER,
  LINKS_LAYER,
  LINKS_LABELS_LAYER,
  SEEK_WEDGE_FILL_LAYER,
  SEEK_WEDGE_OUTLINE_LAYER,
  SEEK_ANCILLARY_LINES_LAYER,
  SEEK_ANCILLARY_LINES_LABELS_LAYER,
  SEEK_LINES_LAYER,
  SEEK_LINES_LABELS_LAYER,
  SEEK_PATH_LAYER,
  SEEK_GOAL_LINE_LAYER,
  SEEK_CANDIDATES_LAYER,
  SEEK_CANDIDATES_LABELS_LAYER,
  SITES_CIRCLE,
  SITES_LABELS,
  SITES_SELECTED,
]

export const SEEK_WEDGE_NEAR_DEG = 10
export const SEEK_WEDGE_FAR_DEG = 50
export const SEEK_ANCILLARY_LINKS_DEBOUNCE_MS = 450
export const SEEK_PLAN_SAVE_MS = 400
export const SEEK_PEAK_BIN_MIN_M = 500
export const SEEK_PEAK_BIN_MAX_M = 1500
export const SEEK_PEAK_BINS_ACROSS_VIEWPORT = 20
export const SEEK_SCAN_PIN = '__seek_scan__'
export const SEEK_PROGRESS_POLL_MS = 400
export const SEEK_GOAL_SAME_AS_START_M = 50
export const SEEK_HOP_VIEWSHED_PREFIX = '_seek_hop_'

export const LAND_DEFAULT_FILL_COLOR = '#4a6cf7'
export const LAND_DEFAULT_FILL_OPACITY = 0.48
export const LAND_DEFAULT_LINE_COLOR = '#1e40af'
export const LAND_LINE_WIDTH = 1.25
export const LAND_PREVIEW_LINE_WIDTH = 2.5

export const VIEWSHED_OPACITY_DEFAULT = 0.75
export const VIEWSHED_OVERLAY_BATCH = 4
export const DRAFT_VIEWSHED_SLUG = '_draft'
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
export function seekStateKey(projectSlug) {
  return `peaky.seek.v1.${projectSlug}`
}

/** @param {string} projectSlug */
export function seekRedoKey(projectSlug) {
  return `peaky.seek.redo.v1.${projectSlug}`
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
