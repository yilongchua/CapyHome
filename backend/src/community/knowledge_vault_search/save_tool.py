"""LangChain tool for explicitly saving curated content into the knowledge vault."""

from __future__ import annotations

import json
import logging

from langchain.tools import tool

logger = logging.getLogger(__name__)


@tool("save_to_knowledge_vault", parse_docstring=True)
def save_to_knowledge_vault_tool(
    title: str,
    content: str,
    topic: str = "",
    source_url: str = "",
) -> str:
    """Persist a useful answer or research artifact into the local knowledge vault.

    Args:
        title: Short human-readable title for the saved artifact.
        content: Markdown or plain text content to store.
        topic: Optional topic hint used for synthesis routing and tags.
        source_url: Optional original source URL if the content came from a page.
    """
    try:
        from src.control_plane.service import get_control_plane_service

        payload = get_control_plane_service().save_to_vault(
            title=title,
            content=content,
            topic=topic,
            source_url=source_url,
        )
        return json.dumps({"ok": True, "result": payload}, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.exception("save_to_knowledge_vault failed")
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
