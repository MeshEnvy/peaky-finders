#!/usr/bin/env python3
"""Download ArcGIS Map Viewer feature layers into ``data/exclude/nv_arcgis_webmap/``.

Writes:

- ``gpkg/<slug>.gpkg`` — one GeoPackage per **polygon** layer (Peaky ``bundle.exclude`` input).
  Layer name is always ``exclusion``.
- ``kml/<slug>.kml`` — one standalone LIBKML file per layer (any geometry) for Google Earth toggles.
- ``manifest.json`` — titles, URLs, geometry types, feature counts, bundle eligibility.

Run once after cloning (datasets can be large)::

    poetry run python scripts/export_webmap_exclude_layers.py data.pjson

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

from peaky_finders.webmap_arcgis import (
    arcgis_geometry_exclude_eligible,
    fetch_layer_metadata,
    geojson_features_to_gdf,
    iter_arcgis_geojson_features,
    layer_definition_where,
    stable_layer_slug,
    walk_arcgis_feature_layers,
)


GPKG_LAYER = "exclusion"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Web map ArcGIS layers → exclude GPKG + per-layer KML")
    parser.add_argument(
        "webmap",
        nargs="?",
        type=Path,
        default=root / "data.pjson",
        help="ArcGIS web map JSON path",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=root / "data",
        help="Repo ``data/`` root (default: <repo>/data)",
    )
    parser.add_argument("--include-tables", action="store_true")
    parser.add_argument("--skip-hidden", action="store_true")
    parser.add_argument("--page-size", type=int, default=2000)
    parser.add_argument("--max-features-per-layer", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Fetch layer metadata only (geometry types); write manifest snippet to stdout",
    )
    args = parser.parse_args()

    wm_path = args.webmap.expanduser().resolve()
    if not wm_path.is_file():
        print(f"Not found: {wm_path}", file=sys.stderr)
        sys.exit(2)

    wm = json.loads(wm_path.read_text(encoding="utf-8"))
    if not isinstance(wm, dict):
        print("Web map root must be a JSON object", file=sys.stderr)
        sys.exit(2)

    layers = walk_arcgis_feature_layers(
        wm,
        include_tables=args.include_tables,
        skip_hidden=args.skip_hidden,
    )

    data_dir = args.data_dir.expanduser().resolve()
    out_root = data_dir / "exclude" / "nv_arcgis_webmap"
    gpkg_dir = out_root / "gpkg"
    kml_dir = out_root / "kml"

    manifest_layers: list[dict[str, object]] = []

    for layer in layers:
        url = str(layer["url"])
        title = str(layer.get("title") or Path(url).name)
        slug = stable_layer_slug(title, url)
        where = layer_definition_where(layer)
        try:
            meta = fetch_layer_metadata(url, timeout_s=args.timeout)
        except Exception as e:
            print(f"[meta-fail] {title}: {e}", file=sys.stderr)
            continue
        geom_type = meta.get("geometryType")
        if not isinstance(geom_type, str):
            geom_type = None
        bundle_ex = arcgis_geometry_exclude_eligible(geom_type)

        entry: dict[str, object] = {
            "title": title,
            "url": url,
            "slug": slug,
            "geometryType": geom_type,
            "bundle_exclude": bundle_ex,
            "relative_gpkg": f"exclude/nv_arcgis_webmap/gpkg/{slug}.gpkg",
            "relative_kml": f"exclude/nv_arcgis_webmap/kml/{slug}.kml",
        }

        if args.plan_only:
            manifest_layers.append(entry)
            continue

        feats: list[dict[str, object]] = []
        try:
            for feat in iter_arcgis_geojson_features(
                url,
                where=where,
                page_size=max(1, args.page_size),
                timeout_s=args.timeout,
                max_features=args.max_features_per_layer,
            ):
                feats.append(feat)
        except RuntimeError as e:
            print(f"[skip] {title}: {e}", file=sys.stderr)
            manifest_layers.append({**entry, "features": 0, "error": str(e)})
            continue

        gdf = geojson_features_to_gdf(feats)
        if gdf is None or gdf.empty:
            print(f"[skip] {title}: empty", file=sys.stderr)
            manifest_layers.append({**entry, "features": 0})
            continue

        if gdf.crs is None:
            gdf.set_crs("EPSG:4326", inplace=True)
        else:
            gdf = gdf.to_crs("EPSG:4326")

        kml_dir.mkdir(parents=True, exist_ok=True)
        kml_path = kml_dir / f"{slug}.kml"
        try:
            gdf.to_file(kml_path, driver="LIBKML")
        except Exception as e:
            print(f"[kml-fail] {title}: {e}", file=sys.stderr)

        if bundle_ex:
            gpkg_dir.mkdir(parents=True, exist_ok=True)
            gpkg_path = gpkg_dir / f"{slug}.gpkg"
            try:
                gdf.to_file(gpkg_path, driver="GPKG", layer=GPKG_LAYER)
            except Exception as e:
                print(f"[gpkg-fail] {title}: {e}", file=sys.stderr)
                manifest_layers.append({**entry, "features": len(gdf), "gpkg_error": str(e)})
                continue

        print(f"[ok] {title}: {len(gdf)} features (exclude_gpkg={bundle_ex})", file=sys.stderr)
        manifest_layers.append({**entry, "features": len(gdf)})

    manifest = {"webmap": wm_path.name, "layers": manifest_layers}
    if args.plan_only:
        print(json.dumps(manifest, indent=2))
        return

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(out_root / "manifest.json", file=sys.stderr)


if __name__ == "__main__":
    main()
