"""Fetch AWS Skadi SRTM HGT tiles (gzip) for SPLAT and DEM tooling."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

VOID_SRTM = -32768

DEFAULT_SKADI_BUCKET = "elevation-tiles-prod"
DEFAULT_SKADI_PREFIX = "v2/skadi"

# SPLAT-produced terrain under mirror root — keep keys verbatim (POSIX-safe).
SKADI_MIRROR_SDF_SUBPARTS = Path("_peaky_derived") / "sdf"

logger = logging.getLogger(__name__)


def skadi_mirror_relative_key_safe(key: str) -> bool:
    return bool(key) and "/" not in key and "\\" not in key and not key.startswith(".")


def skadi_mirror_resolve_root(mirror_dir: str | Path) -> Path:
    """Absolute mirror directory; mkdir parent chain."""
    p = Path(mirror_dir).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def skadi_mirror_tile_gz_path(mirror_root: Path, tile_name: str) -> Path:
    if (
        not skadi_mirror_relative_key_safe(tile_name)
        or not str(tile_name).endswith(".hgt.gz")
    ):
        raise ValueError(f"unsafe or invalid Skadi tile mirror key: {tile_name!r}")
    return mirror_root / tile_name


def skadi_mirror_sdf_path(mirror_root: Path, sdf_filename: str) -> Path:
    if not skadi_mirror_relative_key_safe(sdf_filename) or not sdf_filename.endswith(
        ".sdf"
    ):
        raise ValueError(f"unsafe or invalid SPLAT sdf mirror key: {sdf_filename!r}")
    return mirror_root / SKADI_MIRROR_SDF_SUBPARTS / sdf_filename


def skadi_write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)


def skadi_unsigned_s3_client():  # type: ignore[no-untyped-def]
    return boto3.client("s3", config=Config(signature_version=UNSIGNED))


def fetch_skadi_hgt_gzip_bytes(
    s3_client,
    tile_name: str,
    *,
    bucket_name: str = DEFAULT_SKADI_BUCKET,
    bucket_prefix: str = DEFAULT_SKADI_PREFIX,
) -> bytes:
    """Download one Skadi tile (gzip blob) via public S3; same paths as SPLAT."""
    tile_dir_prefix = tile_name[:3]
    s3_key = f"{bucket_prefix}/{tile_dir_prefix}/{tile_name}"
    try:
        return s3_client.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchKey":
            raise
    s3_key = f"skadi/{tile_dir_prefix}/{tile_name}"
    return s3_client.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()


_SKADI_TILE_STEM_RE = re.compile(
    r"^([NS])(?P<lat>\d{2})([EW])(?P<lon>\d{3})(?:\.hgt)?$",
    re.IGNORECASE,
)


def skadi_tile_wgs84_bounds(tile_name: str) -> tuple[float, float, float, float]:
    """Axis-aligned EPSG:4326 bounds ``(minx, miny, maxx, maxy)`` for one 1° Skadi HGT cell.

    Accepts ``N36W117.hgt.gz``, ``N36W117.hgt``, or ``N36W117`` (same tiling as
    :func:`iter_skadi_tile_names_for_wgs84_bounds`).
    """
    stem = Path(tile_name).name.replace(".gz", "").removesuffix(".hgt")
    m = _SKADI_TILE_STEM_RE.match(stem)
    if not m:
        raise ValueError(f"not a Skadi tile name: {tile_name!r}")
    lat_deg = int(m.group("lat"))
    lon_deg = int(m.group("lon"))
    if m.group(1).upper() == "N":
        miny, maxy = float(lat_deg), float(lat_deg) + 1.0
    else:
        miny, maxy = float(-lat_deg), float(-lat_deg) + 1.0
    if m.group(3).upper() == "E":
        minx, maxx = float(lon_deg), float(lon_deg) + 1.0
    else:
        minx, maxx = float(-lon_deg), float(-lon_deg) + 1.0
    return (minx, miny, maxx, maxy)


def iter_skadi_tile_names_for_wgs84_bounds(
    minx: float, miny: float, maxx: float, maxy: float
) -> list[str]:
    """1° Skadi cells intersecting EPSG:4326 bbox (half-open tiling via floor, same regime as SPLAT)."""
    lon_min_tile = int(math.floor(minx))
    lon_max_tile = int(math.floor(maxx))
    lat_min_tile = int(math.floor(miny))
    lat_max_tile = int(math.floor(maxy))
    names: list[str] = []
    for lat_tile in range(lat_min_tile, lat_max_tile + 1):
        for lon_tile in range(lon_min_tile, lon_max_tile + 1):
            ns = "N" if lat_tile >= 0 else "S"
            ew = "E" if lon_tile >= 0 else "W"
            names.append(f"{ns}{abs(lat_tile):02d}{ew}{abs(lon_tile):03d}.hgt.gz")
    return names


DEM_PREFETCH_STAMP_FMT = "dem_prefetch_v1"


def skadi_tile_set_fingerprint_body(
    minx: float, miny: float, maxx: float, maxy: float
) -> str:
    tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    lines = "\n".join(sorted(tiles))
    return f"format={DEM_PREFETCH_STAMP_FMT}\ntiles\n{lines}\n"


def skadi_tile_set_fingerprint(minx: float, miny: float, maxx: float, maxy: float) -> str:
    body = skadi_tile_set_fingerprint_body(minx, miny, maxx, maxy)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def skadi_mirror_tile_cached(mirror_root: Path, tile_name: str) -> bool:
    try:
        path = skadi_mirror_tile_gz_path(mirror_root, tile_name)
    except ValueError:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def skadi_missing_mirror_tiles_for_bounds(
    *,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    mirror_root: Path,
) -> tuple[str, tuple[str, ...]]:
    """Return ``(tile_set_fingerprint, missing_tile_names)`` for the bbox."""
    tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    fp = skadi_tile_set_fingerprint(minx, miny, maxx, maxy)
    root = mirror_root.expanduser().resolve()
    missing = tuple(t for t in tiles if not skadi_mirror_tile_cached(root, t))
    return fp, missing


def read_dem_prefetch_stamp_fingerprint(stamp_path: Path) -> str | None:
    try:
        lines = stamp_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    return lines[0]


def write_dem_prefetch_stamp(stamp_path: Path, fingerprint: str) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(f"{fingerprint}\n", encoding="utf-8")


def fetch_skadi_hgt_tile_always(
    tile_name: str,
    splat_tile_cache_dir: str | Path,
    *,
    bucket_name: str = DEFAULT_SKADI_BUCKET,
    bucket_prefix: str = DEFAULT_SKADI_PREFIX,
) -> None:
    """Download one Skadi ``*.hgt.gz`` tile and write to the mirror (overwrites)."""
    tn = tile_name if str(tile_name).endswith(".hgt.gz") else f"{tile_name}.hgt.gz"
    mirror_root = skadi_mirror_resolve_root(splat_tile_cache_dir)
    cli = skadi_unsigned_s3_client()
    blob = fetch_skadi_hgt_gzip_bytes(cli, tn, bucket_name=bucket_name, bucket_prefix=bucket_prefix)
    skadi_write_bytes_atomic(skadi_mirror_tile_gz_path(mirror_root, tn), blob)


def prefetch_skadi_hgt_for_bounds(
    *,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    splat_tile_cache_dir: str | Path,
    max_workers: int = 16,
    bucket_name: str = DEFAULT_SKADI_BUCKET,
    bucket_prefix: str = DEFAULT_SKADI_PREFIX,
    verbose_log: Callable[[str], None] | None = None,
    log_parallel_errors: Callable[[str], None] | None = None,
) -> tuple[int, int, list[str]]:
    """Fetch Skadi tiles intersecting the bbox; skip non-empty mirror files.

    Returns ``(n_downloaded, n_skipped, errs)``.
    """
    all_tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    if not all_tiles:
        return (0, 0, [])
    mirror_root = skadi_mirror_resolve_root(splat_tile_cache_dir)
    to_fetch = [t for t in all_tiles if not skadi_mirror_tile_cached(mirror_root, t)]
    n_skipped = len(all_tiles) - len(to_fetch)
    errs: list[str] = []

    def _say(msg: str) -> None:
        if verbose_log is not None:
            verbose_log(msg)

    _say(
        f"dem prefetch bbox (4326)=({minx},{miny},{maxx},{maxy}) "
        f"tiles={len(all_tiles)} fetch={len(to_fetch)} cached={n_skipped} mirror={mirror_root}"
    )

    if not to_fetch:
        return (0, n_skipped, [])

    def _pull_one(tile_name: str) -> tuple[str, bytes]:
        cli = skadi_unsigned_s3_client()
        return tile_name, fetch_skadi_hgt_gzip_bytes(
            cli, tile_name, bucket_name=bucket_name, bucket_prefix=bucket_prefix
        )

    n_downloaded = 0
    worker_cap = max(1, min(max_workers, len(to_fetch)))
    with ThreadPoolExecutor(max_workers=worker_cap) as ex:
        futs = {ex.submit(_pull_one, t): t for t in to_fetch}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                tile_name, blob = fut.result()
                skadi_write_bytes_atomic(
                    skadi_mirror_tile_gz_path(mirror_root, tile_name), blob
                )
                n_downloaded += 1
                _say(f"dem prefetch downloaded {tile_name} ({len(blob)} bytes)")
            except Exception as e:  # noqa: BLE001
                msg = f"{t}: {e}"
                errs.append(msg)
                if log_parallel_errors is not None:
                    log_parallel_errors(msg)
                else:
                    logger.warning("dem prefetch failed: %s", msg)
    return (n_downloaded, n_skipped, errs)


def prefetch_skadi_hgt_for_bounds_fatal(
    *,
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    splat_tile_cache_dir: str | Path,
    max_workers: int = 16,
    verbose_log: Callable[[str], None] | None = None,
    log_parallel_errors: Callable[[str], None] | None = None,
) -> tuple[int, int, int]:
    """Prefetch tiles for bbox; return ``(tile_count, downloaded, skipped)``."""
    all_tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    n_downloaded, n_skipped, errs = prefetch_skadi_hgt_for_bounds(
        minx=minx,
        miny=miny,
        maxx=maxx,
        maxy=maxy,
        splat_tile_cache_dir=splat_tile_cache_dir,
        max_workers=max_workers,
        verbose_log=verbose_log,
        log_parallel_errors=log_parallel_errors,
    )
    if errs:
        preview = errs[:10]
        more = f" (+{len(errs) - 10} more)" if len(errs) > 10 else ""
        raise RuntimeError(f"dem prefetch failures ({len(errs)}): {'; '.join(preview)}{more}")
    return (len(all_tiles), n_downloaded, n_skipped)
