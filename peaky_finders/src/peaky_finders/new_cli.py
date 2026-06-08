"""``peaky new`` — scaffold a project directory with ``config.yaml`` and ``data/`` layout."""

from __future__ import annotations

import argparse
import re
import sys
from importlib.resources import files
from pathlib import Path

from peaky_finders.sites_job import load_preset, peaky_projects_dir

_PROJECT_SLUG_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_DATA_SUBDIRS = ("aoi", "include", "exclude")


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


def _project_template_text() -> str:
    ref = files("peaky_finders.data").joinpath("new_project_config.yaml")
    return ref.read_text(encoding="utf-8")


def resolve_new_project_dir(
    slug: str,
    *,
    parent: Path | None = None,
    here: bool = False,
    cwd: Path | None = None,
) -> Path:
    """Return the directory that will receive ``config.yaml`` and ``data/``."""
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
    """Create project tree; return project directory. Raises ``FileExistsError`` when blocked."""
    project_dir = resolve_new_project_dir(slug, parent=parent, here=here, cwd=cwd)
    config_path = project_dir / "config.yaml"

    if config_path.is_file() and not force:
        raise FileExistsError(f"config.yaml already exists at {config_path} (use --force to overwrite)")

    if project_dir.is_file():
        raise FileExistsError(f"project path is a file, not a directory: {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)
    for sub in _DATA_SUBDIRS:
        (project_dir / "data" / sub).mkdir(parents=True, exist_ok=True)

    config_path.write_text(_project_template_text(), encoding="utf-8")
    load_preset(config_path)
    return project_dir


def build_new_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Scaffold a Peaky project (config.yaml + data/ layout).",
        add_help=False,
    )
    p.add_argument("slug", metavar="SLUG", help="Project id (directory name and KMZ slug)")
    p.add_argument(
        "--parent",
        type=Path,
        default=None,
        metavar="DIR",
        help=f"Parent directory (default: {peaky_projects_dir()!s})",
    )
    p.add_argument(
        "--here",
        action="store_true",
        help="Scaffold in the current directory instead of <parent>/<slug>/",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config.yaml",
    )
    return p


def run_new(args: argparse.Namespace) -> int:
    slug = str(getattr(args, "slug", "")).strip()
    try:
        project_dir = scaffold_project(
            slug,
            parent=getattr(args, "parent", None),
            here=bool(getattr(args, "here", False)),
            force=bool(getattr(args, "force", False)),
        )
    except ValueError as e:
        print(f"new: {e}", file=sys.stderr)
        return 2
    except FileExistsError as e:
        print(f"new: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"new: {e}", file=sys.stderr)
        return 1

    rel = project_dir
    try:
        rel = project_dir.relative_to(Path.cwd())
    except ValueError:
        pass

    print(f"new: created project at {project_dir}", flush=True)
    print(f"  cd {rel}", flush=True)
    print("  # add GDB/KML files under data/; list layers with: peaky inspect data/aoi/your.gdb", flush=True)
    print("  peaky build --verbose", flush=True)
    return 0
