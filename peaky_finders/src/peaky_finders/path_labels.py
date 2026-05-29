"""Human-readable filesystem labels for preset build artifact directories.

Directories are labeled by site slug, pair of slugs, or fixed role names—not content hashes.
"""

from __future__ import annotations

import hashlib
import re

_MAX_SLUG_CHARS = 120
def filesystem_safe_slug(raw: str, *, max_chars: int = _MAX_SLUG_CHARS) -> str:
    """One path segment derived from a site slug or preset id (POSIX-portable).

    Keeps ASCII letters/digits/dot/underscore/hyphen; merges other runs into single ``_``.
    """
    s = str(raw).strip()
    if s in {"", ".", ".."}:
        return "site"
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    s = s.strip("_.").strip() or "site"
    if len(s) > max_chars:
        suf = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:8]
        keep = max(1, max_chars - len(suf) - 2)
        s = f"{s[:keep]}__{suf}"
    return s


def mesh_pairwise_rel_dir(site_slug_a: str, site_slug_b: str) -> str:
    """Relative directory under ``mesh/pairwise/`` for one unordered site pair."""
    aa = filesystem_safe_slug(site_slug_a)
    bb = filesystem_safe_slug(site_slug_b)
    lo, hi = sorted((aa, bb))
    return f"{lo}__{hi}"


def mesh_depth_network_rel_dir(*, max_raster_dimension: int) -> str:
    """Relative directory under mesh depth cache for full-preset mesh depth run."""
    return f"coverage_depth_r{int(max_raster_dimension)}"


# Fixed subdir under preset ``eligible_union`` root (digest still stored in metadata).
ELIGIBLE_UNION_DIRNAME = "eligible_land_use_union"
