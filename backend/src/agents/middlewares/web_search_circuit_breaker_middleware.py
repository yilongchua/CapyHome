"""Circuit breaker for repeated web_search failures within a user run."""

from __future__ import annotations

import json
import os
from typing import Any, override

import httpx
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from src.agents.middlewares.model_timeout_middleware import TIMEOUT_MESSAGE_FINGERPRINT

_CIRCUIT_OPEN_FINGERPRINT = "[web_search_circuit_open]"
_FAILURE_THRESHOLD = 2
_HEALTH_URL = os.getenv("WEBSEARCH_BASE_URL", "http://localhost:9000").rstrip("/") + "/health"
_HEALTH_CHECK_TIMEOUT_S = 3.0


def _message_type(message: Any) -> str:
    raw = getattr(message, "type", None)
    if isinstance(raw, str):
        return raw
    if isinstance(message, dict):
        value = message.get("type")
        if isinstance(value, str):
            return value
    return ""


def _message_name(message: Any) -> str:
    raw = getattr(message, "name", None)
    if isinstance(raw, str):
        return raw
    if isinstance(message, dict):
        value = message.get("name")
        if isinstance(value, str):
            return value
    return ""


def _message_content(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(message, dict):
        value = message.get("content", "")
        if isinstance(value, str):
            return value
    return str(content) if content else ""


def _message_status(message: Any) -> str:
    raw = getattr(message, "status", None)
    if isinstance(raw, str):
        return raw
    if isinstance(message, dict):
        value = message.get("status")
        if isinstance(value, str):
            return value
    return ""


def _is_real_human(message: Any) -> bool:
    return _message_type(message) == "human" and not _message_name(message)


def _is_web_search_tool(name: str) -> bool:
    """True for tool names that represent a web/searx search (builtin or MCP)."""
    n = name.lower()
    return "web_search" in n or "websearch" in n or "searx" in n


def _is_web_search_failure(message: Any) -> bool:
    if _message_type(message) != "tool":
        return False
    name = _message_name(message).lower()
    if name and not _is_web_search_tool(name):
        return False
    # ToolErrorBoundaryMiddleware converts MCP transport exceptions (e.g. 504,
    # ExceptionGroup from TaskGroup) into ToolMessage(status="error"). Check
    # status first so the circuit recognises those without requiring a specific
    # content fingerprint or JSON shape.
    if _message_status(message) == "error":
        return True
    content = _message_content(message)
    if TIMEOUT_MESSAGE_FINGERPRINT in content or _CIRCUIT_OPEN_FINGERPRINT in content:
        return True
    try:
        payload = json.loads(content)
    except Exception:
        return False
    if isinstance(payload, dict) and payload.get("ok") is False:
        return True
    return False


def _failure_count_since_latest_user(messages: list[Any]) -> int:
    start_idx = 0
    for idx, message in enumerate(messages):
        if _is_real_human(message):
            start_idx = idx + 1
    return sum(1 for message in messages[start_idx:] if _is_web_search_failure(message))


def _websearch_healthy(url: str = _HEALTH_URL) -> bool:
    """Synchronous health check. Returns True if the websearch container responds 2xx."""
    try:
        resp = httpx.get(url, timeout=_HEALTH_CHECK_TIMEOUT_S)
        return resp.is_success
    except Exception:
        return False


async def _websearch_healthy_async(url: str = _HEALTH_URL) -> bool:
    """Async health check. Returns True if the websearch container responds 2xx."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=_HEALTH_CHECK_TIMEOUT_S)
            return resp.is_success
    except Exception:
        return False


class WebSearchCircuitBreakerMiddleware(AgentMiddleware[AgentState]):
    """Blocks repeated web_search retries after a failed batch.

    After _FAILURE_THRESHOLD failures the middleware performs a health check
    against the websearch container (_HEALTH_URL). If the container is reachable
    (HTTP 2xx) the block is suppressed — the prior failures were transient and
    the tool may succeed on this attempt. If the container is down, the circuit
    opens and the model is told to fall back to available results.
    """

    def _threshold_reached(self, request: ToolCallRequest) -> tuple[str, int] | None:
        """Return (tool_name, failure_count) when the failure threshold is met, else None."""
        tool_name = str(request.tool_call.get("name") or "")
        if not _is_web_search_tool(tool_name):
            return None
        state = request.state or {}
        messages = state.get("messages", []) if isinstance(state, dict) else []
        failures = _failure_count_since_latest_user(list(messages or []))
        if failures < _FAILURE_THRESHOLD:
            return None
        return tool_name, failures

    def _block_message(self, tool_name: str, failures: int, tool_call_id: str) -> ToolMessage:
        return ToolMessage(
            name=tool_name,
            tool_call_id=tool_call_id,
            content=(
                f"{_CIRCUIT_OPEN_FINGERPRINT}\n"
                f"Web search ({tool_name}) already failed {failures} time(s) in this user run "
                "and the websearch service is not reachable. "
                "Skip further web search retries for now. Use successful prior results, "
                "query_knowledge_vault if available, or answer from established knowledge with clear caveats."
            ),
        )

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage:
        hit = self._threshold_reached(request)
        if hit is not None:
            tool_name, failures = hit
            if not _websearch_healthy():
                return self._block_message(tool_name, failures, request.tool_call.get("id", ""))
        return handler(request)

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage:
        hit = self._threshold_reached(request)
        if hit is not None:
            tool_name, failures = hit
            if not await _websearch_healthy_async():
                return self._block_message(tool_name, failures, request.tool_call.get("id", ""))
        return await handler(request)
