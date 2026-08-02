"""Preset YAML read/write with locked round-trip updates."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from peaky_finders.core.preset.model import (
    Preset,
    SiteEntry,
    coerce_preset_sites,
    validate_project_preset_document,
)
from peaky_finders.core.preset.paths import require_preset_yaml_path


def _preset_yaml_typ_rt() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    return y


def yaml_plain_preset_value(o: Any) -> Any:
    """Recursively coerce ruamel round-trip mappings / sequences to plain dict/list."""
    if isinstance(o, Mapping):
        return {str(k): yaml_plain_preset_value(v) for k, v in o.items()}
    if isinstance(o, str | int | float | bool):
        return o
    if o is None:
        return None
    if isinstance(o, bytes | bytearray):
        return bytes(o).decode("utf-8")
    if isinstance(o, Sequence):
        return [yaml_plain_preset_value(item) for item in o]
    return o


def read_preset_yaml_tree(path: Path) -> tuple[YAML, Any]:
    """Load preset as ruamel ``rt`` trees (preserve comments/format on dump)."""
    path = Path(path).expanduser()
    require_preset_yaml_path(path)
    y = _preset_yaml_typ_rt()
    with path.open(encoding="utf-8") as fh:
        root = y.load(fh)
    if root is None:
        raise ValueError(f"preset YAML is empty or has no document root: {path}")
    return y, root


def dump_preset_yaml_document(y: YAML, data: Any, path: Path) -> None:
    """Write preset document with ``y`` formatter."""
    path = Path(path).expanduser()
    require_preset_yaml_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)
    tmp.replace(path)


_preset_yaml_locks: dict[str, threading.Lock] = {}
_preset_yaml_locks_mu = threading.Lock()

_rt_cache: dict[str, tuple[float, YAML, Any]] = {}
_rt_cache_mu = threading.Lock()


def _preset_yaml_lock(path: Path) -> threading.Lock:
    key = str(Path(path).expanduser().resolve())
    with _preset_yaml_locks_mu:
        return _preset_yaml_locks.setdefault(key, threading.Lock())


def _preset_path_key(path: Path) -> str:
    return str(Path(path).expanduser().resolve())


def _preset_file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def invalidate_preset_yaml_rt_cache(path: Path | None = None) -> None:
    """Drop cached ruamel trees (all presets, or one path after a failed mutate)."""
    with _rt_cache_mu:
        if path is None:
            _rt_cache.clear()
        else:
            _rt_cache.pop(_preset_path_key(path), None)


def reset_preset_yaml_rt_cache_for_tests() -> None:
    invalidate_preset_yaml_rt_cache(None)


def _cache_rt_tree(path: Path, yaml_rt: YAML, root: Any) -> None:
    with _rt_cache_mu:
        _rt_cache[_preset_path_key(path)] = (_preset_file_mtime(path), yaml_rt, root)


def _load_rt_tree(path: Path) -> tuple[YAML, Any]:
    key = _preset_path_key(path)
    mtime = _preset_file_mtime(path)
    with _rt_cache_mu:
        hit = _rt_cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1], hit[2]
    yaml_rt, root = read_preset_yaml_tree(path)
    return yaml_rt, root


@contextmanager
def preset_yaml_transaction(path: Path) -> Iterator[tuple[YAML, Any]]:
    """Hold the per-preset in-process lock while reading the round-trip YAML tree."""
    path = Path(path).expanduser().resolve()
    with _preset_yaml_lock(path):
        yaml_rt, root = _load_rt_tree(path)
        yield yaml_rt, root


def _replace_rt_mapping(root: Any, payload: Mapping[str, Any]) -> None:
    for key in list(root.keys()):
        del root[key]
    for key, val in payload.items():
        root[key] = val


def update_preset_yaml_tree(
    path: Path,
    mutator: Callable[[YAML, Any], Any],
    *,
    validate: bool = True,
    prune: bool = True,
) -> Any:
    """Read, mutate, validate merged preset, prune defaults, and atomically write project YAML."""
    from peaky_finders.core.home.preset_defaults import (
        load_preset_defaults,
        prune_project_preset_dict,
        resolve_preset_raw,
    )

    if validate:
        prune = True

    path = Path(path).expanduser().resolve()
    try:
        with preset_yaml_transaction(path) as (yaml_rt, root):
            result = mutator(yaml_rt, root)
            if validate or prune:
                plain = yaml_plain_preset_value(root)
                if not isinstance(plain, dict):
                    raise ValueError(f"preset YAML root must be a mapping at {path}")
                if validate:
                    validate_project_preset_document(plain)
                    parse_preset_dict(resolve_preset_raw(plain))
                if prune:
                    defaults = load_preset_defaults()
                    pruned = prune_project_preset_dict(plain, defaults)
                    _replace_rt_mapping(root, pruned)
            dump_preset_yaml_document(yaml_rt, root, path)
            _cache_rt_tree(path, yaml_rt, root)
            return result
    except Exception:
        invalidate_preset_yaml_rt_cache(path)
        raise


def read_preset_document(path: Path) -> dict[str, Any]:
    """Load preset file as a plain ``dict``."""
    _, root = read_preset_yaml_tree(path)
    plain = yaml_plain_preset_value(root)
    if not isinstance(plain, dict):
        raise ValueError(f"preset YAML root must be a mapping at {path}")
    return plain


def write_preset_document(path: Path, payload: Mapping[str, Any]) -> None:
    """Overwrite preset with YAML."""
    require_preset_yaml_path(path)
    path = Path(path).expanduser().resolve()
    with _preset_yaml_lock(path):
        y = _preset_yaml_typ_rt()
        dump_preset_yaml_document(y, dict(payload), path)
        invalidate_preset_yaml_rt_cache(path)


def parse_preset_sites_dict(raw: Mapping[str, Any]) -> dict[str, SiteEntry]:
    return coerce_preset_sites(raw.get("sites"))


def parse_preset_dict(raw: Mapping[str, Any]) -> Preset:
    """Coerce/validate a merged preset mapping (defaults + project)."""
    raw = {
        **raw,
        "sites": coerce_preset_sites(raw.get("sites")),
    }
    return Preset.model_validate(raw)


def load_preset_sites(path: Path) -> dict[str, SiteEntry]:
    raw = read_preset_document(path)
    validate_project_preset_document(raw)
    return parse_preset_sites_dict(raw)


def load_preset(path: Path) -> Preset:
    from peaky_finders.core.home.preset_defaults import resolve_preset_raw

    project = read_preset_document(path)
    validate_project_preset_document(project)
    return parse_preset_dict(resolve_preset_raw(project))


def load_preset_for_coverage(path: Path) -> Preset:
    """Load preset for splatter/viewshed (all sites participate)."""
    return load_preset(path)
