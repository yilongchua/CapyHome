"""Background sync for living plan files."""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from src.agents.middlewares.handoff_sync import ensure_plan_state, sync_handoff_files_from_state
from src.agents.middlewares.runtime_events import append_runtime_event

logger = logging.getLogger(__name__)


class PlanFileSyncState(AgentState):
    plan: NotRequired[dict | None]
    todo_graph: NotRequired[dict | None]
    artifacts: NotRequired[list[str] | None]
    handoff_artifacts: NotRequired[list[str] | None]
    thread_data: NotRequired[dict | None]


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _run_background_plan_sync(snapshot: dict[str, Any]) -> None:
    time.sleep(1.0)
    try:
        ensure_plan_state(snapshot)
        sync_handoff_files_from_state(snapshot)
    except Exception:
        logger.exception("Background plan file sync failed")


class PlanFileSyncMiddleware(AgentMiddleware[PlanFileSyncState]):
    """Keeps plan.md and its latest backup copy fresh without blocking the foreground turn."""

    state_schema = PlanFileSyncState

    def _is_terminal_ai_response(self, state: PlanFileSyncState) -> bool:
        messages = state.get("messages", []) or []
        if not messages:
            return False
        last = messages[-1]
        if getattr(last, "type", None) != "ai":
            return False
        if getattr(last, "tool_calls", None):
            return False
        return bool(_extract_text(getattr(last, "content", "")).strip())

    @override
    def after_model(self, state: PlanFileSyncState, runtime: Runtime) -> dict | None:
        runtime_context = getattr(runtime, "context", None) or {}
        if bool(runtime_context.get("background_followup")):
            return None
        if not state.get("todo_graph") and not state.get("plan"):
            return None

        ensured_plan = ensure_plan_state(dict(state))
        if ensured_plan is None:
            return None
        if not self._is_terminal_ai_response(state):
            return {"plan": ensured_plan} if not state.get("plan") else None

        snapshot = copy.deepcopy(dict(state))
        snapshot["plan"] = ensured_plan
        worker = threading.Thread(
            target=_run_background_plan_sync,
            kwargs={"snapshot": snapshot},
            name=f"plan-file-sync-{str(runtime_context.get('thread_id') or '')[:8]}",
            daemon=True,
        )
        worker.start()
        append_runtime_event(
            runtime,
            {
                "source": "plan_file_sync_middleware",
                "event": "background_plan_sync_started",
                "summary": "Refreshing living plan files in background",
            },
        )
        if not state.get("plan"):
            return {"plan": ensured_plan}
        return None

    @override
    async def aafter_model(self, state: PlanFileSyncState, runtime: Runtime) -> dict | None:
        return self.after_model(state, runtime)
