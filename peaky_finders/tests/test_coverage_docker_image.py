"""ensure_coverage_docker_image builds at most once per tag per process."""

from __future__ import annotations

from pathlib import Path

import peaky_finders.cli as cli


def test_ensure_coverage_docker_image_skips_when_present(monkeypatch, tmp_path: Path) -> None:
    cli._docker_images_ready.clear()
    build_calls = 0

    def fake_exists(image: str) -> bool:
        assert image == "splatter:latest"
        return True

    def fake_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return 0

    monkeypatch.setattr(cli, "_docker_image_exists", fake_exists)
    monkeypatch.setattr(cli, "_splatter_image_has_run_batch", lambda _image: True)
    monkeypatch.setattr(cli.subprocess, "run", fake_build)

    rc = cli.ensure_coverage_docker_image(
        tmp_path,
        dockerfile_name="splatter/Dockerfile",
        image="splatter:latest",
        context="splatter",
    )
    assert rc == 0
    assert build_calls == 0
    assert "splatter:latest" in cli._docker_images_ready


def test_ensure_coverage_docker_image_builds_once(monkeypatch, tmp_path: Path) -> None:
    cli._docker_images_ready.clear()
    inspect_calls = 0
    build_calls = 0

    def fake_exists(image: str) -> bool:
        nonlocal inspect_calls
        inspect_calls += 1
        return False

    def fake_run(cmd, **kwargs):
        nonlocal build_calls
        if cmd[0:2] == ["docker", "build"]:
            build_calls += 1
            return type("R", (), {"returncode": 0})()
        raise AssertionError(cmd)

    monkeypatch.setattr(cli, "_docker_image_exists", fake_exists)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    for _ in range(3):
        rc = cli.ensure_coverage_docker_image(
            tmp_path,
            dockerfile_name="splatter/Dockerfile",
            image="splatter:test-once",
            context="splatter",
        )
        assert rc == 0

    assert inspect_calls == 1
    assert build_calls == 1


def test_ensure_coverage_docker_image_rebuilds_stale_splatter(monkeypatch, tmp_path: Path) -> None:
    cli._docker_images_ready.clear()
    build_calls = 0

    def fake_exists(image: str) -> bool:
        assert image == "splatter:latest"
        return True

    def fake_run_batch(_image: str) -> bool:
        return False

    def fake_build(cmd, **kwargs):
        nonlocal build_calls
        if cmd[0:2] == ["docker", "build"]:
            build_calls += 1
            return type("R", (), {"returncode": 0})()
        raise AssertionError(cmd)

    monkeypatch.setattr(cli, "_docker_image_exists", fake_exists)
    monkeypatch.setattr(cli, "_splatter_image_has_run_batch", fake_run_batch)
    monkeypatch.setattr(cli.subprocess, "run", fake_build)

    rc = cli.ensure_coverage_docker_image(
        tmp_path,
        dockerfile_name="splatter/Dockerfile",
        image="splatter:latest",
        context="splatter",
    )
    assert rc == 0
    assert build_calls == 1
