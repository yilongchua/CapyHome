from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from src.control_plane.vault_learning import VaultLearningManager
from src.control_plane.vault_text_utils import utcnow as _utcnow


def test_discover_writes_inbox_and_filters_urls(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, allowed_domains=["example.com"])
    report = vault.discover(
        urls=[
            "https://example.com/post-1",
            "ftp://example.com/bad",
            "https://not-allowed.com/post-2",
            "https://example.com/post-1",
        ],
        source="test",
    )

    assert report["candidate_count"] == 1
    assert report["rejected_count"] == 2
    assert Path(report["inbox_path"]).exists()


def test_ingest_creates_raw_and_compiled_layout(tmp_path: Path, monkeypatch) -> None:
    class _MockResponse:
        def __init__(self) -> None:
            self.text = (
                "<html><head><title>Test Article</title></head>"
                "<body><main><p>Hello CapyHome Vault. Maritime data quality improves.</p></main></body></html>"
            )

        def raise_for_status(self) -> None:
            return None

    def _fake_get(*args, **kwargs):  # noqa: ANN002, ANN003
        return _MockResponse()

    monkeypatch.setattr(httpx, "get", _fake_get)

    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)
    report = vault.ingest(
        urls=["https://example.com/test-article"],
        source="test",
        topic="maritime data quality",
    )

    assert report["ingested_count"] == 1
    assert (tmp_path / "00_schema" / "VAULT_SCHEMA.md").exists()
    assert list((tmp_path / "01_raw" / "sources").rglob("metadata.json"))
    assert list((tmp_path / "02_compiled" / "sources").glob("*.md"))
    assert (tmp_path / "02_compiled" / "index.md").exists()
    assert (tmp_path / "02_compiled" / "log.md").exists()
    assert list((tmp_path / "02_compiled" / "syntheses").glob("*.md"))


def test_query_retention_dedupes_for_72h(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, query_retention_hours=72)
    first = vault.write_query_note(
        query_text="vessel particulars for maritime data quality",
        topic_tags=["maritime-data-quality"],
        content="first note",
    )
    second = vault.write_query_note(
        query_text="vessel particulars for maritime data quality",
        topic_tags=["maritime-data-quality"],
        content="second note",
    )

    assert first["status"] == "created"
    assert second["status"] == "deduped"
    assert len(list((tmp_path / "02_compiled" / "queries").glob("*.md"))) == 1


def test_search_results_queue_requires_extracted_content_and_dedupes(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path)
    report = vault.enqueue_search_results(
        query="maritime quality",
        results=[
            {
                "title": "A",
                "url": "https://example.com/a",
                "snippet": "AA",
                "extracted_content": "# A\n\nAlpha",
                "topic_tags": ["maritime-quality"],
                "concept_refs": ["vessel-particulars"],
            },
            {
                "title": "B",
                "url": "https://example.com/b",
                "snippet": "BB",
            },
            {
                "title": "A2",
                "url": "https://example.com/a",
                "snippet": "AA",
                "extracted_content": "# A\n\nAlpha",
                "topic_tags": ["maritime-quality"],
            },
        ],
    )

    assert report["appended_count"] == 1
    assert report["duplicate_count"] == 1
    assert report["skipped_count"] == 1
    queue_items = vault.claim_search_queue_items(topic="maritime-quality", max_items=5)
    assert len(queue_items) == 1
    assert queue_items[0]["status"] == "claimed"


def test_queue_ingest_uses_extracted_content_and_updates_queue(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)
    enqueue = vault.enqueue_search_results(
        query="vessel particulars",
        results=[
            {
                "title": "Marine Data Quality",
                "url": "https://example.com/vessel-quality",
                "snippet": "desc",
                "extracted_content": "# Marine Data Quality\n\nVessel particulars improve data quality baselines.",
                "topic_tags": ["maritime-data-quality"],
                "concept_refs": ["vessel-particulars"],
                "target_synthesis_refs": ["maritime-data-quality-vessel-particulars"],
            }
        ],
    )
    assert enqueue["appended_count"] == 1

    queue_items = vault.claim_search_queue_items(topic="maritime-data-quality", max_items=5)
    report = vault.ingest(urls=[], source="autoresearch", topic="maritime data quality", queue_items=queue_items)
    assert report["queue_items_claimed"] == 1
    assert report["ingested_count"] == 1

    lint = vault.lint_vault(freshness_window_days=30)
    assert "queue_backlog_count" in lint

    search = vault.search(query="vessel particulars", limit=5)
    assert search["total"] >= 1


