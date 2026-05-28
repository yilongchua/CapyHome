"""Regression test for the plan_agent → work_agent canonical-plan.md handoff.

Verifies that when the user manually edits ``plan.md`` between plan approval
and work-mode handoff, those edits are honored by ``_load_canonical_plan_overrides``
rather than being silently discarded in favor of checkpointed state.
"""

from __future__ import annotations

from pathlib import Path

from src.agents.common.handoff import serialize_plan_md
from src.agents.middlewares.work_run_handoff import _load_canonical_plan_overrides


def _write_canonical_plan_md(workspace: Path, plan: dict, todo_graph: dict) -> Path:
    plan_path = workspace / "plan.md"
    plan_path.write_text(serialize_plan_md(plan, todo_graph), encoding="utf-8")
    return plan_path


def test_load_overrides_returns_empty_when_no_plan_in_values():
    assert _load_canonical_plan_overrides({}) == {}
    assert _load_canonical_plan_overrides({"plan": None}) == {}


def test_load_overrides_returns_empty_when_plan_md_missing(tmp_path: Path):
    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md"},
        "thread_data": {"workspace_path": str(tmp_path)},  # workspace exists, file does not
    }
    assert _load_canonical_plan_overrides(values) == {}


def test_load_overrides_reads_canonical_plan_md_from_workspace(tmp_path: Path):
    plan = {
        "plan_id": "plan-canonical-1",
        "title": "Compare bubble tea",
        "status": "approved",
        "domain": "trip",
        "target_mode": "work",
        "objective": "Find the best bubble tea in central SG.",
        "summary": "Three-part comparison.",
    }
    todo_graph = {
        "nodes": [
            {"id": "t1", "content": "List candidate shops", "status": "completed", "depends_on": []},
            {"id": "t2", "content": "Visit and rate top 5", "status": "in_progress", "depends_on": ["t1"]},
        ],
        "ready_ids": ["t2"],
    }
    _write_canonical_plan_md(tmp_path, plan, todo_graph)

    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md", "plan_id": "plan-canonical-1"},
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    overrides = _load_canonical_plan_overrides(values)
    assert "plan" in overrides
    assert "todo_graph" in overrides
    assert overrides["plan"]["plan_id"] == "plan-canonical-1"
    assert overrides["plan"]["status"] == "approved"
    assert len(overrides["todo_graph"]["nodes"]) == 2
    assert overrides["todo_graph"]["ready_ids"] == ["t2"]


def test_user_edit_to_plan_md_propagates_through_handoff(tmp_path: Path):
    """Core regression: a manual edit to plan.md content must reach work-mode state."""
    plan = {
        "plan_id": "plan-canonical-2",
        "title": "Compare bubble tea",
        "status": "approved",
        "domain": "trip",
        "target_mode": "work",
    }
    todo_graph = {
        "nodes": [{"id": "t1", "content": "Visit 5 shops", "status": "pending", "depends_on": []}],
        "ready_ids": ["t1"],
    }
    plan_path = _write_canonical_plan_md(tmp_path, plan, todo_graph)

    # Simulate a user editing the file directly (e.g. via the workspace UI).
    edited_text = plan_path.read_text(encoding="utf-8").replace(
        "Visit 5 shops",
        "Visit 8 shops (user expanded scope)",
    )
    plan_path.write_text(edited_text, encoding="utf-8")

    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md"},
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    overrides = _load_canonical_plan_overrides(values)

    edited_node = overrides["todo_graph"]["nodes"][0]
    assert edited_node["content"] == "Visit 8 shops (user expanded scope)"


def test_runtime_only_fields_are_preserved_across_canonical_parse(tmp_path: Path):
    """The parsed plan must keep fields that only live in runtime state (paths, timestamps)."""
    plan = {
        "plan_id": "plan-rt-1",
        "title": "T",
        "status": "approved",
        "domain": "generic",
    }
    todo_graph = {"nodes": [{"id": "t1", "content": "do thing", "status": "pending", "depends_on": []}], "ready_ids": ["t1"]}
    _write_canonical_plan_md(tmp_path, plan, todo_graph)

    runtime_plan = {
        "latest_alias_path": "/mnt/user-data/workspace/plan.md",
        "plan_path": "/mnt/user-data/workspace/plans/plan-rt-1.md",
        "approved_at": "2026-05-27T10:00:00Z",
        "execution_handoff_started": True,
    }
    values = {"plan": runtime_plan, "thread_data": {"workspace_path": str(tmp_path)}}
    overrides = _load_canonical_plan_overrides(values)

    # These fields aren't in the frontmatter but must survive the override.
    assert overrides["plan"]["plan_path"] == runtime_plan["plan_path"]
    assert overrides["plan"]["latest_alias_path"] == runtime_plan["latest_alias_path"]
    assert overrides["plan"]["approved_at"] == runtime_plan["approved_at"]
    assert overrides["plan"]["execution_handoff_started"] is True


def test_load_overrides_falls_back_when_plan_md_is_legacy_format(tmp_path: Path):
    """An older plan.md (v4) shouldn't trigger overrides — handoff falls back to checkpoint state."""
    legacy = (
        "---\n"
        "plan_version: 4\n"
        'plan_id: "plan-old"\n'
        'title: "Old plan"\n'
        "---\n\n"
        "# Old body\n"
    )
    (tmp_path / "plan.md").write_text(legacy, encoding="utf-8")
    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md"},
        "thread_data": {"workspace_path": str(tmp_path)},
    }
    assert _load_canonical_plan_overrides(values) == {}


def test_load_overrides_falls_back_when_plan_md_has_cycle(tmp_path: Path):
    plan = {"plan_id": "plan-cycle", "title": "Cycle", "status": "approved", "target_mode": "work"}
    todo_graph = {
        "nodes": [
            {"id": "a", "content": "A", "status": "pending", "depends_on": ["b"]},
            {"id": "b", "content": "B", "status": "pending", "depends_on": ["a"]},
        ],
        "ready_ids": [],
    }
    _write_canonical_plan_md(tmp_path, plan, todo_graph)

    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md"},
        "thread_data": {"workspace_path": str(tmp_path)},
    }

    assert _load_canonical_plan_overrides(values) == {}


def test_load_overrides_falls_back_when_plan_md_has_invalid_target_endpoint(tmp_path: Path):
    plan = {"plan_id": "plan-endpoint", "title": "Endpoint", "status": "approved", "target_mode": "work"}
    todo_graph = {
        "nodes": [
            {
                "id": "a",
                "content": "A",
                "status": "pending",
                "depends_on": [],
                "target_endpoint": "not-real",
            },
        ],
        "ready_ids": ["a"],
    }
    _write_canonical_plan_md(tmp_path, plan, todo_graph)

    values = {
        "plan": {"latest_alias_path": "/mnt/user-data/workspace/plan.md"},
        "thread_data": {"workspace_path": str(tmp_path)},
    }

    assert _load_canonical_plan_overrides(values) == {}
