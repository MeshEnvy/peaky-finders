"""Host-safe coverage input fingerprint (no AWS / raster deps).

Stable digest of propagation parameters for workspace grouping (``viewshed_workspace_digest``)
and viewshed metadata. Schema ``v`` bumps when normalization or coverage semantics change.
Version **6**: mandatory ``modem`` block; LoRa cutoff + RSS reliability margin hashed.

Coverage runs via native ``splatter`` on PATH.
Skadi tiles use global ``SPLAT_CACHE`` (default ``/.peaky/splat_cache`` in the container).
"""

from __future__ import annotations

import hashlib
import json

from peaky_finders.models import SplatCoverageRequest

# Bump when SPLAT invocation, normalization, or output semantics change.
SPLAT_CACHE_SCHEMA_VERSION = 6


def normalize_splat_request(request: SplatCoverageRequest) -> SplatCoverageRequest:
    """Normalize request fields that affect splatter output (radius cap, terrain resolution)."""
    r = request.model_copy()
    r.high_resolution = True
    if r.radius > 100000:
        r.radius = 100000
    r.fresnel_clearance_fraction = min(1.0, max(0.0, float(r.fresnel_clearance_fraction)))
    return r


def splat_input_sha256(request: SplatCoverageRequest) -> str:
    """Stable hash of inputs that determine coverage output (terrain aside)."""
    r = normalize_splat_request(request)
    blob = json.dumps(
        {"v": SPLAT_CACHE_SCHEMA_VERSION, "req": r.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
