"""Round-trip tests for the canonical plan.md serializer/parser.

These guard the plan_agent → work_agent handoff contract. A regression here
means manual user edits to plan.md could silently desync from runtime state.
"""

from __future__ import annotations

import pytest

from src.agents.common.handoff import (
    CANONICAL_PLAN_VERSION,
    parse_plan_md,
    serialize_plan_md,
)


def _basic_plan() -> dict:
    return {
        "plan_id": "plan-abc-123",
        "title": "Compare bubble tea spots",
        "status": "approved",
        "domain": "trip",
        "target_mode": "work",
        "created_at": "2026-05-27T12:00:00Z",
        "objective": "Find the best bubble tea in central Singapore.",
        "summary": "Three-part comparison: location, taste, value.",
        "assumptions": ["User can travel within central Singapore"],
        "constraints": ["Visit must be on a weekday"],
        "risks": [{"risk": "Shop closures", "mitigation": "Confirm hours by phone"}],
        "acceptance_criteria": ["A ranked shortlist of 3+ shops"],
    }


def _basic_todo_graph() -> dict:
    return {
        "nodes": [
            {"id": "t1", "content": "List candidate shops", "status": "completed", "depends_on": []},
            {"id": "t2", "content": "Visit and rate top 5", "status": "in_progress", "depends_on": ["t1"]},
            {"id": "t3", "content": "Write final comparison", "status": "pending", "depends_on": ["t2"]},
        ],
        "ready_ids": ["t2"],
    }


def test_basic_roundtrip_preserves_plan_fields():
    plan = _basic_plan()
    todo_graph = _basic_todo_graph()

    text = serialize_plan_md(plan, todo_graph)
    parsed = parse_plan_md(text)
    assert parsed is not None
    parsed_plan, parsed_graph = parsed

    for key in ("plan_id", "title", "status", "domain", "target_mode", "objective", "summary"):
        assert parsed_plan[key] == plan[key], f"field {key} drifted"
    assert parsed_plan["assumptions"] == plan["assumptions"]
    assert parsed_plan["constraints"] == plan["constraints"]
    assert parsed_plan["risks"] == plan["risks"]
    assert parsed_plan["acceptance_criteria"] == plan["acceptance_criteria"]


def test_basic_roundtrip_preserves_todo_graph():
    plan = _basic_plan()
    todo_graph = _basic_todo_graph()

    text = serialize_plan_md(plan, todo_graph)
    parsed = parse_plan_md(text)
    assert parsed is not None
    _, parsed_graph = parsed

    assert len(parsed_graph["nodes"]) == 3
    for original, roundtripped in zip(todo_graph["nodes"], parsed_graph["nodes"], strict=True):
        assert roundtripped["id"] == original["id"]
        assert roundtripped["content"] == original["content"]
        assert roundtripped["status"] == original["status"]
        assert roundtripped["depends_on"] == original["depends_on"]
    assert parsed_graph["ready_ids"] == todo_graph["ready_ids"]


def test_user_edit_to_todo_content_propagates_through_parse():
    """The core canonical-handoff regression test.

    Simulates a user editing the content of a todo in plan.md before approving,
    then verifies the work-mode handoff sees the edited content (not the
    original).
    """
    plan = _basic_plan()
    todo_graph = _basic_todo_graph()
    text = serialize_plan_md(plan, todo_graph)

    edited = text.replace(
        "Visit and rate top 5",
        "Visit and rate top 7 (user expanded scope)",
    )
    assert "user expanded scope" in edited

    parsed = parse_plan_md(edited)
    assert parsed is not None
    _, parsed_graph = parsed
    t2 = next(node for node in parsed_graph["nodes"] if node["id"] == "t2")
    assert t2["content"] == "Visit and rate top 7 (user expanded scope)"


def test_v6_plan_md_does_not_carry_clarifications_in_frontmatter():
    """As of v6, clarifications live on ThreadState (top-level), not in the
    plan frontmatter. Serializing a plan with clarifications attached should
    silently drop them — the serializer is plan-only."""
    plan = _basic_plan()
    plan["clarifications"] = [{"question": "Vegan options only?", "options": [{"label": "yes"}]}]
    plan["clarification_pending"] = True

    text = serialize_plan_md(plan, _basic_todo_graph())
    assert "clarifications:" not in text  # not emitted in v6 frontmatter
    parsed = parse_plan_md(text)
    assert parsed is not None
    parsed_plan, _ = parsed
    # v6 parse path doesn't surface clarifications on the plan dict at all.
    assert "clarifications" not in parsed_plan


