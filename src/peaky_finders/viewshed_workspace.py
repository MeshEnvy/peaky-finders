"""Filesystem layout for SPLAT workspaces (preset ``build/viewsheds/`` subtrees).

One directory per propagation fingerprint (:func:`viewshed_workspace_digest`). Sites that
share identical SPLAT inputs reuse the same workspace folder.

Provider / Docker mirror / overlay knobs remain outside ``splat_input_sha256``; delete the workspace
when those change materially.
"""

from __future__ import annotations

from pathlib import Path

from peaky_finders.models import SplatCoverageRequest
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import Preset
from peaky_finders.splat_input_hash import splat_input_sha256


def viewshed_workspace_digest(*, request: SplatCoverageRequest) -> str:
    """Fingerprint for propagation inputs (same as SPLAT reuse key). Used in stamps/metadata."""
    return splat_input_sha256(request)


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
