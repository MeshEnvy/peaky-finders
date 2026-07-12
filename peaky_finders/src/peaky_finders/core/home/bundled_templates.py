"""Bundled YAML templates seeded into ``$PEAKY_HOME`` on first use."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_TEMPLATES_DIR = "core/home/templates"

PEAKY_HOME_TEMPLATE_FILES = ("config.yaml", "modems.yaml", "environments.yaml")


def bundled_template_path(filename: str) -> Path:
    """Return package path to ``peaky_finders/core/home/templates/<filename>``."""
    return Path(str(files("peaky_finders").joinpath(_TEMPLATES_DIR, filename)))


def ensure_bundled_home_file(filename: str) -> None:
    """Copy one bundled template into ``$PEAKY_HOME`` when the destination file is missing."""
    from peaky_finders.core.preset.paths import peaky_home

    home = peaky_home()
    home.mkdir(parents=True, exist_ok=True)
    dest = home / filename
    if dest.is_file():
        return
    dest.write_text(bundled_template_path(filename).read_text(encoding="utf-8"), encoding="utf-8")


def ensure_peaky_home() -> None:
    """Seed missing ``$PEAKY_HOME`` YAML templates and ensure ``projects/`` exists."""
    from peaky_finders.core.preset.paths import peaky_projects_dir

    for name in PEAKY_HOME_TEMPLATE_FILES:
        ensure_bundled_home_file(name)
    peaky_projects_dir().mkdir(parents=True, exist_ok=True)
