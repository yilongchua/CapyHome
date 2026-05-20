"""Tests for draft-plan execution gating middleware."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agents.middlewares.plan_execution_gate_middleware import PlanExecutionGateMiddleware


def _request(tool_name: str, *, plan: dict | None = None):
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": "tc-1", "args": {}},
        runtime=SimpleNamespace(context={}, state={"plan": plan or {}}),
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
    for tool_name in ("ask_clarification", "write_todos", "recall"):
        result = middleware.wrap_tool_call(_request(tool_name, plan={"status": "draft"}), _handler)
        assert isinstance(result, ToolMessage)
        assert result.content == "ok"


def test_draft_plan_blocks_research_and_presentation_tools():
    middleware = PlanExecutionGateMiddleware()
    for tool_name in ("web_search", "query_knowledge_vault", "query_lightrag", "present_files"):
        result = middleware.wrap_tool_call(_request(tool_name, plan={"status": "draft"}), _handler)
        assert isinstance(result, Command)
        message = result.update["messages"][0]
        assert "[plan_gate]" in str(message.content)


def test_approved_plan_allows_execution():
    middleware = PlanExecutionGateMiddleware()
    result = middleware.wrap_tool_call(_request("write_file", plan={"status": "approved"}), _handler)
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


def test_plan_mode_blocks_execution_even_when_plan_is_approved():
    middleware = PlanExecutionGateMiddleware()
    request = _request("write_file", plan={"status": "approved"})
    request.runtime.context = {"mode": "plan"}

    result = middleware.wrap_tool_call(request, _handler)

    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert "still in Plan Mode" in str(message.content)


def test_plan_mode_allows_safe_read_only_tools():
    middleware = PlanExecutionGateMiddleware()
    safe_tools = [
        ("read_file", {}),
        ("web_search", {}),
        ("bash", {"command": "rg -n \"plan_mode\" /mnt/user-data/workspace/backend/src"}),
    ]
    for tool_name, args in safe_tools:
        request = _request(tool_name, plan={"status": "approved"})
        request.runtime.context = {"mode": "plan"}
        request.tool_call["args"] = args
        result = middleware.wrap_tool_call(request, _handler)
        assert isinstance(result, ToolMessage)
        assert result.content == "ok"


def test_plan_mode_blocks_mutating_bash_commands():
    middleware = PlanExecutionGateMiddleware()
    request = _request("bash", plan={"status": "approved"})
    request.runtime.context = {"mode": "plan"}
    request.tool_call["args"] = {"command": "echo hi > /mnt/user-data/workspace/tmp.txt"}

    result = middleware.wrap_tool_call(request, _handler)

    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert "read-only investigation" in str(message.content)
