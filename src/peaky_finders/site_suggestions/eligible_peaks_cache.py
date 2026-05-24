"""Disk cache of Skadi binned peaks over eligible land for site suggest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.pairwise_dem_peak import skadi_binned_peaks_in_polygon
from peaky_finders.site_suggestions.log import suggest_log, suggest_step
from peaky_finders.skadi_dem import skadi_tile_set_fingerprint

ELIGIBLE_PEAKS_FORMAT = "peaky_suggest_eligible_peaks/v1"
ELIGIBLE_PEAKS_ALGO = "skadi_binned_v1"


def eligible_peaks_cache_digest(
    *,
    eligible_sha: str,
    bin_size_m: float,
    eligible_ll: BaseGeometry,
) -> str:
    minx, miny, maxx, maxy = eligible_ll.bounds
    dem_fp = skadi_tile_set_fingerprint(minx, miny, maxx, maxy)
    body = "\n".join(
        (
            ELIGIBLE_PEAKS_FORMAT,
            ELIGIBLE_PEAKS_ALGO,
            str(eligible_sha),
            f"{float(bin_size_m):.3f}",
            dem_fp,
        )
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def eligible_peaks_cache_path(suggest_root: Path, digest: str) -> Path:
    return Path(suggest_root).expanduser().resolve() / "eligible_peaks" / f"{digest}.json"


def _peaks_to_json(peaks_llz: list[tuple[float, float, float]]) -> list[dict[str, float]]:
    return [{"lon": float(lon), "lat": float(lat), "elev_m": float(elev_m)} for lon, lat, elev_m in peaks_llz]


def _peaks_from_json(raw: list[dict]) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for row in raw:
        out.append((float(row["lon"]), float(row["lat"]), float(row["elev_m"])))
    return out


def read_eligible_peaks_cache(path: Path) -> list[tuple[float, float, float]] | None:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if body.get("format") != ELIGIBLE_PEAKS_FORMAT or body.get("algo") != ELIGIBLE_PEAKS_ALGO:
        return None
    peaks = body.get("peaks")
    if not isinstance(peaks, list):
        return None
    try:
        return _peaks_from_json(peaks)
    except (KeyError, TypeError, ValueError):
        return None


def write_eligible_peaks_cache(
    path: Path,
    *,
    digest: str,
    eligible_sha: str,
    bin_size_m: float,
    eligible_ll: BaseGeometry,
    peaks_llz: list[tuple[float, float, float]],
) -> Path:
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    minx, miny, maxx, maxy = eligible_ll.bounds
    body = {
        "format": ELIGIBLE_PEAKS_FORMAT,
        "algo": ELIGIBLE_PEAKS_ALGO,
        "digest": digest,
        "eligible_sha": eligible_sha,
        "bin_size_m": float(bin_size_m),
        "dem_fingerprint": skadi_tile_set_fingerprint(minx, miny, maxx, maxy),
        "n_peaks": len(peaks_llz),
        "peaks": _peaks_to_json(peaks_llz),
    }
    p.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def load_or_build_eligible_peaks(
    *,
    suggest_root: Path,
    eligible_sha: str,
    eligible_ll: BaseGeometry,
    dem_mirror_root: Path,
    bin_size_m: float,
    jobs: int = 1,
    verbose: bool = False,
) -> tuple[list[tuple[float, float, float]], bool]:
    """Return ``(peaks, from_cache)`` sorted by descending elevation."""
    digest = eligible_peaks_cache_digest(
        eligible_sha=eligible_sha,
        bin_size_m=bin_size_m,
        eligible_ll=eligible_ll,
    )
    cache_path = eligible_peaks_cache_path(suggest_root, digest)
    cached = read_eligible_peaks_cache(cache_path)
    if cached is not None:
        suggest_log(verbose, f"site suggest:     eligible peaks cache hit ({len(cached)} peaks) → {cache_path}")
        return cached, True

    suggest_log(
        verbose,
        f"site suggest:     eligible peaks cache miss (digest={digest})",
    )
    with suggest_step(verbose, "Skadi scan over eligible land"):
        peaks = skadi_binned_peaks_in_polygon(
            eligible_ll,
            dem_mirror_root,
            bin_size_m=float(bin_size_m),
            max_workers=max(1, int(jobs)),
            verbose=verbose,
            log_prefix="site suggest:       ",
        )
    with suggest_step(verbose, f"write eligible peaks cache ({len(peaks)} peaks)"):
        write_eligible_peaks_cache(
            cache_path,
            digest=digest,
            eligible_sha=eligible_sha,
            bin_size_m=bin_size_m,
            eligible_ll=eligible_ll,
            peaks_llz=peaks,
        )
        suggest_log(verbose, f"site suggest:     cache → {cache_path}")
    return peaks, False
