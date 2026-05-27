"""Container entrypoint: read request.json, run SPLAT, write splat.png."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from peaky_finders.kml_bundle import point_splat_output_kml_at_png
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.splat_engine import Splat
from peaky_finders.splat_ppm_to_png import write_splat_png_from_ppm


def _root_log_level() -> int:
    name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return int(getattr(logging, name, logging.INFO))


logging.basicConfig(level=_root_log_level())
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run SPLAT coverage into /work")
    p.add_argument("--work-dir", default="/work", help="Mounted output directory")
    args = p.parse_args(argv)
    work = Path(args.work_dir)
    req_file = work / "request.json"
    if not req_file.is_file():
        logger.error("Missing %s", req_file)
        return 2

    request = SplatCoverageRequest.model_validate_json(req_file.read_text(encoding="utf-8"))
    splat_path = os.environ.get("SPLAT_PATH", "/opt/splat")
    cache_dir = os.environ.get("SPLAT_CACHE") or str(work / ".tile_cache")

    service = Splat(splat_path, cache_dir=cache_dir)

    try:
        service.run_coverage_to_workdir(request, work)
    except Exception:
        logger.exception("SPLAT run failed")
        return 1

    ppm = work / "output.ppm"
    if not ppm.is_file():
        logger.error("output.ppm missing after run")
        return 1

    # White no-RF SPLAT pixels → transparent RGBA; large rasters scaled for Earth overlays
    # (GPU texture caps — see PEAKY_SPLAT_OVERLAY_MAX_EDGE in splat_ppm_to_png.py).
    write_splat_png_from_ppm(ppm_path=ppm, png_path=work / "splat.png")
    point_splat_output_kml_at_png(work / "output.kml")

    logger.info("Wrote patched %s and splat.png", work / "output.kml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
