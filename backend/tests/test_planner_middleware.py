"""Tests for the planner-as-tool architecture (B++).

Plan authoring lives in the ``write_plan`` tool; ``PlannerMiddleware`` only
orchestrates turn-halting + work-mode handoff. These tests cover both halves.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from src.agents.middlewares.planner_middleware import PlannerMiddleware
from src.tools.builtins.write_plan_tool import write_plan_tool


def _runtime(*, auto_mode: bool = False, plan_behavior: str = "plan_foreground", state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        context={"auto_mode": auto_mode, "plan_behavior": plan_behavior, "mode": "plan", "thread_id": "thread-1"},
        state=state or {},
    )


def _call_write_plan(runtime: SimpleNamespace, **kwargs):
    base = {
        "title": "Research Plan",
        "objective": "Deliver a structured report.",
        "summary": "Research and synthesize findings.",
        "domain": "research",
        "todos": [
            {"id": "todo-1", "content": "Research current status", "rationale": "Gather facts."},
            {"id": "todo-2", "content": "Write synthesis report", "depends_on": ["todo-1"], "rationale": "Deliver output."},
        ],
    }
    base.update(kwargs)
    return write_plan_tool.func(runtime=runtime, tool_call_id="tc-1", **base).update


# --------------------------------------------------------------------------- #
# write_plan tool                                                             #
# --------------------------------------------------------------------------- #


def test_write_plan_produces_draft_with_preserved_dependencies() -> None:
    update = _call_write_plan(_runtime())
    assert update["plan"]["status"] == "draft"
    assert update["plan"]["awaiting_execution_approval"] is True
    assert update["plan_just_written"] is True
    nodes = {n["id"]: n for n in update["todo_graph"]["nodes"]}
    assert nodes["todo-2"]["depends_on"] == ["todo-1"]
    assert update["todo_graph"]["ready_ids"] == ["todo-1"]
    summary_message = update["messages"][-1]
    assert summary_message.type == "ai"
    assert summary_message.name == "plan_summary"
    assert "### Research Plan" in summary_message.content
    assert "Research and synthesize findings." in summary_message.content
    assert "[plan.md](/mnt/user-data/workspace/plan.md)" in summary_message.content


def test_write_plan_caps_user_visible_summary_at_180_characters() -> None:
    update = _call_write_plan(_runtime(), summary="x" * 220)

    summary_message = update["messages"][-1]
    summary_line = summary_message.content.splitlines()[2]
    assert len(summary_line) == 180
    assert summary_line.endswith("...")


def test_write_plan_auto_mode_marks_approved() -> None:
    update = _call_write_plan(_runtime(auto_mode=True))
    assert update["plan"]["status"] == "approved"
    assert update["plan"].get("approved_at")
    assert update["plan"].get("awaiting_execution_approval") is False
    assert all(message.type != "ai" for message in update["messages"])


def test_write_plan_preserves_rich_todo_fields() -> None:
    update = _call_write_plan(
        _runtime(),
        todos=[
            {
                "id": "todo-1",
                "content": "Shortlist venues",
                "objective": "Produce a candidate venue list.",
                "failure_fallback": "Ask the user for the city.",
                "steps": [
                    {"description": "Search venues", "completion_requirement": "List has >= 10 entries"},
                ],
            }
        ],
    )
    node = update["todo_graph"]["nodes"][0]
    assert node["objective"] == "Produce a candidate venue list."
    assert node["failure_fallback"] == "Ask the user for the city."
    assert node["steps"][0]["completion_requirement"] == "List has >= 10 entries"


def test_write_plan_clarifications_normalize_recommended_first_and_capped() -> None:
    update = _call_write_plan(
        _runtime(),
        clarifications=[
            {
                "question": "Which timeframe should we use?",
                "options": [
                    {"label": "Last 3 years"},
                    {"label": "Last 12 months"},
                    {"label": "Since 2020"},
                    {"label": "This quarter"},
                    {"label": "Too many"},
                ],
            }
        ],
    )
    assert update["plan"]["clarification_pending"] is True
    options = update["plan"]["clarifications"][0]["options"]
    assert 2 <= len(options) <= 4
    assert options[0]["recommended"] is True
    assert all(message.type != "ai" for message in update["messages"])


def test_write_plan_auto_mode_answers_inline_clarifications_with_recommended() -> None:
    update = _call_write_plan(
        _runtime(auto_mode=True),
        clarifications=[
            {
                "question": "Which timeframe should we use?",
                "options": [
                    {"label": "Last 12 months"},
                    {"label": "2024 to 2026", "recommended": True},
                ],
            }
        ],
    )
    assert update["plan"]["status"] == "approved"
    assert update["plan"]["clarification_pending"] is False
    assert update["clarification_pending"] is False
    assert update["plan"]["clarification_resolved"] is True
    assert update["plan"]["clarification_question"] is None
    assert update["plan"]["clarification_answers"][0]["selected_label"] == "2024 to 2026"
    assert update["plan"]["clarifications"][0]["status"] == "answered"
    assert update["plan"]["clarifications"][0]["answer"] == "2024 to 2026"
    assert update["plan"]["awaiting_execution_approval"] is False


def test_write_plan_auto_mode_answers_inline_clarifications_with_first_option_fallback() -> None:
    update = _call_write_plan(
        _runtime(auto_mode=True),
        clarifications=[
            {
                "question": "Which format should we use?",
                "options": [
                    {"label": "Markdown"},
                    {"label": "CSV"},
                ],
            }
        ],
    )
    assert update["plan"]["status"] == "approved"
    assert update["plan"]["clarification_pending"] is False
    assert update["plan"]["clarification_answers"][0]["selected_label"] == "Markdown"
    assert update["plan"]["clarifications"][0]["answer"] == "Markdown"


def test_write_plan_replan_reuses_id_and_bumps_revision() -> None:
    existing = {"plan_id": "plan-abc123", "revision": 0, "status": "draft", "clarification_pending": False}
    update = _call_write_plan(_runtime(state={"plan": existing}))
    assert update["plan"]["plan_id"] == "plan-abc123"
    assert update["plan"]["revision"] == 1


def test_write_plan_writes_versioned_file_and_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = _runtime(state={"thread_data": {"workspace_path": str(workspace)}})
    update = _call_write_plan(runtime)
    assert (workspace / "plan.md").exists()
    versioned = list((workspace / "plans").glob("plan-*.md"))
    assert versioned, "versioned plan file must be written"
    assert str(update["plan"]["latest_alias_path"]).endswith("/workspace/plan.md")
    assert "/workspace/plans/plan-" in str(update["plan"]["plan_path"])


def test_write_plan_dependency_cycle_is_stripped_not_fatal() -> None:
    update = _call_write_plan(
        _runtime(),
        todos=[
            {"id": "todo-1", "content": "A", "depends_on": ["todo-2"]},
            {"id": "todo-2", "content": "B", "depends_on": ["todo-1"]},
        ],
    )
    for node in update["todo_graph"]["nodes"]:
        assert node["depends_on"] == []


# --------------------------------------------------------------------------- #
# PlannerMiddleware orchestration                                             #
# --------------------------------------------------------------------------- #


def test_plan_just_written_halts_turn_in_foreground() -> None:
    middleware = PlannerMiddleware()
    state = {"plan_just_written": True, "plan": {"plan_id": "p1", "status": "draft", "clarification_pending": False}}
    update = middleware.before_model(state, _runtime())
    assert update is not None
    assert update.get("jump_to") == "end"
    assert update.get("plan_just_written") is False


def test_plan_just_written_auto_approved_spawns_work_handoff(monkeypatch) -> None:
    spawn_calls: list[dict] = []
    monkeypatch.setattr(
        "src.agents.middlewares.planner_middleware.spawn_work_mode_handoff",
        lambda **kwargs: spawn_calls.append(kwargs),
    )
    middleware = PlannerMiddleware()
    state = {
        "plan_just_written": True,
        "plan": {"plan_id": "p1", "status": "approved", "clarification_pending": False},
    }
    update = middleware.before_model(state, _runtime(auto_mode=True))
    assert update is not None
    assert update.get("jump_to") == "end"
    assert update.get("plan_just_written") is False
    assert len(spawn_calls) == 1


def test_no_plan_just_written_and_no_clarification_is_noop() -> None:
    middleware = PlannerMiddleware()
    update = middleware.before_model({"messages": []}, _runtime())
    assert update is None


def test_after_model_retries_when_planner_exits_without_write_plan() -> None:
    middleware = PlannerMiddleware()
    update = middleware.after_model({"messages": [AIMessage(content="Here is a plan in prose.")]}, _runtime())
    assert update is not None
    assert update.get("jump_to") == "model"
    assert update["messages"][0].name == "planner_write_plan_required"


def test_after_model_does_not_retry_when_write_plan_already_called() -> None:
    middleware = PlannerMiddleware()
    update = middleware.after_model(
        {
            "plan_just_written": True,
            "messages": [AIMessage(content="Plan written.")],
        },
        _runtime(),
    )
    assert update is None
