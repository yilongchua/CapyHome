"""Memory backend backed by the original ``memory.json`` + SQLite index.

This is a thin adapter, deliberately. It must reproduce today's behaviour
exactly so the migration's first phase is pure indirection with no behaviour
change; every quirk it preserves is documented in
``docs/memory_migration/issues_memory.md``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from src.agents.memory.backend import MemoryScopes
from src.agents.memory.vector_store import get_memory_vector_store

logger = logging.getLogger(__name__)


class LegacyJsonBackend:
    """Adapter over ``src.agents.memory.updater`` and the lexical index."""

    def __init__(self) -> None:
        # Serializes writers per (agent, scope, scope_id). Two threads must never
        # read-modify-write the same memory.json concurrently. This lock lived in
        # MemoryUpdateQueue before the backend seam; it is a storage invariant, so
        # it belongs with the storage.
        self._lock = threading.Lock()
        self._scope_locks: dict[tuple[str, str, str], threading.Lock] = {}

    def _scope_lock(self, *, agent_name: str | None, scope: str, scope_id: str | None) -> threading.Lock:
        key = (agent_name or "_global", scope, scope_id or "_")
        with self._lock:
            lock = self._scope_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._scope_locks[key] = lock
            return lock

    # -- writes ---------------------------------------------------------------

    def ingest(
        self,
        messages: list[Any],
        *,
        thread_id: str | None,
        agent_name: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        """Run the legacy two-pass extraction (global + workspace).

        Preserves defect Q-2 (two LLM calls per conversation) intentionally —
        collapsing it is the mem0 backend's job, not this adapter's.
        """
        from src.agents.memory.updater import MemoryUpdater

        updater = MemoryUpdater()
        resolved_workspace_id = workspace_id or thread_id

        with self._scope_lock(agent_name=agent_name, scope="global", scope_id=None):
            success_global = updater.update_memory(
                messages=messages,
                thread_id=thread_id,
                agent_name=agent_name,
                scope="global",
            )
        with self._scope_lock(agent_name=agent_name, scope="workspace", scope_id=resolved_workspace_id):
            success_workspace = updater.update_memory(
                messages=messages,
                thread_id=thread_id,
                agent_name=agent_name,
                scope="workspace",
                workspace_id=resolved_workspace_id,
            )
        return bool(success_global or success_workspace)

    # -- reads ----------------------------------------------------------------

    def search(self, query: str, *, scopes: MemoryScopes, top_k: int) -> list[dict[str, Any]]:
        return get_memory_vector_store().query(
            query=query,
            scopes=scopes.retrieval_pairs(),
            top_k=top_k,
        )

    def get_profile(self, *, scopes: MemoryScopes) -> dict[str, Any]:
        from src.agents.memory.updater import get_memory_data

        return get_memory_data(
            scopes.agent_name,
            scope=scopes.scope,
            workspace_id=scopes.workspace_id,
        )

    def reload_profile(self, *, scopes: MemoryScopes) -> dict[str, Any]:
        from src.agents.memory.updater import reload_memory_data

        return reload_memory_data(
            scopes.agent_name,
            scope=scopes.scope,
            workspace_id=scopes.workspace_id,
        )

    # -- fact CRUD ------------------------------------------------------------

    def upsert_fact(
        self,
        *,
        fact_id: str,
        content: str,
        scopes: MemoryScopes,
        category: str = "context",
        confidence: float = 0.9,
        source: str = "manual",
    ) -> dict[str, Any]:
        from src.agents.memory.updater import upsert_fact

        return upsert_fact(
            fact_id=fact_id,
            content=content,
            category=category,
            confidence=confidence,
            source=source,
            scope=scopes.scope,
            workspace_id=scopes.workspace_id,
        )

    def delete_fact(self, *, fact_id: str, scopes: MemoryScopes) -> bool:
        from src.agents.memory.updater import delete_fact

        return delete_fact(fact_id=fact_id, scope=scopes.scope, workspace_id=scopes.workspace_id)

    def delete_scope(self, *, scopes: MemoryScopes, source: str = "memory-ui") -> dict[str, Any]:
        from src.agents.memory.updater import clear_memory

        return clear_memory(scope=scopes.scope, workspace_id=scopes.workspace_id, source=source)

    def forget_run(self, run_id: str, *, scopes: MemoryScopes) -> int:
        from src.agents.memory.updater import forget_thread_facts

        return forget_thread_facts(run_id, scope=scopes.scope, workspace_id=scopes.workspace_id)

    # -- behavior rules -------------------------------------------------------

    def add_rule(self, *, instruction: str, scopes: MemoryScopes, source: str = "api", active: bool = True) -> dict[str, Any]:
        from src.agents.memory.updater import add_behavior_rule

        return add_behavior_rule(
            instruction=instruction,
            scope=scopes.scope,
            workspace_id=scopes.workspace_id,
            source=source,
            active=active,
        )

    def update_rule(
        self,
        *,
        rule_id: str,
        scopes: MemoryScopes,
        instruction: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        from src.agents.memory.updater import update_behavior_rule

        return update_behavior_rule(
            rule_id=rule_id,
            instruction=instruction,
            active=active,
            scope=scopes.scope,
            workspace_id=scopes.workspace_id,
        )

    def delete_rule(self, *, rule_id: str, scopes: MemoryScopes) -> bool:
        from src.agents.memory.updater import delete_behavior_rule

        return delete_behavior_rule(rule_id=rule_id, scope=scopes.scope, workspace_id=scopes.workspace_id)
