"""Ollama tool-calling loop for mesh-grow-ai."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    ollama_health_ok,
)
from peaky_finders.site_suggestions.providers.mesh_grow_ai.prompts import SYSTEM_PROMPT
from peaky_finders.site_suggestions.providers.mesh_grow_ai.session import AgentSession
from peaky_finders.site_suggestions.providers.mesh_grow_ai.tools import (
    TOOL_SCHEMAS,
    dispatch_tool,
    proposals_to_candidates,
    truncate_tool_result_for_stream,
)

EmitFn = Callable[[str, Any], None]


def run_mesh_grow_agent(
    ctx: SiteSuggestionContext,
    *,
    preset_path: Path,
    iteration: int,
    session: AgentSession | None = None,
    emit: EmitFn | None = None,
    chat_client: Callable[..., dict[str, Any]] | None = None,
) -> list[SiteCandidate]:
    """Run one planner-iteration agent episode; return accepted proposals."""
    ai = ctx.cfg.mesh_grow_ai
    if not ollama_health_ok(ai.ollama_base_url):
        raise OllamaError(f"Ollama not reachable at {ai.ollama_base_url!r}")

    agent_session = session or AgentSession(iteration=iteration)
    agent_session.reset_for_iteration(iteration)

    if emit:
        emit("chat.system", text=SYSTEM_PROMPT)
        emit("chat.divider", label=f"planner iteration {iteration}")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Planner iteration {iteration}. Find up to {ai.max_candidates_per_round} "
                f"repeater sites toward configured goals. Start with snapshot."
            ),
        },
    ]

    client = chat_client or chat_completions
    max_steps = int(ai.max_agent_steps)
    prefix = "mesh-grow-ai: "

    if ctx.verbose:
        suggest_log(
            ctx.verbose,
            f"{prefix}episode start iteration={iteration} max_steps={max_steps} "
            f"viewshed_budget={ai.max_viewshed_evals_per_episode}",
        )

    for step in range(max_steps):
        if agent_session.submitted:
            break

        if ctx.verbose:
            suggest_log(ctx.verbose, f"{prefix}ollama chat step {step + 1}/{max_steps}", flush=True)

        try:
            resp = client(
                base_url=ai.ollama_base_url,
                model=ai.ollama_model,
                messages=messages,
                tools=TOOL_SCHEMAS,
                temperature=float(ai.temperature),
            )
        except OllamaError:
            raise

        choices = resp.get("choices") or []
        if not choices:
            if ctx.verbose:
                suggest_log(ctx.verbose, f"{prefix}empty Ollama response; stopping")
            break
        message = choices[0].get("message") or {}
        messages.append(message)

        content = message.get("content")
        if content and emit:
            emit("chat.assistant", text=str(content))
        elif content and ctx.verbose:
            suggest_log(ctx.verbose, f"{prefix}assistant: {str(content)[:200]}")

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            if ctx.verbose:
                suggest_log(ctx.verbose, f"{prefix}no tool_calls; stopping")
            break

        for call in tool_calls:
            fn = (call.get("function") or {})
            name = str(fn.get("name") or "")
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(raw_args)
            call_id = str(call.get("id") or name)

            if emit:
                emit("chat.tool_call", call_id=call_id, name=name, arguments=args)
            if ctx.verbose:
                suggest_log(ctx.verbose, f"{prefix}tool {name} args={args}")

            result = dispatch_tool(
                name,
                args,
                ctx=ctx,
                session=agent_session,
                preset_path=preset_path,
            )

            if emit:
                emit(
                    "chat.tool_result",
                    call_id=call_id,
                    name=name,
                    result=truncate_tool_result_for_stream(result),
                )
            if ctx.verbose and name == "evaluate_site":
                budget_left = int(ai.max_viewshed_evals_per_episode) - agent_session.viewshed_evals_used
                suggest_log(
                    ctx.verbose,
                    f"{prefix}evaluate_site done viewshed_budget_remaining={budget_left}",
                    flush=True,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(result, default=str),
                }
            )

            if name == "submit_proposals":
                if ctx.verbose:
                    n = (agent_session.submit_result or {}).get("count", 0)
                    suggest_log(ctx.verbose, f"{prefix}submit_proposals accepted={n}", flush=True)
                break

        if agent_session.submitted:
            break

    if not agent_session.submitted:
        dispatch_tool("submit_proposals", {}, ctx=ctx, session=agent_session, preset_path=preset_path)

    candidates = proposals_to_candidates(agent_session)
    if ctx.verbose:
        suggest_log(ctx.verbose, f"{prefix}episode done: {len(candidates)} candidate(s)", flush=True)
    return candidates
