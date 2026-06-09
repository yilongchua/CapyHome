"""Tests for ToolErrorBoundaryMiddleware.

Covers the regression from thread 937911b9: an unhandled MCP transport error
(e.g. a 504 from websearch.search) must become a recoverable error ToolMessage
rather than crashing the whole run. Also pins the ordering invariant that the
boundary is the *outermost* tool-call wrapper (outer to retry).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt

from src.agents.middlewares.tool_error_boundary_middleware import ToolErrorBoundaryMiddleware


def _request(name: str = "websearch.search", call_id: str = "call-1"):
    return SimpleNamespace(
        tool_call={"name": name, "id": call_id},
        runtime=SimpleNamespace(context={}),
    )


def test_unhandled_exception_becomes_error_tool_message():
    middleware = ToolErrorBoundaryMiddleware()

    def handler(_request):
        raise RuntimeError("Server error '504 Gateway Time-out' for url 'http://localhost:9000/mcp'")

    result = middleware.wrap_tool_call(_request(), handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.name == "websearch.search"
    assert result.tool_call_id == "call-1"
    assert "504 Gateway Time-out" in result.content
    assert "RuntimeError" in result.content


def test_successful_tool_call_passes_through_unchanged():
    middleware = ToolErrorBoundaryMiddleware()
    ok = ToolMessage(content="fine", tool_call_id="call-1", name="websearch.search")

    result = middleware.wrap_tool_call(_request(), lambda _r: ok)

    assert result is ok


def test_control_flow_exceptions_are_reraised():
    middleware = ToolErrorBoundaryMiddleware()

    def handler(_request):
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        middleware.wrap_tool_call(_request(), handler)


def test_async_unhandled_exception_becomes_error_tool_message():
    import asyncio

    middleware = ToolErrorBoundaryMiddleware()

    async def handler(_request):
        raise ValueError("boom")

    result = asyncio.run(middleware.awrap_tool_call(_request(), handler))

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "boom" in result.content


def test_boundary_sorts_outer_to_retry_and_inner_failure_wrappers():
    """The boundary must sort *before* (outer to) retry — so RetryPolicyMiddleware
    still sees raw exceptions and can retry first — and before the other
    failure-prone tool wrappers it is meant to backstop (model_timeout,
    circuit_breaker, truncation, and web_search_*). Observers that
    legitimately stay outer (trajectory, dangling_tool_call, permissions, …) are
    not constrained here: when the boundary returns an error ToolMessage they see
    a normal result instead of a crashing exception."""
    from src.agents.common.middleware_registry import topological_sort_middleware_specs
    from src.agents.work_agent.agent import _build_middleware_registry

    specs = _build_middleware_registry({"configurable": {"model_name": None, "subagent_enabled": True}}, model_name=None)
    ordered = topological_sort_middleware_specs(specs)
    names_in_order = [s.name for s in ordered]

    assert "tool_error_boundary" in names_in_order
    boundary_idx = names_in_order.index("tool_error_boundary")

    must_be_inner = [
        "retry",
        "model_timeout",
        "web_search_circuit_breaker",
        "tool_result_truncation",
        "web_search_summary",
        "web_search_ingestion",
    ]
    for name in must_be_inner:
        assert name in names_in_order, f"expected {name} in chain; order={names_in_order}"
        assert boundary_idx < names_in_order.index(name), f"tool_error_boundary must be outer to {name}; order={names_in_order}"
