"""Docker SPLAT run per site plus footprint polygon exports."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from PIL import Image

from peaky_finders import kml_bundle
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.sites_job import BundleKmlLayerStyle, CoverageProvider
from peaky_finders.splat_input_hash import splat_input_sha256
from peaky_finders.splat_ppm_to_png import (
    overlay_max_edge_from_env,
    write_splat_png_from_ppm,
)
from peaky_finders.splat_polygonize import (
    COVERAGE_GPKG_NAME,
    COVERAGE_KML_NAME,
    write_coverage_polygons,
)

TILE_CACHE_CONTAINER_PATH = "/splat_cache"


def coverage_docker_run_needed(*, data_dir: Path, force_splat: bool) -> bool:
    """True when ``run_splat_site_job`` would invoke Docker for this workspace."""
    if force_splat:
        return True
    return not splat_outputs_cache_valid(data_dir)


def splat_outputs_cache_valid(data_dir: Path) -> bool:
    """True when a Docker SPLAT re-run is unnecessary: manifest hash matches and SPLAT outputs exist."""
    req_path = data_dir / "request.json"
    manifest_path = data_dir / "manifest.json"
    kml = data_dir / "output.kml"
    ppm = data_dir / "output.ppm"
    if not (req_path.is_file() and manifest_path.is_file() and kml.is_file() and ppm.is_file()):
        return False
    try:
        req = SplatCoverageRequest.model_validate_json(req_path.read_text(encoding="utf-8"))
        want = splat_input_sha256(req)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValidationError):
        return False
    if manifest.get("splat_exit_code") != 0:
        return False
    if manifest.get("splat_input_sha256") != want:
        return False
    return True


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
    """Ensure ``splat.png`` from ``output.ppm`` when needed; always point ``output.kml`` at ``splat.png``."""
    ppm = data_dir / "output.ppm"
    png = data_dir / "splat.png"
    if not ppm.is_file():
        raise FileNotFoundError(f"Cannot build splat.png: missing {ppm} under {data_dir}")

    ppm_m = ppm.stat().st_mtime
    mx = overlay_max_edge_from_env()
    skip = False
    if png.is_file():
        png_m = png.stat().st_mtime
        with Image.open(png) as im:
            pw, ph = im.size
        oversized = mx is not None and max(pw, ph) > mx
        skip = not oversized and png_m >= ppm_m
    if not skip:
        print(f"Raster: {site_name.strip()} (splat.png ← output.ppm)", flush=True)
        write_splat_png_from_ppm(ppm_path=ppm, png_path=png)
    kml_bundle.point_splat_output_kml_at_png(data_dir / "output.kml")


def write_coverage_footprints(
    *,
    data_dir: Path,
    polygon_style: BundleKmlLayerStyle,
) -> bool:
    """Write ``coverage_area`` GPKG/KML from ``output.ppm`` and the SPLAT LatLonBox."""
    ppm = data_dir / "output.ppm"
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


@dataclass(frozen=True)
class SplatSiteResult:
    exit_code: int
    site_name: str
    data_dir: Path
    polygons_written: bool


def run_splat_site_job(
    *,
    site_name: str,
    image: str,
    provider: CoverageProvider,
    data_dir: Path,
    tile_cache_dir: Path,
    force_splat: bool,
    coverage_kml_style: BundleKmlLayerStyle,
    coverage_verbose: bool = False,
) -> SplatSiteResult:
    """Run SPLAT in Docker when needed; then raster PNG; then ``coverage_area`` vectors from PPM."""
    if not coverage_docker_run_needed(data_dir=data_dir, force_splat=force_splat):
        print(
            f"Coverage: {site_name.strip()} (skipped Docker; RF inputs unchanged — KMZ still rebuilt)",
            flush=True,
        )
        code = 0
    else:
        print(f"Coverage: {site_name.strip()}", flush=True)
        code = run_container(
            image=image,
            data_dir=data_dir,
            tile_cache_dir=tile_cache_dir,
            provider=provider,
            coverage_verbose=coverage_verbose,
        )

    if code != 0:
        return SplatSiteResult(
            exit_code=code,
            site_name=site_name,
            data_dir=data_dir,
            polygons_written=False,
        )

    print(f"KMZ prep: {site_name.strip()} — splat.png + coverage vectors (from PPM)...", flush=True)
    ensure_splat_raster_png(site_name=site_name, data_dir=data_dir)

    written = write_coverage_footprints(
        data_dir=data_dir,
        polygon_style=coverage_kml_style,
    )

    print(
        f"KMZ prep: {site_name.strip()} — done (coverage footprint: {'yes' if written else 'empty'})",
        flush=True,
    )

    return SplatSiteResult(
        exit_code=0,
        site_name=site_name,
        data_dir=data_dir,
        polygons_written=written,
    )
