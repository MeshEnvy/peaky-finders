"""Per-episode agent state for mesh-grow-ai."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentSession:
    """Mutable episode buffer (one planner iteration)."""

    iteration: int
    proposals: list[dict[str, Any]] = field(default_factory=list)
    evaluated: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    viewshed_evals_used: int = 0
    submitted: bool = False
    submit_result: dict[str, Any] | None = None

    def reset_for_iteration(self, iteration: int) -> None:
        self.iteration = iteration
        self.proposals.clear()
        self.evaluated.clear()
        self.viewshed_evals_used = 0
        self.submitted = False
        self.submit_result = None