def test_loop_guard_blocks_repeated_attempts(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path)
    first = vault.check_loop_guard(
        objective_id="obj-maritime",
        topic="maritime",
        query_text="maritime data quality",
        cooldown_hours=24,
        retry_budget=2,
    )
    second = vault.check_loop_guard(
        objective_id="obj-maritime",
        topic="maritime",
        query_text="maritime data quality",
        cooldown_hours=24,
        retry_budget=2,
    )
    assert first["allowed"] is True
    assert second["allowed"] is False
    assert second["reason"] in {"cooldown_active", "retry_budget_exhausted"}


def test_status_includes_memory_progress_and_action_items(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path)
    (tmp_path / "01_raw" / "sources" / "sample").mkdir(parents=True, exist_ok=True)
    (tmp_path / "01_raw" / "sources" / "sample" / "file.txt").write_text("abc", encoding="utf-8")
    summary = vault.get_run_summary()
    assert "memory" in summary
    assert "progress" in summary
    assert "action_items" in summary
    assert int(summary["memory"]["raw_bytes"]) >= 3


def test_reprocess_existing_sources_backfills_entities_and_concepts(
    tmp_path: Path, monkeypatch
) -> None:
    class _MockResponse:
        def __init__(self) -> None:
            self.text = (
                "<html><head><title>Quantum Compute Review</title></head>"
                "<body><main><p>Quantum hybrid architectures power scientific computing research today.</p></main></body></html>"
            )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _MockResponse())

    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)
    vault.ingest(
        urls=["https://example.com/quantum-review"],
        source="test",
        topic="quantum hybrid computing",
    )

    # Simulate older buggy records that left entity_refs/concept_refs empty.
    sources = vault._manifest["sources"]
    assert sources, "Ingest should have produced at least one source"
    for record in sources.values():
        record["entity_refs"] = []
        record["concept_refs"] = []
    vault._save_manifest()

    progress_events: list[tuple[int, int, str]] = []

    def _track(index, total, source_id, title, status, error):  # noqa: ANN001
        progress_events.append((index, total, status))

    report = vault.reprocess_existing_sources(progress_callback=_track)

    assert report["total"] >= 1
    assert report["processed"] >= 1
    assert progress_events, "Progress callback should fire at least once"
    refreshed = list(vault._manifest["sources"].values())[0]
    assert refreshed["concept_refs"], "Reprocess should backfill concept_refs"
    compiled_concepts = list((tmp_path / "02_compiled" / "concepts").glob("*.md"))
    assert any(path.name != "index.md" for path in compiled_concepts)


def test_reprocess_existing_sources_only_missing_skips_populated(tmp_path: Path, monkeypatch) -> None:
    class _MockResponse:
        def __init__(self) -> None:
            self.text = (
                "<html><head><title>Maritime Data</title></head>"
                "<body><main><p>Maritime quality programs continue to improve significantly.</p></main></body></html>"
            )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _MockResponse())

    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)
    vault.ingest(
        urls=["https://example.com/maritime"],
        source="test",
        topic="maritime data quality",
    )

    for record in vault._manifest["sources"].values():
        record["entity_refs"] = ["existing-entity"]
        record["concept_refs"] = ["existing-concept"]
    vault._save_manifest()

    report = vault.reprocess_existing_sources(only_missing=True)
    assert report["total"] == 0
    assert report["processed"] == 0


def test_queue_ingest_duplicate_urls_mixed_outcomes_are_mapped_by_queue_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)

    monkeypatch.setattr(
        "src.control_plane.vault_learning.UnifiedVaultSearchService.ensure_vector_ready",
        lambda self: {"status": "ok"},
    )
    monkeypatch.setattr(vault, "compile_incremental", lambda: {"status": "ok"})

    with vault._queue_txn() as queue:
        queue.extend(
            [
                {
                    "queue_id": "q1",
                    "query": "topic-a",
                    "title": "First",
                    "url": "https://example.com/shared",
                    "status": "queued",
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "attempt_count": 0,
                },
                {
                    "queue_id": "q2",
                    "query": "topic-a",
                    "title": "Second",
                    "url": "https://example.com/shared",
                    "status": "queued",
                    "queued_at": "2026-01-01T00:00:01+00:00",
                    "updated_at": "2026-01-01T00:00:01+00:00",
                    "attempt_count": 0,
                },
            ]
        )

    claimed = vault.claim_search_queue_items(topic="", max_items=10)
    assert {item["queue_id"] for item in claimed} == {"q1", "q2"}

    def _fake_reingest_if_changed(**kwargs):  # noqa: ANN003
        queue_entry = kwargs.get("queue_entry") or {}
        queue_id = str(queue_entry.get("queue_id") or "")
        if queue_id == "q1":
            return {"status": "ingested", "source_id": "s1", "url": "https://example.com/shared"}
        if queue_id == "q2":
            raise RuntimeError("forced transient failure")
        raise AssertionError(f"unexpected queue_id: {queue_id}")

    monkeypatch.setattr(vault, "reingest_if_changed", _fake_reingest_if_changed)

    report = vault.ingest(urls=[], source="autoresearch", topic="", queue_items=claimed)
    assert report["ingested_count"] == 1
    assert report["fetch_failed_count"] == 1

    queue_state = {str(item.get("queue_id")): item for item in vault._load_queue()}
    # q1 ingested successfully and is purged at the end of the run; q2 failed
    # and stays queued for retry.
    assert "q1" not in queue_state
    assert queue_state["q2"]["status"] == "queued"
    assert queue_state["q2"]["reason"] == "fetch_failed_retry"


