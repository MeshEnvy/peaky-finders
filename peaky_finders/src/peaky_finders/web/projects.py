"""Discover Peaky projects from ``projects/*/config.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.sites_job import load_preset, peaky_projects_dir


def list_projects() -> list[dict[str, Any]]:
    root = peaky_projects_dir()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        cfg = entry / "config.yaml"
        if not cfg.is_file():
            yml = entry / "config.yml"
            cfg = yml if yml.is_file() else cfg
        if not cfg.is_file():
            continue
        try:
            preset = load_preset(cfg)
        except Exception:
            continue
        strategy = "land-grab"
        goal_count = 0
        if preset.bundle and preset.bundle.site_suggestions:
            ss = preset.bundle.site_suggestions
            strategy = ss.strategy.value
            if strategy == "mesh-grow-ai":
                goal_count = len(ss.mesh_grow_ai.goals)
            elif strategy == "mesh-backbone":
                goal_count = len(ss.mesh_backbone.goals)
        out.append(
            {
                "slug": entry.name,
                "path": str(cfg.resolve()),
                "strategy": strategy,
                "goals": goal_count,
                "site_count": len(preset.sites),
            }
        )
    return out


def project_context(slug: str) -> dict[str, Any]:
    root = peaky_projects_dir()
    cfg = root / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    preset = load_preset(cfg)
    sites = [
        {"slug": s, "lat": e.lat, "lon": e.lon, "type": e.type.value}
        for s, e in sorted(preset.sites.items())
    ]
    goals: list[dict[str, Any]] = []
    if preset.bundle and preset.bundle.site_suggestions:
        ss = preset.bundle.site_suggestions
        if ss.strategy.value == "mesh-grow-ai":
            gcfg = ss.mesh_grow_ai.goals
        elif ss.strategy.value == "mesh-backbone":
            gcfg = ss.mesh_backbone.goals
        else:
            gcfg = {}
        for key, ent in gcfg.items():
            goals.append({"key": key, "lat": ent.lat, "lon": ent.lon})
    bbox = None
    if sites:
        lats = [s["lat"] for s in sites]
        lons = [s["lon"] for s in sites]
        pad = 0.25
        bbox = [min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad]
    return {"slug": slug, "sites": sites, "goals": goals, "bbox": bbox}
