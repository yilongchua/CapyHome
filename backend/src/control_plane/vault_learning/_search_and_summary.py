from __future__ import annotations

from typing import Any

from src.control_plane.services.unified_vault_search import UnifiedVaultSearchService
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class SearchSummaryMixin:
    def search(self, *, query: str, limit: int = 10) -> dict[str, Any]:
        return UnifiedVaultSearchService(self.vault_root).search_payload(query=query, limit=limit)

    def get_run_summary(self) -> dict[str, Any]:
        queue = self._load_queue()
        search_service = UnifiedVaultSearchService(self.vault_root)
        vector_status = search_service.vector_status()
        raw_bytes = self._raw_memory_bytes()
        memory = {
            "raw_bytes": raw_bytes,
            "raw_human": self._human_bytes(raw_bytes),
            "scope": "knowledge_vault/01_raw",
            "updated_at": _utcnow_iso(),
        }
        progress = self._coverage_progress()
        latest_sufficiency = {}
        sufficiency_state = self._manifest.get("sufficiency_state", {})
        if isinstance(sufficiency_state, dict) and sufficiency_state:
            latest_key = sorted(
                sufficiency_state.items(),
                key=lambda item: str(item[1].get("updated_at") if isinstance(item[1], dict) else ""),
                reverse=True,
            )[0][0]
            latest_sufficiency = {"objective_id": latest_key, **sufficiency_state.get(latest_key, {})}
        action_items = self.get_action_items(limit=50)
        return {
            "summary": self._manifest.get("last_run_summary", {}),
            "counts": {
                "sources_total": len(self._manifest.get("sources", {})),
                "queries_total": len(self._manifest.get("queries", {})),
                "candidates_total": len(self._manifest.get("candidates", {})),
                "trust_decisions_total": len(self._manifest.get("trust_decisions", {})),
                "search_index_total": len(self._manifest.get("search_index", {})),
                "dirty_pages": len(self._manifest.get("dirty_pages", [])),
                "queued_search_results": len([item for item in queue if str(item.get("status") or "") == "queued"]),
                "queued_clips": len([item for item in queue if str(item.get("source_tool") or "") == "browser_clipper" and str(item.get("status") or "") == "queued"]),
                "saved_outputs_total": len([item for item in self._manifest.get("sources", {}).values() if str(item.get("source") or "") == "explicit_save"]),
                "clip_sources_total": len([item for item in self._manifest.get("sources", {}).values() if str(item.get("source_tool") or "") == "browser_clipper"]),
                "vector_index_enabled": bool(vector_status.get("enabled")),
                "vector_index_chunks": int(vector_status.get("chunk_count") or 0),
                "vector_index_built_at": vector_status.get("built_at"),
                "last_compile_at": self._manifest.get("last_compile_at"),
                "last_lint_at": self._manifest.get("last_lint_at"),
            },
            "memory": memory,
            "progress": progress,
            "sufficiency": latest_sufficiency,
            "action_items": action_items.get("counts", {}),
            "objectives": {"total": len(self._manifest.get("objectives", {}))},
        }
