"""Filesystem layout for SPLAT workspaces (preset ``build/viewsheds/`` subtrees).

One directory per *canonical site slug*: sites that share identical propagation fingerprints
(:func:`viewshed_workspace_digest`) read and write via the alphabetically-first slug's folder.

Provider / Docker mirror / overlay knobs remain outside ``splat_input_sha256``; delete the workspace
when those change materially.
"""

from __future__ import annotations

from pathlib import Path

from peaky_finders.models import SplatCoverageRequest
from peaky_finders.path_labels import filesystem_safe_slug
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import Preset
from peaky_finders.splat_input_hash import splat_input_sha256


def viewshed_workspace_digest(*, request: SplatCoverageRequest) -> str:
    """Fingerprint for propagation inputs (same as SPLAT reuse key). Used in stamps/metadata."""
    return splat_input_sha256(request)


def propagation_digest_to_workspace_master_slug(preset: Preset) -> dict[str, str]:
    """Digest → alphabetically-first site slug defining the on-disk workspace directory.

    Matches full ``preset.sites`` (not a filtered/limit subset) so sibling sites share storage.
    """

    vd_to_slugs: dict[str, list[str]] = {}
    for site_slug, site in preset.sites.items():
        req = preset_to_request(preset, float(site.lat), float(site.lon))
        vd = viewshed_workspace_digest(request=req)
        vd_to_slugs.setdefault(vd, []).append(site_slug)
    return {vd: sorted(slugs)[0] for vd, slugs in vd_to_slugs.items()}


def resolved_viewshed_workdir(*, canonical_site_slug: str, viewshed_root: Path) -> Path:
    """One SPLAT workspace directory — named after the group's canonical preset site slug."""
    label = filesystem_safe_slug(canonical_site_slug)
    return Path(viewshed_root).expanduser().resolve() / label
