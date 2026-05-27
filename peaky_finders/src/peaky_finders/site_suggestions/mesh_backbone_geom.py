"""Mesh-grow goal geometry."""

from __future__ import annotations

from dataclasses import dataclass

from peaky_finders.sites_job import MeshBackboneStrategyConfig


@dataclass(frozen=True)
class GoalPoint:
    key: str
    lat: float
    lon: float


def goals_from_config(cfg: MeshBackboneStrategyConfig) -> dict[str, GoalPoint]:
    """Resolve ``mesh_backbone.goals`` to ``GoalPoint`` locations."""
    return {
        key: GoalPoint(key=str(key), lat=float(goal.lat), lon=float(goal.lon))
        for key, goal in cfg.goals.items()
    }
