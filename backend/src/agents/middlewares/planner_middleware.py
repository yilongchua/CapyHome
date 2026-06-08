"""Planner orchestration middleware for Plan-mode runs.

Plan authoring now lives in the ``write_plan`` tool
(:mod:`src.tools.builtins.write_plan_tool`): the plan_agent investigates with
read-only tools and then calls ``write_plan`` to emit the canonical ``plan.md``
plus the ``plan`` / ``todo_graph`` state. This middleware no longer runs a blind
one-shot planner LLM call. Its remaining jobs are pure orchestration:

* **Halt the planning turn** after a plan is authored (``plan_just_written``) so
  the agent doesn't keep chatting past the plan, and spawn the work-mode handoff
  when the plan is approved + foreground (auto mode).
* **Advance inline clarifications** — when the user answers a clarification the
  plan carried (via the Execute Plan popup), record the answer, advance/resolve,
  and hand off when fully resolved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from src.agents.common.runtime_context import get_runtime_context
from src.agents.middlewares.message_selection import original_user_prompt
from src.agents.middlewares.plan_execution import (
    apply_clarification_progress,
    approve_plan_if_auto_mode,
    mark_handoff_requested,
    should_spawn_work_handoff,
)
from src.agents.middlewares.runtime_events import append_runtime_event
from src.agents.middlewares.work_run_handoff import spawn_work_mode_handoff

logger = logging.getLogger(__name__)

_WRITE_PLAN_REQUIRED_REMINDER_NAME = "planner_write_plan_required"
_MAX_WRITE_PLAN_REQUIRED_REMINDERS = 1


class PlannerState(AgentState):
    plan: NotRequired[dict | None]
    todo_graph: NotRequired[dict | None]
    todos: NotRequired[list | None]
    handoff_artifacts: NotRequired[list[str] | None]
    artifacts: NotRequired[list[str] | None]
    plan_evaluated: NotRequired[bool]
    plan_history: NotRequired[list[dict[str, Any]] | None]
    # Transient signal written by the ``write_plan`` tool: a plan was authored
    # this turn, so the planner should finalize (handoff + halt) on the next
    # ``before_model`` and clear the flag.
    plan_just_written: NotRequired[bool]


def _runtime_context(runtime: Runtime) -> dict[str, Any]:
    return get_runtime_context(runtime)


def _plan_behavior(runtime: Runtime) -> str:
    return str(_runtime_context(runtime).get("plan_behavior") or "").strip().lower()


def _auto_mode_enabled(runtime: Runtime, state: PlannerState) -> bool:
    ctx = _runtime_context(runtime)
    if bool(ctx.get("auto_mode")):
        return True
    return bool(state.get("auto_mode"))


def _count_write_plan_required_reminders(messages: list[Any]) -> int:
    return sum(1 for message in messages if getattr(message, "name", None) == _WRITE_PLAN_REQUIRED_REMINDER_NAME)


class PlannerMiddleware(AgentMiddleware[PlannerState]):
    """Orchestrates plan-turn halting + work-mode handoff (no plan generation)."""

    state_schema = PlannerState

    def __init__(self, **_ignored: Any) -> None:
        # Accepts (and ignores) legacy constructor kwargs — plan generation moved
        # to the ``write_plan`` tool, so the model/limit/config knobs are no
        # longer needed here. Kept tolerant so existing call sites don't break.
        super().__init__()

    def _finalize_plan_handoff(
        self,
        *,
        payload: dict[str, Any],
        plan_dict: dict[str, Any],
        runtime: Runtime,
        auto_mode: bool,
        user_prompt: str | None,
        thread_name_suffix: str,
        clarification_pending: bool,
    ) -> dict[str, Any]:
        """Spawn a work-mode handoff and (conditionally) end the planning turn.

        ``jump_to=end`` only fires when ALL of: we have a real ``thread_id``,
        the plan has no pending clarifications, and ``plan_behavior ==
        'plan_foreground'``. On a successful spawn we emit a
        ``plan_handoff_started`` SSE so the frontend has a clean transition
        signal between plan-mode and work-mode event streams.
        """
        plan_behavior = _plan_behavior(runtime)
        runtime_context = _runtime_context(runtime)
        thread_id = runtime_context.get("thread_id")

        handoff_spawned = False
        if isinstance(thread_id, str) and thread_id:
            requested_model_name = runtime_context.get("model_name")
            plan_dict = mark_handoff_requested(plan_dict)
            payload["plan"] = plan_dict
            spawn_work_mode_handoff(
                thread_id=thread_id,
                requested_model_name=requested_model_name if isinstance(requested_model_name, str) else None,
                auto_mode=auto_mode,
                original_user_request=user_prompt or None,
                thread_name_suffix=thread_name_suffix,
            )
            handoff_spawned = True
            try:
                writer = get_stream_writer()
                writer({
                    "type": "plan_handoff_started",
                    "source": "planner_middleware",
                    "plan_id": plan_dict.get("plan_id"),
                    "status": plan_dict.get("status"),
                    "thread_id": thread_id,
                })
            except Exception:
                logger.exception("Failed to emit plan_handoff_started SSE")
            append_runtime_event(
                runtime,
                {
                    "source": "planner_middleware",
                    "event": "plan_handoff_started",
                    "plan_id": plan_dict.get("plan_id"),
                },
            )

        if handoff_spawned and not clarification_pending and plan_behavior == "plan_foreground":
            payload["jump_to"] = "end"
        return payload

    def _after_plan_written(self, state: PlannerState, runtime: Runtime) -> dict[str, Any]:
        """Finalize a plan authored this turn by ``write_plan``.

        Clears the transient flag, spawns the work handoff when the plan is
        approved + foreground, and halts the planning turn so the agent does
        not continue past the plan. Auto-mode with a pending clarification must
        not block, so we fall through (no ``jump_to``) in that case.
        """
        plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
        auto_mode = _auto_mode_enabled(runtime, state)
        clarification_pending = bool(plan.get("clarification_pending"))
        plan_status = str(plan.get("status") or "").strip().lower()

        payload: dict[str, Any] = {"plan_just_written": False}
        if should_spawn_work_handoff(plan, plan_behavior=_plan_behavior(runtime), plan_status=plan_status):
            messages = state.get("messages", []) or []
            user_prompt = original_user_prompt(messages) or ""
            payload = self._finalize_plan_handoff(
                payload=payload,
                plan_dict=dict(plan),
                runtime=runtime,
                auto_mode=auto_mode,
                user_prompt=user_prompt,
                thread_name_suffix="-planner-auto",
                clarification_pending=clarification_pending,
            )
            payload["plan_just_written"] = False

        if _plan_behavior(runtime) == "plan_foreground" and not (clarification_pending and auto_mode):
            payload["jump_to"] = "end"
        return payload

    @override
    def before_model(self, state: PlannerState, runtime: Runtime) -> dict | None:
        # 1. A plan was just authored via write_plan this turn: finalize + halt.
        if bool(state.get("plan_just_written")):
            return self._after_plan_written(state, runtime)

        # 2. Resume after an inline clarification (Execute Plan popup) was answered.
        current_plan = state.get("plan")
        if isinstance(current_plan, dict) and bool(current_plan.get("clarification_pending")):
            messages = state.get("messages", []) or []
            progress = apply_clarification_progress(current_plan, messages)
            if progress is not None:
                resolved_plan = dict(progress["plan"])
                auto_mode = _auto_mode_enabled(runtime, state)
                if not bool(resolved_plan.get("clarification_pending")):
                    resolved_plan = approve_plan_if_auto_mode(resolved_plan, auto_mode=auto_mode)
                append_runtime_event(
                    runtime,
                    {
                        "source": "planner_middleware",
                        "decision": "clarification_resolved",
                        "plan_id": resolved_plan.get("plan_id"),
                        "clarification_pending": bool(resolved_plan.get("clarification_pending")),
                    },
                )
                payload: dict[str, Any] = {"plan": resolved_plan}
                if progress.get("messages"):
                    payload["messages"] = progress["messages"]
                    return payload

                plan_status = str(resolved_plan.get("status") or "").strip().lower()
                clarification_pending = bool(resolved_plan.get("clarification_pending"))
                if should_spawn_work_handoff(
                    resolved_plan,
                    plan_behavior=_plan_behavior(runtime),
                    plan_status=plan_status,
                ):
                    user_prompt = original_user_prompt(messages) or ""
                    payload = self._finalize_plan_handoff(
                        payload=payload,
                        plan_dict=resolved_plan,
                        runtime=runtime,
                        auto_mode=auto_mode,
                        user_prompt=user_prompt,
                        thread_name_suffix="-planner-clarification-auto",
                        clarification_pending=clarification_pending,
                    )
                return payload

        # Otherwise: nothing to orchestrate — let the agent investigate and call
        # write_plan. The planner no longer generates plans here.
        return None

    @override
    def after_model(self, state: PlannerState, runtime: Runtime) -> dict | None:
        """Re-engage the planner if it tries to finish without calling write_plan."""
        if state.get("plan") or bool(state.get("plan_just_written")):
            return None

        messages = state.get("messages") or []
        if not messages:
            return None
        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None
        if getattr(last_msg, "tool_calls", None):
            return None
        if _count_write_plan_required_reminders(messages) >= _MAX_WRITE_PLAN_REQUIRED_REMINDERS:
            return None

        append_runtime_event(runtime, {"source": "planner_middleware", "event": "write_plan_required_retry"})
        reminder = HumanMessage(
            name=_WRITE_PLAN_REQUIRED_REMINDER_NAME,
            content=(
                "<system_reminder>\n"
                "Plan Mode cannot finish with a conversational answer. Your terminal action must be a single `write_plan` "
                "tool call that creates the canonical plan.md, plan state, and todo_graph. Do not answer the user's task; "
                "call `write_plan` now with the execution plan.\n"
                "</system_reminder>"
            ),
        )
        return {"messages": [reminder], "jump_to": "model"}

    @override
    async def abefore_model(self, state: PlannerState, runtime: Runtime) -> dict | None:
        return await asyncio.to_thread(self.before_model, state, runtime)

    @override
    async def aafter_model(self, state: PlannerState, runtime: Runtime) -> dict | None:
        return await asyncio.to_thread(self.after_model, state, runtime)


# End the planning turn before the lead model runs once a plan has been authored.
PlannerMiddleware.before_model.__can_jump_to__ = ["end"]  # type: ignore[attr-defined]
PlannerMiddleware.abefore_model.__can_jump_to__ = ["end"]  # type: ignore[attr-defined]
PlannerMiddleware.after_model.__can_jump_to__ = ["model"]  # type: ignore[attr-defined]
PlannerMiddleware.aafter_model.__can_jump_to__ = ["model"]  # type: ignore[attr-defined]
