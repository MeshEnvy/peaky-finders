"""Project discovery and scaffolding (no argparse)."""

from __future__ import annotations

import re
from pathlib import Path

from peaky_finders.core.home.bundled_templates import bundled_template_path
from peaky_finders.core.home.preset_defaults import ensure_peaky_home_config
from peaky_finders.core.home.profiles import ensure_peaky_home_profiles
from peaky_finders.core.preset.io import load_preset
from peaky_finders.core.preset.paths import peaky_projects_dir

_PROJECT_SLUG_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def validate_project_slug(slug: str) -> str:
    """Return *slug* if it is a safe single path component for a project id."""
    slug = slug.strip()
    if not slug:
        raise ValueError("project slug must not be empty")
    if "/" in slug or slug in {".", ".."}:
        raise ValueError(f"invalid project slug {slug!r} — use a single name (letters, digits, -, _)")
    if not _PROJECT_SLUG_RE.match(slug):
        raise ValueError(
            f"invalid project slug {slug!r} — start with a letter; then letters, digits, hyphens, or underscores"
        )
    return slug


def discover_projects(projects_root: Path | None = None) -> list[str]:
    """Return sorted project slugs (dirs with ``config.yaml``) under *projects_root*."""
    root = peaky_projects_dir() if projects_root is None else Path(projects_root).expanduser().resolve()
    if not root.is_dir():
        return []
    slugs: list[str] = []
    for child in root.iterdir():
        if child.is_dir() and (child / "config.yaml").is_file():
            slugs.append(child.name)
    return sorted(slugs)


def resolve_new_project_dir(
    slug: str,
    *,
    parent: Path | None = None,
    here: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Return the directory that will receive ``config.yaml``."""
    validate_project_slug(slug)
    base = Path.cwd() if cwd is None else Path(cwd)
    if here:
        return base.expanduser().resolve()
    root = peaky_projects_dir() if parent is None else Path(parent).expanduser().resolve()
    return (root / slug).resolve()


def scaffold_project(
    slug: str,
    *,
    parent: Path | None = None,
    here: bool = False,
    force: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Create project tree with ``config.yaml``; return project directory."""
    project_dir = resolve_new_project_dir(slug, parent=parent, here=here, cwd=cwd)
    config_path = project_dir / "config.yaml"

    if config_path.is_file() and not force:
        raise FileExistsError(f"config.yaml already exists at {config_path} (use --force to overwrite)")

    if project_dir.is_file():
        raise FileExistsError(f"project path is a file, not a directory: {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)
    ensure_peaky_home_profiles()
    ensure_peaky_home_config()
    config_path.write_text(
        bundled_template_path("new_project_config.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    load_preset(config_path)
    return project_dir