def test_queue_ingest_unknown_status_falls_back_to_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)

    monkeypatch.setattr(
        "src.control_plane.vault_learning.UnifiedVaultSearchService.ensure_vector_ready",
        lambda self: {"status": "ok"},
    )
    monkeypatch.setattr(vault, "compile_incremental", lambda: {"status": "ok"})

    enqueue = vault.enqueue_search_results(
        query="topic-b",
        results=[
            {
                "title": "Unknown Status Source",
                "url": "https://example.com/unknown-status",
                "snippet": "desc",
                "extracted_content": "# Source\n\nBody",
                "topic_tags": ["topic-b"],
            }
        ],
    )
    assert enqueue["appended_count"] == 1
    claimed = vault.claim_search_queue_items(topic="", max_items=10)
    assert len(claimed) == 1

    monkeypatch.setattr(
        vault,
        "reingest_if_changed",
        lambda **kwargs: {"status": "unexpected_status", "source_id": "s-unknown", "url": "https://example.com/unknown-status"},
    )

    report = vault.ingest(urls=[], source="autoresearch", topic="", queue_items=claimed)
    assert report["processed_count"] == 1
    assert report["ingested_count"] == 0
    assert report["fetch_failed_count"] == 0

    queue_state = vault._load_queue()
    assert len(queue_state) == 1
    assert queue_state[0]["status"] == "queued"
    assert queue_state[0]["reason"] == "unhandled_status_retry"


def _seed_queue_row(vault: VaultLearningManager, *, status: str, url: str, ts_iso: str) -> dict:
    """Append a raw queue row with a controlled status + timestamp."""
    row = {
        "queue_id": f"queue-{status}-{url.rsplit('/', 1)[-1]}",
        "queued_at": ts_iso,
        "updated_at": ts_iso,
        "source_tool": "web_search",
        "query": "q",
        "title": "T",
        "url": url,
        "snippet": "s",
        "extracted_content": "# T\n\nbody",
        "status": status,
        "reason": status,
        "content_hash": f"hash-{url}",
        "attempt_count": 1,
    }
    queue = vault._load_queue()
    queue.append(row)
    vault._save_queue(queue)
    return row


def test_purge_ingested_queue_items_removes_only_ingested(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path)
    now = _utcnow().isoformat()
    _seed_queue_row(vault, status="ingested", url="https://example.com/i1", ts_iso=now)
    _seed_queue_row(vault, status="ingested", url="https://example.com/i2", ts_iso=now)
    _seed_queue_row(vault, status="queued", url="https://example.com/q1", ts_iso=now)
    _seed_queue_row(vault, status="claimed", url="https://example.com/c1", ts_iso=now)
    _seed_queue_row(vault, status="rejected", url="https://example.com/r1", ts_iso=now)

    removed = vault.purge_ingested_queue_items()

    assert removed == 2
    statuses = sorted(str(item.get("status")) for item in vault._load_queue())
    assert statuses == ["claimed", "queued", "rejected"]


def test_purge_ingested_queue_items_noop_when_none(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path)
    _seed_queue_row(vault, status="queued", url="https://example.com/q1", ts_iso=_utcnow().isoformat())
    assert vault.purge_ingested_queue_items() == 0
    assert len(vault._load_queue()) == 1


