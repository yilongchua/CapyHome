"""LangChain tool that exposes hybrid search over the local knowledge vault."""

from __future__ import annotations

import json
import logging

from langchain.tools import tool

from src.config.paths import get_paths

from .search import VALID_CATEGORIES, VaultSearcher

logger = logging.getLogger(__name__)

# Lazy singleton — resolved from get_paths() on first call so the vault root
# path is always consistent with however the application is configured.
_searcher: VaultSearcher | None = None


def _get_searcher() -> VaultSearcher:
    global _searcher
    if _searcher is None:
        vault_root = get_paths().base_dir / "knowledge_vault"
        _searcher = VaultSearcher(vault_root)
        logger.debug("VaultSearcher initialised at %s", vault_root)
    return _searcher


@tool("query_knowledge_vault", parse_docstring=True)
def query_knowledge_vault_tool(
    query: str,
    categories: list[str] | None = None,
    limit: int = 10,
) -> str:
    """Query the local knowledge vault for saved research and compiled knowledge.

    Use this tool when the user asks about topics that may have been previously
    researched and stored in the knowledge vault. Check this alongside web_search
    for topics the user has recently researched or asked about — most vault content
    is collected automatically from prior searches, not just manually saved.

    The search backend combines keyword/BM25 matching with vector search when
    vault vector indexing is enabled and embeddings are available.

    Returns a JSON object with an ``ok`` flag and a ``results`` list. Each
    result contains:
    - ``title``: page title
    - ``category``: vault section (sources/entities/concepts/syntheses/queries)
    - ``score``: hybrid relevance score, or keyword relevance when vector
      search is unavailable
    - ``excerpt``: short excerpt centred on the best matching passage
    - ``tags``: frontmatter tags list
    - ``source_url``: original source URL (may be empty for synthesised pages)
    - ``path``: absolute path to the vault markdown file
    - ``vector_status``: build-free status for vector search; stale/missing
      indexes may be rebuilt asynchronously while lexical results are returned

    Args:
        query: Natural language description of what to search for.
        categories: Optional list of vault sections to restrict the search to.
            Allowed sections are "sources", "entities", "concepts", "syntheses",
            and "queries". Omit to search all sections.
        limit: Maximum number of results to return (1–30, default 10).
    """
    try:
        limit = max(1, min(30, int(limit)))

        if categories is not None:
            invalid = [c for c in categories if c not in VALID_CATEGORIES]
            if invalid:
                return json.dumps(
                    {
                        "ok": False,
                        "error": "invalid_categories",
                        "message": f"Unknown categories: {invalid}. Valid values: {list(VALID_CATEGORIES)}",
                    },
                    ensure_ascii=False,
                )

        searcher = _get_searcher()
        results = searcher.search(query, categories=categories, limit=limit)
        try:
            vector_status = searcher.vector_status(build_if_stale=False)
        except Exception as exc:
            logger.warning("query_knowledge_vault vector status unavailable: %s", exc)
            vector_status = {
                "enabled": False,
                "available": False,
                "status_reason": "status_unavailable",
            }

        if not results:
            return json.dumps(
                {
                    "ok": True,
                    "results": [],
                    "vector_status": vector_status,
                    "message": "No matching pages found in the knowledge vault.",
                },
                ensure_ascii=False,
            )

        return json.dumps({"ok": True, "results": results, "vector_status": vector_status}, ensure_ascii=False, indent=2)

    except Exception as exc:
        logger.exception("query_knowledge_vault failed")
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
