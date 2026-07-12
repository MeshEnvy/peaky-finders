"""Filesystem layout for SPLAT workspaces (``.peaky/cache/viewsheds/`` subtrees)."""

from __future__ import annotations

import json
from pathlib import Path

from peaky_finders.core.rf.models import SplatCoverageRequest
from peaky_finders.core.rf.mapping import preset_to_request
from peaky_finders.core.preset.model import Preset
from peaky_finders.core.viewshed.input_hash import splat_input_sha256


def viewshed_workspace_digest(*, request: SplatCoverageRequest) -> str:
    """Fingerprint for propagation inputs (same as SPLAT reuse key). Used in stamps/metadata."""
    return splat_input_sha256(request)


def viewshed_request_digest_matches(workdir: Path, *, expected_workspace_digest: str) -> bool:
    """True when ``request.json`` in *workdir* hashes to *expected_workspace_digest*."""
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


def resolved_viewshed_workdir(*, digest: str, viewshed_root: Path) -> Path:
    """One SPLAT workspace directory — named after the propagation digest."""
    label = str(digest).strip().lower()
    if not label:
        raise ValueError("viewshed workspace digest must be non-empty")
    return Path(viewshed_root).expanduser().resolve() / label


def resolved_viewshed_workdir_for_coords(
    *,
    preset: Preset,
    viewshed_root: Path,
    lat: float,
    lon: float,
) -> Path:
    req = preset_to_request(preset, float(lat), float(lon))
    digest = viewshed_workspace_digest(request=req)
    return resolved_viewshed_workdir(digest=digest, viewshed_root=viewshed_root)