def test_ingest_run_purges_ingested_and_aged_rejected_rows(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2, search_results_rejected_retention_hours=72)
    # Seed a stale rejected row that should be aged out by the ingest run, and a
    # fresh rejected row that should survive.
    _seed_queue_row(vault, status="rejected", url="https://example.com/old-reject", ts_iso=(_utcnow() - timedelta(hours=100)).isoformat())
    _seed_queue_row(vault, status="rejected", url="https://example.com/new-reject", ts_iso=(_utcnow() - timedelta(hours=1)).isoformat())
    vault.enqueue_search_results(
        query="vessel particulars",
        results=[
            {
                "title": "Marine Data Quality",
                "url": "https://example.com/vessel-quality",
                "snippet": "desc",
                "extracted_content": "# Marine Data Quality\n\nVessel particulars improve data quality baselines.",
                "topic_tags": ["maritime-data-quality"],
            }
        ],
    )
    claimed = vault.claim_search_queue_items(topic="maritime-data-quality", max_items=5)
    report = vault.ingest(urls=[], source="autoresearch", topic="maritime data quality", queue_items=claimed)

    assert report["ingested_count"] == 1
    assert report["purged_ingested_count"] == 1
    assert report["purged_rejected_count"] == 1
    # The ingested row and the stale rejected row must be gone; the fresh
    # rejected row stays until it ages past the retention window.
    urls = {str(item.get("url")) for item in vault._load_queue()}
    assert all(str(item.get("status")) != "ingested" for item in vault._load_queue())
    assert "https://example.com/old-reject" not in urls
    assert "https://example.com/new-reject" in urls


def test_ingest_after_purge_dedupes_via_manifest_not_queue(tmp_path: Path) -> None:
    """Removing the ingested row must not let a re-queued duplicate create a
    second vault source — the manifest hash_history backstop catches it."""
    vault = VaultLearningManager(vault_root=tmp_path, min_trust_score=0.2)
    payload = {
        "title": "Marine Data Quality",
        "url": "https://example.com/vessel-quality",
        "snippet": "desc",
        "extracted_content": "# Marine Data Quality\n\nVessel particulars improve data quality baselines.",
        "topic_tags": ["maritime-data-quality"],
    }
    vault.enqueue_search_results(query="vessel particulars", results=[payload])
    claimed = vault.claim_search_queue_items(topic="maritime-data-quality", max_items=5)
    first = vault.ingest(urls=[], source="autoresearch", topic="maritime data quality", queue_items=claimed)
    assert first["ingested_count"] == 1
    source_count_after_first = len(vault._manifest["sources"])

    # Re-enqueue the same url+content. With the ingested row purged, the queue
    # dedup guard no longer blocks it, so it is appended again.
    second_enqueue = vault.enqueue_search_results(query="vessel particulars", results=[payload])
    assert second_enqueue["appended_count"] == 1

    claimed2 = vault.claim_search_queue_items(topic="maritime-data-quality", max_items=5)
    second = vault.ingest(urls=[], source="autoresearch", topic="maritime data quality", queue_items=claimed2)

    # Ingest dedups against manifest hash_history -> skipped_unchanged, no new source.
    assert second["ingested_count"] == 0
    assert second["skipped_unchanged_count"] == 1
    assert len(vault._manifest["sources"]) == source_count_after_first


def test_purge_aged_rejected_respects_retention_window(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, search_results_rejected_retention_hours=72)
    fresh = (_utcnow() - timedelta(hours=1)).isoformat()
    aged = (_utcnow() - timedelta(hours=100)).isoformat()
    _seed_queue_row(vault, status="rejected", url="https://example.com/old", ts_iso=aged)
    _seed_queue_row(vault, status="rejected", url="https://example.com/new", ts_iso=fresh)
    _seed_queue_row(vault, status="queued", url="https://example.com/q1", ts_iso=aged)

    removed = vault.purge_aged_rejected_queue_items()

    assert removed == 1
    remaining = {str(item.get("url")) for item in vault._load_queue()}
    assert "https://example.com/old" not in remaining
    assert "https://example.com/new" in remaining
    assert "https://example.com/q1" in remaining  # queued never aged out here


def test_lint_vault_purges_aged_rejected_and_reports_count(tmp_path: Path) -> None:
    vault = VaultLearningManager(vault_root=tmp_path, search_results_rejected_retention_hours=72)
    aged = (_utcnow() - timedelta(hours=100)).isoformat()
    _seed_queue_row(vault, status="rejected", url="https://example.com/old", ts_iso=aged)

    report = vault.lint_vault(freshness_window_days=30)

    assert report["purged_rejected_count"] == 1
    assert all(str(item.get("status")) != "rejected" for item in vault._load_queue())
