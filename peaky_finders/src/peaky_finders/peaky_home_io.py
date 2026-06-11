"""Atomic ruamel round-trip writes for ``$PEAKY_HOME`` YAML files."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ruamel.yaml import YAML

from peaky_finders.sites_job import dump_preset_yaml_document, preset_yaml_transaction, yaml_plain_preset_value

T = TypeVar("T")


def update_home_yaml_tree(path: Path, mutator: Callable[[YAML, Any], T]) -> T:
    """Read, mutate, and atomically write a home YAML file (no project default pruning)."""
    with preset_yaml_transaction(path) as (yaml_rt, root):
        result = mutator(yaml_rt, root)
        plain = yaml_plain_preset_value(root)
        if not isinstance(plain, dict):
            raise ValueError(f"YAML root must be a mapping at {path}")
        dump_preset_yaml_document(yaml_rt, root, path)
        return result
