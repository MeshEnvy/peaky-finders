"""Legacy SPLAT! ITM coverage via external binaries."""

from __future__ import annotations

import gzip
import io
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine

from peaky_finders.core.preset import CoverageProvider, ensure_skadi_mirror_dir
from peaky_finders.core.rf.models import SplatCoverageRequest
from peaky_finders.core.viewshed import kml
from peaky_finders.core.viewshed.input_hash import (
    SPLAT_CACHE_SCHEMA_VERSION,
    normalize_splat_request,
    splat_input_sha256,
)
from peaky_finders.core.viewshed.providers.protocol import ViewshedCoverageProvider
from peaky_finders.core.viewshed.skadi_mirror import (
    DEFAULT_SKADI_BUCKET,
    DEFAULT_SKADI_PREFIX,
    fetch_skadi_hgt_gzip_bytes,
    skadi_mirror_resolve_root,
    skadi_mirror_sdf_path,
    skadi_mirror_tile_gz_path,
    skadi_unsigned_s3_client,
    skadi_write_bytes_atomic,
)

logger = logging.getLogger(__name__)


class SplatEngine:
    """Run SPLAT! in a scratch dir; copy SPLAT-shaped outputs into the workspace."""

    def __init__(
        self,
        splat_path: str,
        cache_dir: str | Path,
        bucket_name: str = DEFAULT_SKADI_BUCKET,
        bucket_prefix: str = DEFAULT_SKADI_PREFIX,
    ) -> None:
        if not os.path.isdir(splat_path):
            raise FileNotFoundError(f"SPLAT path {splat_path!r} is not a directory")

        self.splat_binary = os.path.join(splat_path, "splat")
        self.splat_hd_binary = os.path.join(splat_path, "splat-hd")
        self.srtm2sdf_binary = os.path.join(splat_path, "srtm2sdf")
        self.srtm2sdf_hd_binary = os.path.join(splat_path, "srtm2sdf-hd")

        for bin_path, label in [
            (self.splat_binary, "splat"),
            (self.splat_hd_binary, "splat-hd"),
            (self.srtm2sdf_binary, "srtm2sdf"),
            (self.srtm2sdf_hd_binary, "srtm2sdf-hd"),
        ]:
            if not os.path.isfile(bin_path) or not os.access(bin_path, os.X_OK):
                raise FileNotFoundError(f"{label!r} not found or not executable at {bin_path!r}")

        self.mirror_root = skadi_mirror_resolve_root(cache_dir)
        self.s3 = skadi_unsigned_s3_client()
        self.bucket_name = bucket_name
        self.bucket_prefix = bucket_prefix

    def run_coverage_to_workdir(
        self,
        request: SplatCoverageRequest,
        work_dir: Path,
        *,
        high_resolution: bool = True,
    ) -> None:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        run_dir = work_dir / "_run"
        if run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            tmpdir = str(run_dir)
            input_sha256 = splat_input_sha256(request)
            request = normalize_splat_request(request)

            required_tiles = self._calculate_required_terrain_tiles(
                request.lat, request.lon, request.radius
            )

            for tile_name, sdf_name, sdf_hd_name in required_tiles:
                tile_data = self._download_terrain_tile(tile_name)
                sdf_data = self._convert_hgt_to_sdf(
                    tile_data, tile_name, high_resolution=high_resolution
                )
                sdf_file = sdf_hd_name if high_resolution else sdf_name
                Path(tmpdir, sdf_file).write_bytes(sdf_data)

            Path(tmpdir, "tx.qth").write_bytes(
                self._create_splat_qth("tx", request.lat, request.lon, request.tx_height)
            )
            Path(tmpdir, "splat.lrp").write_bytes(
                self._create_splat_lrp(
                    ground_dielectric=request.ground_dielectric,
                    ground_conductivity=request.ground_conductivity,
                    atmosphere_bending=request.atmosphere_bending,
                    frequency_mhz=request.frequency_mhz,
                    radio_climate=request.radio_climate,
                    polarization=request.polarization,
                    situation_fraction=request.situation_fraction,
                    time_fraction=request.time_fraction,
                    tx_power=request.tx_power,
                    tx_gain=request.tx_gain,
                    system_loss=request.system_loss,
                )
            )
            Path(tmpdir, "splat.dcf").write_bytes(
                self._create_splat_dcf(
                    colormap_name=request.colormap,
                    min_dbm=request.min_dbm,
                    max_dbm=request.max_dbm,
                )
            )

            splat_cmd = [
                self.splat_hd_binary if high_resolution else self.splat_binary,
                "-t",
                "tx.qth",
                "-L",
                str(request.rx_height),
                "-metric",
                "-R",
                str(request.radius / 1000.0),
                "-sc",
                "-gc",
                str(request.clutter_height),
                "-ngs",
                "-N",
                "-o",
                "output.ppm",
                "-dbm",
                "-db",
                str(request.signal_threshold),
                "-kml",
                "-olditm",
            ]
            splat_result = subprocess.run(
                splat_cmd, cwd=tmpdir, capture_output=True, text=True, check=False
            )

            manifest: dict[str, object] = {
                "splat_exit_code": splat_result.returncode,
                "splat_input_sha256": input_sha256,
                "splat_cache_schema_version": SPLAT_CACHE_SCHEMA_VERSION,
                "splat_stdout_tail": splat_result.stdout[-8000:] if splat_result.stdout else "",
                "splat_stderr_tail": splat_result.stderr[-8000:] if splat_result.stderr else "",
            }

            if splat_result.returncode != 0:
                Path(work_dir, "manifest.json").write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                raise RuntimeError(
                    f"SPLAT! failed ({splat_result.returncode})\n"
                    f"stdout:\n{splat_result.stdout}\nstderr:\n{splat_result.stderr}"
                )

            kml_path = Path(tmpdir, "output.kml")
            ppm_path = Path(tmpdir, "output.ppm")
            if not kml_path.is_file() or not ppm_path.is_file():
                Path(work_dir, "manifest.json").write_text(
                    json.dumps(manifest, indent=2), encoding="utf-8"
                )
                raise RuntimeError("SPLAT did not produce output.kml / output.ppm")

            bbox = kml.parse_lat_lon_box(kml_path.read_bytes())
            manifest["bbox"] = bbox

            shutil.copy2(ppm_path, work_dir / "output.ppm")
            shutil.copy2(kml_path, work_dir / "output.kml")
            Path(work_dir, "manifest.json").write_text(
                json.dumps(manifest, indent=2), encoding="utf-8"
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _calculate_required_terrain_tiles(
        lat: float, lon: float, radius: float
    ) -> list[tuple[str, str, str]]:
        earth_radius = 6_378_137
        delta_deg = (radius / earth_radius) * (180 / math.pi)
        lat_min = lat - delta_deg
        lat_max = lat + delta_deg
        lon_min = lon - delta_deg / math.cos(math.radians(lat))
        lon_max = lon + delta_deg / math.cos(math.radians(lat))
        lat_min_tile = math.floor(lat_min)
        lat_max_tile = math.floor(lat_max)
        lon_min_tile = math.floor(lon_min)
        lon_max_tile = math.floor(lon_max)
        tile_names: list[tuple[str, str, str]] = []
        for lat_tile in range(lat_min_tile, lat_max_tile + 1):
            for lon_tile in range(lon_min_tile, lon_max_tile + 1):
                ns = "N" if lat_tile >= 0 else "S"
                ew = "E" if lon_tile >= 0 else "W"
                tile_name = f"{ns}{abs(lat_tile):02d}{ew}{abs(lon_tile):03d}.hgt.gz"
                sdf_filename = SplatEngine._hgt_filename_to_sdf_filename(
                    tile_name, high_resolution=False
                )
                sdf_hd_filename = SplatEngine._hgt_filename_to_sdf_filename(
                    tile_name, high_resolution=True
                )
                tile_names.append((tile_name, sdf_filename, sdf_hd_filename))
        return tile_names

    @staticmethod
    def _create_splat_qth(name: str, latitude: float, longitude: float, elevation: float) -> bytes:
        contents = (
            f"{name}\n"
            f"{latitude:.6f}\n"
            f"{abs(longitude) if longitude < 0 else 360 - longitude:.6f}\n"
            f"{elevation:.2f}\n"
        )
        return contents.encode("utf-8")

    @staticmethod
    def _create_splat_lrp(
        *,
        ground_dielectric: float,
        ground_conductivity: float,
        atmosphere_bending: float,
        frequency_mhz: float,
        radio_climate: Literal[
            "equatorial",
            "continental_subtropical",
            "maritime_subtropical",
            "desert",
            "continental_temperate",
            "maritime_temperate_land",
            "maritime_temperate_sea",
        ],
        polarization: Literal["horizontal", "vertical"],
        situation_fraction: float,
        time_fraction: float,
        tx_power: float,
        tx_gain: float,
        system_loss: float,
    ) -> bytes:
        climate_map = {
            "equatorial": 1,
            "continental_subtropical": 2,
            "maritime_subtropical": 3,
            "desert": 4,
            "continental_temperate": 5,
            "maritime_temperate_land": 6,
            "maritime_temperate_sea": 7,
        }
        polarization_map = {"horizontal": 0, "vertical": 1}
        erp_watts = 10 ** ((tx_power + tx_gain - system_loss - 30) / 10)
        contents = (
            f"{ground_dielectric:.3f}  ; Earth Dielectric Constant\n"
            f"{ground_conductivity:.6f}  ; Earth Conductivity\n"
            f"{atmosphere_bending:.3f}  ; Atmospheric Bending Constant\n"
            f"{frequency_mhz:.3f}  ; Frequency in MHz\n"
            f"{climate_map[radio_climate]}  ; Radio Climate\n"
            f"{polarization_map[polarization]}  ; Polarization\n"
            f"{situation_fraction / 100.0:.2f} ; Fraction of situations\n"
            f"{time_fraction / 100.0:.2f}  ; Fraction of time\n"
            f"{erp_watts:.2f}  ; ERP in Watts\n"
        )
        return contents.encode("utf-8")

    @staticmethod
    def _create_splat_dcf(colormap_name: str, min_dbm: float, max_dbm: float) -> bytes:
        cmap = plt.get_cmap(colormap_name)
        cmap_values = np.linspace(max_dbm, min_dbm, 32)
        cmap_norm = plt.Normalize(vmin=min_dbm, vmax=max_dbm)
        rgb_colors = (cmap(cmap_norm(cmap_values))[:, :3] * 255).astype(int)
        contents = "; SPLAT! Auto-generated DBM Signal Level Color Definition\n;\n"
        contents += "; Format: dBm: red, green, blue\n;\n"
        for value, rgb in zip(cmap_values, rgb_colors):
            contents += f"{int(value):+4d}: {rgb[0]:3d}, {rgb[1]:3d}, {rgb[2]:3d}\n"
        return contents.encode("utf-8")

    def _download_terrain_tile(self, tile_name: str) -> bytes:
        gz = skadi_mirror_tile_gz_path(self.mirror_root, tile_name)
        if gz.is_file():
            return gz.read_bytes()
        tile_data = fetch_skadi_hgt_gzip_bytes(
            self.s3,
            tile_name,
            bucket_name=self.bucket_name,
            bucket_prefix=self.bucket_prefix,
        )
        skadi_write_bytes_atomic(gz, tile_data)
        return tile_data

    @staticmethod
    def _hgt_filename_to_sdf_filename(hgt_filename: str, high_resolution: bool = False) -> str:
        lat = int(hgt_filename[1:3]) * (1 if hgt_filename[0] == "N" else -1)
        min_lon = int(hgt_filename[4:7]) - (-1 if hgt_filename[3] == "E" else 1)
        min_lon = 360 - min_lon if hgt_filename[3] == "E" else min_lon
        max_lon = 0 if min_lon == 359 else min_lon + 1
        return f"{lat}:{lat + 1}:{min_lon}:{max_lon}{'-hd.sdf' if high_resolution else '.sdf'}"

    def _convert_hgt_to_sdf(self, tile: bytes, tile_name: str, high_resolution: bool = False) -> bytes:
        sdf_filename = self._hgt_filename_to_sdf_filename(tile_name, high_resolution)
        sdf_path = skadi_mirror_sdf_path(self.mirror_root, sdf_filename)
        if sdf_path.is_file():
            return sdf_path.read_bytes()

        scratch = self.mirror_root / "_sdf_scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(scratch)) as tmpdir:
            hgt_path = os.path.join(tmpdir, tile_name.replace(".gz", ""))
            with gzip.GzipFile(fileobj=io.BytesIO(tile)) as gz_file:
                Path(hgt_path).write_bytes(gz_file.read())

            if not high_resolution:
                with rasterio.open(hgt_path) as src:
                    transform = src.transform * Affine.scale(3, 3)
                    data = src.read(
                        out_shape=(src.count, 1201, 1201),
                        resampling=Resampling.average,
                    )
                    meta = src.meta.copy()
                    meta.update({"transform": transform, "width": 1201, "height": 1201})
                with rasterio.open(hgt_path, "w", **meta) as dst:
                    dst.write(data)

            cmd = self.srtm2sdf_hd_binary if high_resolution else self.srtm2sdf_binary
            subprocess.run(
                [cmd, os.path.basename(tile_name.replace(".gz", ""))],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                check=True,
            )
            generated = os.path.join(tmpdir, sdf_filename)
            if not os.path.exists(generated):
                raise RuntimeError(f"Failed to generate .sdf file: {generated}")
            sdf_data = Path(generated).read_bytes()
            skadi_write_bytes_atomic(sdf_path, sdf_data)
            return sdf_data


def _splat_high_resolution_from_request(raw: dict[str, object]) -> bool:
    if "high_resolution" in raw:
        return bool(raw["high_resolution"])
    raster = raw.get("raster_dimension")
    if raster is not None:
        return int(raster) > 1200
    return True


class SplatViewshedProvider:
    name = CoverageProvider.SPLAT

    def run_site(self, *, data_dir: Path, coverage_verbose: bool = False) -> int:
        wd = Path(data_dir).expanduser().resolve()
        req_file = wd / "request.json"
        if not req_file.is_file():
            raise FileNotFoundError(f"Missing {req_file}")
        raw = json.loads(req_file.read_text(encoding="utf-8"))
        request = SplatCoverageRequest.model_validate(raw)
        splat_path = os.environ.get("SPLAT_PATH", "/opt/splat")
        if coverage_verbose:
            logging.getLogger(__name__).setLevel(logging.DEBUG)
        print(f"Coverage: SPLAT ({wd.name})", flush=True)
        service = SplatEngine(splat_path, cache_dir=str(ensure_skadi_mirror_dir()))
        try:
            service.run_coverage_to_workdir(
                request,
                wd,
                high_resolution=_splat_high_resolution_from_request(raw),
            )
        except Exception:
            logger.exception("SPLAT run failed")
            return 1
        return 0

    def run_batch(
        self,
        *,
        viewshed_root: Path,
        batch_jobs: int = 1,
        coverage_verbose: bool = False,
        requests_json: str | None = None,
    ) -> int:
        del batch_jobs, requests_json
        root = Path(viewshed_root).expanduser().resolve()
        workdirs = sorted(
            p.parent for p in root.glob("*/request.json") if p.parent.is_dir()
        )
        if not workdirs:
            single = root / "request.json"
            if single.is_file():
                workdirs = [root]
        if not workdirs:
            return 0
        print(f"Coverage batch: SPLAT {len(workdirs)} workspace(s)", flush=True)
        for wd in workdirs:
            rc = self.run_site(data_dir=wd, coverage_verbose=coverage_verbose)
            if rc != 0:
                return rc
        return 0


def splat_provider() -> ViewshedCoverageProvider:
    return SplatViewshedProvider()
