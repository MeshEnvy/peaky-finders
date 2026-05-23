"""Filesystem layout for SPLAT workspaces (outside bundle job dirs).

Workspaces live under ``<preset-dir>/build/viewsheds/<digest>/`` where ``digest`` is
:func:`splat_input_sha256` of the propagation request. Provider, Docker image, tile
mirror, and host overlay knobs are not part of the key — delete the workspace directory
to evict after changing those.
"""

from __future__ import annotations

from pathlib import Path

from peaky_finders.models import SplatCoverageRequest
from peaky_finders.splat_input_hash import splat_input_sha256


def viewshed_workspace_digest(*, request: SplatCoverageRequest) -> str:
    """Cache directory name for one propagation input (``splat_input_sha256``)."""
    return splat_input_sha256(request)


def resolved_viewshed_workdir(*, workspace_digest: str, viewshed_root: Path) -> Path:
    """``viewshed_root / digest`` — one SPLAT workspace per propagation fingerprint."""
    return Path(viewshed_root).expanduser().resolve() / workspace_digest
