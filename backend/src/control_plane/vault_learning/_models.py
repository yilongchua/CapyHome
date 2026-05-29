from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)

# ---------------------------------------------------------------------------
# Cross-instance coordination
#
# `_default_vault_manager()` builds a fresh `VaultLearningManager` per call, so
# instance-level locks would not coordinate concurrent ingest runs. We keep a
# module-level registry keyed by the vault root: every manager pointing at the
# same vault shares the same queue lock, manifest lock, and active-runner
# counter. The counter is used to gate destructive cleanup operations against
# concurrent ingest writes.
# ---------------------------------------------------------------------------

@dataclass
class PrefetchedIngest:
    """Precomputed bundle for a single queue item.

    Holds all the heavy I/O results — URL fetch, raw payload, trust scoring,
    and LLM analysis/generation — so the per-item ``reingest_if_changed`` call
    inside the manifest lock can become a pure manifest-write step. This is
    what lets concurrent workers actually parallelise (the LLM calls run in
    ``_prefetch_for_ingest`` outside the shared manifest lock).
    """

    source_id: str
    url: str
    title: str
    raw_text: str
    raw_payload: str
    raw_extension: str
    content_hash: str
    fetched_at: datetime
    trust_score: float
    trust_reasons: list[str]
    topic_tags: list[str]
    concept_refs: list[str]
    entity_refs: list[str]
    target_synthesis_refs: list[str]
    analysis: dict[str, Any]
    generated_page: dict[str, Any]
    raw_source_path: Path | None = None
    raw_metadata_path: Path | None = None
    appears_unchanged: bool = False
    appears_untrusted: bool = False


@dataclass
class _VaultCoordination:
    queue_lock: threading.RLock = field(default_factory=threading.RLock)
    manifest_lock: threading.RLock = field(default_factory=threading.RLock)
    counter_lock: threading.Lock = field(default_factory=threading.Lock)
    active_runners: int = 0


_VAULT_COORDINATION: dict[Path, _VaultCoordination] = {}
_VAULT_COORDINATION_GLOBAL_LOCK = threading.Lock()


def _get_vault_coordination(vault_root: Path) -> _VaultCoordination:
    with _VAULT_COORDINATION_GLOBAL_LOCK:
        coord = _VAULT_COORDINATION.get(vault_root)
        if coord is None:
            coord = _VaultCoordination()
            _VAULT_COORDINATION[vault_root] = coord
        return coord


class VaultLoopGuardConfig(BaseModel):
    cooldown_hours: int = 24
    retry_budget: int = 3
    model_config = ConfigDict(extra="allow")


class VaultManifest(BaseModel):
    version: str = "vault-manifest.v4"
    updated_at: str = ""
    last_compile_at: str | None = None
    last_lint_at: str | None = None
    sources: dict[str, Any] = Field(default_factory=dict)
    queries: dict[str, Any] = Field(default_factory=dict)
    candidates: dict[str, Any] = Field(default_factory=dict)
    trust_decisions: dict[str, Any] = Field(default_factory=dict)
    dirty_pages: list[str] = Field(default_factory=list)
    source_dependencies: dict[str, Any] = Field(default_factory=dict)
    search_index: dict[str, Any] = Field(default_factory=dict)
    topic_syntheses: dict[str, Any] = Field(default_factory=dict)
    last_run_summary: dict[str, Any] = Field(default_factory=dict)
    objectives: dict[str, Any] = Field(default_factory=dict)
    action_history: list[dict[str, Any]] = Field(default_factory=list)
    attempt_fingerprints: dict[str, Any] = Field(default_factory=dict)
    loop_guard: VaultLoopGuardConfig = Field(default_factory=VaultLoopGuardConfig)
    coverage_signals: dict[str, Any] = Field(default_factory=dict)
    sufficiency_state: dict[str, Any] = Field(default_factory=dict)
    memory_stats: dict[str, Any] = Field(default_factory=dict)
    entity_dismissals: dict[str, Any] = Field(default_factory=dict)
    schema_migrated_from: str = "vault-manifest.v4"
    model_config = ConfigDict(extra="allow")


def _query_id_for_identity(query_text: str, topic_tags: list[str]) -> str:
    normalized = f"{query_text.strip().lower()}|{'|'.join(sorted(_slugify(tag) for tag in topic_tags if tag.strip()))}"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
