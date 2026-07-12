"""Manual mutual site link pairs from preset ``links``."""

from __future__ import annotations

from typing import Sequence


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def manual_link_slug_pairs(manual_links: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Canonical slug pairs from preset ``links`` (already sorted per pair)."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for a, b in manual_links:
        key = _canonical_pair(str(a).strip(), str(b).strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return sorted(out)
