"""Tests for the Work-Mode draft-plan execution gate.

Plan Mode is intentionally NOT gated by this middleware (the per-mode tool
catalog restricts the Plan-Mode surface). The gate's job is a Work-Mode
backstop: a draft, never-approved plan (e.g. recovered via ``/recover``) must
not execute until it is approved.
"""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agents.middlewares.plan_execution_gate_middleware import PlanExecutionGateMiddleware


def _request(tool_name: str, *, plan: dict | None = None, mode: str = "work", context: dict | None = None):
    ctx = {"current_mode": mode, **(context or {})}
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": "tc-1", "args": {}},
        runtime=SimpleNamespace(context=ctx, state={"plan": plan}),
        state={},
    )


def _handler(_: object) -> ToolMessage:
    return ToolMessage(content="ok", tool_call_id="tc-1", name="handler")


def test_draft_plan_blocks_execution_tools():
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(_request("write_file", plan={"status": "draft"}), _handler)
    assert isinstance(result, Command)
    assert getattr(result, "goto", ()) == ()
    message = result.update["messages"][0]
    assert "[plan_gate]" in str(message.content)


def test_draft_plan_allows_clarification_and_todo_updates():
    middleware = PlanExecutionGateMiddleware()
    for tool_name in ("ask_user_for_clarification", "write_todos", "recall"):
        result = middleware.wrap_tool_call(_request(tool_name, plan={"status": "draft"}), _handler)
        assert isinstance(result, ToolMessage)
        assert result.content == "ok"


def test_draft_plan_blocks_research_and_presentation_tools():
    middleware = PlanExecutionGateMiddleware()
    for tool_name in ("web_search", "query_knowledge_vault", "present_files"):
        result = middleware.wrap_tool_call(_request(tool_name, plan={"status": "draft"}), _handler)
        assert isinstance(result, Command)
        message = result.update["messages"][0]
        assert "[plan_gate]" in str(message.content)


def test_approved_plan_allows_execution():
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(_request("write_file", plan={"status": "approved"}), _handler)
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


def test_no_plan_allows_execution():
    """Plain Work Mode (no plan in state) is never gated."""
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(_request("write_file", plan=None), _handler)
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


def test_recover_command_bypasses_draft_gate():
    """`/recover` is an explicit instruction to finish the plan — never blocked."""
    middleware = PlanExecutionGateMiddleware()
    for tool_name in ("write_file", "bash", "web_search"):
        result = middleware.wrap_tool_call(
            _request(tool_name, plan={"status": "draft"}, context={"recover_todo_command": True}),
            _handler,
        )
        assert isinstance(result, ToolMessage), f"{tool_name} blocked despite /recover"
        assert result.content == "ok"


def test_recover_command_bypasses_pending_clarification():
    """`/recover` proceeds even when a clarification is still pending."""
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(
        _request(
            "write_file",
            plan={"status": "draft", "clarification_pending": True},
            context={"recover_todo_command": True},
        ),
        _handler,
    )
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


def test_pending_clarification_blocks_non_clarification_tools():
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(
        _request(
            "bash",
            plan={"status": "draft", "clarification_pending": True, "clarification_question": "What years should this cover?"},
        ),
        _handler,
    )
    assert isinstance(result, Command)
    assert getattr(result, "goto", ()) == ()
    message = result.update["messages"][0]
    assert "What years should this cover?" in str(message.content)


def test_plan_mode_is_not_gated():
    """Plan Mode is restricted by the tool catalog, not this gate.

    web_search / task / grep are intentionally available in Plan Mode, so the
    gate must let everything through there even for a draft plan.
    """
    middleware = PlanExecutionGateMiddleware()
    for tool_name in ("write_file", "web_search", "task", "grep", "bash"):
        result = middleware.wrap_tool_call(
            _request(tool_name, plan={"status": "draft"}, mode="plan"),
            _handler,
        )
        assert isinstance(result, ToolMessage), f"{tool_name} unexpectedly gated in Plan Mode"
        assert result.content == "ok"
