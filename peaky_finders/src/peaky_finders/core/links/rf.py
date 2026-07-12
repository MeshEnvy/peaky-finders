"""Pairwise RF link checks via in-process splatter session."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Sequence

from peaky_finders.core.preset.model import Preset
from peaky_finders.core.preset.paths import ensure_skadi_mirror_dir
from peaky_finders.core.rf.mapping import preset_to_request

if TYPE_CHECKING:
    from splatter import Session


def propagation_request_json(preset: Preset, *, lat: float = 0.0, lon: float = 0.0) -> str:
    """JSON propagation contract for splatter link evaluation (position is arbitrary)."""
    req = preset_to_request(preset, float(lat), float(lon))
    return req.model_dump_json(exclude_none=True)


def max_hop_range_m(preset: Preset) -> float:
    return float(preset.simulation.radius_km) * 1000.0


def splatter_session(*, verbose: bool = False) -> Session:
    from splatter import get_session

    mirror = str(ensure_skadi_mirror_dir())
    return get_session(mirror_root=mirror, verbose=verbose)


def ensure_dem_for_points(
    session: Session,
    points: Sequence[tuple[float, float]],
    *,
    buffer_m: float,
) -> None:
    if not points:
        return
    session.ensure_tiles_for_points(list(points), float(buffer_m))


def mutual_hop_viable(
    session: Session,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    rf_json: str,
) -> bool:
    return bool(
        session.link_mutual_viable(
            float(lat_a),
            float(lon_a),
            float(lat_b),
            float(lon_b),
            rf_json,
        )
    )


def mutual_hop_batch(
    session: Session,
    pairs: Sequence[tuple[float, float, float, float]],
    *,
    rf_json: str,
) -> list[bool]:
    if not pairs:
        return []
    return list(session.link_mutual_batch(list(pairs), rf_json))


def mutual_hop_batches(
    pairs: Sequence[tuple[float, float, float, float]],
    *,
    rf_json: str,
    chunk_size: int = 64,
    jobs: int = 1,
    verbose: bool = False,
) -> list[bool]:
    """Evaluate mutual-hop viability in fixed-size batches; parallel across batches when ``jobs > 1``."""
    if not pairs:
        return []
    size = max(1, int(chunk_size))
    chunks = [list(pairs[start : start + size]) for start in range(0, len(pairs), size)]
    workers = max(1, int(jobs))
    if verbose:
        print(
            f"RF hops: {len(pairs)} pair(s) in {len(chunks)} batch(es), workers={workers}",
            flush=True,
        )
    if workers <= 1 or len(chunks) <= 1:
        session = splatter_session(verbose=verbose)
        out: list[bool] = []
        for chunk in chunks:
            out.extend(mutual_hop_batch(session, chunk, rf_json=rf_json))
            if verbose:
                print(f"RF hops: {len(out)}/{len(pairs)} evaluated", flush=True)
        return out

    def _worker(chunk: list[tuple[float, float, float, float]]) -> list[bool]:
        session = splatter_session(verbose=False)
        return mutual_hop_batch(session, chunk, rf_json=rf_json)

    mx = min(workers, len(chunks))
    results: list[list[bool] | None] = [None] * len(chunks)
    done = 0
    with ThreadPoolExecutor(max_workers=mx) as pool:
        futs = {pool.submit(_worker, chunk): idx for idx, chunk in enumerate(chunks)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            done += 1
            evaluated = sum(len(chunk) for chunk in results if chunk is not None)
            if verbose:
                print(
                    f"RF hops: {evaluated}/{len(pairs)} evaluated ({done}/{len(chunks)} batch(es))",
                    flush=True,
                )
    return [item for chunk in results if chunk is not None for item in chunk]


def rf_json_for_preset(preset: Preset) -> str:
    """Cached-friendly RF JSON keyed only on preset propagation (not position)."""
    return propagation_request_json(preset)
