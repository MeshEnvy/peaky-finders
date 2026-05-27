"""Docker SPLAT run per site plus footprint polygon exports."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from peaky_finders import kml_bundle
from peaky_finders.sites_job import BundleKmlLayerStyle, CoverageProvider
from peaky_finders.splat_ppm_to_png import write_splat_png_from_ppm
from peaky_finders.splat_polygonize import (
    SPLAT_GPKG_NAME,
    SPLAT_KML_NAME,
    SPLAT_OUTPUT_PPM_BASENAME,
    write_coverage_polygons,
)

TILE_CACHE_CONTAINER_PATH = "/splat_cache"


def _limit_nested_blas_threads() -> None:
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(key, "1")


def footprint_vectorize_needed(data_dir: Path) -> bool:
    """True when ``output.ppm`` exists and ``splat.gpkg`` is missing or older than the PPM."""
    wd = Path(data_dir).expanduser().resolve()
    ppm = wd / SPLAT_OUTPUT_PPM_BASENAME
    gpkg = wd / SPLAT_GPKG_NAME
    if not ppm.is_file():
        return False
    if not gpkg.is_file():
        return True
    return ppm.stat().st_mtime > gpkg.stat().st_mtime


def _vectorize_footprint_worker(args: tuple[str, dict]) -> tuple[str, bool]:
    """Process-pool worker: vectorize one workspace when ``output.ppm`` is newer than GPKG."""
    workdir_str, style_dict = args
    wd = Path(workdir_str)
    if not footprint_vectorize_needed(wd):
        return workdir_str, True
    polygon_style = BundleKmlLayerStyle.model_validate(style_dict)
    ok = write_coverage_footprints(data_dir=wd, polygon_style=polygon_style)
    return workdir_str, ok


def vectorize_coverage_footprints_parallel(
    *,
    workdirs: list[Path] | tuple[Path, ...],
    polygon_style: BundleKmlLayerStyle,
    jobs: int = 1,
) -> None:
    """Vectorize ``output.ppm`` → ``splat.gpkg`` across independent workspaces (CPU-bound)."""
    pending = [Path(wd).expanduser().resolve() for wd in workdirs if footprint_vectorize_needed(wd)]
    if not pending:
        return

    workers = max(1, int(jobs))
    mx = min(workers, len(pending))
    style_dict = polygon_style.model_dump()
    print(
        f"footprint vectorize: {len(pending)} workspace(s), workers={mx}",
        flush=True,
    )
    if mx <= 1:
        for wd in pending:
            _vectorize_footprint_worker((str(wd), style_dict))
        return

    with ProcessPoolExecutor(max_workers=mx, initializer=_limit_nested_blas_threads) as pool:
        futs = {
            pool.submit(_vectorize_footprint_worker, (str(wd), style_dict)): wd for wd in pending
        }
        for fut in as_completed(futs):
            _wd, ok = fut.result()
            if not ok:
                raise RuntimeError(f"footprint vectorize failed under {_wd}")


def run_container(
    *,
    image: str,
    data_dir: Path,
    tile_cache_dir: Path,
    provider: CoverageProvider,
    coverage_verbose: bool = False,
) -> int:
    cmd = _coverage_docker_cmd(
        image=image,
        mount_dir=data_dir,
        tile_cache_dir=tile_cache_dir,
        provider=provider,
        coverage_verbose=coverage_verbose,
        splatter_subcommand=("run", "--work-dir", "/work"),
    )
    print("Running coverage in Docker...", flush=True)
    return subprocess.run(cmd, check=False).returncode


def run_batch_container(
    *,
    image: str,
    viewshed_root: Path,
    tile_cache_dir: Path,
    batch_jobs: int = 1,
    coverage_verbose: bool = False,
) -> int:
    """Run ``splatter run-batch`` with array ``request.json`` mounted at ``/work``."""
    cmd = _coverage_docker_cmd(
        image=image,
        mount_dir=viewshed_root,
        tile_cache_dir=tile_cache_dir,
        provider=CoverageProvider.LOS,
        coverage_verbose=coverage_verbose,
        splatter_subcommand=("run-batch", "--work-dir", "/work"),
        extra_env=(("SPLATTER_BATCH_JOBS", str(max(1, int(batch_jobs)))),),
    )
    print("Running coverage batch in Docker...", flush=True)
    return subprocess.run(cmd, check=False).returncode


def _coverage_docker_cmd(
    *,
    image: str,
    mount_dir: Path,
    tile_cache_dir: Path,
    provider: CoverageProvider,
    coverage_verbose: bool,
    splatter_subcommand: tuple[str, ...],
    extra_env: tuple[tuple[str, str], ...] = (),
) -> list[str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{Path(mount_dir).resolve()}:/work",
        "-v",
        f"{Path(tile_cache_dir).resolve()}:{TILE_CACHE_CONTAINER_PATH}",
    ]
    if provider == CoverageProvider.SPLAT:
        cmd.extend(["-e", "SPLAT_PATH=/opt/splat"])
        if coverage_verbose:
            cmd.extend(["-e", "LOG_LEVEL=DEBUG"])
    cmd.extend(["-e", f"SPLAT_CACHE={TILE_CACHE_CONTAINER_PATH}"])
    for key, val in extra_env:
        cmd.extend(["-e", f"{key}={val}"])
    cmd.append(image)
    cmd.extend(splatter_subcommand)
    if coverage_verbose and provider == CoverageProvider.LOS:
        cmd.append("--verbose")
    return cmd


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
    """Write ``splat.gpkg`` / ``splat.kml`` from ``output.ppm`` and the SPLAT LatLonBox."""
    ppm = data_dir / SPLAT_OUTPUT_PPM_BASENAME
    if not ppm.is_file():
        raise FileNotFoundError(f"Cannot vectorize footprint: missing {ppm}")
    bounds = load_splat_bbox(data_dir)
    return write_coverage_polygons(
        ppm_path=ppm,
        bbox=bounds,
        out_gpkg=data_dir / SPLAT_GPKG_NAME,
        out_kml=data_dir / SPLAT_KML_NAME,
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
