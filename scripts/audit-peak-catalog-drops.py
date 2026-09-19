#!/usr/bin/env python3
"""Bucket peaks removed between two git commits (session tool, not shipped).

Usage:
  python3 scripts/audit-peak-catalog-drops.py /path/to/peaky-nevada HEAD~1 HEAD

Reads peak slugs from peaks/*.yaml at OLD and NEW refs, prints removed slugs
grouped by coarse reason using thin-row facts when present.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml


def slugs_at(repo: Path, rev: str) -> set[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", rev, "peaks"],
        text=True,
    )
    slugs = set()
    for line in out.splitlines():
        name = Path(line).name
        if not name.endswith((".yaml", ".yml")) or name.startswith("_"):
            continue
        slugs.add(Path(name).stem)
    return slugs


def read_peak(repo: Path, rev: str, slug: str) -> dict | None:
    path = f"peaks/{slug}.yaml"
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{rev}:{path}"], text=True
        )
    except subprocess.CalledProcessError:
        return None
    return yaml.safe_load(raw) or {}


def bucket(entry: dict | None) -> str:
    if not entry:
        return "not_reseeded"
    if entry.get("deny"):
        return "deny"
    if not entry.get("road_m") and not entry.get("road_loc"):
        return "road"
    max_grade = entry.get("hike_max_grade_pct")
    if max_grade is not None and float(max_grade) > 110.0:
        return "slope_over_extreme"
    if not entry.get("jeep_m") and not entry.get("jeep_highway"):
        return "jeep"
    return "unknown_stale"


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__.strip())
        sys.exit(2)
    repo = Path(sys.argv[1]).resolve()
    old_rev, new_rev = sys.argv[2], sys.argv[3]
    old_slugs = slugs_at(repo, old_rev)
    new_slugs = slugs_at(repo, new_rev)
    removed = sorted(old_slugs - new_slugs)
    print(f"old={len(old_slugs)} new={len(new_slugs)} removed={len(removed)}")
    counts: Counter[str] = Counter()
    for slug in removed:
        entry = read_peak(repo, old_rev, slug)
        reason = bucket(entry)
        counts[reason] += 1
        print(f"{slug}\t{reason}")
    print("\nBuckets:")
    for k, v in counts.most_common():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
