"""Host-safe SPLAT input fingerprint (no SPLAT / AWS / raster deps).

Stable digest of propagation parameters for workspace grouping (``viewshed_workspace_digest``)
and SPLAT metadata. Schema ``v`` bumps when normalization or coverage semantics change
(e.g. SPLAT ITM vs splatter Fresnel/FSPL).
Version **6**: mandatory ``modem`` block for splatter; LoRa cutoff + RSS reliability margin hashed; SPLAT ignores ``modem``.

Coverage engines run as native binaries (``splatter`` on PATH, ``SPLAT_PATH`` for legacy SPLAT).
Skadi tiles use global ``SPLAT_CACHE`` (default ``/.peaky/splat_cache`` in the container).
"""

from __future__ import annotations

import hashlib
import json

from peaky_finders.models import SplatCoverageRequest

# Bump when SPLAT invocation, normalization, or output semantics change.
SPLAT_CACHE_SCHEMA_VERSION = 6


def normalize_splat_request(request: SplatCoverageRequest) -> SplatCoverageRequest:
    """Match ``Splat.run_coverage_to_workdir`` input shaping (radius cap, always SPLAT-HD terrain)."""
    r = request.model_copy()
    r.high_resolution = True
    if r.radius > 100000:
        r.radius = 100000
    r.fresnel_clearance_fraction = min(1.0, max(0.0, float(r.fresnel_clearance_fraction)))
    return r


def splat_input_sha256(request: SplatCoverageRequest) -> str:
    """Stable hash of inputs that determine SPLAT output (terrain aside)."""
    r = normalize_splat_request(request)
    blob = json.dumps(
        {"v": SPLAT_CACHE_SCHEMA_VERSION, "req": r.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
