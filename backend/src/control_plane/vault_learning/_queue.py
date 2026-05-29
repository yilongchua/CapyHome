from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.control_plane.vault_text_utils import (
    frontmatter_dump as _frontmatter_dump,
)
from src.control_plane.vault_text_utils import (
    parse_frontmatter as _parse_frontmatter,
)
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)

from ._models import _query_id_for_identity


class QueueMixin:
    def enqueue_search_results(self, *, query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        appended: list[dict[str, Any]] = []
        duplicates = 0
        skipped = 0
        now = _utcnow()
        dedupe_deadline = now - timedelta(hours=self.search_results_dedupe_window_hours)

        with self._queue_txn() as queue:
            for result in results:
                if not isinstance(result, dict):
                    skipped += 1
                    continue
                extracted = str(result.get("extracted_content") or "").strip()
                url = str(result.get("url") or "").strip()
                if not extracted or not url:
                    skipped += 1
                    continue
                content_hash = hashlib.sha256(extracted.encode("utf-8")).hexdigest()
                duplicate_match = next(
                    (
                        item
                        for item in queue
                        if str(item.get("url") or "") == url
                        and str(item.get("content_hash") or "") == content_hash
                        and str(item.get("status") or "") in {"queued", "claimed", "ingested"}
                        and datetime.fromisoformat(str(item.get("queued_at"))).replace(tzinfo=UTC) >= dedupe_deadline
                    ),
                    None,
                )
                if duplicate_match is not None:
                    duplicates += 1
                    continue

                entry = {
                    "queue_id": f"queue-{uuid4().hex[:12]}",
                    "queued_at": now.isoformat(),
                    "source_tool": str(result.get("source_tool") or "web_search").strip() or "web_search",
                    "query": query,
                    "title": str(result.get("title") or "").strip(),
                    "url": url,
                    "snippet": str(result.get("snippet") or "").strip(),
                    "extracted_content": extracted,
                    "topic_tags": [str(item).strip() for item in result.get("topic_tags", []) if str(item).strip()],
                    "concept_refs": [str(item).strip() for item in result.get("concept_refs", []) if str(item).strip()],
                    "entity_refs": [str(item).strip() for item in result.get("entity_refs", []) if str(item).strip()],
                    "target_synthesis_refs": [str(item).strip() for item in result.get("target_synthesis_refs", []) if str(item).strip()],
                    "status": "queued",
                    "reason": str(result.get("reason") or "enriched_web_search_result").strip() or "enriched_web_search_result",
                    "content_hash": content_hash,
                    "attempt_count": 0,
                }
                source_markdown_path = str(result.get("source_markdown_path") or "").strip()
                if source_markdown_path:
                    entry["source_markdown_path"] = source_markdown_path
                metadata = result.get("metadata")
                if isinstance(metadata, dict) and metadata:
                    entry["metadata"] = metadata
                queue.append(entry)
                appended.append(entry)

        return {
            "query": query,
            "appended_count": len(appended),
            "duplicate_count": duplicates,
            "skipped_count": skipped,
            "queue_path": str(self.search_results_queue_path),
            "items": appended,
        }

    def claim_search_queue_items(self, *, topic: str = "", max_items: int = 10) -> list[dict[str, Any]]:
        """Atomically claim up to `max_items` queue entries for processing.

        A claim is *also* eligible for stealing if its lease has expired —
        this lets a new runner pick up items left behind by a worker that
        crashed or hung, without waiting for an explicit orphan-rescue pass.
        Each claim stamps `claim_lease_until` and bumps `attempt_count`.
        """
        claimed: list[dict[str, Any]] = []
        topic_slug = self._topic_slug(topic) if topic.strip() else ""
        now = _utcnow()
        now_iso = now.isoformat()
        lease_until = (now + timedelta(seconds=self.claim_lease_seconds)).isoformat()

        with self._queue_txn() as queue:
            for item in queue:
                status = str(item.get("status") or "")
                if status == "queued":
                    pass
                elif status == "claimed":
                    lease_value = item.get("claim_lease_until")
                    if not lease_value:
                        # Legacy claim without a lease — treat as stealable.
                        pass
                    else:
                        try:
                            lease_dt = datetime.fromisoformat(str(lease_value)).replace(tzinfo=UTC)
                        except Exception:
                            lease_dt = now  # Malformed → consider expired.
                        if lease_dt > now:
                            continue
                else:
                    continue
                if topic_slug:
                    tags = [str(tag).strip() for tag in item.get("topic_tags", [])]
                    text = f"{item.get('query', '')} {item.get('title', '')}".lower()
                    if topic_slug not in tags and topic_slug not in text:
                        continue
                item["status"] = "claimed"
                item["claimed_at"] = now_iso
                item["claim_lease_until"] = lease_until
                item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
                claimed.append(dict(item))
                if len(claimed) >= max(1, int(max_items)):
                    break

        return claimed

    def renew_queue_claim_lease(self, queue_ids: list[str]) -> None:
        """Extend the lease on currently-claimed items. Long-running ingest
        loops should call this periodically so a slow job is not stolen by
        another runner mid-process."""
        if not queue_ids:
            return
        now = _utcnow()
        lease_until = (now + timedelta(seconds=self.claim_lease_seconds)).isoformat()
        queue_id_set = set(queue_ids)
        with self._queue_txn() as queue:
            for item in queue:
                if str(item.get("queue_id") or "") not in queue_id_set:
                    continue
                if str(item.get("status") or "") != "claimed":
                    continue
                item["claim_lease_until"] = lease_until

    def _mark_queue_items(self, queue_ids: list[str], *, status: str, reason: str = "") -> None:
        if not queue_ids:
            return
        now = _utcnow_iso()
        queue_id_set = set(queue_ids)
        with self._queue_txn() as queue:
            for item in queue:
                if str(item.get("queue_id") or "") not in queue_id_set:
                    continue
                item["status"] = status
                item["updated_at"] = now
                if reason:
                    item["reason"] = reason
                # Terminal statuses release the lease and remove transient fields.
                if status in {"ingested", "rejected"}:
                    item.pop("claim_lease_until", None)

    def requeue_claimed_items(self, queue_ids: list[str], *, reason: str = "ingest_failed_retry") -> None:
        """Return claimed items back to queued so a later ingest run can retry them.

        Items whose `attempt_count` has reached `max_ingest_attempts` are
        marked `rejected` with reason `max_attempts_exceeded` instead, so a
        poison-pill URL doesn't bounce between runners forever.
        """
        if not queue_ids:
            return
        now = _utcnow_iso()
        queue_id_set = set(queue_ids)
        with self._queue_txn() as queue:
            for item in queue:
                if str(item.get("queue_id") or "") not in queue_id_set:
                    continue
                if str(item.get("status") or "") != "claimed":
                    continue
                attempts = int(item.get("attempt_count") or 0)
                if attempts >= self.max_ingest_attempts:
                    item["status"] = "rejected"
                    item["reason"] = "max_attempts_exceeded"
                else:
                    item["status"] = "queued"
                    item["reason"] = reason
                item["updated_at"] = now
                item.pop("claim_lease_until", None)
                item.pop("claimed_at", None)

    def requeue_all_claimed_items(
        self,
        *,
        reason: str = "orphaned_from_prior_run",
        force: bool = False,
    ) -> int:
        """Return every `claimed` item with an expired (or missing) lease back to `queued`.

        Live claims with an unexpired lease are left alone — a parallel
        runner may still be working on them. The "rescue all" semantics from
        before parallel ingest landed are no longer correct because a fresh
        job no longer implies no other job exists.

        Pass `force=True` to ignore the lease check entirely. This is only
        safe when the caller knows no live worker is holding the claim — for
        example, at gateway startup when the in-process runner registry is
        empty so any "claimed" item must be from a previous process that
        died before its lease expired.
        """
        now = _utcnow()
        now_iso = now.isoformat()
        count = 0
        with self._queue_txn() as queue:
            for item in queue:
                if str(item.get("status") or "") != "claimed":
                    continue
                if not force:
                    lease_value = item.get("claim_lease_until")
                    if lease_value:
                        try:
                            lease_dt = datetime.fromisoformat(str(lease_value)).replace(tzinfo=UTC)
                        except Exception:
                            lease_dt = now
                        if lease_dt > now:
                            continue
                item["status"] = "queued"
                item["updated_at"] = now_iso
                if reason:
                    item["reason"] = reason
                item.pop("claim_lease_until", None)
                item.pop("claimed_at", None)
                count += 1
        return count

    def clear_queued_search_results(self, *, reason: str = "rejected_by_user") -> int:
        with self._queue_txn() as queue:
            queued_ids = [
                str(item.get("queue_id") or "")
                for item in queue
                if str(item.get("status") or "") == "queued"
            ]
            queued_ids = [queue_id for queue_id in queued_ids if queue_id]
            if not queued_ids:
                return 0
            now = _utcnow_iso()
            queue_id_set = set(queued_ids)
            for item in queue:
                if str(item.get("queue_id") or "") not in queue_id_set:
                    continue
                item["status"] = "rejected"
                item["updated_at"] = now
                item["reason"] = reason
                item.pop("claim_lease_until", None)
        return len(queued_ids)

    def dedupe_recent_queries(self, *, query_text: str, topic_tags: list[str] | None = None) -> dict[str, Any] | None:
        normalized_key = _query_id_for_identity(query_text, topic_tags or [])
        now = _utcnow()
        for record in self._manifest["queries"].values():
            if str(record.get("identity_key") or "") != normalized_key:
                continue
            expires_at = record.get("expires_at")
            if not expires_at:
                continue
            if datetime.fromisoformat(str(expires_at)).replace(tzinfo=UTC) < now:
                continue
            return record
        return None

    def write_query_note(
        self,
        *,
        query_text: str,
        topic_tags: list[str] | None = None,
        concept_refs: list[str] | None = None,
        synthesis_refs: list[str] | None = None,
        content: str = "",
    ) -> dict[str, Any]:
        topic_tags = [str(item).strip() for item in (topic_tags or []) if str(item).strip()]
        identity_key = _query_id_for_identity(query_text, topic_tags)
        existing = self.dedupe_recent_queries(query_text=query_text, topic_tags=topic_tags)
        if existing is not None:
            existing["last_seen_at"] = _utcnow_iso()
            self._manifest["queries"][str(existing["query_id"])] = existing
            self._save_manifest()
            return {"status": "deduped", "query_id": existing["query_id"], "path": existing["path"]}

        query_id = self._query_id_for_text(query_text)
        created_at = _utcnow()
        expires_at = created_at + timedelta(hours=self.query_retention_hours)
        path = self._compiled_query_path(query_id)
        payload = {
            "query_id": query_id,
            "query_text": query_text,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "status": "active",
            "topic_tags": topic_tags,
            "concept_refs": concept_refs or [],
            "synthesis_refs": synthesis_refs or [],
        }
        sections = [content.strip() or "## Summary\n\nTransient research note retained for anti-duplication purposes."]
        self._write_page(path=path, frontmatter=payload, title=query_text, sections=sections)
        record = {
            **payload,
            "identity_key": identity_key,
            "path": str(path),
            "last_seen_at": created_at.isoformat(),
        }
        self._manifest["queries"][query_id] = record
        self._manifest["dirty_pages"] = sorted(set(self._manifest["dirty_pages"]) | {"queries/index.md", "index.md"})
        self._index_document(
            doc_id=query_id,
            kind="query",
            title=query_text,
            path=path,
            text=content or query_text,
            tags=topic_tags,
        )
        self._save_manifest()
        return {"status": "created", "query_id": query_id, "path": str(path)}

    def expire_queries(self) -> dict[str, Any]:
        expired: list[str] = []
        now = _utcnow()
        for query_id, record in list(self._manifest["queries"].items()):
            expires_at = record.get("expires_at")
            if not expires_at:
                continue
            if datetime.fromisoformat(str(expires_at)).replace(tzinfo=UTC) > now:
                continue
            if str(record.get("status") or "") == "active":
                record["status"] = "expired"
                expired.append(query_id)
                path = Path(str(record.get("path") or ""))
                if path.exists():
                    frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
                    frontmatter["status"] = "expired"
                    path.write_text(f"{_frontmatter_dump(frontmatter)}\n\n{body}", encoding="utf-8")
        if expired:
            self._save_manifest()
        return {"expired_count": len(expired), "expired_query_ids": expired}
