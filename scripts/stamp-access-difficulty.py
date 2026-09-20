#!/usr/bin/env python3
"""One-shot: stamp required difficulty fields on access profiles and peak thin rows.

Greenfield migration after PeakHikeProfile / PeakJeepProfile gained required
``difficulty``. Classifies from frozen profile stats using ``scan.peaks.difficulty``
bands in the project ``config.yaml``.

Usage:
  python3 scripts/stamp-access-difficulty.py /path/to/peaky-nevada
  python3 scripts/stamp-access-difficulty.py /path/to/peaky-nevada --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import yaml

DEFAULT_HIKE = {
    "medium": {"min_max_grade_pct": 15, "min_avg_grade_pct": 8, "min_gain_m": 40},
    "difficult": {"min_gain_m": 80, "min_avg_grade_pct": 15},
    "extreme": {"min_gain_m": 250, "min_avg_grade_pct": 18, "max_segment_grade_pct": 110},
}
DEFAULT_JEEP = {
    "medium": {
        "highways": ["unclassified", "residential", "service"],
        "tracktypes": ["grade2"],
    },
    "difficult": {"tracktypes": ["grade3", "grade4"]},
    "extreme": {"tracktypes": ["grade5"], "max_route_m": 32187},
}


def load_bands(project: Path) -> dict:
    cfg = yaml.safe_load((project / "config.yaml").read_text()) or {}
    scan = (cfg.get("scan") or {}).get("peaks") or {}
    diff = scan.get("difficulty") or {}
    hike = diff.get("hike") or DEFAULT_HIKE
    jeep = diff.get("jeep") or DEFAULT_JEEP
    for tier in ("medium", "difficult", "extreme"):
        hike.setdefault(tier, DEFAULT_HIKE[tier])
        jeep.setdefault(tier, DEFAULT_JEEP[tier])
    return {"hike": hike, "jeep": jeep}


def classify_hike(max_grade_pct: float, avg_grade_pct: float, gain_m: float, bands: dict) -> str:
    ex, df, md = bands["extreme"], bands["difficult"], bands["medium"]
    if gain_m >= ex["min_gain_m"] and avg_grade_pct >= ex["min_avg_grade_pct"]:
        return "extreme"
    if gain_m >= df["min_gain_m"] and avg_grade_pct >= df["min_avg_grade_pct"]:
        return "difficult"
    if (
        max_grade_pct >= md["min_max_grade_pct"]
        or avg_grade_pct >= md["min_avg_grade_pct"]
        or gain_m >= md["min_gain_m"]
    ):
        return "medium"
    return "easy"


def jeep_segment_rank(highway: str, tracktype: str | None, bands: dict) -> int:
    if tracktype:
        if tracktype in bands["extreme"]["tracktypes"]:
            return 3
        if tracktype in bands["difficult"]["tracktypes"]:
            return 2
        if tracktype in bands["medium"]["tracktypes"]:
            return 1
    if highway in ("motorway", "trunk", "primary", "secondary", "tertiary"):
        return 0
    if highway in ("residential", "unclassified"):
        return 0 if tracktype == "grade1" else 1
    if highway == "service":
        return 1
    if highway == "track":
        return 1
    if highway in bands["medium"]["highways"]:
        return 1
    return 1


def coalesce_segments(segments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in segments:
        dist = float(s.get("dist_m") or 0)
        if dist <= 0:
            continue
        hw = str(s.get("highway") or "")
        tt = s.get("tracktype")
        if out and out[-1]["highway"] == hw and out[-1].get("tracktype") == tt:
            out[-1]["dist_m"] += dist
        else:
            out.append({"highway": hw, "tracktype": tt, "dist_m": dist})
    return out


def qualifying_segments(segments: list[dict]) -> list[dict]:
    coalesced = coalesce_segments(segments)
    total = sum(max(0.0, s["dist_m"]) for s in coalesced)
    min_len = max(total * 0.02, 40.0)
    if total < 80.0:
        return coalesced
    return [s for s in coalesced if s["dist_m"] >= min_len]


def classify_jeep(segments: list[dict], bands: dict) -> str:
    kept = qualifying_segments(segments)
    rank = max(
        (jeep_segment_rank(s["highway"], s.get("tracktype"), bands) for s in kept),
        default=0,
    )
    return {0: "easy", 1: "medium", 2: "difficult", 3: "extreme"}[rank]


def worse(a: str, b: str) -> str:
    order = {"easy": 0, "medium": 1, "difficult": 2, "extreme": 3}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def stamp_access(path: Path, bands: dict, dry_run: bool) -> tuple[bool, str | None, str | None]:
    data = yaml.safe_load(path.read_text()) or {}
    hike_label = jeep_label = None
    changed = False

    hike = data.get("hike")
    if isinstance(hike, dict) and not hike.get("difficulty"):
        hike_label = classify_hike(
            float(hike.get("max_grade_pct") or 0),
            float(hike.get("avg_grade_pct") or 0),
            float(hike.get("gain_m") or 0),
            bands["hike"],
        )
        hike["difficulty"] = hike_label
        changed = True

    jeep = data.get("jeep")
    if isinstance(jeep, dict) and not jeep.get("difficulty"):
        jeep_label = classify_jeep(jeep.get("segments") or [], bands["jeep"])
        jeep["difficulty"] = jeep_label
        changed = True

    if changed and not dry_run:
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return changed, hike_label, jeep_label


def stamp_peak(path: Path, access: dict | None, bands: dict, dry_run: bool) -> bool:
    data = yaml.safe_load(path.read_text()) or {}
    changed = False

    hike_label = jeep_label = None
    if access:
        if isinstance(access.get("hike"), dict):
            hike_label = access["hike"].get("difficulty")
        if isinstance(access.get("jeep"), dict):
            jeep_label = access["jeep"].get("difficulty")

    if not data.get("hike_difficulty"):
        if hike_label:
            data["hike_difficulty"] = hike_label
            changed = True
        elif data.get("hike_max_grade_pct") is not None and data.get("hike_gain_m") is not None:
            data["hike_difficulty"] = classify_hike(
                float(data.get("hike_max_grade_pct") or 0),
                float(data.get("hike_avg_grade_pct") or 0),
                float(data.get("hike_gain_m") or 0),
                bands["hike"],
            )
            changed = True

    if not data.get("jeep_difficulty"):
        if jeep_label:
            data["jeep_difficulty"] = jeep_label
            changed = True
        elif access and isinstance(access.get("jeep"), dict):
            data["jeep_difficulty"] = classify_jeep(
                access["jeep"].get("segments") or [], bands["jeep"]
            )
            changed = True

    if changed and not dry_run:
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return changed


def sync_peaks_meta(project: Path, bands: dict, dry_run: bool) -> bool:
    meta_path = project / "peaks" / "_meta.yaml"
    if not meta_path.is_file():
        return False
    data = yaml.safe_load(meta_path.read_text()) or {}
    rules = data.setdefault("rules", {})
    rules["difficulty"] = bands
    rules["max_slope_grade_pct"] = bands["hike"]["extreme"]["max_segment_grade_pct"]
    import math

    rules["max_slope_deg"] = math.degrees(
        math.atan(rules["max_slope_grade_pct"] / 100.0)
    )
    if not dry_run:
        meta_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", type=Path, help="Peaky project dir (config.yaml + access/)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    project = args.project.resolve()
    if not (project / "config.yaml").is_file():
        print(f"missing config.yaml under {project}", file=sys.stderr)
        return 1

    bands = load_bands(project)
    access_dir = project / "access"
    peaks_dir = project / "peaks"
    access_changed = peak_changed = 0
    labels = Counter()

    for path in sorted(access_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        changed, h, j = stamp_access(path, bands, args.dry_run)
        if changed:
            access_changed += 1
            if h:
                labels[f"hike:{h}"] += 1
            if j:
                labels[f"jeep:{j}"] += 1

    for path in sorted(peaks_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        slug = path.stem
        access_path = access_dir / f"{slug}.yaml"
        access = None
        if access_path.is_file():
            access = yaml.safe_load(access_path.read_text()) or {}
        if stamp_peak(path, access, bands, args.dry_run):
            peak_changed += 1

    meta = sync_peaks_meta(project, bands, args.dry_run)
    mode = "dry-run" if args.dry_run else "wrote"
    print(f"{mode}: access={access_changed} peaks={peak_changed} meta={'yes' if meta else 'no'}")
    for k, n in sorted(labels.items()):
        print(f"  {k}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
