"""Shared grow-goals config for mesh-backbone and mesh-grow-ai strategies."""

from __future__ import annotations

from typing import Protocol

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneStrategyConfig,
    MeshGrowAiStrategyConfig,
    SiteSuggestionStrategy,
)


class GrowGoalsConfig(Protocol):
    goals: dict[str, MeshBackboneGoalEntry]
    goal_order: list[str]
    max_nodes: int | None


def is_mesh_grow_strategy(strategy: SiteSuggestionStrategy) -> bool:
    return strategy in (
        SiteSuggestionStrategy.MESH_BACKBONE,
        SiteSuggestionStrategy.MESH_GROW_AI,
    )


def grow_goals_config(cfg: BundleSiteSuggestionsConfig) -> MeshBackboneStrategyConfig | MeshGrowAiStrategyConfig:
    if cfg.strategy == SiteSuggestionStrategy.MESH_GROW_AI:
        return cfg.mesh_grow_ai
    return cfg.mesh_backbone


def grow_goals_config_from_ctx(ctx: SiteSuggestionContext) -> MeshBackboneStrategyConfig | MeshGrowAiStrategyConfig:
    return grow_goals_config(ctx.cfg)
