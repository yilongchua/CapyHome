"""Batch clarification answer endpoint.

Front-end submits ``{answers: [{clarification_id, answer}, ...]}`` once the
user has filled in every tab in the side panel (single submit, all-or-
nothing). Backend applies the answers to
``ThreadState.clarifications`` in one update, recomputes
``clarification_pending`` and the DAG's effective ready_ids, and injects
a high-salience operational reminder for active Work Mode.

In Plan Mode, resolved clarification answers are packaged with the original user
request, any existing plan reference, and the full answered-question set. The
endpoint then starts a fresh Plan Mode run; the planner may recover prior
context with read-only tools and must call ``write_plan`` as its terminal action.
``write_plan`` remains the only canonical plan-authoring path.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from src.agents.middlewares.message_selection import extract_text, is_synthetic_human_message, message_type
from src.agents.middlewares.plan_execution import work_execution_underway

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


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content", "")
    return getattr(message, "content", "")


def _latest_real_user_prompt(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if message_type(message) != "human" or is_synthetic_human_message(message):
            continue
        text = extract_text(_message_content(message)).strip()
        if text:
            return text
    return ""


def _plan_reference(plan: dict[str, Any] | None) -> str:
    default_path = "/mnt/user-data/workspace/plan.md"
    lines = ["Existing plan reference:"]
    if not isinstance(plan, dict):
        lines.extend(
            [
                f"- plan.md: {default_path}",
                "- Exists: unknown",
                "- State: no structured plan is currently recorded",
            ]
        )
        return "\n".join(lines)
    plan_path = str(plan.get("plan_path") or default_path).strip() or default_path
    title = str(plan.get("title") or "").strip()
    status = str(plan.get("status") or "").strip()
    objective = str(plan.get("objective") or "").strip()
    summary = str(plan.get("summary") or "").strip()
    todo_ids = [str(item).strip() for item in (plan.get("todo_ids") or []) if str(item).strip()]
    lines.extend(
        [
            f"- plan.md: {plan_path}",
            "- Exists: unknown",
        ]
    )
    if title:
        lines.append(f"- Title: {title}")
    if status:
        lines.append(f"- Status: {status}")
    if objective:
        lines.append(f"- Objective: {objective}")
    if summary:
        lines.append(f"- Summary: {summary}")
    if todo_ids:
        lines.append(f"- Todo ids: {', '.join(todo_ids[:20])}")
    return "\n".join(lines)


def _answered_summary(projected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in projected:
        if str(entry.get("status") or "pending") != "answered":
            continue
        answer = str(entry.get("answer") or "").strip()
        if not answer:
            continue
        summary.append(
            {
                "id": str(entry.get("id") or "").strip(),
                "question": str(entry.get("question") or "Question").strip(),
                "answer": answer,
            }
        )
    return summary


def _build_replan_human_message(
    *,
    original_request: str,
    current_plan: dict[str, Any] | None,
    answered: list[dict[str, Any]],
) -> dict[str, Any]:
    """A fresh, NON-synthetic user message that re-triggers the planner.

    Clarification answers are user input, so Plan Mode should reconsider the
    plan from the original request plus these answers. The message deliberately
    has no `name` so downstream user-prompt selection treats it as a real
    planning request.
    """
    answer_lines = "\n".join(f"- {(a.get('question') or 'Question').strip()} → {(a.get('answer') or '').strip()}" for a in answered)
    sections = [
        "Please regenerate the Plan Mode draft using the original request, existing plan reference, and clarification answers below.",
        "You must call `write_plan` as your terminal action.",
    ]
    if original_request:
        sections.append(f"Original request:\n{original_request}")
    sections.append(_plan_reference(current_plan))
    sections.append(f"Clarification answer(s):\n{answer_lines}")
    sections.append(
        "Prior planning context:\n"
        "The previous planner turn may have already used read-only tools such as web search, file reads, grep, or recall. "
        "Reuse relevant information visible in this thread. If needed, inspect plan.md or use available read-only tools, including recall when relevant, "
        "to verify or recover context before calling `write_plan`."
    )
    return {"type": "human", "content": "\n\n".join(sections)}


def _should_start_plan_turn(values: dict[str, Any], *, clarification_pending: bool) -> bool:
    """Resolved Plan Mode clarifications always re-enter the planner.

    Active Work Mode remains the exception: those clarification answers steer
    the running executor rather than creating a new draft plan.
    """
    if clarification_pending:
        return False
    return not work_execution_underway(values)


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

    # Any fully resolved Plan Mode clarification is treated as fresh user input:
    # start a new Plan Mode turn and require `write_plan` as the terminal action.
    # If a plan already exists, mirror the answers into it before the new turn so
    # the planner sees resolved state and `write_plan` can preserve plan_id /
    # bump revision. Active Work Mode remains the exception: those answers steer
    # the executor via an operational reminder instead of regenerating a draft.
    plan = values.get("plan") if isinstance(values.get("plan"), dict) else None
    should_start_plan_turn = _should_start_plan_turn(values, clarification_pending=clarification_pending)

    answered_summary = _answered_summary(projected)

    # Recompute effective ready_ids so the next agent turn doesn't see stale
    # gating.
    update_payload: dict[str, Any] = {
        "clarifications": answered_entries,  # reducer merges by id
        "clarification_pending": clarification_pending,
    }
    if should_start_plan_turn and isinstance(plan, dict):
        update_payload["plan"] = _resolve_plan_clarifications(plan, {e["id"]: e["answer"] for e in answered_entries}, now)
    if not should_start_plan_turn:
        # The re-plan trigger message (run input below) replaces the reminder
        # in Plan Mode; here, the reminder steers the next work-mode tick.
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
    if should_start_plan_turn:
        # Re-enter Plan Mode so the agent incorporates the clarification answer
        # and calls `write_plan` again. When a prior plan exists, `write_plan`
        # reuses plan_id and bumps the revision; when no plan exists yet, this
        # creates the first canonical draft.
        try:
            original_request = _latest_real_user_prompt(values.get("messages"))
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
                input={
                    "messages": [
                        _build_replan_human_message(
                            original_request=original_request,
                            current_plan=plan,
                            answered=answered_summary,
                        )
                    ]
                },
                context=context,
                metadata={"trigger": "clarification_replan"},
            )
            resumed_run_id = created.get("run_id") if isinstance(created, dict) else str(created)
        except Exception:
            logger.exception("Failed to start Plan Mode run after clarifications; state was still updated")
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
