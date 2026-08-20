import * as C from './constants.js'
import {
  haversineMeters,
  bearingDeg,
  destinationPointLatLon,
  buildSeekWedgeFeature,
  buildSeekGoalLineFeature,
  seekWedgeHalfAngleDeg,
  formatCoord,
  parseCoordPairFromText,
  coordsUsableForMarker,
  slugifyName,
  lngLatBoundsFromPoints,
  padMapBounds,
  compareHuman,
  arrayBufferToBase64,
  haversineMeters as haversineMetersGeo,
} from './geo.js'

/** @param {Record<string, unknown>} scope */
export function installInteractions(scope) {
function wireMapLongPress() {
  const canvas = map.getCanvas();
  canvas.addEventListener(
    "touchstart",
    (ev) => {
      if (scope.editMode || ev.touches.length !== 1) return;
      const touch = ev.touches[0];
      longPressStart = { x: touch.clientX, y: touch.clientY };
      clearLongPressTimer();
      longPressTimer = setTimeout(() => {
        longPressTimer = null;
        if (!longPressStart || !scope.mapReady) return;
        const { x, y } = longPressStart;
        longPressStart = null;
        const lngLat = lngLatFromClientPoint(x, y);
        beginCreateAtMapPoint(lngLat.lat, lngLat.lng);
      }, LONG_PRESS_MS);
    },
    { passive: true },
  );
  canvas.addEventListener(
    "touchmove",
    (ev) => {
      if (!longPressStart || !longPressTimer || ev.touches.length !== 1)
        return;
      const touch = ev.touches[0];
      const dx = touch.clientX - longPressStart.x;
      const dy = touch.clientY - longPressStart.y;
      if (Math.hypot(dx, dy) > LONG_PRESS_MOVE_PX) clearLongPressTimer();
    },
    { passive: true },
  );
  canvas.addEventListener("touchend", clearLongPressTimer);
  canvas.addEventListener("touchcancel", clearLongPressTimer);
}

function wireMapInteractions() {
  const siteLayerIds = [SITES_CIRCLE, SITES_LABELS];
  map.on("mousemove", (ev) => {
    if (addPlacementMode || scope.editMode || seekGoalPlacementMode) {
      map.getCanvas().style.cursor = "crosshair";
      return;
    }
    if (scope.seekSessionActive()) {
      if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
        const seekFeats = map.queryRenderedFeatures(ev.point, {
          layers: [SEEK_CANDIDATES_LAYER],
        });
        if (seekFeats.length) {
          map.getCanvas().style.cursor = "pointer";
          return;
        }
      }
      const siteFeats = map.queryRenderedFeatures(ev.point, {
        layers: siteLayerIds,
      });
      if (siteFeats.length) {
        const slug = siteFeats[0].properties?.slug;
        if (slug && seekSiteCandidateSlugs.has(slug)) {
          map.getCanvas().style.cursor = "pointer";
          return;
        }
      }
    }
    scope.syncMapCursor();
  });
  for (const layerId of siteLayerIds) {
    map.on("mouseenter", layerId, () => {
      if (addPlacementMode || scope.editMode || seekGoalPlacementMode) {
        map.getCanvas().style.cursor = "crosshair";
        return;
      }
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", layerId, () => {
      scope.syncMapCursor();
    });
  }
  map.on("contextmenu", (ev) => {
    if (scope.editMode) return;
    ev.preventDefault();
    beginCreateAtMapPoint(ev.lngLat.lat, ev.lngLat.lng);
  });
  map.on("click", (ev) => {
    if (scope.editMode) {
      sitePanelEditLat.value = scope.formatCoord(ev.lngLat.lat);
      sitePanelEditLon.value = scope.formatCoord(ev.lngLat.lng);
      onEditCoordsChanged();
      return;
    }
    if (seekGoalPlacementMode && seekPanelOpen) {
      setSeekGoalAt(ev.lngLat.lat, ev.lngLat.lng);
      return;
    }
    if (scope.seekSessionActive()) {
      if (map.getLayer(SEEK_CANDIDATES_LAYER)) {
        const seekFeats = map.queryRenderedFeatures(ev.point, {
          layers: [SEEK_CANDIDATES_LAYER],
        });
        if (seekFeats.length) {
          const props = seekFeats[0].properties || {};
          if (!props.is_goal) {
            commitSeekCandidate(seekFeats[0]);
          }
          return;
        }
      }
      const siteSeekFeats = map.queryRenderedFeatures(ev.point, {
        layers: siteLayerIds,
      });
      if (siteSeekFeats.length) {
        const slug = siteSeekFeats[0].properties?.slug;
        if (slug && seekSiteCandidateSlugs.has(slug)) {
          const site = siteBySlug.get(slug);
          if (site) {
            commitSeekCandidate({
              geometry: { type: "Point", coordinates: [site.lon, site.lat] },
              properties: {
                is_site: true,
                site_slug: slug,
                site_name: site.name,
                elev_m: site.height_m ?? null,


  scope.wireMapLongPress = wireMapLongPress
  scope.wireMapInteractions = wireMapInteractions
}
