#!/usr/bin/env python3
"""One-time import of EIP KMZ tower sites into a preset ``config.yaml``.

Filters placemarks to those inside the Nevada state boundary GDB layer, then
appends new ``sites:`` entries (skips slugs that already exist).

Run from repo root via the peaky:dev image (same GDAL deps as ``./peaky test``):

  docker run --rm \\
    -v "$PWD/peaky_finders:/app/peaky_finders" \\
    -v "${PEAKY_HOME:-$PWD/peaky_home}:/.peaky" -e PEAKY_HOME=/.peaky \\
    -w /app/peaky_finders \\
    --entrypoint bash peaky:dev \\
    -lc 'poetry run python scripts/import_eip_sites_once.py'

Default is dry-run (prints summary only). Add ``--apply`` to write ``config.yaml``.
Use ``--verbose`` for per-site progress.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from shapely import contains_xy, make_valid, unary_union

from peaky_finders.bundle_build import openfilegdb_dataset_path
from peaky_finders.sites_job import SiteType, _slugify_files_segment, update_preset_yaml_tree

KML_NS = "http://www.opengis.net/kml/2.2"
KML = f"{{{KML_NS}}}"
EIP_NAME_SUFFIX = " {EIP}"


@dataclass(frozen=True)
class EipSite:
    site_name: str
    asset_id: str
    lat: float
    lon: float
    state_code: str
    tower_type: str
    tower_height: str
    ground_elevation_m: float | None
    development_stage: str
    street_address: str
    city: str


def _default_peaky_home() -> Path:
    import os

    return Path(os.environ.get("PEAKY_HOME", Path.cwd().parent.parent / "peaky_home")).expanduser()


def _default_project_dir(peaky_home: Path) -> Path:
    return peaky_home / "projects" / "nevada"


def _parse_kmz(kmz_path: Path) -> list[EipSite]:
    with zipfile.ZipFile(kmz_path) as zf:
        kml_name = next((n for n in zf.namelist() if n.endswith(".kml")), None)
        if kml_name is None:
            raise ValueError(f"no .kml member in {kmz_path}")
        root = ET.fromstring(zf.read(kml_name))

    out: list[EipSite] = []
    for pm in root.iter(f"{KML}Placemark"):
        data: dict[str, str] = {}
        for sd in pm.iter(f"{KML}SimpleData"):
            data[sd.attrib["name"]] = (sd.text or "").strip()

        lat = _float_or_none(data.get("Lat"))
        lon = _float_or_none(data.get("Long"))
        if lat is None or lon is None:
            coords = pm.find(f".//{KML}Point/{KML}coordinates")
            if coords is not None and coords.text:
                parts = [p.strip() for p in coords.text.split(",")]
                if len(parts) >= 2:
                    lon = float(parts[0])
                    lat = float(parts[1])

        if lat is None or lon is None:
            continue

        out.append(
            EipSite(
                site_name=data.get("Site_Name", "").strip() or data.get("Asset_ID", "EIP site"),
                asset_id=data.get("Asset_ID", "").strip(),
                lat=float(lat),
                lon=float(lon),
                state_code=data.get("State_Province_Code", "").strip(),
                tower_type=data.get("Tower_Type", "").strip(),
                tower_height=data.get("Tower_Height", "").strip(),
                ground_elevation_m=_float_or_none(data.get("Ground_Elevation")),
                development_stage=data.get("Development_Stage", "").strip(),
                street_address=data.get("Street_Address", "").strip(),
                city=data.get("City", "").strip(),
            )
        )
    return out


def _float_or_none(raw: str | None) -> float | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _load_nevada_boundary_polygon(gdb_path: Path, *, layer: str) -> Any:
    with openfilegdb_dataset_path(gdb_path) as ds:
        raw = pyogrio.read_dataframe(ds, layer=layer)
    gdf = gpd.GeoDataFrame(raw, geometry="geometry", crs=raw.crs)
    if gdf.empty:
        raise ValueError(f"empty boundary layer {layer!r} in {gdb_path}")
    poly = make_valid(unary_union(gdf.geometry))
    if gdf.crs is not None and str(gdf.crs) != "EPSG:4326":
        poly = gpd.GeoSeries([poly], crs=gdf.crs).to_crs("EPSG:4326").iloc[0]
    return poly


def _filter_inside_boundary(sites: list[EipSite], boundary: Any) -> list[EipSite]:
    if not sites:
        return []
    lats = [s.lat for s in sites]
    lons = [s.lon for s in sites]
    mask = contains_xy(boundary, lons, lats)
    return [s for s, ok in zip(sites, mask) if ok]


def _site_slug(site: EipSite, existing: set[str]) -> str:
    asset = re.sub(r"[^a-z0-9]+", "", site.asset_id.lower())
    if asset:
        base = f"eip-{asset}"
    else:
        base = f"eip-{_slugify_files_segment(site.site_name)}"
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def _display_name(site: EipSite) -> str:
    name = site.site_name.strip() or site.asset_id.strip() or "EIP site"
    if not name.endswith(EIP_NAME_SUFFIX):
        name = f"{name}{EIP_NAME_SUFFIX}"
    return name


def _site_payload(site: EipSite, *, site_type: SiteType) -> dict[str, Any]:
    parts = [p for p in (site.street_address, site.city, site.state_code) if p]
    address = ", ".join(parts)
    rationale_bits = ["EIP site list import"]
    if site.development_stage:
        rationale_bits.append(site.development_stage)
    if site.asset_id:
        rationale_bits.append(f"Asset ID {site.asset_id}")
    if site.tower_type or site.tower_height:
        tower = site.tower_type
        if site.tower_height:
            tower = f"{tower} {site.tower_height} ft".strip()
        rationale_bits.append(tower)

    body: dict[str, Any] = {
        "type": site_type.value,
        "name": _display_name(site),
        "loc": [round(site.lat, 6), round(site.lon, 6)],
        "rationale": "; ".join(rationale_bits),
    }
    if site.ground_elevation_m is not None:
        body["elevation_m"] = round(site.ground_elevation_m, 1)
    if address:
        body["description"] = address
    return body


def _append_sites(
    preset_path: Path,
    entries: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> list[str]:
        sites_raw = root.get("sites")
        if sites_raw is None:
            sites_raw = {}
            root["sites"] = sites_raw
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")

        added: list[str] = []
        for slug, body in entries:
            if slug in sites_raw:
                continue
            sites_raw[slug] = body
            added.append(slug)
        return added

    return update_preset_yaml_tree(preset_path, mutator, validate=False)


def main(argv: list[str] | None = None) -> int:
    peaky_home = _default_peaky_home()
    project_dir = _default_project_dir(peaky_home)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        type=Path,
        default=project_dir / "config.yaml",
        help="Preset YAML to update (default: nevada config.yaml)",
    )
    parser.add_argument(
        "--kmz",
        type=Path,
        default=project_dir / "data" / "EIP Site List_Coal Creek_3_31_26 v2.csv.kmz",
        help="Source KMZ with EIP placemarks",
    )
    parser.add_argument(
        "--boundary-gdb",
        type=Path,
        default=project_dir / "data" / "State_of_Nevada_Boundary_74510041082641794",
        help="Nevada state boundary FileGDB directory",
    )
    parser.add_argument(
        "--boundary-layer",
        default="Nevada_State_Boundary",
        help="Polygon layer name inside the boundary GDB",
    )
    parser.add_argument(
        "--type",
        dest="site_type",
        choices=[t.value for t in SiteType if t != SiteType.SUGGESTED and t != SiteType.GOAL],
        default=SiteType.INSTALLED.value,
        help="Preset site type for imported entries (default: installed)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write matching sites into config.yaml (default: dry-run only)",
    )
    parser.add_argument("--verbose", action="store_true", help="Log per-site progress")
    args = parser.parse_args(argv)

    kmz_path = args.kmz.expanduser().resolve()
    preset_path = args.preset.expanduser().resolve()
    gdb_path = args.boundary_gdb.expanduser().resolve()
    site_type = SiteType(args.site_type)

    prefix = "import_eip:"
    t0 = time.monotonic()
    print(f"{prefix} start: kmz={kmz_path.name}, preset={preset_path}", flush=True)

    all_sites = _parse_kmz(kmz_path)
    print(f"{prefix} parsed {len(all_sites)} placemark(s) from KMZ", flush=True)

    print(
        f"{prefix} loading boundary {gdb_path.name} / {args.boundary_layer!r}",
        flush=True,
    )
    boundary = _load_nevada_boundary_polygon(gdb_path, layer=args.boundary_layer)
    inside = _filter_inside_boundary(all_sites, boundary)
    print(
        f"{prefix} AOI filter: {len(inside)} inside Nevada / {len(all_sites)} total "
        f"({len(all_sites) - len(inside)} outside)",
        flush=True,
    )

    existing_slugs: set[str] = set()
    entries: list[tuple[str, dict[str, Any]]] = []
    for i, site in enumerate(sorted(inside, key=lambda s: (s.site_name.lower(), s.asset_id)), start=1):
        slug = _site_slug(site, existing_slugs)
        body = _site_payload(site, site_type=site_type)
        entries.append((slug, body))
        if args.verbose:
            print(
                f"{prefix} [{i}/{len(inside)}] {slug}: {site.site_name!r} "
                f"({site.lat:.5f}, {site.lon:.5f})",
                flush=True,
            )

    if not args.apply:
        print(f"{prefix} dry-run: would add {len(entries)} site(s); re-run with --apply to write", flush=True)
        for slug, body in entries[:10]:
            loc = body["loc"]
            print(f"{prefix}   {slug}: {body['name']} @ {loc[0]}, {loc[1]}", flush=True)
        if len(entries) > 10:
            print(f"{prefix}   … and {len(entries) - 10} more", flush=True)
        elapsed = time.monotonic() - t0
        print(f"{prefix} done (dry-run, {elapsed:.1f}s)", flush=True)
        return 0

    added = _append_sites(preset_path, entries)
    skipped = len(entries) - len(added)
    elapsed = time.monotonic() - t0
    print(
        f"{prefix} wrote {len(added)} new site(s) to {preset_path}"
        + (f" ({skipped} slug collision(s) skipped)" if skipped else ""),
        flush=True,
    )
    print(f"{prefix} done ({elapsed:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
