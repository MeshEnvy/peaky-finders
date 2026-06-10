"""Mesh-grow goal geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from peaky_finders.sites_job import GoalEntry


@dataclass(frozen=True)
class GoalPoint:
    key: str
    lat: float
    lon: float


def goals_from_preset(goals: Mapping[str, GoalEntry]) -> dict[str, GoalPoint]:
    """Resolve top-level ``goals`` to ``GoalPoint`` locations."""
    return {
        key: GoalPoint(key=str(key), lat=float(goal.lat), lon=float(goal.lon))
        for key, goal in goals.items()
    }
