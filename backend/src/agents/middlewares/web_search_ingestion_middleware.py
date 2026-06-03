"""Web search → vault ingestion-queue bridge middleware.

Restores the enqueue that used to live inside the in-backend ``web_search``
community tool. When web search moved out to the standalone ``websearch.search``
MCP server (``http://localhost:9000/mcp``), the tool kept producing markdown but
lost all coupling to the knowledge vault — the external service has no knowledge
of ``search_results_ingestion_queue.json``. As a result the queue stopped
receiving rows after the cutover (its newest ``web_search`` row predates the
migration).

This middleware re-establishes the link without re-coupling the external
service: it observes the ``websearch.search`` (or any web-search) tool result as
it passes back through the backend, re-renders each result to markdown via the
canonical ``_render_result_markdown`` helper, and appends rows to the ingestion
queue via ``enqueue_search_results``. It is a pure side effect — the
``ToolMessage`` is passed through unchanged.

Ordering: this middleware must run *inner* to ``web_search_summary`` (i.e. it
must see the raw tool result before the summary middleware can replace the
content with an LLM summary). In LangChain's ``wrap_tool_call`` composition the
first middleware is outermost and the response flows tool → last → first, so
"inner" means *later* in the resolved list — hence ``after={"web_search_summary"}``
in the registry spec. If it ran outer to summary, large results would be
enqueued as truncated summaries (or fail to parse as JSON), losing the full
crawled markdown the vault wants.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from src.agents.middlewares.runtime_events import append_runtime_event
from src.community.web_search.tools import _render_result_markdown
from src.config import get_app_config
from src.control_plane.service import get_control_plane_service

logger = logging.getLogger(__name__)


def _is_web_search_tool(name: str | None) -> bool:
    """True for tool names that represent a web/searx search (builtin or MCP).

    Mirrors ``web_search_circuit_breaker_middleware._is_web_search_tool`` so the
    two stay in lock-step on which tool names count as web search.
    """
    n = (name or "").lower()
    return "web_search" in n or "websearch" in n or "searx" in n


def _coerce_content_text(content: Any) -> str | None:
    """Return the tool result as a string, or ``None`` if it can't be coerced.

    MCP HTTP tools normally return a JSON string, but the adapter can also hand
    back a list of content blocks; join the text blocks defensively.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts) if parts else None
    return None


class WebSearchIngestionMiddleware(AgentMiddleware[AgentState]):
    """Append ``websearch.search`` results to the vault ingestion queue.

    Activates when:
    - the tool name matches a web-search tool (``_is_web_search_tool``)
    - the result parses as the search envelope ``{query, results[], package}``
    - the knowledge vault and its search-results queue are both enabled
    """

    def _enqueue(self, request: ToolCallRequest, result: ToolMessage | Command) -> None:
        if not isinstance(result, ToolMessage):
            return
        tool_name = str(request.tool_call.get("name") or "")
        if not _is_web_search_tool(tool_name):
            return

        app_cfg = get_app_config()
        vault_cfg = app_cfg.knowledge_vault
        if not (vault_cfg.enabled and vault_cfg.search_results_queue_enabled):
            return

        content = _coerce_content_text(getattr(result, "content", None))
        if not content:
            return
        try:
            payload = json.loads(content)
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return

        query = str(payload.get("query") or (request.tool_call.get("args") or {}).get("query") or "").strip()
        package = payload.get("package")
        package_markdown_path = str((package or {}).get("markdown_path") or "").strip() if isinstance(package, dict) else ""
        if package_markdown_path and Path(package_markdown_path).exists():
            package_markdown_path = str(Path(package_markdown_path).resolve())
        else:
            package_markdown_path = ""

        # Faithful to the logic stranded in web_search/tools.py: re-render each
        # result to markdown and force it into extracted_content so the ingestion
        # pipeline (which consumes extracted_content) keeps the structure.
        queue_results: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            markdown_content = _render_result_markdown(query=query, item=item)
            queue_item = {
                **item,
                "extracted_content": markdown_content,
                # Distinguish from the legacy in-backend tool so queue provenance
                # is honest about which tool produced the row.
                "source_tool": "websearch.search",
            }
            if package_markdown_path:
                queue_item["source_markdown_path"] = package_markdown_path
            queue_results.append(queue_item)

        if not queue_results:
            return

        manager = get_control_plane_service()._default_vault_manager()
        report = manager.enqueue_search_results(query=query, results=queue_results)
        append_runtime_event(
            request.runtime,
            {
                "source": "web_search_ingestion",
                "tool": tool_name,
                "query": query,
                "appended_count": report.get("appended_count", 0),
                "duplicate_count": report.get("duplicate_count", 0),
                "skipped_count": report.get("skipped_count", 0),
            },
        )

    @override
    def wrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command:
        result = handler(request)
        try:
            self._enqueue(request, result)
        except Exception:
            logger.exception("web_search ingestion enqueue failed")
        return result

    @override
    async def awrap_tool_call(self, request: ToolCallRequest, handler) -> ToolMessage | Command:
        result = await handler(request)
        try:
            self._enqueue(request, result)
        except Exception:
            logger.exception("web_search ingestion enqueue failed")
        return result
