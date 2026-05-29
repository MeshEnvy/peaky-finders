from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from peaky_finders.sites_job import MeshBackboneGoalEntry, MeshBackboneStrategyConfig, MeshGrowAiStrategyConfig


@dataclass(frozen=True)
class GoalPoint:
    key: str
    lat: float
    lon: float


def goals_from_goals(goals: Mapping[str, MeshBackboneGoalEntry]) -> dict[str, GoalPoint]:
    return {
        key: GoalPoint(key=str(key), lat=float(goal.lat), lon=float(goal.lon))
        for key, goal in goals.items()
    }


def goals_from_config(cfg: MeshBackboneStrategyConfig | MeshGrowAiStrategyConfig) -> dict[str, GoalPoint]:
    """Resolve configured goals to ``GoalPoint`` locations."""
    return goals_from_goals(cfg.goals)
