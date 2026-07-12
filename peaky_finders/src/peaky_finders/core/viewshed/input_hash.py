"""Host-safe coverage input fingerprint (no AWS / raster deps).

Stable digest of propagation parameters for workspace grouping (``viewshed_workspace_digest``)
and viewshed metadata. Schema ``v`` bumps when normalization or coverage semantics change.
Version **7**: ``raster_dimension`` replaces legacy ``high_resolution``; LoRa cutoff + RSS margin hashed.

Hashing is implemented in the splatter PyO3 extension (single source of truth).
Skadi tiles use global ``SPLAT_CACHE`` (default ``/.peaky/splat_cache`` in the container).
"""

from __future__ import annotations

import json

from peaky_finders.core.rf.models import SplatCoverageRequest

try:
    from splatter import SPLAT_CACHE_SCHEMA_VERSION as _RUST_SCHEMA_VERSION
    from splatter import input_sha256 as _rust_input_sha256
except ImportError:  # pragma: no cover - dev without extension built
    _RUST_SCHEMA_VERSION = None
    _rust_input_sha256 = None

# Bump when SPLAT invocation, normalization, or output semantics change.
SPLAT_CACHE_SCHEMA_VERSION = 7


def normalize_splat_request(request: SplatCoverageRequest) -> SplatCoverageRequest:
    """Normalize request fields that affect splatter output (radius cap, raster bounds)."""
    r = request.model_copy()
    if r.radius > 100000:
        r.radius = 100000
    r.raster_dimension = min(4096, max(128, int(r.raster_dimension)))
    r.fresnel_clearance_fraction = min(1.0, max(0.0, float(r.fresnel_clearance_fraction)))
    return r


def splat_input_sha256(request: SplatCoverageRequest) -> str:
    """Stable hash of inputs that determine coverage output (terrain aside)."""
    if _rust_input_sha256 is not None:
        if _RUST_SCHEMA_VERSION is not None and int(_RUST_SCHEMA_VERSION) != SPLAT_CACHE_SCHEMA_VERSION:
            raise RuntimeError(
                "splatter extension schema "
                f"v{_RUST_SCHEMA_VERSION} != Python v{SPLAT_CACHE_SCHEMA_VERSION}"
            )
        normalized = normalize_splat_request(request)
        return _rust_input_sha256(json.dumps(normalized.model_dump(mode="json")))

    import hashlib

    r = normalize_splat_request(request)
    blob = json.dumps(
        {"v": SPLAT_CACHE_SCHEMA_VERSION, "req": r.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
