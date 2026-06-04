"""Batch clarification answer endpoint.

Front-end submits ``{answers: [{clarification_id, answer}, ...]}`` once the
user has filled in every tab in the side panel (single submit, all-or-
nothing). Backend applies the answers to
``ThreadState.clarifications`` in one update, recomputes
``clarification_pending`` and the DAG's effective ready_ids, and injects
a high-salience operational reminder so the next agent turn consumes the
new answers.

When a run is paused on a ``urgency="blocking"`` interrupt, an optional
``run_id`` in the request body lets the endpoint call ``Command(resume=)``
in addition to the state mutation, otherwise the next scheduled tick
picks up the state diff naturally.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["clarifications"])

logger = logging.getLogger(__name__)


def _langgraph_url() -> str:
    return os.getenv("CAPYBARA_LANGGRAPH_URL") or os.getenv("LANGGRAPH_URL") or "http://localhost:2024"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ClarificationAnswer(BaseModel):
    clarification_id: str = Field(..., description="Id of the clarification entry being answered.")
    answer: str = Field(..., description="The user's answer (free text or option label).")


class ClarifyBatchRequest(BaseModel):
    answers: list[ClarificationAnswer] = Field(default_factory=list)
    run_id: str | None = Field(
        default=None,
        description=(
            "Optional. If the run is paused on a blocking interrupt, supply the run_id "
            "and the endpoint will resume it. Otherwise the next scheduled tick picks up "
            "the state mutation."
        ),
    )


class ClarifyBatchResponse(BaseModel):
    thread_id: str
    applied: int
    unresolved: int
    clarification_pending: bool
    resumed_run_id: str | None = None


_PLAN_MODE_ASSISTANT_ID = "plan_agent"


def _build_replan_human_message(objective: str, answered: list[dict[str, Any]]) -> dict[str, Any]:
    """A fresh, NON-synthetic user message that re-triggers the planner.

    The planner regenerates a plan from a single user prompt (it does not see
    the prior plan), so this message carries both the objective and the
    answers. It deliberately has no `name` so `original_user_prompt` treats it
    as the current user intent and the planner re-plans against it.
    """
    lines = "\n".join(
        f"- {(a.get('question') or 'Question').strip()} → {(a.get('answer') or '').strip()}"
        for a in answered
    )
    head = f'Please revise the plan for "{objective}" using my answers:' if objective else "Please revise the plan using my answers:"
    return {"type": "human", "content": f"{head}\n{lines}"}


def _resolve_plan_clarifications(plan: dict[str, Any], answered_by_id: dict[str, str], now: str) -> dict[str, Any]:
    """Fold batch answers into the nested plan and clear its clarification flag.

    The planner's `before_model` only re-plans (vs. resolving) when
    `plan.clarification_pending` is False, so we must mirror the top-level
    resolution into the plan. Matches by clarification id (planner-issued
    clarifications now carry ids).
    """
    updated = dict(plan)
    answers_record = [a for a in (updated.get("clarification_answers") or []) if isinstance(a, dict)]
    clars = updated.get("clarifications")
    if isinstance(clars, list):
        new_clars: list[Any] = []
        for entry in clars:
            cid = str(entry.get("id") or "").strip() if isinstance(entry, dict) else ""
            if cid and cid in answered_by_id:
                answer = answered_by_id[cid]
                entry = {**entry, "status": "answered", "answer": answer}
                answers_record.append({"question": str(entry.get("question") or "").strip(), "selected_label": answer, "answered_at": now})
            new_clars.append(entry)
        updated["clarifications"] = new_clars
    updated["clarification_answers"] = answers_record
    updated["clarification_pending"] = False
    updated["clarification_resolved"] = True
    updated["clarification_question"] = None
    return updated


def _build_operational_reminder(applied_entries: list[dict[str, Any]]) -> HumanMessage:
    lines = ["<clarifications_resolved>"]
    for entry in applied_entries:
        qid = entry.get("id") or "?"
        question = (entry.get("question") or "").strip()
        answer = (entry.get("answer") or "").strip()
        lines.append(f"- [{qid}] {question} → {answer}")
    lines.append(
        "These answers were just supplied by the user. Update your plan and todo "
        "graph accordingly before any further tool call, and acknowledge the "
        "answer(s) in your next response."
    )
    lines.append("</clarifications_resolved>")
    return HumanMessage(content="\n".join(lines), name="clarifications_resolved")


@router.post(
    "/threads/{thread_id}/clarify",
    response_model=ClarifyBatchResponse,
    summary="Submit a batch of clarification answers",
    description=(
        "Applies one or more answers to pending entries in ThreadState.clarifications "
        "in a single state update. Flips entries to status='answered', recomputes "
        "clarification_pending and the DAG ready_ids, and injects a high-salience "
        "<clarifications_resolved> reminder for the next agent turn."
    ),
)
async def clarify_batch(thread_id: str, request: ClarifyBatchRequest) -> ClarifyBatchResponse:
    if not request.answers:
        raise HTTPException(status_code=400, detail="answers must not be empty")

    try:
        from langgraph_sdk import get_client
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"langgraph_sdk unavailable: {exc}") from exc

    client = get_client(url=_langgraph_url())

    try:
        state = await client.threads.get_state(thread_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found: {exc}") from exc

    values = state.get("values") if isinstance(state, dict) else {}
    if not isinstance(values, dict):
        values = {}

    # Source clarifications from top-level state first; fall back to nested
    # plan.clarifications for any legacy thread that still carries them there.
    raw_clarifications = values.get("clarifications")
    if not isinstance(raw_clarifications, list):
        plan = values.get("plan") if isinstance(values.get("plan"), dict) else {}
        raw_clarifications = plan.get("clarifications") if isinstance(plan, dict) else None
    if not isinstance(raw_clarifications, list) or not raw_clarifications:
        raise HTTPException(status_code=409, detail="No clarifications are pending on this thread.")

    by_id: dict[str, dict[str, Any]] = {}
    for entry in raw_clarifications:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or "").strip()
        if cid:
            by_id[cid] = entry

    answered_entries: list[dict[str, Any]] = []
    now = _utc_now_iso()
    for ans in request.answers:
        cid = ans.clarification_id.strip()
        if cid not in by_id:
            raise HTTPException(status_code=409, detail=f"Unknown clarification id: {cid}")
        existing = by_id[cid]
        if str(existing.get("status") or "pending") == "answered":
            # Idempotent re-answer: skip silently rather than 409 so retries are safe.
            continue
        answered_entries.append(
            {
                "id": cid,
                "status": "answered",
                "answer": ans.answer,
                "answered_at": now,
            }
        )

    if not answered_entries:
        return ClarifyBatchResponse(
            thread_id=thread_id,
            applied=0,
            unresolved=sum(1 for e in raw_clarifications if isinstance(e, dict) and str(e.get("status") or "pending") == "pending"),
            clarification_pending=any(isinstance(e, dict) and str(e.get("status") or "pending") == "pending" for e in raw_clarifications),
        )

    # Compute the post-update clarification list to derive the pending flag
    # and the effective ready_ids. The reducer on ThreadState.clarifications
    # will merge our patches by id when the update lands.
    projected: list[dict[str, Any]] = []
    answered_index = {e["id"]: e for e in answered_entries}
    for entry in raw_clarifications:
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or "").strip()
        if cid in answered_index:
            projected.append({**entry, **answered_index[cid]})
        else:
            projected.append(dict(entry))

    still_pending = [e for e in projected if str(e.get("status") or "pending") == "pending"]
    clarification_pending = bool(still_pending)

    # A draft plan whose clarifications are now fully resolved is revised in
    # place: clear the plan's own clarification flag + fold in the answers, then
    # (below) start a Plan-Mode run so the planner re-plans — reusing plan_id,
    # bumping `revision`, and re-emitting `plan_created`. Work-mode / non-draft
    # clarifications keep the existing "inject a reminder, next tick picks it up"
    # behaviour.
    plan = values.get("plan") if isinstance(values.get("plan"), dict) else None
    plan_status = str(plan.get("status") or "").strip().lower() if isinstance(plan, dict) else ""
    should_replan = isinstance(plan, dict) and plan_status == "draft" and not clarification_pending

    answered_summary = [{"id": e["id"], "question": by_id[e["id"]].get("question"), "answer": e["answer"]} for e in answered_entries]

    # Recompute effective ready_ids so the next agent turn doesn't see stale
    # gating.
    update_payload: dict[str, Any] = {
        "clarifications": answered_entries,  # reducer merges by id
        "clarification_pending": clarification_pending,
    }
    if should_replan:
        update_payload["plan"] = _resolve_plan_clarifications(plan, {e["id"]: e["answer"] for e in answered_entries}, now)
    else:
        # The re-plan trigger message (run input below) replaces the reminder
        # in the plan case; here, the reminder steers the next work-mode tick.
        update_payload["messages"] = [_build_operational_reminder(answered_summary)]

    todo_graph = values.get("todo_graph")
    if isinstance(todo_graph, dict):
        try:
            from src.agents.middlewares.todo_dag_middleware import compute_effective_ready_ids

            nodes = todo_graph.get("nodes") or []
            new_ready = compute_effective_ready_ids(nodes if isinstance(nodes, list) else None, projected)
            new_graph = dict(todo_graph)
            new_graph["ready_ids"] = new_ready
            new_graph["updated_at"] = _utc_now_iso()
            update_payload["todo_graph"] = new_graph
        except Exception:  # pragma: no cover - defensive, recompute is best-effort
            logger.exception("Failed to recompute effective ready_ids on clarify batch")

    try:
        await client.threads.update_state(thread_id, update_payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to apply clarification answers: {exc}") from exc

    resumed_run_id: str | None = None
    if should_replan:
        # Re-enter Plan Mode so the planner revises the draft in place. The
        # planner sees clarification_pending=False (set above) and the fresh
        # answer message, so `_should_plan` triggers an in-place re-plan.
        try:
            objective = str(plan.get("objective") or plan.get("title") or "").strip()
            context: dict[str, Any] = {
                "thread_id": thread_id,
                "current_mode": "plan",
                "mode": "plan",
                "is_plan_mode": True,
                "plan_behavior": "plan_foreground",
                "subagent_enabled": True,
                "thinking_enabled": True,
                "auto_mode": bool(values.get("auto_mode")),
            }
            model_name = values.get("model_name")
            if isinstance(model_name, str) and model_name.strip():
                context["model_name"] = model_name.strip()
            created = await client.runs.create(
                thread_id,
                _PLAN_MODE_ASSISTANT_ID,
                input={"messages": [_build_replan_human_message(objective, answered_summary)]},
                context=context,
                metadata={"trigger": "clarification_replan"},
            )
            resumed_run_id = created.get("run_id") if isinstance(created, dict) else str(created)
        except Exception:
            logger.exception("Failed to start plan re-plan run after clarifications; state was still updated")
    elif request.run_id:
        try:
            run = await client.runs.get(thread_id, request.run_id)
            assistant_id = run.get("assistant_id") if isinstance(run, dict) else None
            if assistant_id:
                created = await client.runs.create(
                    thread_id,
                    assistant_id,
                    command={"resume": {"run_id": request.run_id}},
                    metadata={"resumed_from_run_id": request.run_id, "resume_source": "clarification_batch"},
                )
                resumed_run_id = created.get("run_id") if isinstance(created, dict) else str(created)
        except Exception:
            logger.exception("Failed to resume run %s after applying clarifications; state was still updated", request.run_id)

    return ClarifyBatchResponse(
        thread_id=thread_id,
        applied=len(answered_entries),
        unresolved=len(still_pending),
        clarification_pending=clarification_pending,
        resumed_run_id=resumed_run_id,
    )
