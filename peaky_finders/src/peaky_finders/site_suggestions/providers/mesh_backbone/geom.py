"""Mesh-grow goal geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from peaky_finders.sites_job import SiteEntry


@dataclass(frozen=True)
class GoalPoint:
    key: str
    lat: float
    lon: float


def goals_from_preset(goals: Mapping[str, SiteEntry]) -> dict[str, GoalPoint]:
    """Resolve ``type: goal`` sites to ``GoalPoint`` locations."""
    return {
        key: GoalPoint(key=str(key), lat=float(goal.lat), lon=float(goal.lon))
        for key, goal in goals.items()
    }
