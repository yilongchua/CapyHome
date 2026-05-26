"""Phase 4: PhaseToolFilterMiddleware must respect JSON-attached tool policies.

Existing behavior (blocked-name lists for draft/work) stays intact; the policy
layer hides additional JSON-annotated tools whose `mode`/`phase` exclude the
current request.
"""

from __future__ import annotations

from langchain.tools import tool

from src.agents.middlewares.phase_tool_filter_middleware import _filter_tools_by_policy
from src.tools.loader import build_structured_tool
from src.tools.schema import ToolDefinition


@tool("plan_only_fixture", parse_docstring=True)
def plan_only_fixture(value: str) -> str:
    """Fixture tool only valid in plan mode.

    Args:
        value: Anything.
    """
    return value


@tool("approved_only_fixture", parse_docstring=True)
def approved_only_fixture(value: str) -> str:
    """Fixture tool only valid post-approval.

    Args:
        value: Anything.
    """
    return value


@tool("plain_unannotated_fixture", parse_docstring=True)
def plain_unannotated_fixture(value: str) -> str:
    """Plain tool that never receives a JSON policy (separate object instance).

    Args:
        value: Anything.
    """
    return value


def _annotate(handler_path: str, *, mode: list[str], phase: list[str], name: str) -> object:
    defn = ToolDefinition.model_validate(
        {
            "name": name,
            "description": "policy-fixture tool with a sufficiently long description.",
            "handler": handler_path,
            "mode": mode,
            "phase": phase,
            "parameters": {
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"type": "string"}},
            },
        }
    )
    return build_structured_tool(defn)


HANDLER_PLAN = f"{__name__}:plan_only_fixture"
HANDLER_APPROVED = f"{__name__}:approved_only_fixture"


def test_policy_filter_hides_tool_outside_its_modes() -> None:
    plan_tool = _annotate(HANDLER_PLAN, name="plan_only_fixture", mode=["plan"], phase=["draft", "approved"])
    kept, hidden = _filter_tools_by_policy([plan_tool], mode="work", phase="approved")
    assert kept == []
    assert hidden == ["plan_only_fixture"]


def test_policy_filter_keeps_tool_within_its_modes() -> None:
    plan_tool = _annotate(HANDLER_PLAN, name="plan_only_fixture", mode=["plan"], phase=["draft", "approved"])
    kept, hidden = _filter_tools_by_policy([plan_tool], mode="plan", phase="draft")
    assert kept == [plan_tool]
    assert hidden == []


def test_policy_filter_hides_tool_outside_its_phases() -> None:
    work_tool = _annotate(
        HANDLER_APPROVED,
        name="approved_only_fixture",
        mode=["plan", "work", "auto"],
        phase=["approved"],
    )
    kept, hidden = _filter_tools_by_policy([work_tool], mode="work", phase="draft")
    assert kept == []
    assert hidden == ["approved_only_fixture"]


def test_policy_filter_passes_through_unannotated_tools() -> None:
    # Plain @tool function without JSON metadata — should pass through unfiltered.
    kept, hidden = _filter_tools_by_policy([plain_unannotated_fixture], mode="work", phase="draft")
    assert kept == [plain_unannotated_fixture]
    assert hidden == []


def test_policy_filter_mixed_list_keeps_unannotated_drops_annotated() -> None:
    plan_tool = _annotate(HANDLER_PLAN, name="plan_only_fixture", mode=["plan"], phase=["draft", "approved"])
    tools = [plain_unannotated_fixture, plan_tool]  # one unannotated, one annotated
    kept, hidden = _filter_tools_by_policy(tools, mode="work", phase="approved")
    # Unannotated stays, annotated drops.
    assert plain_unannotated_fixture in kept
    assert plan_tool not in kept
    assert hidden == ["plan_only_fixture"]


def test_policy_filter_empty_mode_or_phase_means_no_restriction() -> None:
    # An entry with empty mode/phase arrays falls back to no restriction.
    no_restriction = _annotate(
        HANDLER_PLAN,
        name="plan_only_fixture",
        mode=["plan", "work", "auto"],
        phase=["draft", "approved"],
    )
    kept, hidden = _filter_tools_by_policy([no_restriction], mode="auto", phase="approved")
    assert kept == [no_restriction]
    assert hidden == []
