"""Outermost tool-call error boundary.

Converts an *unhandled* exception raised during tool execution into a
recoverable ``ToolMessage(status="error")`` so a single failing tool call no
longer crashes the entire run.

Why this exists
---------------
A real incident (thread ``937911b9``): the agent fired parallel
``websearch.search`` MCP calls; one exceeded the websearch container's 120s
gateway timeout, so the server returned ``504 Gateway Time-out``. The MCP
streamable-HTTP client calls ``response.raise_for_status()`` and raises
``httpx.HTTPStatusError``. Nothing caught it — ``RetryPolicyMiddleware`` only
retries *configured retryable* errors and otherwise **re-raises** — so the
exception propagated out of the tools node and failed the whole background run.
LangGraph reported the run as errored and the frontend showed the generic
"An internal error occurred", losing the work from the other (successful)
searches in the same turn.

This mirrors the behaviour of LangGraph's ``ToolNode(handle_tool_errors=True)``
default, but does it explicitly in the middleware chain so it composes with the
project's other ``wrap_tool_call`` middlewares.

Ordering
--------
This must be the **outermost** tool-call wrapper — i.e. it must sort *before*
every other ``wrap_tool_call`` middleware in the resolved list (LangChain
composes ``wrap_tool_call`` first→outermost). In particular it must be outer to
``retry`` so that ``RetryPolicyMiddleware`` still sees raw exceptions and can
retry; only when retry has exhausted its attempts and re-raises does this
boundary catch the final exception and turn it into an error ``ToolMessage``.
It is wired with ``before={"retry"}`` in the registry.

Control flow exceptions (interrupts, parent commands, recursion limits) are
re-raised untouched so plan/clarification interrupts and the framework's own
control flow keep working. ``asyncio.CancelledError``/``KeyboardInterrupt``/
``SystemExit`` derive from ``BaseException`` and are therefore never caught by
the ``except Exception`` clause.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp, GraphRecursionError
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from src.agents.middlewares.runtime_events import append_runtime_event

logger = logging.getLogger(__name__)

# Langgraph control-flow signals that must never be swallowed: GraphBubbleUp is
# the base for GraphInterrupt and ParentCommand (used by plan/clarification
# interrupts and Command propagation); GraphRecursionError is the recursion-limit
# terminal that the framework handles itself.
_CONTROL_FLOW_EXCEPTIONS = (GraphBubbleUp, GraphRecursionError)

_MAX_ERROR_CHARS = 600


class ToolErrorBoundaryMiddleware(AgentMiddleware[AgentState]):
    """Catch unhandled tool exceptions and return a recoverable error message."""

    def _error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_name = str(request.tool_call.get("name") or "unknown")
        tool_call_id = str(request.tool_call.get("id") or "")
        detail = f"{type(exc).__name__}: {exc}".strip()
        if len(detail) > _MAX_ERROR_CHARS:
            detail = detail[:_MAX_ERROR_CHARS] + "… (truncated)"

        logger.warning("Tool '%s' raised an unhandled error; returning recoverable error message: %s", tool_name, detail)
        append_runtime_event(
            request.runtime,
            {
                "source": "tool_error_boundary",
                "tool": tool_name,
                "error": detail,
            },
        )

        content = (
            f"Tool `{tool_name}` failed and could not complete: {detail}. "
            "This is a tool/transport error, not a problem with the request itself. "
            "You may retry (optionally with a simpler or narrower input), try a different "
            "approach or tool, or continue with the information you already have. "
            "Do not assume the task is impossible because of this single failure."
        )
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name, status="error")

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        try:
            return handler(request)
        except _CONTROL_FLOW_EXCEPTIONS:
            raise
        except Exception as exc:
            return self._error_message(request, exc)

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]) -> ToolMessage | Command:
        try:
            return await handler(request)
        except _CONTROL_FLOW_EXCEPTIONS:
            raise
        except Exception as exc:
            return self._error_message(request, exc)
