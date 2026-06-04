"""Gate execution tools while a plan is still in draft (Work Mode backstop).

Plan-Mode tool restriction is handled up-front by the per-mode tool catalogs
(``internal_tools_plan.json`` excludes ``write_file``/``bash``/``str_replace``,
and intentionally *exposes* ``web_search``, ``grep`` and the read-only planning
subagents via ``task``). This middleware therefore no longer gates Plan Mode at
all — doing so would re-block those tools and force an extra scope-classifier
model call per search.

Its remaining job is a Work-Mode backstop: when a plan exists in state and is
still ``draft``, execution tools are blocked until the plan is explicitly
approved via the Execute Plan action (auto-mode marks the plan ``approved``
up-front, so it is unaffected). It also blocks execution while a clarification
is still pending so the run cannot silently fabricate answers the user never
gave.

The ``/recover`` command is exempt: it is an explicit user instruction to finish
an existing plan ("complete the incomplete todos"), so the gate treats the
``recover_todo_command`` runtime-context flag as approval and lets the run
proceed even on a draft plan.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

# Tools that may run while a plan is still draft (planning / clarification only).
_ALLOWED_WHEN_DRAFT = {
    "ask_user_for_clarification",
    "write_todos",
    "recall",
}


def _is_read_only_tool(tool_name: str) -> bool:
    return tool_name.startswith("read_") or tool_name.startswith("list_") or tool_name.startswith("get_")


def _is_plan_mode(runtime: Any) -> bool:
    context = getattr(runtime, "context", None) or {}
    raw = context.get("current_mode") or context.get("mode") or ("plan" if context.get("is_plan_mode") else "")
    return str(raw).strip().lower() == "plan"


class PlanExecutionGateState(AgentState):
    plan: dict[str, Any] | None


def _normalize_plan_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"draft", "approved", "executing", "completed"}:
        return value
    if value:
        logger.warning("Unknown plan status %r coerced to 'draft'", value)
    return "draft"


class PlanExecutionGateMiddleware(AgentMiddleware[PlanExecutionGateState]):
    """Blocks execution tools while a Work-Mode plan is still draft.

    Plan Mode is intentionally NOT gated here — the per-mode tool catalog
    already restricts the Plan-Mode surface. This is a Work-Mode backstop so a
    draft, never-approved plan cannot run to completion until the user approves
    it via the Execute Plan action. The ``/recover`` command (carrying the
    ``recover_todo_command`` context flag) is exempt — it is itself an explicit
    instruction to complete the plan.
    """

    state_schema = PlanExecutionGateState

    def _build_block_command(self, request: ToolCallRequest, message: str) -> Command:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=message,
                        tool_call_id=request.tool_call.get("id", ""),
                        name=request.tool_call.get("name", "tool"),
                    )
                ]
            },
        )

    def _maybe_block(self, request: ToolCallRequest) -> Command | None:
        # Plan Mode is restricted by the per-mode tool catalog, not at runtime.
        # web_search, grep, and the finder subagents via `task` are intentionally
        # available there, so the gate must not interfere.
        if _is_plan_mode(request.runtime):
            return None

        # `/recover` is an explicit user instruction to finish an existing plan
        # in Work Mode ("complete the incomplete todos"). Treat it as approval:
        # bypass the draft gate entirely so recovery is never blocked.
        runtime_context = getattr(request.runtime, "context", None) or {}
        if bool(runtime_context.get("recover_todo_command")):
            return None

        state = request.state if isinstance(getattr(request, "state", None), dict) else {}
        if not state:
            runtime_obj = getattr(request, "runtime", None)
            state = getattr(runtime_obj, "state", {}) if isinstance(getattr(runtime_obj, "state", None), dict) else {}
        plan = state.get("plan") if isinstance(state, dict) else None
        if not isinstance(plan, dict):
            return None

        if _normalize_plan_status(plan.get("status")) != "draft":
            return None

        tool_name = str(request.tool_call.get("name") or "")

        if bool(plan.get("clarification_pending")) and tool_name != "ask_user_for_clarification":
            question = str(plan.get("clarification_question") or "Please answer the pending clarification before execution.")
            return self._build_block_command(
                request,
                (
                    "[plan_gate] Clarification is required before plan execution. "
                    "Call `ask_user_for_clarification` first.\n"
                    f"Pending question: {question}"
                ),
            )

        if tool_name in _ALLOWED_WHEN_DRAFT or _is_read_only_tool(tool_name):
            return None

        plan_id = str(plan.get("plan_id") or "").strip()
        plan_hint = f" Plan ID: {plan_id}." if plan_id else ""
        return self._build_block_command(
            request,
            (
                "[plan_gate] Plan is still draft. Execution tools are blocked until explicit plan approval "
                f"via the Execute Plan action in the UI (or enable auto-mode).{plan_hint} "
                "Do not substitute training-data answers for blocked research tools."
            ),
        )

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return handler(request)

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command:
        blocked = self._maybe_block(request)
        if blocked is not None:
            return blocked
        return await handler(request)
