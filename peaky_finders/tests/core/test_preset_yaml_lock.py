"""Preset YAML transactional write locking."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from peaky_finders.core.preset import load_preset, update_preset_yaml_tree

from fixture_paths import MINIMAL_PRESET_YAML as FIXTURE_MINIMAL


def _copy_minimal_preset(tmp_path: Path) -> Path:
    preset_path = tmp_path / "config.yaml"
    shutil.copy(FIXTURE_MINIMAL, preset_path)
    return preset_path


def test_concurrent_preset_writes_preserve_both_sites(tmp_path: Path) -> None:
    preset_path = _copy_minimal_preset(tmp_path)
    barrier = threading.Barrier(2)
    errors: list[str] = []

    def add_site(slug: str, name: str) -> None:
        try:
            barrier.wait(timeout=5.0)

            def mutator(_yaml_rt, root: dict) -> None:
                sites = root.setdefault("sites", {})
                sites[slug] = {
                    "name": name,
                    "loc": [39.0, -117.0],
                }

            update_preset_yaml_tree(preset_path, mutator, validate=False)
        except Exception as exc:
            errors.append(str(exc))

    threads = [
        threading.Thread(target=add_site, args=("concurrent-a", "Concurrent A")),
        threading.Thread(target=add_site, args=("concurrent-b", "Concurrent B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert not errors
    preset = load_preset(preset_path)
    assert "concurrent-a" in preset.sites
    assert "concurrent-b" in preset.sites


def test_atomic_dump_leaves_no_partial_file(tmp_path: Path) -> None:
    preset_path = _copy_minimal_preset(tmp_path)
    partial = preset_path.with_name(preset_path.name + ".partial")

    def mutator(_yaml_rt, root: dict) -> None:
        root.setdefault("sites", {})["note-site"] = {
            "name": "Note",
            "loc": [39.1, -117.1],
        }

    update_preset_yaml_tree(preset_path, mutator, validate=False)

    assert not partial.exists()
    preset = load_preset(preset_path)
    assert "note-site" in preset.sites
