"""Bundled YAML templates seeded into ``$PEAKY_HOME`` on first use."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

_TEMPLATES_DIR = "templates"


def bundled_template_path(filename: str) -> Path:
    """Return package path to ``peaky_finders/templates/<filename>``."""
    return Path(str(files("peaky_finders").joinpath(_TEMPLATES_DIR, filename)))
