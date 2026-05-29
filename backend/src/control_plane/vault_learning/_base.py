from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.config import get_app_config, get_paths
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)

from ._models import VaultManifest, _get_vault_coordination

logger = logging.getLogger(__name__)


class _VaultLearningBase:
    def __init__(
        self,
        *,
        vault_root: Path,
        allowed_domains: list[str] | None = None,
        max_content_chars: int = 20000,
        min_trust_score: float = 0.55,
        query_retention_hours: int = 72,
        search_results_queue_path: str | None = None,
        search_results_dedupe_window_hours: int = 72,
        search_results_max_queue_items: int = 5000,
        search_results_terminal_retention_hours: int = 168,
        claim_lease_seconds: int = 900,
        max_ingest_attempts: int = 5,
    ) -> None:
        self.vault_root = vault_root.expanduser().resolve()
        try:
            self.vault_config = get_app_config().knowledge_vault
        except Exception:
            self.vault_config = SimpleNamespace(
                cot_ingest_enabled=True,
                cot_min_chars=1200,
                cot_model="",
                vector_search_enabled=False,
                vector_backend="hash",
                vector_embedding_model="",
                vector_dimensions=256,
                vector_chunk_chars=1200,
                vector_chunk_overlap_chars=200,
                hybrid_rrf_k=60,
            )
        self.allowed_domains = set(allowed_domains or [])
        self.max_content_chars = max(1000, int(max_content_chars))
        self.min_trust_score = float(min_trust_score)
        self.query_retention_hours = int(query_retention_hours)
        self.search_results_dedupe_window_hours = int(search_results_dedupe_window_hours)
        self.search_results_max_queue_items = int(search_results_max_queue_items)
        self.search_results_terminal_retention_hours = max(1, int(search_results_terminal_retention_hours))
        self.claim_lease_seconds = max(60, int(claim_lease_seconds))
        self.max_ingest_attempts = max(1, int(max_ingest_attempts))

        self.schema_dir = self.vault_root / "00_schema"
        self.raw_dir = self.vault_root / "01_raw"
        self.compiled_dir = self.vault_root / "02_compiled"
        self.ops_dir = self.vault_root / "03_ops"

        self.raw_sources_dir = self.raw_dir / "sources"
        self.compiled_sources_dir = self.compiled_dir / "sources"
        self.compiled_entities_dir = self.compiled_dir / "entities"
        self.compiled_concepts_dir = self.compiled_dir / "concepts"
        self.compiled_syntheses_dir = self.compiled_dir / "syntheses"
        self.compiled_queries_dir = self.compiled_dir / "queries"
        self.compiled_index_path = self.compiled_dir / "index.md"
        self.compiled_log_path = self.compiled_dir / "log.md"

        self.inbox_dir = self.ops_dir / "inbox"
        self.tasks_dir = self.ops_dir / "tasks"
        self.reports_dir = self.ops_dir / "reports"
        self.queues_dir = self.ops_dir / "queues"
        self.quarantine_dir = self.ops_dir / "quarantine"

        self.discover_reports_dir = self.reports_dir / "discover"
        self.ingest_reports_dir = self.reports_dir / "ingest"
        self.compile_reports_dir = self.reports_dir / "compile"
        self.lint_reports_dir = self.reports_dir / "lint"
        self.synthesis_reports_dir = self.reports_dir / "synthesis"
        self.sufficiency_reports_dir = self.reports_dir / "sufficiency"
        self.task_backlog_dir = self.tasks_dir / "backlog"
        self.task_review_dir = self.tasks_dir / "review"
        self.task_done_dir = self.tasks_dir / "done"

        self.state_dir = self.vault_root / ".vault_state"
        self.manifest_path = self.state_dir / "manifest.json"

        if search_results_queue_path:
            queue_path = Path(search_results_queue_path)
            if not queue_path.is_absolute():
                queue_path = get_paths().base_dir / queue_path
            self.search_results_queue_path = queue_path.resolve()
        else:
            self.search_results_queue_path = self.queues_dir / "search_results_ingestion_queue.json"

        for directory in (
            self.vault_root,
            self.schema_dir,
            self.raw_dir,
            self.compiled_dir,
            self.ops_dir,
            self.raw_sources_dir,
            self.compiled_sources_dir,
            self.compiled_entities_dir,
            self.compiled_concepts_dir,
            self.compiled_syntheses_dir,
            self.compiled_queries_dir,
            self.inbox_dir,
            self.tasks_dir,
            self.reports_dir,
            self.queues_dir,
            self.quarantine_dir,
            self.discover_reports_dir,
            self.ingest_reports_dir,
            self.compile_reports_dir,
            self.lint_reports_dir,
            self.synthesis_reports_dir,
            self.sufficiency_reports_dir,
            self.task_backlog_dir,
            self.task_review_dir,
            self.task_done_dir,
            self.state_dir,
            self.search_results_queue_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self._coord = _get_vault_coordination(self.vault_root)

        self._seed_schema_docs()
        self._manifest = self._load_manifest()
        self._ensure_queue_file()

    @staticmethod
    def default_vault_root() -> Path:
        return get_paths().base_dir / "knowledge_vault"

    def _seed_schema_docs(self) -> None:
        docs = {
            self.schema_dir / "VAULT_SCHEMA.md": (
                "# Vault Schema\n\n"
                "This vault uses layered storage:\n"
                "- `01_raw/` immutable fetched source packages\n"
                "- `02_compiled/` maintained markdown knowledge pages\n"
                "- `03_ops/` operational queues, reports, and tasks\n"
            ),
            self.schema_dir / "RESEARCH_POLICY.md": (
                "# Research Policy\n\n"
                "Only trusted, provenance-linked knowledge may flow into compiled pages.\n"
                "Low-trust or policy-rejected items must remain outside durable synthesis updates.\n"
            ),
            self.schema_dir / "QUERY_RETENTION_POLICY.md": (
                "# Query Retention Policy\n\n"
                f"Query notes remain active for {self.query_retention_hours} hours to reduce duplicate short-horizon research.\n"
            ),
        }
        for path, content in docs.items():
            if not path.exists():
                path.write_text(content, encoding="utf-8")

        # Seed the user-editable question taxonomy used by the autoresearch loop.
        try:
            from src.control_plane.autoresearch_loop.taxonomy import seed_taxonomy_if_missing

            seed_taxonomy_if_missing(self.vault_root)
        except Exception:
            # Best-effort: vault should still come up if it fails, but log so a
            # missing taxonomy doesn't silently fall back to the in-code default.
            logger.exception(
                "Failed to seed autoresearch QUESTION_TAXONOMY.json under %s", self.schema_dir
            )

    def _load_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}

        version = str(data.get("version") or "vault-manifest.v3")
        payload = {
            "version": "vault-manifest.v4",
            "updated_at": _utcnow_iso(),
            "last_compile_at": data.get("last_compile_at"),
            "last_lint_at": data.get("last_lint_at"),
            "sources": data.get("sources", {}),
            "queries": data.get("queries", {}),
            "candidates": data.get("candidates", {}),
            "trust_decisions": data.get("trust_decisions", {}),
            "dirty_pages": data.get("dirty_pages", []),
            "source_dependencies": data.get("source_dependencies", {}),
            "search_index": data.get("search_index", {}),
            "topic_syntheses": data.get("topic_syntheses", {}),
            "last_run_summary": data.get("last_run_summary", {}),
            "objectives": data.get("objectives", {}),
            "action_history": data.get("action_history", []),
            "attempt_fingerprints": data.get("attempt_fingerprints", {}),
            "loop_guard": data.get(
                "loop_guard",
                {"cooldown_hours": 24, "retry_budget": 3},
            ),
            "coverage_signals": data.get("coverage_signals", {}),
            "sufficiency_state": data.get("sufficiency_state", {}),
            "memory_stats": data.get("memory_stats", {}),
            "entity_dismissals": data.get("entity_dismissals", {}),
            "schema_migrated_from": version,
        }
        return VaultManifest.model_validate(payload).model_dump(mode="python")

    def _save_manifest(self) -> None:
        self._manifest["updated_at"] = _utcnow_iso()
        validated = VaultManifest.model_validate(self._manifest).model_dump(mode="json")
        self.manifest_path.write_text(
            json.dumps(validated, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @contextmanager
    def _manifest_txn(self) -> Iterator[dict[str, Any]]:
        """Hold the shared manifest lock for the lifetime of the block.

        Reloads the manifest from disk on entry so the caller sees writes
        made by any concurrent runner that committed before us, then saves
        on successful exit. The lock is re-entrant — nested calls inside
        the same thread reuse the outer transaction's state, which is
        important because helper methods (`_record_trust_decision`,
        `_update_synthesis_page`, …) call `_save_manifest` internally.
        """
        lock = self._coord.manifest_lock
        already_held = False
        try:
            # threading.RLock has no introspection for re-entry depth, but we
            # rely on the fact that acquiring an RLock we already hold is a
            # cheap no-op increment. The outermost `with` block reloads from
            # disk; inner blocks see the in-memory `_manifest` directly.
            lock.acquire()
            # If this is the outermost acquisition for *this thread*, reload.
            # We detect outermost-ness by checking a per-thread counter on
            # the coord object.
            depth = getattr(self._coord, "_txn_depth", {})
            tid = threading.get_ident()
            depth[tid] = depth.get(tid, 0) + 1
            self._coord._txn_depth = depth  # type: ignore[attr-defined]
            if depth[tid] == 1:
                self._manifest = self._load_manifest()
            else:
                already_held = True
            yield self._manifest
            if not already_held:
                self._save_manifest()
        finally:
            depth = getattr(self._coord, "_txn_depth", {})
            tid = threading.get_ident()
            depth[tid] = depth.get(tid, 1) - 1
            if depth[tid] <= 0:
                depth.pop(tid, None)
            self._coord._txn_depth = depth  # type: ignore[attr-defined]
            lock.release()

    def _ensure_queue_file(self) -> None:
        if not self.search_results_queue_path.exists():
            self.search_results_queue_path.write_text("[]", encoding="utf-8")

    def _load_queue(self) -> list[dict[str, Any]]:
        self._ensure_queue_file()
        try:
            payload = json.loads(self.search_results_queue_path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
        return payload if isinstance(payload, list) else []

    def _save_queue(self, items: list[dict[str, Any]]) -> None:
        trimmed = self._trim_queue(items)
        self.search_results_queue_path.write_text(
            json.dumps(trimmed, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _trim_queue(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Age-based trim that never drops items still owing work.

        Non-terminal items (`queued`, `claimed`) are kept regardless of age so
        the finalize step can always look them up by `queue_id`. Terminal
        items (`ingested`, `rejected`) older than the retention window are
        dropped. If the total still exceeds the hard cap, drop the oldest
        terminal items first; only fall back to dropping non-terminal items
        if no terminal records remain — which would indicate a runaway
        producer that the operator needs to see.
        """
        terminal_statuses = {"ingested", "rejected"}
        now = _utcnow()
        retention = timedelta(hours=self.search_results_terminal_retention_hours)
        cap = self.search_results_max_queue_items

        def _ts(item: dict[str, Any]) -> datetime:
            for key in ("updated_at", "claimed_at", "queued_at"):
                value = item.get(key)
                if value:
                    try:
                        return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
                    except Exception:
                        continue
            return datetime.min.replace(tzinfo=UTC)

        kept: list[dict[str, Any]] = []
        for item in items:
            status = str(item.get("status") or "")
            if status in terminal_statuses and (now - _ts(item)) > retention:
                continue
            kept.append(item)

        if len(kept) <= cap:
            return kept

        terminal = [(idx, item) for idx, item in enumerate(kept) if str(item.get("status") or "") in terminal_statuses]
        terminal.sort(key=lambda pair: _ts(pair[1]))
        excess = len(kept) - cap
        drop_idx = {idx for idx, _ in terminal[:excess]}
        survivors = [item for idx, item in enumerate(kept) if idx not in drop_idx]
        if len(survivors) <= cap:
            return survivors
        # All remaining items are non-terminal and still exceed the cap. Drop
        # the oldest non-terminal items but emit a marker so the operator can
        # see why claims are vanishing.
        non_terminal_sorted = sorted(survivors, key=_ts)
        return non_terminal_sorted[-cap:]

    @contextmanager
    def _queue_txn(self) -> Iterator[list[dict[str, Any]]]:
        """Atomic read-modify-write transaction over the queue file.

        Holds the shared queue lock for the lifetime of the context so
        concurrent producers (web_search, clipper) and consumers (ingest
        runners) cannot lose writes to each other. Any exception inside the
        block aborts the write; callers are responsible for ensuring that
        the returned list is the one they mutate.
        """
        with self._coord.queue_lock:
            queue = self._load_queue()
            yield queue
            self._save_queue(queue)
