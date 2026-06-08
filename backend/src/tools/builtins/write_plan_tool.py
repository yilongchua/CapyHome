"""``write_plan`` — the plan_agent's terminal action: emit the structured plan.

This replaces the old ``PlannerMiddleware`` one-shot LLM call. In Plan Mode the
agent investigates with read-only tools (``ls``/``grep``/``read_file``/``task``/
``web_search``/``recall``), asks the user via ``ask_user_for_clarification`` when
genuinely blocked, and then calls ``write_plan(...)`` to author the canonical
``plan.md`` plus the ``plan`` / ``todo_graph`` state the work_agent reads on
handoff.

The tool ARGUMENTS are the plan contract — the schema is where determinism now
lives (instead of a separate blind model call). The handler runs the exact same
pipeline the middleware used to run: ``normalize_todo_nodes`` → DAG ready-set →
``serialize_plan_md`` → write ``plan.md`` + versioned snapshot → ``plan_created``
SSE → ``Command(update=...)``.

Turn-ending and the work-mode handoff stay in ``PlannerMiddleware`` (it observes
the ``plan_just_written`` flag this tool sets and decides whether to spawn the
work run and halt the planning turn).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, TypedDict
from uuid import uuid4

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.common.handoff import serialize_plan_md
from src.agents.middlewares._fs_utils import atomic_write_text
from src.agents.middlewares.handoff_sync import render_plan_md, versioned_plan_filename
from src.agents.middlewares.runtime_events import append_runtime_event
from src.agents.middlewares.todo_dag_middleware import (
    _legacy_todos,
    _materialize_ready_ids,
    normalize_todo_nodes,
)
from src.config.planner_config import get_planner_config
from src.sandbox.path_mapping import to_virtual_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool argument schema (the plan contract)
# ---------------------------------------------------------------------------


class PlanStepInput(TypedDict, total=False):
    description: str
    completion_requirement: str
    subagent_types: list[str]
    tools: list[str]
    output_artifact_path: str | None


class PlanTodoInput(TypedDict, total=False):
    id: str
    content: str
    rationale: str
    depends_on: list[str]
    owner: Literal["lead", "subagent"]
    subagent_type: str | None
    objective: str
    failure_fallback: str
    completion_requirement: str
    steps: list[PlanStepInput]


class PlanRiskInput(TypedDict, total=False):
    risk: str
    mitigation: str


class PlanClarificationOptionInput(TypedDict, total=False):
    label: str
    recommended: bool
    description: str | None


class PlanClarificationInput(TypedDict, total=False):
    question: str
    options: list[PlanClarificationOptionInput]


class _PlanToolState(TypedDict, total=False):
    plan: NotRequired[dict | None]
    plan_history: NotRequired[list[dict[str, Any]] | None]
    todo_graph: NotRequired[dict | None]
    todos: NotRequired[list | None]
    thread_data: NotRequired[dict | None]
    messages: NotRequired[list | None]


# ---------------------------------------------------------------------------
# Normalization helpers (moved from planner_middleware so the tool owns the
# contract end-to-end).
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_risks(raw: Any) -> list[dict[str, str]]:
    risks: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        risk_text = str(item.get("risk") or "").strip()
        mitigation_text = str(item.get("mitigation") or "").strip()
        if not risk_text and not mitigation_text:
            continue
        risks.append({"risk": risk_text, "mitigation": mitigation_text})
    return risks


def _normalize_todo_steps(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        output_artifact_path = item.get("output_artifact_path")
        if output_artifact_path is not None:
            output_artifact_path = str(output_artifact_path).strip() or None
        normalized.append(
            {
                "description": description,
                "subagent_types": _clean_str_list(item.get("subagent_types")),
                "tools": _clean_str_list(item.get("tools")),
                "output_artifact_path": output_artifact_path,
                "completion_requirement": str(item.get("completion_requirement") or "").strip(),
            }
        )
    return normalized


def _coerce_todo_nodes(raw_todos: list[PlanTodoInput], max_steps: int) -> list[dict[str, Any]]:
    """Coerce typed tool todos into the dict shape ``normalize_todo_nodes`` expects."""
    coerced: list[dict[str, Any]] = []
    for i, item in enumerate(list(raw_todos)[:max_steps]):
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        coerced.append(
            {
                "id": item.get("id") or f"todo-{i + 1}",
                "content": content,
                "status": "pending",
                "depends_on": [str(d) for d in (item.get("depends_on") or [])],
                "owner": item.get("owner") or "lead",
                "subagent_type": item.get("subagent_type"),
                "rationale": str(item.get("rationale") or "").strip(),
                "objective": str(item.get("objective") or "").strip(),
                "completion_requirement": str(item.get("completion_requirement") or "").strip(),
                "failure_fallback": str(item.get("failure_fallback") or "").strip(),
                "steps": _normalize_todo_steps(item.get("steps")),
            }
        )
    if not coerced:
        coerced = [{"id": "todo-1", "content": "Complete the user request end-to-end.", "status": "pending", "depends_on": [], "owner": "lead"}]
    return coerced


def _ordered_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recommended = [o for o in options if o.get("recommended")]
    non_recommended = [o for o in options if not o.get("recommended")]
    return [*recommended[:1], *non_recommended, *recommended[1:]]


def _normalize_clarifications(raw: Any, max_clarifications: int) -> list[dict[str, Any]]:
    """Dedupe by question text, order recommended-first, cap options at 4."""
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for clar in raw or []:
        if not isinstance(clar, dict):
            continue
        question = str(clar.get("question") or "").strip()
        if not question or question.lower() in seen:
            continue
        raw_options = [o for o in (clar.get("options") or []) if isinstance(o, dict) and str(o.get("label") or "").strip()]
        options = [
            {
                "label": str(o.get("label") or "").strip(),
                "recommended": bool(o.get("recommended", False)),
                "description": o.get("description"),
            }
            for o in _ordered_options(raw_options)
        ][:4]
        if len(options) < 2:
            continue
        if not any(o["recommended"] for o in options):
            options[0]["recommended"] = True
        seen.add(question.lower())
        deduped.append({"question": question, "options": options})
    return deduped[:max_clarifications]


def _auto_selected_option(clarification: dict[str, Any]) -> dict[str, Any] | None:
    options = clarification.get("options")
    if not isinstance(options, list) or not options:
        return None
    for option in options:
        if isinstance(option, dict) and option.get("recommended"):
            return option
    first = options[0]
    return first if isinstance(first, dict) else None


def _ctx(runtime: ToolRuntime) -> dict[str, Any]:
    ctx = getattr(runtime, "context", None)
    return ctx if isinstance(ctx, dict) else {}


def _auto_mode_enabled(runtime: ToolRuntime, state: dict[str, Any]) -> bool:
    if bool(_ctx(runtime).get("auto_mode")):
        return True
    return bool(state.get("auto_mode"))


def _build_auto_clarification_answers(inline_clarifications: list[dict[str, Any]], *, auto_mode: bool) -> list[dict[str, Any]]:
    if not auto_mode:
        return []
    answers: list[dict[str, Any]] = []
    for clarification in inline_clarifications:
        selected = _auto_selected_option(clarification)
        if selected is None:
            continue
        selected_label = str(selected.get("label") or "").strip()
        if not selected_label:
            continue
        description = selected.get("description")
        answers.append(
            {
                "question": str(clarification.get("question") or "").strip(),
                "selected_label": selected_label,
                "selected_description": str(description).strip() if description else None,
                "answered_at": _utc_now_iso(),
            }
        )
    return answers


def _plan_identity(state: dict[str, Any]) -> tuple[str, int]:
    existing_plan = state.get("plan") if isinstance(state.get("plan"), dict) else None
    is_replan = isinstance(existing_plan, dict) and not bool(existing_plan.get("clarification_pending"))
    if is_replan:
        plan_id = str(existing_plan.get("plan_id") or "").strip() or f"plan-{uuid4().hex[:10]}"
        revision = int(existing_plan.get("revision") or 0) + 1
        return plan_id, revision
    return f"plan-{uuid4().hex[:10]}", 0


def _build_clarifications_payload(
    inline_clarifications: list[dict[str, Any]],
    *,
    auto_mode: bool,
    auto_answers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for index, clarification in enumerate(inline_clarifications):
        auto_answer = auto_answers[index] if auto_mode and index < len(auto_answers) else None
        entry: dict[str, Any] = {
            "id": f"clarif-{uuid4().hex[:8]}",
            "question": clarification["question"],
            "clarification_type": "approach_choice",
            "options": clarification["options"],
            "blocks": [],
            "status": "answered" if auto_mode else "pending",
            "answer": auto_answer["selected_label"] if auto_answer else None,
        }
        if auto_answer:
            entry["answered_at"] = auto_answer["answered_at"]
        payload.append(entry)
    return payload


def _serialize_plan_content(
    *,
    plan_id: str,
    title: str,
    summary: str,
    status: str,
    domain: str,
    created_at: str,
    objective: str,
    assumptions: list[str],
    constraints: list[str],
    risks: list[dict[str, str]],
    acceptance_criteria: list[str],
    clarifications_payload: list[dict[str, Any]],
    clarification_answers: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    ready_ids: list[str],
) -> str:
    canonical_plan_for_md = {
        "plan_id": plan_id,
        "title": title,
        "status": status,
        "domain": domain,
        "target_mode": "work",
        "created_at": created_at,
        "objective": objective,
        "summary": summary,
        "assumptions": assumptions,
        "constraints": constraints,
        "risks": risks,
        "acceptance_criteria": acceptance_criteria,
        "clarifications": clarifications_payload or [],
    }
    canonical_todo_graph_for_md = {"nodes": nodes, "ready_ids": ready_ids}

    def _render_body(_plan: dict, _nodes: list[dict]) -> str:
        return render_plan_md(
            title,
            summary,
            _nodes,
            domain=domain,
            plan_id=plan_id,
            status=status,
            created_at=created_at,
            objective=objective,
            assumptions=assumptions,
            constraints=constraints,
            risks=risks,
            acceptance_criteria=acceptance_criteria,
            clarifications=clarifications_payload or None,
            clarification_answers=clarification_answers or None,
            include_frontmatter=False,
        )

    return serialize_plan_md(canonical_plan_for_md, canonical_todo_graph_for_md, body_renderer=_render_body)


def _write_plan_artifacts(
    *,
    state: dict[str, Any],
    title: str,
    created_at_dt: datetime,
    plan_md_content: str,
) -> tuple[list[str], str | None, str | None]:
    thread_data = state.get("thread_data") or {}
    plan_root = thread_data.get("workspace_path")
    artifact_paths: list[str] = []
    plan_path: str | None = None
    latest_alias_path: str | None = None
    if not plan_root:
        return artifact_paths, plan_path, latest_alias_path

    plans_dir = Path(plan_root) / "plans"
    versioned_plan_file = plans_dir / versioned_plan_filename(title, created_at_dt)
    latest_plan_alias_file = Path(plan_root) / "plan.md"
    try:
        atomic_write_text(versioned_plan_file, plan_md_content)
        atomic_write_text(latest_plan_alias_file, plan_md_content)
        plan_path = to_virtual_path(str(versioned_plan_file), thread_data) or str(versioned_plan_file)
        latest_alias_path = to_virtual_path(str(latest_plan_alias_file), thread_data) or str(latest_plan_alias_file)
        artifact_paths.extend([plan_path, latest_alias_path])
    except Exception:
        logger.exception("write_plan: failed to write plan artifacts")
    return artifact_paths, plan_path, latest_alias_path


def _emit_plan_created(
    runtime: ToolRuntime[ContextT, _PlanToolState] | None,
    *,
    title: str,
    summary: str,
    domain: str,
    plan_id: str,
    plan_status: str,
    nodes: list[dict[str, Any]],
    clarification_pending: bool,
    clarifications_payload: list[dict[str, Any]],
    plan_path: str | None,
    revision: int,
) -> None:
    if runtime is not None:
        append_runtime_event(
            runtime,
            {
                "source": "write_plan_tool",
                "decision": "plan_auto_approved" if plan_status == "approved" else "plan_created",
                "todo_count": len(nodes),
                "domain": domain,
                "has_deps": any(n.get("depends_on") for n in nodes),
                "has_clarifications": bool(clarifications_payload),
                "plan_status": plan_status,
            },
        )

    try:
        writer = get_stream_writer()
        writer({
            "type": "plan_created",
            "source": "write_plan_tool",
            "title": title,
            "summary": summary,
            "domain": domain,
            "plan_id": plan_id,
            "status": plan_status,
            "auto_approved": plan_status == "approved",
            "todo_count": len(nodes),
            "first_todos": [n.get("content", "") for n in nodes[:5]],
            "plan_path": plan_path,
            "clarification_pending": clarification_pending,
            "clarifications": clarifications_payload,
            "clarification_index": 0,
            "revision": revision,
        })
    except Exception:
        logger.exception("write_plan: failed to emit plan_created SSE")


def _build_plan_dict(
    *,
    state: dict[str, Any],
    plan_id: str,
    plan_status: str,
    title: str,
    objective: str,
    summary: str,
    assumptions: list[str],
    constraints: list[str],
    risks: list[dict[str, str]],
    acceptance_criteria: list[str],
    domain: str,
    nodes: list[dict[str, Any]],
    plan_path: str | None,
    latest_alias_path: str | None,
    clarifications_payload: list[dict[str, Any]],
    clarification_pending: bool,
    clarification_answers: list[dict[str, Any]],
    primary_question: str | None,
    created_at: str,
    revision: int,
    approved_at: str | None,
) -> dict[str, Any]:
    plan_dict: dict[str, Any] = {
        "plan_id": plan_id,
        "status": plan_status,
        "title": title,
        "objective": objective,
        "summary": summary,
        "assumptions": assumptions,
        "constraints": constraints,
        "risks": risks,
        "acceptance_criteria": acceptance_criteria,
        "domain": domain,
        "todo_ids": [node["id"] for node in nodes],
        "plan_path": plan_path,
        "latest_alias_path": latest_alias_path,
        "clarifications": clarifications_payload,
        "clarification_pending": clarification_pending,
        "clarification_index": 0,
        "clarification_answers": clarification_answers,
        "clarification_resolved": not clarification_pending,
        "clarification_question": primary_question,
        "created_at": created_at,
        "revision": revision,
        "human_messages_at_plan": sum(1 for msg in (state.get("messages") or []) if getattr(msg, "type", None) == "human"),
        "updated_at": _utc_now_iso(),
    }
    if approved_at:
        plan_dict["approved_at"] = approved_at
        plan_dict["awaiting_execution_approval"] = False
    else:
        plan_dict["awaiting_execution_approval"] = True
    return plan_dict


def _build_plan_history(state: dict[str, Any], *, plan_id: str, title: str, plan_path: str | None, created_at: str, plan_status: str) -> list[dict[str, Any]]:
    existing_history = [item for item in (state.get("plan_history") or []) if isinstance(item, dict)]
    return [
        *existing_history,
        {"plan_id": plan_id, "title": title, "path": plan_path, "created_at": created_at, "status": plan_status},
    ][-40:]


# ---------------------------------------------------------------------------
# write_plan tool
# ---------------------------------------------------------------------------


@tool("write_plan")
def write_plan_tool(
    runtime: ToolRuntime[ContextT, _PlanToolState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    title: str,
    objective: str,
    summary: str,
    todos: list[PlanTodoInput],
    domain: str = "generic",
    assumptions: list[str] | None = None,
    constraints: list[str] | None = None,
    risks: list[PlanRiskInput] | None = None,
    acceptance_criteria: list[str] | None = None,
    clarifications: list[PlanClarificationInput] | None = None,
) -> Command:
    """Emit the finished execution plan as the canonical plan.md + plan state.

    Call this ONCE, as the final action of a plan turn, after you have
    investigated enough to scope the work. Each todo should carry an observable
    completion_requirement; add depends_on only for real data dependencies.

    Args:
        title: Short plan title (<= 8 words).
        objective: One-paragraph description of the intended end state.
        summary: 1-2 sentence overview of what will be accomplished.
        todos: Ordered execution todos with ids ("todo-1", ...), rationale,
            depends_on, and observable steps[].completion_requirement.
        domain: One of code|research|legal|life_admin|data|shopping|food|
            events|travel|learning|generic.
        assumptions: Stated assumptions the plan relies on.
        constraints: Hard constraints the work must respect.
        risks: [{risk, mitigation}] for the main delivery risks.
        acceptance_criteria: Observable success criteria for the whole plan.
        clarifications: Optional [{question, options:[{label, recommended,
            description}]}] surfaced in the Execute Plan popup. Use only when a
            missing detail would fundamentally change the plan.
    """
    state = dict(runtime.state or {}) if runtime is not None else {}
    planner_cfg = get_planner_config()
    max_steps = int(planner_cfg.max_plan_steps)
    max_clarifications = int(planner_cfg.max_clarifications)

    title = str(title or "Execution Plan").strip() or "Execution Plan"
    objective = str(objective or "").strip()
    summary = str(summary or "").strip()
    domain = str(domain or "generic").strip() or "generic"
    objective = objective or summary or "Deliver the user request with a structured implementation approach."
    summary = summary or objective

    assumptions_l = _clean_str_list(assumptions)
    constraints_l = _clean_str_list(constraints)
    acceptance_l = _clean_str_list(acceptance_criteria)
    risks_l = _normalize_risks(risks)

    # Validate + normalize the DAG (strip deps on a cycle rather than failing).
    coerced = _coerce_todo_nodes(todos, max_steps)
    try:
        nodes = normalize_todo_nodes(coerced)
    except ValueError as exc:
        logger.warning("write_plan dependency cycle (%s); stripping deps", exc)
        for node in coerced:
            node["depends_on"] = []
        nodes = normalize_todo_nodes(coerced)
    ready_ids = _materialize_ready_ids(nodes)

    inline_clarifications = _normalize_clarifications(clarifications, max_clarifications)
    auto_mode = _auto_mode_enabled(runtime, state)
    auto_clarification_answers = _build_auto_clarification_answers(inline_clarifications, auto_mode=auto_mode)
    clarification_pending = bool(inline_clarifications) and not auto_mode

    plan_id, revision = _plan_identity(state)
    plan_status = "approved" if auto_mode else "draft"
    approved_at = _utc_now_iso() if plan_status == "approved" else None
    created_at_dt = datetime.now(UTC)
    created_at = created_at_dt.isoformat()
    clarifications_payload = _build_clarifications_payload(
        inline_clarifications,
        auto_mode=auto_mode,
        auto_answers=auto_clarification_answers,
    )
    primary_question = inline_clarifications[0]["question"] if clarification_pending else None

    plan_md_content = _serialize_plan_content(
        plan_id=plan_id,
        title=title,
        summary=summary,
        status=plan_status,
        domain=domain,
        created_at=created_at,
        objective=objective,
        assumptions=assumptions_l,
        constraints=constraints_l,
        risks=risks_l,
        acceptance_criteria=acceptance_l,
        clarifications_payload=clarifications_payload,
        clarification_answers=auto_clarification_answers,
        nodes=nodes,
        ready_ids=ready_ids,
    )
    artifact_paths, plan_path, latest_alias_path = _write_plan_artifacts(
        state=state,
        title=title,
        created_at_dt=created_at_dt,
        plan_md_content=plan_md_content,
    )
    _emit_plan_created(
        runtime,
        title=title,
        summary=summary,
        domain=domain,
        plan_id=plan_id,
        plan_status=plan_status,
        nodes=nodes,
        clarification_pending=clarification_pending,
        clarifications_payload=clarifications_payload,
        plan_path=plan_path,
        revision=revision,
    )
    plan_history = _build_plan_history(state, plan_id=plan_id, title=title, plan_path=plan_path, created_at=created_at, plan_status=plan_status)
    plan_dict = _build_plan_dict(
        state=state,
        plan_id=plan_id,
        plan_status=plan_status,
        title=title,
        objective=objective,
        summary=summary,
        assumptions=assumptions_l,
        constraints=constraints_l,
        risks=risks_l,
        acceptance_criteria=acceptance_l,
        domain=domain,
        nodes=nodes,
        plan_path=plan_path,
        latest_alias_path=latest_alias_path,
        clarifications_payload=clarifications_payload,
        clarification_pending=clarification_pending,
        clarification_answers=auto_clarification_answers,
        primary_question=primary_question,
        created_at=created_at,
        revision=revision,
        approved_at=approved_at,
    )

    update_payload: dict[str, Any] = {
        "plan": plan_dict,
        "plan_history": plan_history,
        "todo_graph": {"nodes": nodes, "ready_ids": ready_ids, "updated_at": _utc_now_iso()},
        "todos": _legacy_todos(nodes),
        "handoff_artifacts": [p for p in [plan_path, latest_alias_path] if p],
        "artifacts": artifact_paths,
        "plan_evaluated": False,
        "clarification_pending": clarification_pending,
        # Transient signal for PlannerMiddleware: a plan was authored this turn,
        # so it should finalize (spawn work handoff if approved+foreground) and
        # halt the planning turn instead of letting the agent keep chatting.
        "plan_just_written": True,
        "messages": [
            ToolMessage(
                content=(
                    f"Plan '{title}' written ({len(nodes)} todos, status={plan_status}"
                    + (", clarification pending" if clarification_pending else "")
                    + "). plan.md saved."
                ),
                tool_call_id=tool_call_id,
            )
        ],
    }
    if clarifications_payload:
        # Mirror into the canonical top-level clarifications queue (merged by id).
        update_payload["clarifications"] = clarifications_payload
    return Command(update=update_payload)
