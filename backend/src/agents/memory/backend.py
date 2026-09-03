"""Storage-agnostic seam for the persistent-memory subsystem.

Every read and write of long-term memory goes through :class:`MemoryBackend`.
Two implementations exist during the mem0 migration:

* ``LegacyJsonBackend`` — wraps the original ``memory.json`` + SQLite index path.
* ``Mem0Backend``       — self-hosted mem0 store (see docs/memory_migration/).

The selected implementation is controlled by ``memory.backend`` in ``config.yaml``.

Design notes
------------
* **The API schema is a projection.** ``get_profile`` returns the historical
  ``MemoryResponse`` shape regardless of backend, which is what keeps the REST
  endpoints and the frontend unchanged. Never leak backend-native shapes past
  this seam.
* **Sync by design.** mem0's client is synchronous and this repo has a known
  failure mode where a sync call inside an ``async def`` router blocks the whole
  gateway. Callers must invoke backends from threads (the memory queue already
  does) or from ``def`` endpoints.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

MEMORY_SCOPE_GLOBAL = "global"
MEMORY_SCOPE_WORKSPACE = "workspace"


def normalize_scope(scope: str | None) -> str:
    """Coerce *scope* to a known value, defaulting to ``global``."""
    normalized = (scope or MEMORY_SCOPE_GLOBAL).strip().lower()
    return normalized if normalized in {MEMORY_SCOPE_GLOBAL, MEMORY_SCOPE_WORKSPACE} else MEMORY_SCOPE_GLOBAL


@dataclass(frozen=True)
class MemoryScopes:
    """Resolved scope identity for one memory operation.

    Collapses the ``(scope, workspace_id, agent_name)`` triple that today is
    threaded through 20+ signatures and re-normalized in three separate modules.

    ``scope``/``scope_id`` name the *primary* target. ``include_global`` widens
    a read to also cover the global scope — retrieval wants both, CRUD does not.
    """

    scope: str = MEMORY_SCOPE_GLOBAL
    scope_id: str = MEMORY_SCOPE_GLOBAL
    agent_name: str | None = None
    include_global: bool = False

    @classmethod
    def resolve(
        cls,
        scope: str | None = MEMORY_SCOPE_GLOBAL,
        workspace_id: str | None = None,
        agent_name: str | None = None,
        *,
        include_global: bool = False,
    ) -> MemoryScopes:
        """Build scopes from the legacy parameter triple."""
        normalized = normalize_scope(scope)
        if normalized == MEMORY_SCOPE_WORKSPACE:
            return cls(normalized, str(workspace_id or "default-workspace"), agent_name, include_global)
        return cls(MEMORY_SCOPE_GLOBAL, MEMORY_SCOPE_GLOBAL, agent_name, include_global)

    @property
    def is_workspace(self) -> bool:
        return self.scope == MEMORY_SCOPE_WORKSPACE

    @property
    def workspace_id(self) -> str | None:
        """``scope_id`` when workspace-scoped, else ``None`` (legacy call shape)."""
        return self.scope_id if self.is_workspace else None

    # -- mem0 identity mapping -------------------------------------------------
    # CapyHome is single-user, so `user_id` is a constant and the workspace scope
    # maps onto mem0's `run_id`. See docs/memory_migration/05-mem0-mapping.md.

    @property
    def user_id(self) -> str:
        return MEMORY_SCOPE_GLOBAL

    @property
    def run_id(self) -> str | None:
        return self.scope_id if self.is_workspace else None

    @property
    def agent_id(self) -> str | None:
        return self.agent_name

    def retrieval_pairs(self) -> list[tuple[str, str | None]]:
        """``(scope, scope_id)`` pairs to search, most-specific first."""
        pairs: list[tuple[str, str | None]] = []
        if self.is_workspace:
            pairs.append((MEMORY_SCOPE_WORKSPACE, self.scope_id))
        if self.include_global or not self.is_workspace:
            pairs.append((MEMORY_SCOPE_GLOBAL, MEMORY_SCOPE_GLOBAL))
        return pairs


@runtime_checkable
class MemoryBackend(Protocol):
    """Storage contract for persistent memory."""

    def ingest(
        self,
        messages: list[Any],
        *,
        thread_id: str | None,
        agent_name: str | None = None,
        workspace_id: str | None = None,
    ) -> bool:
        """Extract and persist facts from a filtered conversation.

        One call covers every scope the backend maintains. The legacy backend
        fans this out to two LLM extractions (global + workspace); mem0 does it
        in a single ``add()``.

        Returns True when at least one scope was updated.
        """
        ...

    def search(self, query: str, *, scopes: MemoryScopes, top_k: int) -> list[dict[str, Any]]:
        """Return ranked Fact-shaped dicts. Each result carries a ``score``."""
        ...

    def get_profile(self, *, scopes: MemoryScopes) -> dict[str, Any]:
        """Return a ``MemoryResponse``-shaped dict for the given scope."""
        ...

    def reload_profile(self, *, scopes: MemoryScopes) -> dict[str, Any]:
        """Drop any cache and re-read the profile."""
        ...

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
        """Create or update a single fact. Returns the stored Fact dict."""
        ...

    def delete_fact(self, *, fact_id: str, scopes: MemoryScopes) -> bool:
        """Delete one fact. Returns False when it did not exist."""
        ...

    def delete_scope(self, *, scopes: MemoryScopes, source: str = "memory-ui") -> dict[str, Any]:
        """Clear an entire scope. Returns the resulting empty profile."""
        ...

    def forget_run(self, run_id: str, *, scopes: MemoryScopes) -> int:
        """Forget every fact sourced from *run_id*. Returns the count removed."""
        ...

    def add_rule(self, *, instruction: str, scopes: MemoryScopes, source: str = "api", active: bool = True) -> dict[str, Any]:
        ...

    def update_rule(self, *, rule_id: str, scopes: MemoryScopes, instruction: str | None = None, active: bool | None = None) -> dict[str, Any]:
        ...

    def delete_rule(self, *, rule_id: str, scopes: MemoryScopes) -> bool:
        ...


_BACKEND: MemoryBackend | None = None
_BACKEND_LOCK = threading.Lock()


def get_memory_backend() -> MemoryBackend:
    """Return the process-wide memory backend, constructing it on first use.

    Construction is locked: backends own per-scope write locks, so racing
    threads must not each end up with a private instance (which would silently
    defeat write serialization).
    """
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    with _BACKEND_LOCK:
        if _BACKEND is None:
            _BACKEND = _build_backend()
        return _BACKEND


def _build_backend() -> MemoryBackend:
    from src.config.memory_config import get_memory_config

    selected = (getattr(get_memory_config(), "backend", "legacy") or "legacy").strip().lower()
    if selected in {"mem0", "dual"}:
        raise NotImplementedError(
            f"memory.backend={selected!r} is not implemented yet; see docs/memory_migration/06-migration-plan.md"
        )

    from src.agents.memory.backends.legacy import LegacyJsonBackend

    return LegacyJsonBackend()


def set_memory_backend(backend: MemoryBackend | None) -> None:
    """Override the backend. Test hook — also used to force a rebuild."""
    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = backend


def reset_memory_backend() -> None:
    """Drop the cached backend so the next call rebuilds from config."""
    set_memory_backend(None)
