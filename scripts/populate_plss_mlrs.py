#!/usr/bin/env python3
"""Populate plss and mlrs on a preset YAML (e.g. nevada.yaml) via BLM CadNSDI MapServer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from peaky_finders.plss_mlrs_fetch import (
    _list_site_slug_assignments,
    populate_preset_plss_mlrs_file,
)
from peaky_finders.sites_job import read_preset_document


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Populate plss/mlrs from CadNSDI")
    parser.add_argument("preset", nargs="?", type=Path, default=root / "nevada.yaml", help="Preset YAML path")
    args = parser.parse_args()
    path = Path(args.preset)
    if not path.is_file():
        print(f"Preset not found: {path}", file=sys.stderr)
        sys.exit(2)
    data = read_preset_document(path)
    sites = data.get("sites")
    if not sites:
        print("No sites in preset", file=sys.stderr)
        sys.exit(2)
    if isinstance(sites, dict):
        slugs = set(sites.keys())
    elif isinstance(sites, list):
        slugs = {s for s, _ in _list_site_slug_assignments(sites)}
    else:
        print("sites must be an object or array", file=sys.stderr)
        sys.exit(2)
    populate_preset_plss_mlrs_file(path, site_slugs=slugs)
    print("Wrote", path)


if __name__ == "__main__":
    main()