def test_v5_plan_md_still_parses_and_surfaces_clarifications_for_migration():
    """Legacy v5 plan.md files (clarifications nested in frontmatter) must
    still be readable so ThreadState can hoist the entries on first load."""
    v5_text = (
        "---\n"
        "plan_version: 5\n"
        "plan_id: legacy-1\n"
        "title: Legacy\n"
        "status: draft\n"
        "domain: generic\n"
        "target_mode: work\n"
        "objective: x\n"
        "summary: x\n"
        "assumptions: []\n"
        "constraints: []\n"
        "risks: []\n"
        "acceptance_criteria: []\n"
        "todos: []\n"
        "todo_ready_ids: []\n"
        "clarifications:\n"
        "  - question: Vegan options only?\n"
        "    options:\n"
        '      - label: "yes"\n'
        '      - label: "no"\n'
        "clarification_answers:\n"
        "  - question: Vegan options only?\n"
        '    answer: "no"\n'
        "clarification_pending: false\n"
        "clarification_resolved: true\n"
        "---\n\n# Legacy\n"
    )
    parsed = parse_plan_md(v5_text)
    assert parsed is not None
    parsed_plan, _ = parsed
    assert parsed_plan["clarifications"][0]["question"] == "Vegan options only?"
    assert parsed_plan["clarification_answers"][0]["answer"] == "no"
    assert parsed_plan["clarification_resolved"] is True
    assert parsed_plan["clarification_pending"] is False


def test_rich_todo_fields_survive_roundtrip():
    plan = _basic_plan()
    todo_graph = {
        "nodes": [
            {
                "id": "t1",
                "content": "Survey shops",
                "status": "pending",
                "depends_on": [],
                "rationale": "Need a baseline list before site visits.",
                "completion_requirement": "List of at least 8 candidate shops",
                "subagent_type": "general-purpose",
                "tool_budget": 5,
                "steps": [
                    {"description": "Search local food blogs", "tools": ["web_search"]},
                ],
                "artifacts": ["candidates.md"],
            },
        ],
        "ready_ids": ["t1"],
    }

    text = serialize_plan_md(plan, todo_graph)
    parsed = parse_plan_md(text)
    assert parsed is not None
    _, parsed_graph = parsed
    node = parsed_graph["nodes"][0]
    assert node["rationale"] == "Need a baseline list before site visits."
    assert node["completion_requirement"] == "List of at least 8 candidate shops"
    assert node["subagent_type"] == "general-purpose"
    assert node["tool_budget"] == 5
    assert node["steps"] == [{"description": "Search local food blogs", "tools": ["web_search"]}]
    assert node["artifacts"] == ["candidates.md"]


def test_v6_plan_md_omits_clarification_fields_entirely():
    """v6 plan dicts have no clarification fields at all (top-level state)."""
    plan = _basic_plan()  # no clarifications set
    text = serialize_plan_md(plan, _basic_todo_graph())
    parsed = parse_plan_md(text)
    assert parsed is not None
    parsed_plan, _ = parsed
    for key in ("clarifications", "clarification_answers", "clarification_pending", "clarification_resolved"):
        assert key not in parsed_plan


def test_parser_returns_none_for_non_frontmatter_text():
    assert parse_plan_md("# Just a markdown file\n\nNo frontmatter here.") is None
    assert parse_plan_md("") is None


def test_parser_returns_none_for_older_plan_version():
    older = (
        "---\n"
        "plan_version: 4\n"
        'plan_id: "old-123"\n'
        'title: "Old plan"\n'
        "---\n\n"
        "# Old body\n"
    )
    assert parse_plan_md(older) is None


def test_parser_raises_on_malformed_frontmatter():
    malformed = "---\n: : not valid yaml :\nplan_version: 5\n---\n\nbody\n"
    with pytest.raises(ValueError, match="Malformed plan.md frontmatter"):
        parse_plan_md(malformed)


def test_serialized_text_starts_with_frontmatter_fence():
    text = serialize_plan_md(_basic_plan(), _basic_todo_graph())
    assert text.startswith("---\n")
    assert f"plan_version: {CANONICAL_PLAN_VERSION}" in text


def test_body_renderer_is_invoked_when_provided():
    captured = {}

    def renderer(plan, nodes):
        captured["plan"] = plan
        captured["nodes"] = nodes
        return "# Custom body\nHello."

    text = serialize_plan_md(_basic_plan(), _basic_todo_graph(), body_renderer=renderer)
    assert "# Custom body" in text
    assert captured["plan"]["title"] == "Compare bubble tea spots"
    assert len(captured["nodes"]) == 3
