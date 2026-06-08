(function () {
  const config = window.PEAKY_PROJECT || {};
  const projectSlug = config.slug;
  const sites = config.sites || [];

  const TERRAIN_SOURCE = "terrain-dem";
  const TERRAIN_HILLSHADE = "terrain-hillshade";
  const BASEMAP_REFERENCE_SOURCE = "basemap-reference";
  const BASEMAP_REFERENCE_LAYER = "basemap-reference";
  const SITES_SOURCE = "sites";
  const SITES_CIRCLE = "sites-circle";
  const SITES_LABELS = "sites-labels";
  const LINKS_SOURCE = "site-links";
  const LINKS_LAYER = "site-links-line";
  const VIEWSHED_RASTER_OPACITY = 0.75;
  const PITCH_TERRAIN_ON = 12;
  const PITCH_TERRAIN_OFF = 6;

  const BASEMAPS = {
    street: {
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      maxzoom: 19,
    },
    topo: {
      tiles: ["https://tile.opentopomap.org/{z}/{x}/{y}.png"],
      maxzoom: 17,
    },
    satellite: {
      tiles: [
        "https://clarity.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      referenceTiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      ],
      maxzoom: 19,
    },
  };

  function basemapStyle(key) {
    const bm = BASEMAPS[key] || BASEMAPS.street;
    return {
      version: 8,
      glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
      sources: {
        basemap: {
          type: "raster",
          tiles: bm.tiles,
          tileSize: 256,
          maxzoom: bm.maxzoom,
        },
      },
      layers: [{ id: "basemap", type: "raster", source: "basemap" }],
    };
  }

  function terrainDemSourceSpec() {
    return {
      type: "raster-dem",
      tiles: [
        "https://elevation-tiles-prod.s3.amazonaws.com/v2/terrarium/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      maxzoom: 15,
      encoding: "terrarium",
    };
  }

  function sitesGeoJson() {
    return {
      type: "FeatureCollection",
      features: sites.map((site) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [site.lon, site.lat] },
        properties: { name: site.name, slug: site.slug, type: site.type },
      })),
    };
  }

  const map = new maplibregl.Map({
    container: "map",
    style: basemapStyle("street"),
    center: [-98.35, 39.5],
    zoom: 4,
    maxPitch: 85,
    pitch: 0,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");

  let mapReady = false;
  let terrainActive = false;

  function ensureTerrainSource() {
    if (map.getSource(TERRAIN_SOURCE)) return;
    map.addSource(TERRAIN_SOURCE, terrainDemSourceSpec());
  }

  function ensureHillshadeLayer() {
    if (map.getLayer(TERRAIN_HILLSHADE)) return;
    ensureTerrainSource();
    map.addLayer(
      {
        id: TERRAIN_HILLSHADE,
        type: "hillshade",
        source: TERRAIN_SOURCE,
        paint: {
          "hillshade-exaggeration": 0.35,
          "hillshade-shadow-color": "#0a0e14",
          "hillshade-highlight-color": "#ffffff",
          "hillshade-accent-color": "#64748b",
        },
      },
      "basemap",
    );
  }

  function removeHillshadeLayer() {
    if (map.getLayer(TERRAIN_HILLSHADE)) map.removeLayer(TERRAIN_HILLSHADE);
  }

  function removeTerrainSource() {
    removeHillshadeLayer();
    if (map.getSource(TERRAIN_SOURCE)) map.removeSource(TERRAIN_SOURCE);
  }

  function linksApiUrl() {
    return `/api/p/${projectSlug}/links`;
  }

  function setSiteLinksVisible(visible) {
    if (!mapReady || !map.getLayer(LINKS_LAYER)) return;
    map.setLayoutProperty(LINKS_LAYER, "visibility", visible ? "visible" : "none");
  }

  function addSiteLinksLayer(geojson) {
    if (!geojson || !geojson.features || !geojson.features.length) return;
    if (map.getSource(LINKS_SOURCE)) {
      map.getSource(LINKS_SOURCE).setData(geojson);
      setSiteLinksVisible(document.getElementById("show-links").checked);
      raiseSiteLayers();
      return;
    }
    map.addSource(LINKS_SOURCE, { type: "geojson", data: geojson });
    map.addLayer(
      {
        id: LINKS_LAYER,
        type: "line",
        source: LINKS_SOURCE,
        paint: {
          "line-color": ["case", ["get", "manual"], "#0d9488", "#4a6cf7"],
          "line-width": 2.5,
          "line-opacity": 0.85,
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
          visibility: document.getElementById("show-links").checked ? "visible" : "none",
        },
      },
      SITES_CIRCLE,
    );
    raiseSiteLayers();
  }

  async function loadSiteLinks() {
    try {
      const resp = await fetch(linksApiUrl());
      if (!resp.ok) return;
      const payload = await resp.json();
      if (payload && payload.geojson) addSiteLinksLayer(payload.geojson);
    } catch (_) {
      /* links optional */
    }
  }

  function raiseSiteLayers() {
    for (const id of [LINKS_LAYER, SITES_CIRCLE, SITES_LABELS]) {
      if (map.getLayer(id)) {
        try {
          map.moveLayer(id);
        } catch (_) {
          /* layer may be mid-remove */
        }
      }
    }
  }

  function showTerrainOverlays() {
    ensureTerrainSource();
    ensureHillshadeLayer();
    map.setTerrain({ source: TERRAIN_SOURCE, exaggeration: 1.35 });
    if (map.getLayer("basemap")) {
      map.setPaintProperty("basemap", "raster-opacity", 0.9);
    }
    raiseSiteLayers();
  }

  function hideTerrainOverlays() {
    map.setTerrain(null);
    removeTerrainSource();
    if (map.getLayer("basemap")) {
      map.setPaintProperty("basemap", "raster-opacity", 1);
    }
  }

  function syncTerrainFromPitch() {
    if (!mapReady) return;
    const pitch = map.getPitch();
    if (!terrainActive && pitch >= PITCH_TERRAIN_ON) {
      terrainActive = true;
      showTerrainOverlays();
    } else if (terrainActive && pitch <= PITCH_TERRAIN_OFF) {
      terrainActive = false;
      hideTerrainOverlays();
    }
  }

  function ensureBasemapReference(bm) {
    if (!bm.referenceTiles) return;
    if (!map.getSource(BASEMAP_REFERENCE_SOURCE)) {
      map.addSource(BASEMAP_REFERENCE_SOURCE, {
        type: "raster",
        tiles: bm.referenceTiles,
        tileSize: 256,
        maxzoom: bm.maxzoom,
      });
      map.addLayer(
        {
          id: BASEMAP_REFERENCE_LAYER,
          type: "raster",
          source: BASEMAP_REFERENCE_SOURCE,
        },
        map.getLayer(SITES_CIRCLE) ? SITES_CIRCLE : undefined,
      );
    } else {
      map.getSource(BASEMAP_REFERENCE_SOURCE).setTiles(bm.referenceTiles);
    }
    raiseSiteLayers();
  }

  function removeBasemapReference() {
    if (map.getLayer(BASEMAP_REFERENCE_LAYER)) map.removeLayer(BASEMAP_REFERENCE_LAYER);
    if (map.getSource(BASEMAP_REFERENCE_SOURCE)) map.removeSource(BASEMAP_REFERENCE_SOURCE);
  }

  function viewshedSourceId(slug) {
    return `viewshed-${slug}`;
  }

  function viewshedLayerId(slug) {
    return `viewshed-${slug}-raster`;
  }

  function viewshedMetaUrl(siteSlug) {
    return `/api/p/${projectSlug}/viewsheds/${siteSlug}`;
  }

  function addViewshedLayer(vs) {
    const sourceId = viewshedSourceId(vs.slug);
    const layerId = viewshedLayerId(vs.slug);
    if (map.getSource(sourceId)) return;
    map.addSource(sourceId, {
      type: "image",
      url: vs.url,
      coordinates: vs.coordinates,
    });
    map.addLayer({
      id: layerId,
      type: "raster",
      source: sourceId,
      paint: {
        "raster-opacity": VIEWSHED_RASTER_OPACITY,
        "raster-fade-duration": 0,
      },
    });
    raiseSiteLayers();
  }

  async function loadViewshedForSite(site) {
    try {
      const resp = await fetch(viewshedMetaUrl(site.slug));
      if (!resp.ok) return;
      const vs = await resp.json();
      if (vs && vs.url && vs.coordinates) addViewshedLayer(vs);
    } catch (_) {
      /* overlay optional */
    }
  }

  function loadAllViewsheds() {
    for (const site of sites) {
      void loadViewshedForSite(site);
    }
  }

  function addSiteLayers() {
    if (map.getSource(SITES_SOURCE)) {
      map.getSource(SITES_SOURCE).setData(sitesGeoJson());
      return;
    }
    map.addSource(SITES_SOURCE, { type: "geojson", data: sitesGeoJson() });
    map.addLayer({
      id: SITES_CIRCLE,
      type: "circle",
      source: SITES_SOURCE,
      paint: {
        "circle-radius": 7,
        "circle-color": "#4a6cf7",
        "circle-stroke-width": 2,
        "circle-stroke-color": "#fff",
      },
    });
    map.addLayer({
      id: SITES_LABELS,
      type: "symbol",
      source: SITES_SOURCE,
      layout: {
        "text-field": ["get", "name"],
        "text-size": 12,
        "text-offset": [0, -1.4],
        "text-anchor": "bottom",
        "text-font": ["Noto Sans Bold"],
        "text-allow-overlap": true,
      },
      paint: {
        "text-color": "#e8eaed",
        "text-halo-color": "#1a1a1a",
        "text-halo-width": 2,
      },
    });
  }

  function fitSites() {
    if (!sites.length) return;
    if (sites.length === 1) {
      map.setCenter([sites[0].lon, sites[0].lat]);
      map.setZoom(10);
      return;
    }
    const lons = sites.map((site) => site.lon);
    const lats = sites.map((site) => site.lat);
    map.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 48, bearing: 0, pitch: 0, maxZoom: 15 },
    );
  }

  function setBasemap(key) {
    const bm = BASEMAPS[key];
    if (!bm || !mapReady) return;
    const src = map.getSource("basemap");
    if (!src || typeof src.setTiles !== "function") return;
    src.setTiles(bm.tiles);
    map.setMaxZoom(bm.maxzoom);
    if (bm.referenceTiles) ensureBasemapReference(bm);
    else removeBasemapReference();
    raiseSiteLayers();
  }

  map.on("load", () => {
    mapReady = true;
    addSiteLayers();
    void loadSiteLinks();
    loadAllViewsheds();
    const basemapKey = document.getElementById("basemap").value;
    if (BASEMAPS[basemapKey].referenceTiles) {
      ensureBasemapReference(BASEMAPS[basemapKey]);
    }
    fitSites();
    if (terrainActive) showTerrainOverlays();
  });

  map.on("pitch", syncTerrainFromPitch);
  document.getElementById("basemap").addEventListener("change", (ev) => {
    setBasemap(ev.target.value);
  });
  document.getElementById("show-links").addEventListener("change", (ev) => {
    setSiteLinksVisible(ev.target.checked);
  });
})();
