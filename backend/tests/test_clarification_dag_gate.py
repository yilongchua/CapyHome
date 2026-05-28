"""Tests for clarification-aware DAG readiness in TodoDagMiddleware."""

from __future__ import annotations

from src.agents.middlewares.todo_dag_middleware import (
    collect_clarification_blocked_todo_ids,
    compute_effective_ready_ids,
)
from src.agents.thread_state import merge_clarifications


# --- helper functions ------------------------------------------------------


def test_collect_blocked_ids_returns_empty_on_no_clarifications():
    assert collect_clarification_blocked_todo_ids(None) == set()
    assert collect_clarification_blocked_todo_ids([]) == set()


def test_collect_blocked_ids_ignores_answered_entries():
    clarifications = [
        {"id": "c1", "status": "answered", "blocks": ["todo-a"]},
        {"id": "c2", "status": "pending", "blocks": ["todo-b", "todo-c"]},
    ]
    assert collect_clarification_blocked_todo_ids(clarifications) == {"todo-b", "todo-c"}


def test_compute_effective_ready_ids_filters_blocked_todos():
    nodes = [
        {"id": "todo-a", "status": "pending", "depends_on": []},
        {"id": "todo-b", "status": "pending", "depends_on": []},
        {"id": "todo-c", "status": "pending", "depends_on": ["todo-a"]},
    ]
    clarifications = [
        {"id": "c1", "status": "pending", "blocks": ["todo-a"]},
    ]
    # todo-a is gated; todo-c depends on todo-a so it isn't ready either.
    # Only todo-b is currently ready.
    assert compute_effective_ready_ids(nodes, clarifications) == ["todo-b"]


def test_compute_effective_ready_ids_releases_gate_when_answered():
    nodes = [
        {"id": "todo-a", "status": "pending", "depends_on": []},
        {"id": "todo-b", "status": "pending", "depends_on": []},
    ]
    clarifications = [
        {"id": "c1", "status": "answered", "blocks": ["todo-a"]},
    ]
    assert sorted(compute_effective_ready_ids(nodes, clarifications)) == ["todo-a", "todo-b"]


# --- reducer ---------------------------------------------------------------


def test_merge_clarifications_appends_new_entries():
    existing = [{"id": "c1", "status": "pending", "question": "Q1"}]
    new = [{"id": "c2", "status": "pending", "question": "Q2"}]
    merged = merge_clarifications(existing, new)
    assert [c["id"] for c in merged] == ["c1", "c2"]


def test_merge_clarifications_patches_existing_by_id():
    existing = [{"id": "c1", "status": "pending", "question": "Q1", "blocks": ["todo-x"]}]
    # Patch flips status to answered without restating other fields.
    new = [{"id": "c1", "status": "answered", "answer": "yes", "answered_at": "2026-05-28T00:00:00Z"}]
    merged = merge_clarifications(existing, new)
    assert len(merged) == 1
    assert merged[0]["status"] == "answered"
    assert merged[0]["answer"] == "yes"
    # Untouched fields are preserved.
    assert merged[0]["question"] == "Q1"
    assert merged[0]["blocks"] == ["todo-x"]


def test_merge_clarifications_drops_entries_with_no_id():
    existing = [{"id": "c1", "status": "pending"}]
    new = [{"status": "pending", "question": "no id"}]
    merged = merge_clarifications(existing, new)
    assert [c["id"] for c in merged] == ["c1"]


def test_merge_clarifications_handles_none_existing():
    assert merge_clarifications(None, [{"id": "c1"}]) == [{"id": "c1"}]
    assert merge_clarifications(None, None) == []
