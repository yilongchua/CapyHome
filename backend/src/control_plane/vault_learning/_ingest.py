from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from src.control_plane.services.unified_vault_search import UnifiedVaultSearchService
from src.control_plane.vault_text_utils import (
    extract_title as _extract_title,
)
from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)
from src.control_plane.vault_text_utils import (
    strip_html as _strip_html,
)
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)

from ._models import PrefetchedIngest

logger = logging.getLogger(__name__)


class IngestMixin:
    def _prefetch_for_ingest(
        self,
        *,
        url: str,
        topic: str = "",
        pre_extracted_content: str | None = None,
        queue_entry: dict[str, Any] | None = None,
        prefetch_progress: Callable[[str, str], None] | None = None,
    ) -> PrefetchedIngest:
        """Run all the heavy per-item work for a queue item OUTSIDE the manifest lock.

        Covers: URL fetch (or pre-extracted content read), content hashing,
        advisory dedup peek, trust scoring, raw-file writes, and the two LLM
        calls (``_analyze_source`` and ``_generate_source_sections``). The
        result is a ``PrefetchedIngest`` bundle that the caller passes back
        into ``reingest_if_changed`` so the manifest mutations can happen
        under a brief lock without redoing the slow work.

        Exceptions (network failures, HTTP errors) propagate to the caller
        so the existing ``fetch_failed`` requeue path still fires.
        """
        source_id = self._source_id_for_url(url)
        fetched_at = _utcnow()

        def _emit(phase: str, label: str) -> None:
            if prefetch_progress is None:
                return
            try:
                prefetch_progress(phase, label)
            except Exception:
                logger.exception("prefetch_progress callback raised, ignoring")

        provisional_title = str((queue_entry or {}).get("title") or url).strip() or url
        _emit("fetching", provisional_title)

        queue_markdown_path = str((queue_entry or {}).get("source_markdown_path") or "").strip()
        queue_markdown_content = ""
        if queue_markdown_path:
            try:
                queue_markdown_content = Path(queue_markdown_path).expanduser().resolve().read_text(encoding="utf-8")
            except Exception:
                queue_markdown_content = ""

        if queue_markdown_content or pre_extracted_content:
            markdown_payload = queue_markdown_content or pre_extracted_content or ""
            raw_text = markdown_payload[: self.max_content_chars]
            title = provisional_title
            raw_payload = markdown_payload
            raw_extension = ".md"
        else:
            response = httpx.get(url, timeout=20.0, follow_redirects=True)
            response.raise_for_status()
            html = response.text
            title = _extract_title(html, fallback=url)
            raw_text = _strip_html(html)[: self.max_content_chars]
            raw_payload = html
            raw_extension = ".html"

        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        # Advisory dedup peek: read the source's last hash *without* the
        # manifest lock. CPython dict reads are atomic (GIL), so we either
        # get the previous committed value or the value a concurrent worker
        # just wrote — both correct for comparison. A stale read can only
        # cause a redundant ingest, never a missed one.
        snapshot_record = self._manifest["sources"].get(source_id, {}) or {}
        committed_history = list(snapshot_record.get("hash_history", []))
        if committed_history and committed_history[-1] == content_hash:
            # Bail before doing trust scoring / LLM / disk writes.
            return PrefetchedIngest(
                source_id=source_id,
                url=url,
                title=title,
                raw_text=raw_text,
                raw_payload=raw_payload,
                raw_extension=raw_extension,
                content_hash=content_hash,
                fetched_at=fetched_at,
                trust_score=float(snapshot_record.get("trust_score") or 0.0),
                trust_reasons=[],
                topic_tags=[],
                concept_refs=[],
                entity_refs=[],
                target_synthesis_refs=[],
                analysis={},
                generated_page={},
                appears_unchanged=True,
            )

        trust_score, trust_reasons = self._trust_score(url=url, text=raw_text)

        raw_package_dir = self._raw_package_dir(source_id, fetched_at)
        raw_package_dir.mkdir(parents=True, exist_ok=True)
        raw_source_path = raw_package_dir / f"source{raw_extension}"
        raw_source_path.write_text(raw_payload, encoding="utf-8")
        raw_metadata_path = raw_package_dir / "metadata.json"

        topic_tags = self._topic_tags(topic, queue_entry)
        concept_refs = [str(item).strip() for item in (queue_entry or {}).get("concept_refs", []) if str(item).strip()]
        entity_refs = [str(item).strip() for item in (queue_entry or {}).get("entity_refs", []) if str(item).strip()]
        target_synthesis_refs = [
            str(item).strip() for item in (queue_entry or {}).get("target_synthesis_refs", []) if str(item).strip()
        ]

        if trust_score < self.min_trust_score:
            # Trust check failed — skip LLM, just write the truncated metadata
            # so the file system reflects what was fetched.
            raw_metadata = {
                "source_id": source_id,
                "source": "",
                "url": url,
                "title": title,
                "fetched_at": fetched_at.isoformat(),
                "content_hash": content_hash,
                "mime_type": "text/markdown" if raw_extension == ".md" else "text/html",
                "trust_score": round(trust_score, 4),
                "trust_reasons": trust_reasons,
                "topic_tags": topic_tags,
                "concept_refs": concept_refs,
                "entity_refs": entity_refs,
                "analysis": {},
                "generated_page": {"generation_mode": None, "review_items": []},
            }
            raw_metadata_path.write_text(json.dumps(raw_metadata, indent=2), encoding="utf-8")
            return PrefetchedIngest(
                source_id=source_id,
                url=url,
                title=title,
                raw_text=raw_text,
                raw_payload=raw_payload,
                raw_extension=raw_extension,
                content_hash=content_hash,
                fetched_at=fetched_at,
                trust_score=trust_score,
                trust_reasons=trust_reasons,
                topic_tags=topic_tags,
                concept_refs=concept_refs,
                entity_refs=entity_refs,
                target_synthesis_refs=target_synthesis_refs,
                analysis={},
                generated_page={},
                raw_source_path=raw_source_path,
                raw_metadata_path=raw_metadata_path,
                appears_untrusted=True,
            )

        _emit("analyzing", title)
        analysis = self._analyze_source(
            title=title,
            url=url,
            topic=topic,
            raw_text=raw_text,
            topic_tags=topic_tags,
            concept_refs=concept_refs,
            entity_refs=entity_refs,
            target_synthesis_refs=target_synthesis_refs,
        )
        topic_tags = self._topic_tags(topic, {"topic_tags": analysis.get("topic_tags", topic_tags)})
        concept_refs = list(dict.fromkeys(concept_refs + [str(item).strip() for item in analysis.get("concepts", []) if str(item).strip()]))
        entity_refs = list(dict.fromkeys(entity_refs + [str(item).strip() for item in analysis.get("entities", []) if str(item).strip()]))
        target_synthesis_refs = list(
            dict.fromkeys(target_synthesis_refs + [str(item).strip() for item in analysis.get("synthesis_refs", []) if str(item).strip()])
        )
        _emit("generating", title)
        generated_page = self._generate_source_sections(
            title=title,
            url=url,
            topic=topic,
            raw_text=raw_text,
            analysis=analysis,
        )

        raw_metadata = {
            "source_id": source_id,
            "source": "",
            "url": url,
            "title": title,
            "fetched_at": fetched_at.isoformat(),
            "content_hash": content_hash,
            "mime_type": "text/markdown" if raw_extension == ".md" else "text/html",
            "trust_score": round(trust_score, 4),
            "trust_reasons": trust_reasons,
            "topic_tags": topic_tags,
            "concept_refs": concept_refs,
            "entity_refs": entity_refs,
            "analysis": analysis,
            "generated_page": {
                "generation_mode": generated_page.get("generation_mode"),
                "review_items": generated_page.get("review_items", []),
            },
        }
        raw_metadata_path.write_text(json.dumps(raw_metadata, indent=2), encoding="utf-8")

        return PrefetchedIngest(
            source_id=source_id,
            url=url,
            title=title,
            raw_text=raw_text,
            raw_payload=raw_payload,
            raw_extension=raw_extension,
            content_hash=content_hash,
            fetched_at=fetched_at,
            trust_score=trust_score,
            trust_reasons=trust_reasons,
            topic_tags=topic_tags,
            concept_refs=concept_refs,
            entity_refs=entity_refs,
            target_synthesis_refs=target_synthesis_refs,
            analysis=analysis,
            generated_page=generated_page,
            raw_source_path=raw_source_path,
            raw_metadata_path=raw_metadata_path,
        )

    def reingest_if_changed(
        self,
        *,
        url: str,
        source: str,
        topic: str = "",
        pre_extracted_content: str | None = None,
        queue_entry: dict[str, Any] | None = None,
        tentative_hashes: dict[str, str] | None = None,
        precomputed: PrefetchedIngest | None = None,
    ) -> dict[str, Any]:
        """Ingest one URL or queue item and update the manifest in-memory.

        `tentative_hashes` carries the *uncommitted* content-hash updates
        for the current ingest run. When provided, the new hash is written
        there instead of being appended to `source_record['hash_history']`
        immediately, and the dedupe check consults both the committed
        history and the tentative dict. The caller (`ingest()`) commits or
        discards the tentative dict based on whether `compile_incremental`
        succeeds — this prevents a compile failure from poisoning the
        dedupe cache and silently skipping the retry.
        """
        if precomputed is None:
            source_id = self._source_id_for_url(url)
            fetched_at = _utcnow()

            queue_markdown_path = str((queue_entry or {}).get("source_markdown_path") or "").strip()
            queue_markdown_content = ""
            if queue_markdown_path:
                try:
                    queue_markdown_content = Path(queue_markdown_path).expanduser().resolve().read_text(encoding="utf-8")
                except Exception:
                    queue_markdown_content = ""

            if queue_markdown_content or pre_extracted_content:
                markdown_payload = queue_markdown_content or pre_extracted_content or ""
                raw_text = markdown_payload[: self.max_content_chars]
                title = str((queue_entry or {}).get("title") or url).strip() or url
                raw_payload = markdown_payload
                raw_extension = ".md"
            else:
                response = httpx.get(url, timeout=20.0, follow_redirects=True)
                response.raise_for_status()
                html = response.text
                title = _extract_title(html, fallback=url)
                raw_text = _strip_html(html)[: self.max_content_chars]
                raw_payload = html
                raw_extension = ".html"

            content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        else:
            source_id = precomputed.source_id
            fetched_at = precomputed.fetched_at
            title = precomputed.title
            raw_text = precomputed.raw_text
            raw_payload = precomputed.raw_payload
            raw_extension = precomputed.raw_extension
            content_hash = precomputed.content_hash

        source_record = self._manifest["sources"].get(source_id, {})
        committed_history = list(source_record.get("hash_history", []))
        tentative_hash = tentative_hashes.get(source_id) if tentative_hashes is not None else None
        effective_last_hash = tentative_hash or (committed_history[-1] if committed_history else None)
        if effective_last_hash == content_hash:
            source_record.update(
                {
                    "source_id": source_id,
                    "url": url,
                    "title": title,
                    "status": "skipped_unchanged",
                    "last_seen_at": _utcnow_iso(),
                }
            )
            self._manifest["sources"][source_id] = source_record
            self._record_trust_decision(
                source_id=source_id,
                url=url,
                score=float(source_record.get("trust_score") or 0.0),
                reasons=["content_hash_unchanged"],
                decision="skipped_unchanged",
            )
            return {"status": "skipped_unchanged", "source_id": source_id, "url": url}

        if precomputed is None:
            trust_score, trust_reasons = self._trust_score(url=url, text=raw_text)
            raw_package_dir = self._raw_package_dir(source_id, fetched_at)
            raw_package_dir.mkdir(parents=True, exist_ok=True)
            raw_source_path = raw_package_dir / f"source{raw_extension}"
            raw_source_path.write_text(raw_payload, encoding="utf-8")
            raw_metadata_path = raw_package_dir / "metadata.json"

            topic_tags = self._topic_tags(topic, queue_entry)
            concept_refs = [str(item).strip() for item in (queue_entry or {}).get("concept_refs", []) if str(item).strip()]
            entity_refs = [str(item).strip() for item in (queue_entry or {}).get("entity_refs", []) if str(item).strip()]
            target_synthesis_refs = [
                str(item).strip() for item in (queue_entry or {}).get("target_synthesis_refs", []) if str(item).strip()
            ]
            analysis = self._analyze_source(
                title=title,
                url=url,
                topic=topic,
                raw_text=raw_text,
                topic_tags=topic_tags,
                concept_refs=concept_refs,
                entity_refs=entity_refs,
                target_synthesis_refs=target_synthesis_refs,
            )
            topic_tags = self._topic_tags(topic, {"topic_tags": analysis.get("topic_tags", topic_tags)})
            concept_refs = list(dict.fromkeys(concept_refs + [str(item).strip() for item in analysis.get("concepts", []) if str(item).strip()]))
            entity_refs = list(dict.fromkeys(entity_refs + [str(item).strip() for item in analysis.get("entities", []) if str(item).strip()]))
            target_synthesis_refs = list(
                dict.fromkeys(target_synthesis_refs + [str(item).strip() for item in analysis.get("synthesis_refs", []) if str(item).strip()])
            )
            generated_page = self._generate_source_sections(
                title=title,
                url=url,
                topic=topic,
                raw_text=raw_text,
                analysis=analysis,
            )

            raw_metadata = {
                "source_id": source_id,
                "source": source,
                "url": url,
                "title": title,
                "fetched_at": fetched_at.isoformat(),
                "content_hash": content_hash,
                "mime_type": "text/markdown" if raw_extension == ".md" else "text/html",
                "trust_score": round(trust_score, 4),
                "trust_reasons": trust_reasons,
                "topic_tags": topic_tags,
                "concept_refs": concept_refs,
                "entity_refs": entity_refs,
                "analysis": analysis,
                "generated_page": {
                    "generation_mode": generated_page.get("generation_mode"),
                    "review_items": generated_page.get("review_items", []),
                },
            }
            raw_metadata_path.write_text(json.dumps(raw_metadata, indent=2), encoding="utf-8")
        else:
            trust_score = precomputed.trust_score
            trust_reasons = precomputed.trust_reasons
            raw_source_path = precomputed.raw_source_path or self._raw_package_dir(source_id, fetched_at) / f"source{raw_extension}"
            raw_metadata_path = precomputed.raw_metadata_path or self._raw_package_dir(source_id, fetched_at) / "metadata.json"
            topic_tags = precomputed.topic_tags
            concept_refs = precomputed.concept_refs
            entity_refs = precomputed.entity_refs
            target_synthesis_refs = precomputed.target_synthesis_refs
            analysis = precomputed.analysis
            generated_page = precomputed.generated_page

        if trust_score < self.min_trust_score:
            source_record.update(
                {
                    "source_id": source_id,
                    "url": url,
                    "title": title,
                    "status": "rejected_for_trust",
                    "trust_score": trust_score,
                    "last_seen_at": _utcnow_iso(),
                    "raw_path": str(raw_source_path),
                    "metadata_path": str(raw_metadata_path),
                }
            )
            self._manifest["sources"][source_id] = source_record
            self._record_trust_decision(
                source_id=source_id,
                url=url,
                score=trust_score,
                reasons=trust_reasons,
                decision="rejected_for_trust",
            )
            return {
                "status": "rejected_for_trust",
                "source_id": source_id,
                "url": url,
                "score": trust_score,
                "raw_path": str(raw_source_path),
            }

        compiled_source_path = self._compiled_source_path(source_id)
        synthesis_refs = self._update_synthesis_page(
            topic=topic,
            source_id=source_id,
            source_title=title,
            topic_tags=topic_tags,
            concept_refs=concept_refs,
            entity_refs=entity_refs,
            source_excerpt=raw_text,
            target_synthesis_refs=target_synthesis_refs,
        )
        for concept_ref in concept_refs:
            self._update_reference_page(
                path=self._compiled_concept_path(concept_ref),
                title=concept_ref.replace("-", " ").title(),
                kind="concept",
                source_id=source_id,
                source_title=title,
                topic_tags=topic_tags,
            )
        for entity_ref in entity_refs:
            self._update_reference_page(
                path=self._compiled_entity_path(entity_ref),
                title=entity_ref.replace("-", " ").title(),
                kind="entity",
                source_id=source_id,
                source_title=title,
                topic_tags=topic_tags,
            )

        source_frontmatter = {
            "source_id": source_id,
            "source_url": url,
            "fetched_at": fetched_at.isoformat(),
            "trust_status": "accepted",
            "trust_score": round(trust_score, 4),
            "raw_path": str(raw_source_path),
            "metadata_path": str(raw_metadata_path),
            "topic_tags": topic_tags,
            "entity_refs": entity_refs,
            "concept_refs": concept_refs,
            "synthesis_refs": synthesis_refs,
            "last_reviewed_at": _utcnow_iso(),
            "analysis_mode": analysis.get("analysis_mode"),
            "generation_mode": generated_page.get("generation_mode"),
            "open_questions": analysis.get("open_questions", []),
            "gap_queries": analysis.get("gap_queries", []),
        }
        sections = [
            "## Summary\n\n" + str(generated_page.get("summary_markdown") or raw_text[:1200]).strip(),
            "## Claims\n\n" + str(generated_page.get("claims_markdown") or "").strip(),
            "## Evidence\n\n" + str(generated_page.get("evidence_markdown") or "").strip(),
            "## Backlinks\n\n"
            + "\n".join([f"- {line}" for line in generated_page.get("backlink_lines", [])] or [f"- [[../syntheses/{ref}.md]]" for ref in synthesis_refs] or ["- None"]),
            "## Review Items\n\n" + "\n".join(f"- {item}" for item in (generated_page.get("review_items", [])[:10] or analysis.get("open_questions", [])[:10] or ["None"])),
            "## Gap Queries\n\n" + "\n".join(f"- {item}" for item in (analysis.get("gap_queries", [])[:10] or ["None"])),
        ]
        self._write_page(path=compiled_source_path, frontmatter=source_frontmatter, title=title, sections=sections)

        if tentative_hashes is not None:
            # Defer commit until `ingest()` confirms compile_incremental
            # succeeded. The dedupe check above already consulted this dict
            # for the current run, so within-run idempotency is preserved.
            tentative_hashes[source_id] = content_hash
            stored_history = committed_history[-10:]
        else:
            stored_history = (committed_history + [content_hash])[-10:]
        source_record.update(
            {
                "source_id": source_id,
                "url": url,
                "title": title,
                "status": "ingested",
                "trust_score": trust_score,
                "hash_history": stored_history,
                "last_ingested_at": _utcnow_iso(),
                "compiled_path": str(compiled_source_path),
                "raw_path": str(raw_source_path),
                "metadata_path": str(raw_metadata_path),
                "source": source,
                "topic_tags": topic_tags,
                "source_tool": str((queue_entry or {}).get("source_tool") or source),
                "analysis_mode": analysis.get("analysis_mode"),
                "generation_mode": generated_page.get("generation_mode"),
            }
        )
        self._manifest["sources"][source_id] = source_record
        self._record_trust_decision(
            source_id=source_id,
            url=url,
            score=trust_score,
            reasons=trust_reasons,
            decision="accepted",
        )

        dependencies = set(self._manifest["source_dependencies"].get(source_id, []))
        dependencies.update(
            {
                "02_compiled/index.md",
                "02_compiled/log.md",
                "02_compiled/sources/index.md",
                "02_compiled/syntheses/index.md",
                "02_compiled/queries/index.md",
            }
        )
        self._manifest["source_dependencies"][source_id] = sorted(dependencies)
        self._manifest["dirty_pages"] = sorted(set(self._manifest["dirty_pages"]) | dependencies)

        self._index_document(
            doc_id=source_id,
            kind="source",
            title=title,
            path=compiled_source_path,
            text="\n\n".join(
                [
                    str(analysis.get("summary") or ""),
                    "\n".join(str(item) for item in analysis.get("key_claims", [])[:8]),
                    raw_text,
                ]
            ),
            tags=topic_tags,
        )
        for question in analysis.get("open_questions", [])[:10]:
            question_text = str(question).strip()
            if not question_text:
                continue
            task_name = f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-{_slugify(question_text)[:48] or 'review'}-vault-review.md"
            task_path = self.task_review_dir / task_name
            if not task_path.exists():
                task_path.write_text(
                    f"# Vault Review Item\n\n- Source: `{title}`\n- URL: {url}\n- Review: {question_text}\n",
                    encoding="utf-8",
                )
        return {
            "status": "ingested",
            "source_id": source_id,
            "url": url,
            "score": trust_score,
            "compiled_path": str(compiled_source_path),
            "raw_path": str(raw_source_path),
            "analysis_mode": analysis.get("analysis_mode"),
            "generation_mode": generated_page.get("generation_mode"),
        }

    def ingest(
        self,
        *,
        urls: list[str],
        source: str,
        topic: str = "",
        queue_items: list[dict[str, Any]] | None = None,
        progress_callback: Callable[[int, int, str, str, str, str | None], None] | None = None,
        prefetch_progress: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        # Processing is handled in batches inside _ingest_locked, each with
        # its own manifest transaction, so a crash mid-run only loses the
        # current batch instead of all items.
        return self._ingest_locked(
            urls=urls,
            source=source,
            topic=topic,
            queue_items=queue_items,
            progress_callback=progress_callback,
            prefetch_progress=prefetch_progress,
        )

    def _ingest_locked(
        self,
        *,
        urls: list[str],
        source: str,
        topic: str = "",
        queue_items: list[dict[str, Any]] | None = None,
        progress_callback: Callable[[int, int, str, str, str, str | None], None] | None = None,
        prefetch_progress: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        # Strict embedding gate: do not ingest any sources unless /embeddings is reachable.
        search_service = UnifiedVaultSearchService(self.vault_root)
        try:
            vector_preflight = search_service.ensure_vector_ready()
        except Exception as exc:
            queue_item_ids = [str(item.get("queue_id") or "") for item in (queue_items or []) if str(item.get("queue_id") or "").strip()]
            self.requeue_claimed_items(queue_item_ids, reason="embedding_unavailable_retry")
            report = {
                "source": source,
                "topic": topic,
                "status": "deferred_embedding_unavailable",
                "processed_count": 0,
                "ingested_count": 0,
                "skipped_unchanged_count": 0,
                "rejected_for_trust_count": 0,
                "rejected_for_policy_count": 0,
                "queue_items_claimed": len(queue_items or []),
                "queue_items_requeued": len(queue_item_ids),
                "error": str(exc),
            }
            report_path = self.ingest_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-ingest.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            self._manifest["last_run_summary"] = {
                "step": "ingest",
                "status": report["status"],
                "updated_at": _utcnow_iso(),
                "queue_items_claimed": report["queue_items_claimed"],
                "queue_items_requeued": report["queue_items_requeued"],
            }
            self._save_manifest()
            return report

        BATCH_SIZE = 10

        normalized = self._normalize_urls(urls)
        ingested: list[dict[str, Any]] = []
        skipped_unchanged: list[dict[str, Any]] = []
        rejected_for_trust: list[dict[str, Any]] = []
        rejected_for_policy: list[dict[str, Any]] = []
        fetch_failed: list[dict[str, Any]] = []
        compile_report: dict[str, Any] = {"status": "skipped_no_items"}

        queue_items_list = queue_items or []

        # Phase 1A — Process queue items in batches of BATCH_SIZE.
        # Each batch's heavy I/O (URL fetch, LLM analysis, LLM generation) is
        # done *outside* the manifest lock via ``_prefetch_for_ingest`` so
        # concurrent workers can actually parallelise; the manifest mutations
        # (`reingest_if_changed`) still run serially under the lock.
        for batch_start in range(0, len(queue_items_list), BATCH_SIZE):
            batch = queue_items_list[batch_start:batch_start + BATCH_SIZE]
            batch_tentative_hashes: dict[str, str] = {}
            batch_queue_item_ids: list[str] = []
            batch_queue_outcomes: dict[str, str] = {}
            batch_ingested: list[dict[str, Any]] = []
            batch_skipped_unchanged: list[dict[str, Any]] = []
            batch_rejected_for_trust: list[dict[str, Any]] = []
            batch_fetch_failed: list[dict[str, Any]] = []

            # PHASE A — prefetch each item OUTSIDE the manifest lock. The
            # tuple holds (idx, item, prefetched | None, fetch_exception | None).
            prefetched_batch: list[tuple[int, dict[str, Any], PrefetchedIngest | None, Exception | None]] = []
            for idx, item in enumerate(batch):
                try:
                    pre = self._prefetch_for_ingest(
                        url=str(item.get("url") or ""),
                        topic=topic or str(item.get("query") or ""),
                        pre_extracted_content=str(item.get("extracted_content") or ""),
                        queue_entry=item,
                        prefetch_progress=prefetch_progress,
                    )
                    prefetched_batch.append((idx, item, pre, None))
                except Exception as exc:
                    prefetched_batch.append((idx, item, None, exc))

            # PHASE B — commit each item's manifest mutations under the lock.
            with self._manifest_txn():
                for idx, item, pre, fetch_exc in prefetched_batch:
                    queue_id = str(item.get("queue_id") or "")
                    batch_queue_item_ids.append(queue_id)
                    if fetch_exc is not None:
                        batch_fetch_failed.append({"url": str(item.get("url") or ""), "reason": f"fetch_error:{fetch_exc}"})
                        if queue_id:
                            batch_queue_outcomes[queue_id] = "retry"
                        if progress_callback is not None:
                            progress_callback(
                                batch_start + idx + 1,
                                len(queue_items_list),
                                "",
                                str(item.get("title") or item.get("url", "") or ""),
                                "fetch_failed",
                                str(fetch_exc),
                            )
                        continue
                    try:
                        result = self.reingest_if_changed(
                            url=str(item.get("url") or ""),
                            source=source,
                            topic=topic or str(item.get("query") or ""),
                            pre_extracted_content=str(item.get("extracted_content") or ""),
                            queue_entry=item,
                            tentative_hashes=batch_tentative_hashes,
                            precomputed=pre,
                        )
                    except Exception as exc:
                        batch_fetch_failed.append({"url": str(item.get("url") or ""), "reason": f"fetch_error:{exc}"})
                        if queue_id:
                            batch_queue_outcomes[queue_id] = "retry"
                        if progress_callback is not None:
                            progress_callback(
                                batch_start + idx + 1,
                                len(queue_items_list),
                                "",
                                str(item.get("title") or item.get("url", "") or ""),
                                "fetch_failed",
                                str(exc),
                            )
                        continue
                    status = result.get("status")
                    if progress_callback is not None:
                        progress_callback(
                            batch_start + idx + 1,
                            len(queue_items_list),
                            str(result.get("source_id", "")),
                            str(item.get("title") or result.get("title") or item.get("url", "") or ""),
                            status or "",
                            None,
                        )
                    if status == "ingested":
                        batch_ingested.append(result)
                        if queue_id:
                            batch_queue_outcomes[queue_id] = "ingested"
                    elif status == "skipped_unchanged":
                        batch_skipped_unchanged.append(result)
                        if queue_id:
                            batch_queue_outcomes[queue_id] = "skipped_unchanged"
                    elif status == "rejected_for_trust":
                        batch_rejected_for_trust.append(result)
                        if queue_id:
                            batch_queue_outcomes[queue_id] = "rejected_for_trust"
                    elif queue_id:
                        batch_queue_outcomes[queue_id] = "unhandled"

                try:
                    compile_report = self.compile_incremental()
                except Exception:
                    batch_tentative_hashes.clear()
                    if batch_queue_item_ids:
                        self.requeue_claimed_items(batch_queue_item_ids, reason="compile_failed_retry")
                    raise

                # Promote tentative hashes for this batch
                for source_id, new_hash in batch_tentative_hashes.items():
                    record = self._manifest["sources"].get(source_id)
                    if not isinstance(record, dict):
                        continue
                    committed = list(record.get("hash_history", []))
                    if not committed or committed[-1] != new_hash:
                        committed.append(new_hash)
                        record["hash_history"] = committed[-10:]
                        self._manifest["sources"][source_id] = record

                # Mark this batch's queue items
                if batch_queue_item_ids:
                    queue_to_ingested = [qid for qid, outcome in batch_queue_outcomes.items() if qid and outcome == "ingested"]
                    queue_to_skipped = [qid for qid, outcome in batch_queue_outcomes.items() if qid and outcome == "skipped_unchanged"]
                    queue_to_rejected = [qid for qid, outcome in batch_queue_outcomes.items() if qid and outcome == "rejected_for_trust"]
                    queue_to_retry = [qid for qid, outcome in batch_queue_outcomes.items() if qid and outcome == "retry"]
                    self._mark_queue_items(queue_to_ingested, status="ingested", reason="converted_to_vault_source")
                    self._mark_queue_items(queue_to_skipped, status="ingested", reason="content_hash_unchanged")
                    self._mark_queue_items(queue_to_rejected, status="rejected", reason="trust_score_below_threshold")
                    self.requeue_claimed_items(queue_to_retry, reason="fetch_failed_retry")
                    handled = set(queue_to_ingested) | set(queue_to_skipped) | set(queue_to_rejected) | set(queue_to_retry)
                    unhandled = [qid for qid in batch_queue_item_ids if qid and qid not in handled]
                    if unhandled:
                        self.requeue_claimed_items(unhandled, reason="unhandled_status_retry")

            ingested.extend(batch_ingested)
            skipped_unchanged.extend(batch_skipped_unchanged)
            rejected_for_trust.extend(batch_rejected_for_trust)
            fetch_failed.extend(batch_fetch_failed)

        # Phase 1B — Process normalized URLs in a single transaction
        # (typically zero or few items).
        if normalized:
            url_tentative_hashes: dict[str, str] = {}
            with self._manifest_txn():
                for url in normalized:
                    if not self._is_web_url(url):
                        rejected_for_policy.append({"url": url, "reason": "invalid_scheme"})
                        continue
                    if not self._domain_allowed(url):
                        rejected_for_policy.append({"url": url, "reason": "domain_not_allowed"})
                        continue
                    try:
                        result = self.reingest_if_changed(
                            url=url,
                            source=source,
                            topic=topic,
                            tentative_hashes=url_tentative_hashes,
                        )
                    except Exception as exc:
                        fetch_failed.append({"url": url, "reason": f"fetch_error:{exc}"})
                        continue
                    status = result.get("status")
                    if status == "ingested":
                        ingested.append(result)
                    elif status == "skipped_unchanged":
                        skipped_unchanged.append(result)
                    elif status == "rejected_for_trust":
                        rejected_for_trust.append(result)

                try:
                    compile_report = self.compile_incremental()
                except Exception:
                    url_tentative_hashes.clear()
                    raise

                for source_id, new_hash in url_tentative_hashes.items():
                    record = self._manifest["sources"].get(source_id)
                    if not isinstance(record, dict):
                        continue
                    committed = list(record.get("hash_history", []))
                    if not committed or committed[-1] != new_hash:
                        committed.append(new_hash)
                        record["hash_history"] = committed[-10:]
                        self._manifest["sources"][source_id] = record

        if not queue_items_list and not normalized:
            with self._manifest_txn():
                compile_report = self.compile_incremental()

        report = {
            "source": source,
            "topic": topic,
            "status": "completed",
            "processed_count": len(normalized) + len(queue_items or []),
            "ingested_count": len(ingested),
            "skipped_unchanged_count": len(skipped_unchanged),
            "rejected_for_trust_count": len(rejected_for_trust),
            "rejected_for_policy_count": len(rejected_for_policy),
            "fetch_failed_count": len(fetch_failed),
            "ingested": ingested,
            "skipped_unchanged": skipped_unchanged,
            "rejected_for_trust": rejected_for_trust,
            "rejected_for_policy": rejected_for_policy,
            "fetch_failed": fetch_failed,
            "queue_items_claimed": len(queue_items or []),
            "vector_preflight": vector_preflight,
            "compile": compile_report,
        }
        report_path = self.ingest_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-ingest.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        with self._manifest_txn():
            self._manifest["last_run_summary"] = {
                "step": "ingest",
                "updated_at": _utcnow_iso(),
                **{k: v for k, v in report.items() if k.endswith("_count") or k == "queue_items_claimed"},
            }
        return report
