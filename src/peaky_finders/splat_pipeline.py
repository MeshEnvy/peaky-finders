"""Docker SPLAT run per site plus footprint polygon exports."""

from __future__ import annotations

import subprocess
from pathlib import Path

from peaky_finders import kml_bundle
from peaky_finders.sites_job import BundleKmlLayerStyle, CoverageProvider
from peaky_finders.splat_ppm_to_png import write_splat_png_from_ppm
from peaky_finders.splat_polygonize import (
    COVERAGE_GPKG_NAME,
    COVERAGE_KML_NAME,
    SPLAT_OUTPUT_PPM_BASENAME,
    write_coverage_polygons,
)

TILE_CACHE_CONTAINER_PATH = "/splat_cache"


def run_container(
    *,
    image: str,
    data_dir: Path,
    tile_cache_dir: Path,
    provider: CoverageProvider,
    coverage_verbose: bool = False,
) -> int:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{data_dir.resolve()}:/work",
        "-v",
        f"{tile_cache_dir.resolve()}:{TILE_CACHE_CONTAINER_PATH}",
    ]
    if provider == CoverageProvider.SPLAT:
        cmd.extend(["-e", "SPLAT_PATH=/opt/splat"])
        if coverage_verbose:
            cmd.extend(["-e", "LOG_LEVEL=DEBUG"])
    cmd.extend(
        [
            "-e",
            f"SPLAT_CACHE={TILE_CACHE_CONTAINER_PATH}",
            image,
        ]
    )
    # LOS: ``splatter run [--verbose]``; SPLAT: ``docker_entry`` + optional ``LOG_LEVEL`` above.
    if provider == CoverageProvider.LOS:
        cmd.extend(["run", "--work-dir", "/work"])
        if coverage_verbose:
            cmd.append("--verbose")
    else:
        cmd.extend(["--work-dir", "/work"])
    print("Running coverage in Docker...", flush=True)
    return subprocess.run(cmd, check=False).returncode


def load_splat_bbox(data_dir: Path) -> dict[str, float]:
    manifest_path = data_dir / "manifest.json"
    bounds = kml_bundle.load_bounds_from_manifest(manifest_path)
    if bounds is not None:
        return bounds
    kml_raw = data_dir / "output.kml"
    if not kml_raw.is_file():
        raise FileNotFoundError(f"Missing bbox: {manifest_path} and {kml_raw}")
    return kml_bundle.parse_lat_lon_box(kml_raw.read_bytes())


def ensure_splat_raster_png(*, site_name: str, data_dir: Path) -> None:
    """Build ``splat.png`` from ``output.ppm``; point ``output.kml`` at ``splat.png``."""
    ppm = data_dir / SPLAT_OUTPUT_PPM_BASENAME
    png = data_dir / "splat.png"
    if not ppm.is_file():
        raise FileNotFoundError(f"Cannot build splat.png: missing {ppm} under {data_dir}")

    print(f"Raster: {site_name.strip()} (splat.png ← output.ppm)", flush=True)
    write_splat_png_from_ppm(ppm_path=ppm, png_path=png)
    kml_bundle.point_splat_output_kml_at_png(data_dir / "output.kml")


def write_coverage_footprints(
    *,
    data_dir: Path,
    polygon_style: BundleKmlLayerStyle,
) -> bool:
    """Write ``coverage_area`` GPKG/KML from ``output.ppm`` and the SPLAT LatLonBox."""
    ppm = data_dir / SPLAT_OUTPUT_PPM_BASENAME
    if not ppm.is_file():
        raise FileNotFoundError(f"Cannot vectorize footprint: missing {ppm}")
    bounds = load_splat_bbox(data_dir)
    return write_coverage_polygons(
        ppm_path=ppm,
        bbox=bounds,
        out_gpkg=data_dir / COVERAGE_GPKG_NAME,
        out_kml=data_dir / COVERAGE_KML_NAME,
        polygon_style=polygon_style,
    )


def run_viewshed_docker_only(
    *,
    site_name: str,
    image: str,
    provider: CoverageProvider,
    data_dir: Path,
    tile_cache_dir: Path,
    coverage_verbose: bool = False,
) -> int:
    """Run coverage Docker/SPLAT in *data_dir*; leaves ``output.ppm`` (+ sidecars from the engine)."""

    print(f"Coverage: {site_name.strip()}", flush=True)
    return run_container(
        image=image,
        data_dir=data_dir,
        tile_cache_dir=tile_cache_dir,
        provider=provider,
        coverage_verbose=coverage_verbose,
    )
