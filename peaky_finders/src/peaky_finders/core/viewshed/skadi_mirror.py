"""Skadi HGT tile mirror path helpers (fetch-on-miss owned by splatter Rust)."""

from __future__ import annotations

from pathlib import Path

SKADI_MIRROR_SDF_SUBPARTS = Path("_peaky_derived") / "sdf"


def skadi_mirror_relative_key_safe(key: str) -> bool:
    return bool(key) and "/" not in key and "\\" not in key and not key.startswith(".")


def skadi_mirror_resolve_root(mirror_dir: str | Path) -> Path:
    p = Path(mirror_dir).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def skadi_mirror_tile_gz_path(mirror_root: Path, tile_name: str) -> Path:
    if not skadi_mirror_relative_key_safe(tile_name) or not str(tile_name).endswith(".hgt.gz"):
        raise ValueError(f"unsafe or invalid Skadi tile mirror key: {tile_name!r}")
    return mirror_root / tile_name


def skadi_mirror_sdf_path(mirror_root: Path, sdf_filename: str) -> Path:
    if not skadi_mirror_relative_key_safe(sdf_filename) or not sdf_filename.endswith(".sdf"):
        raise ValueError(f"unsafe or invalid SPLAT sdf mirror key: {sdf_filename!r}")
    return mirror_root / SKADI_MIRROR_SDF_SUBPARTS / sdf_filename


def skadi_write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)
