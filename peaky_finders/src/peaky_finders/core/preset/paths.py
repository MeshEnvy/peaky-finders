"""Filesystem paths for presets, Skadi cache, and viewshed workspaces."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Workspace root: monorepo parent when ``peaky_home/`` or ``projects/`` exists, else package root."""
    pkg_root = Path(__file__).resolve().parents[4]
    workspace = pkg_root.parent
    if (workspace / "peaky_home").is_dir() or (workspace / "projects").is_dir():
        return workspace
    return pkg_root


def peaky_home() -> Path:
    """Runtime home directory (``PEAKY_HOME``, else ``<repo>/peaky_home`` when present, else :func:`repo_root`)."""
    raw = os.environ.get("PEAKY_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    workspace = repo_root()
    candidate = workspace / "peaky_home"
    if candidate.is_dir():
        return candidate.resolve()
    return workspace


def peaky_projects_dir() -> Path:
    """Project presets root (``PEAKY_PROJECTS`` or ``<peaky_home>/projects``)."""
    raw = os.environ.get("PEAKY_PROJECTS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return peaky_home() / "projects"


def peaky_share_dir() -> Path:
    """Optional shared root (``PEAKY_SHARE``); default ``<peaky_home>/share``."""
    raw = os.environ.get("PEAKY_SHARE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return peaky_home() / "share"


def resolved_skadi_mirror_dir() -> Path:
    """Global Skadi tile mirror (``SPLAT_CACHE`` env, else ``<peaky_home>/splat_cache``)."""
    raw = os.environ.get("SPLAT_CACHE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (peaky_home() / "splat_cache").resolve()


def ensure_skadi_mirror_dir() -> Path:
    """Like :func:`resolved_skadi_mirror_dir`, creating the directory when missing."""
    root = resolved_skadi_mirror_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


_PRESET_EXTENSIONS = frozenset({".yaml", ".yml"})


def require_preset_yaml_path(path: Path) -> None:
    """Raise if ``path`` is not accepted as a job preset filename (YAML only)."""
    suf = Path(path).suffix.lower()
    if suf == ".json":
        raise ValueError(
            f"Peaky preset paths must end with `.yaml` or `.yml` (not `{path.suffix}`); "
            f"legacy JSON presets are unsupported: {path}"
        )
    if suf not in _PRESET_EXTENSIONS:
        raise ValueError(f"preset path must end with `.yaml` or `.yml` (got suffix {path.suffix!r}): {path}")


def require_cwd_config_yaml(*, cwd: Path | None = None) -> Path:
    """Return ``./config.yaml`` in *cwd* (default ``Path.cwd()``) or raise ``FileNotFoundError``."""
    base = Path.cwd() if cwd is None else Path(cwd)
    path = (base / "config.yaml").resolve()
    if not path.is_file():
        raise FileNotFoundError(f"config.yaml not found in {base} — run peaky from your project directory")
    require_preset_yaml_path(path)
    return path


def resolved_preset_cache_dir(preset_path: Path) -> Path:
    """Per-preset serve runtime cache: ``<preset-dir>/.peaky/cache``."""
    p = Path(preset_path).expanduser().resolve()
    return (p.parent / ".peaky" / "cache").resolve()


def resolved_viewshed_root(preset_path: Path) -> Path:
    """Per-preset viewshed workspace root: ``<preset-dir>/.peaky/cache/viewsheds``."""
    return resolved_preset_cache_dir(preset_path) / "viewsheds"


def resolved_preset_slug(preset_path: Path) -> str:
    """Stable preset id for document titles."""
    path = Path(preset_path).expanduser().resolve()
    if path.stem == "config":
        return path.parent.name
    return path.stem


def resolve_preset_yaml_arg(
    raw: str | Path,
    *,
    cwd: Path | None = None,
) -> Path:
    """Resolve a CLI preset argument to an existing ``.yaml`` / ``.yml`` file."""
    arg = Path(raw).expanduser()
    base_cwd = Path.cwd() if cwd is None else Path(cwd)
    home = peaky_home()
    projects = peaky_projects_dir()

    candidates: list[Path] = []
    if arg.is_absolute():
        candidates.append(arg)
    else:
        candidates.append(base_cwd / arg)
        if arg.suffix.lower() not in _PRESET_EXTENSIONS:
            candidates.append(base_cwd / f"{arg}.yaml")
            candidates.append(base_cwd / f"{arg}.yml")
        if len(arg.parts) == 1:
            slug = arg.stem if arg.suffix.lower() in _PRESET_EXTENSIONS else str(arg)
            candidates.append(projects / slug / "config.yaml")
            candidates.append(projects / slug / "config.yml")
        candidates.append(home / arg)
        if arg.suffix.lower() not in _PRESET_EXTENSIONS and len(arg.parts) == 1:
            candidates.append(home / f"{arg}.yaml")
            candidates.append(home / f"{arg}.yml")

    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            require_preset_yaml_path(path)
            return path

    return (candidates[0] if candidates else arg).expanduser().resolve()
