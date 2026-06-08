"""Rich todo schema (objective/steps/completion/fallback) + plan.md rendering.

Plan generation moved to the ``write_plan`` tool, so the step-normalisation
helper now lives there. The ``_should_plan`` / revision-cap gating that used to
live in ``PlannerMiddleware.before_model`` was removed — the agent now decides
when to call ``write_plan`` — so those tests are gone. Replan behaviour
(reuse plan_id + bump revision) is covered in ``test_planner_middleware.py``.
"""

from __future__ import annotations

from src.agents.middlewares.handoff_sync import render_plan_md
from src.tools.builtins.write_plan_tool import _normalize_todo_steps


def test_normalize_todo_steps_handles_missing_field() -> None:
    assert _normalize_todo_steps(None) == []
    assert _normalize_todo_steps("not-a-list") == []
    assert _normalize_todo_steps([]) == []


def test_normalize_todo_steps_skips_invalid_entries() -> None:
    steps = _normalize_todo_steps(
        [
            "string-entry-skipped",
            {"description": ""},
            {"description": "ok step", "subagent_types": ["x"], "tools": ["web_search"]},
        ]
    )
    assert len(steps) == 1
    assert steps[0]["description"] == "ok step"
    assert steps[0]["subagent_types"] == ["x"]


def test_render_plan_md_includes_rich_todo_sections() -> None:
    nodes = [
        {
            "id": "todo-1",
            "content": "Search restaurants",
            "status": "pending",
            "objective": "Find 10",
            "completion_requirement": "top10.md exists",
            "failure_fallback": "Use prior knowledge",
            "steps": [
                {
                    "description": "web search",
                    "subagent_types": ["knowledge-researcher"],
                    "tools": ["web_search"],
                    "output_artifact_path": "/mnt/user-data/workspace/candidates.md",
                    "completion_requirement": "15 entries",
                }
            ],
        }
    ]
    md = render_plan_md("Restaurant Plan", "Find 10 restaurants", nodes)
    assert "Objective: Find 10" in md
    assert "Steps:" in md
    assert "1. web search" in md
    assert "Subagent: knowledge-researcher" in md
    assert "Tools: web_search" in md
    assert "/mnt/user-data/workspace/candidates.md" in md
    assert "Done when: top10.md exists" in md
    assert "On failure: Use prior knowledge" in md


def test_render_plan_md_handles_legacy_nodes_without_rich_fields() -> None:
    """Plans created before the rich-todo migration must still render."""
    nodes = [{"id": "todo-1", "content": "Old todo", "status": "pending"}]
    md = render_plan_md("Old Plan", "Legacy", nodes)
    assert "Old todo" in md
    # Rich sections should be absent — not crash.
    assert "Objective:" not in md
    assert "On failure:" not in md
