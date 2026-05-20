"""Host-safe SPLAT input fingerprint (no SPLAT / AWS / raster deps).

Stable hash for skipping reruns when RF inputs are unchanged. Schema ``v`` bumps when
normalization or coverage semantics change (e.g. SPLAT ITM vs splatter Fresnel/FSPL).
Version **5**: splatter uses ITU-R P.526 knife-edge excess loss from terrain/Fresnel violation instead of hard LOS blocking.

Use Docker image ``splatter:latest`` by default; override with ``PEAKY_SPLAT_IMAGE``.
Default coverage image: ``docker build -t splatter:latest splatter/``.
Legacy SPLAT image: ``docker build -f Dockerfile.splat -t peaky-finders-splat:latest``.
"""

from __future__ import annotations

import hashlib
import json

from peaky_finders.models import SplatCoverageRequest

# Bump when SPLAT invocation, normalization, or output semantics change (invalidates host-side skips).
SPLAT_CACHE_SCHEMA_VERSION = 5


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
