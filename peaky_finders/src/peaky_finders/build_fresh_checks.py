"""On-disk artefacts used by incremental ``peaky build``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from peaky_finders.bundle_clips import (
    BUNDLE_RESOLVE_FORMAT,
    COMPOSITE_WORKSPACE_FMT,
    COMPOSITE_WORKSPACE_JSON_NAME,
    ELIGIBLE_WORKSPACE_MANIFEST_FMT,
    ELIGIBLE_WORKSPACE_MANIFEST_NAME,
    MANIFEST_BASENAME,
    CLIP_JOB_WORKSPACE_FMT,
    CLIP_JOB_WORKSPACE_NAME,
    REFERENCE_WORKSPACE_FMT,
    REFERENCE_WORKSPACE_JSON_NAME,
    REFERENCE_EMPTY_MARKER,
    COMPOSITE_AOI_FORMAT,
    COMPOSITE_INCLUDE_FORMAT,
    COMPOSITE_EXCLUDE_FORMAT,
)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def clip_layer_artefacts_fresh(gpkg_path: Path, *, expected_sha: str) -> bool:
    gp = Path(gpkg_path).expanduser().resolve()
    if not gp.is_file():
        return False
    ws = gp.parent / CLIP_JOB_WORKSPACE_NAME
    blob = _read_manifest(ws)
    if blob is None or blob.get("format") != CLIP_JOB_WORKSPACE_FMT:
        return False
    return str(blob.get("clip_sha")) == expected_sha


def composite_artefacts_fresh(
    union_gpkg: Path,
    *,
    composite_workspace_dir: Path,
    expected_workspace_sha: str,
    role: str,
) -> bool:
    gpkg = Path(union_gpkg).expanduser().resolve()
    ws_dir = Path(composite_workspace_dir).expanduser().resolve()
    ws = ws_dir / COMPOSITE_WORKSPACE_JSON_NAME
    blob = _read_manifest(ws)
    if blob is None or blob.get("format") != COMPOSITE_WORKSPACE_FMT:
        return False
    if str(blob.get("composite_sha")) != expected_workspace_sha:
        return False
    if not gpkg.is_file():
        return False
    mf = ws_dir / MANIFEST_BASENAME
    mf_blob = _read_manifest(mf)
    if mf_blob is None:
        return False
    want_fmt = {
        "aoi": COMPOSITE_AOI_FORMAT,
        "include": COMPOSITE_INCLUDE_FORMAT,
        "exclude": COMPOSITE_EXCLUDE_FORMAT,
    }.get(role)
    if want_fmt is None:
        return False
    return mf_blob.get("format") == want_fmt


def eligible_artefacts_fresh(
    eligible_gpkg: Path,
    *,
    eligible_dir: Path,
    expected_eligible_sha: str,
) -> bool:
    gpkg = Path(eligible_gpkg).expanduser().resolve()
    ed = Path(eligible_dir).expanduser().resolve()
    ws = ed / ELIGIBLE_WORKSPACE_MANIFEST_NAME
    blob = _read_manifest(ws)
    if blob is None or blob.get("format") != ELIGIBLE_WORKSPACE_MANIFEST_FMT:
        return False
    if str(blob.get("eligible_sha")) != expected_eligible_sha:
        return False
    return gpkg.is_file()


def reference_artefacts_fresh(
    gpkg_expected: Path,
    *,
    ref_dir: Path,
    reference_sha_expected: str,
) -> bool:
    directory = Path(ref_dir).expanduser().resolve()
    ws = directory / REFERENCE_WORKSPACE_JSON_NAME
    blob = _read_manifest(ws)
    if blob is None or blob.get("format") != REFERENCE_WORKSPACE_FMT:
        return False
    if str(blob.get("reference_sha")) != reference_sha_expected:
        return False
    gp = Path(gpkg_expected).expanduser().resolve()
    if gp.is_file():
        return True
    return (directory / REFERENCE_EMPTY_MARKER).is_file()


def bundle_resolve_fresh(
    resolve_path: Path,
    *,
    clips_root_expected: Path,
    aoi_sha: str,
    include_sha: str,
    exclude_sha: str,
    eligible_sha: str,
    reference: dict[str, str] | None = None,
) -> bool:
    p = Path(resolve_path).expanduser().resolve()
    raw = _read_manifest(p)
    if raw is None or raw.get("format") != BUNDLE_RESOLVE_FORMAT:
        return False
    try:
        cr = Path(str(raw["clips_root"])).resolve()
    except (KeyError, OSError, TypeError):
        return False
    if cr != Path(clips_root_expected).expanduser().resolve():
        return False
    if str(raw.get("aoi")) != aoi_sha:
        return False
    if str(raw.get("include")) != include_sha:
        return False
    if str(raw.get("exclude")) != exclude_sha:
        return False
    if str(raw.get("eligible")) != eligible_sha:
        return False
    want_ref = dict(sorted((reference or {}).items()))
    got_ref = raw.get("reference") or {}
    if not isinstance(got_ref, dict):
        return False
    got_sorted = {str(k): str(v) for k, v in sorted(got_ref.items())}
    return got_sorted == want_ref


def viewshed_request_digest_matches(workdir: Path, *, expected_workspace_digest: str) -> bool:
    from peaky_finders.models import SplatCoverageRequest
    from peaky_finders.splat_input_hash import splat_input_sha256

    p = Path(workdir).expanduser().resolve() / "request.json"
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        req = SplatCoverageRequest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    got = splat_input_sha256(req)
    return str(got) == str(expected_workspace_digest)


def artefact_mtime_stale(outputs: tuple[Path, ...], prereqs: tuple[Path, ...]) -> bool:
    """Stale when missing output or newest prerequisite is newer than any output."""

    outs = tuple(Path(o).expanduser().resolve() for o in outputs)
    pre = tuple(Path(o).expanduser().resolve() for o in prereqs if Path(o).is_file())

    missing = tuple(o for o in outs if not o.is_file())
    if missing:
        return True

    try:
        out_min_ns = min(o.stat().st_mtime_ns for o in outs if o.is_file())
    except OSError:
        return True
    try:
        pre_max_ns = max(p.stat().st_mtime_ns for p in pre if p.is_file())
    except ValueError:
        return False

    return pre_max_ns > out_min_ns
