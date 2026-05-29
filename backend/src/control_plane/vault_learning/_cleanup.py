from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class CleanupMixin:
    def reset_knowledge_graph(self) -> dict[str, Any]:
        """Wipe all sources, concepts, entities, queue items, and manifest state.

        Callers must ensure no ingest runners are active (see
        `_VaultCoordination.active_runners`). Holds the queue and manifest
        locks for the duration of the reset so producers and consumers cannot
        interleave with the wipe.
        """
        removed_dirs = [
            self.raw_dir,
            self.compiled_dir,
            self.ops_dir,
            self.state_dir,
        ]
        external_queue = self.search_results_queue_path.resolve()
        try:
            external_queue.relative_to(self.vault_root)
            queue_inside_vault = True
        except ValueError:
            queue_inside_vault = False

        counts_before = {
            "sources": len(self._manifest.get("sources", {}) or {}),
            "queue_items": len(self._load_queue()),
        }

        with self._coord.queue_lock, self._coord.manifest_lock:
            for directory in removed_dirs:
                if directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)
            if not queue_inside_vault and self.search_results_queue_path.exists():
                self.search_results_queue_path.unlink(missing_ok=True)

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

            self._seed_schema_docs()
            self._manifest = self._load_manifest()
            self._save_manifest()
            self._ensure_queue_file()

        return {
            "status": "cleared",
            "removed": counts_before,
        }

    def cleanup_orphan_compiled_files(self) -> dict[str, int]:
        """Delete compiled artifacts on disk that are unreachable from the current manifest.

        Reachability rules:
          - sources/{source_id}.md kept iff source_id is a key in manifest["sources"].
          - concepts/{slug}.md and entities/{slug}.md kept iff some manifest source's
            concept_refs / entity_refs slugifies to that filename stem.
          - syntheses/{name}.md kept iff some manifest["topic_syntheses"] entry's path
            ends with that filename.
          - queries/{query_id}.md kept iff query_id is a key in manifest["queries"].

        index.md files are never deleted. Returns counts of deleted files per category.
        """
        sources = self._manifest.get("sources", {}) or {}
        kept_source_stems = {str(sid) for sid in sources.keys() if str(sid).strip()}

        dismissed_entity_slugs = {
            str(slug) for slug in (self._manifest.get("entity_dismissals", {}) or {}).keys()
        }

        kept_concept_slugs: set[str] = set()
        kept_entity_slugs: set[str] = set()
        for record in sources.values():
            if not isinstance(record, dict):
                continue
            for ref in record.get("concept_refs", []) or []:
                slug = _slugify(str(ref))
                if slug:
                    kept_concept_slugs.add(slug)
            for ref in record.get("entity_refs", []) or []:
                slug = _slugify(str(ref))
                if slug and slug not in dismissed_entity_slugs:
                    kept_entity_slugs.add(slug)

        kept_synthesis_stems: set[str] = set()
        for entry in (self._manifest.get("topic_syntheses", {}) or {}).values():
            if not isinstance(entry, dict):
                continue
            path_value = str(entry.get("path") or "")
            if path_value:
                kept_synthesis_stems.add(Path(path_value).stem)

        kept_query_stems = {str(qid) for qid in (self._manifest.get("queries", {}) or {}).keys() if str(qid).strip()}

        targets = (
            (self.compiled_sources_dir, kept_source_stems, "sources"),
            (self.compiled_concepts_dir, kept_concept_slugs, "concepts"),
            (self.compiled_entities_dir, kept_entity_slugs, "entities"),
            (self.compiled_syntheses_dir, kept_synthesis_stems, "syntheses"),
            (self.compiled_queries_dir, kept_query_stems, "queries"),
        )

        deleted: dict[str, int] = {}
        total = 0
        for directory, kept_stems, label in targets:
            removed = 0
            if not directory.exists():
                deleted[label] = 0
                continue
            for path in directory.glob("*.md"):
                if path.name == "index.md":
                    continue
                if path.stem in kept_stems:
                    continue
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
            deleted[label] = removed
            total += removed

        if total:
            # Rewrite index pages so they reflect the post-cleanup directory contents.
            for directory, title in (
                (self.compiled_sources_dir, "Sources"),
                (self.compiled_concepts_dir, "Concepts"),
                (self.compiled_entities_dir, "Entities"),
                (self.compiled_syntheses_dir, "Syntheses"),
                (self.compiled_queries_dir, "Queries"),
            ):
                if directory.exists():
                    (directory / "index.md").write_text(self._render_index_for_dir(title, directory), encoding="utf-8")

        deleted["total"] = total
        return deleted

    def purge_objective(self, *, objective_id: str) -> dict[str, Any]:
        normalized_objective_id = objective_id.strip()
        if not normalized_objective_id:
            raise ValueError("Objective id is required.")

        removed_paths: list[str] = []

        def remove_path(path: Path) -> bool:
            if path.is_file():
                path.unlink()
                removed_paths.append(str(path))
                return True
            if path.is_dir():
                shutil.rmtree(path)
                removed_paths.append(str(path))
                return True
            return False

        objective_slug = _slugify(normalized_objective_id) or "objective"
        objective_dir = self.ops_dir / "autoresearch" / "objectives" / objective_slug
        raw_objective_dir = self.raw_dir / normalized_objective_id

        removed_count = 0
        if remove_path(objective_dir):
            removed_count += 1
        if remove_path(raw_objective_dir):
            removed_count += 1

        report_removed_count = 0
        report_dirs = (
            self.discover_reports_dir,
            self.ingest_reports_dir,
            self.compile_reports_dir,
            self.lint_reports_dir,
            self.synthesis_reports_dir,
            self.sufficiency_reports_dir,
        )
        for directory in report_dirs:
            for report_path in directory.glob("*.json"):
                try:
                    payload = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(payload.get("objective_id") or "").strip() != normalized_objective_id:
                    continue
                if remove_path(report_path):
                    report_removed_count += 1

        queue_items = self._load_queue()
        filtered_queue_items = [
            item
            for item in queue_items
            if str(item.get("objective_id") or "").strip() != normalized_objective_id
        ]
        queue_removed_count = len(queue_items) - len(filtered_queue_items)
        if queue_removed_count > 0:
            self._save_queue(filtered_queue_items)

        objectives = self._manifest.get("objectives", {})
        objectives.pop(normalized_objective_id, None)

        sufficiency_state = self._manifest.get("sufficiency_state", {})
        sufficiency_state.pop(normalized_objective_id, None)

        action_history = self._manifest.get("action_history", [])
        self._manifest["action_history"] = [
            item
            for item in action_history
            if str(item.get("objective_id") or "").strip() != normalized_objective_id
        ]

        attempt_fingerprints = self._manifest.get("attempt_fingerprints", {})
        self._manifest["attempt_fingerprints"] = {
            key: value
            for key, value in attempt_fingerprints.items()
            if str((value or {}).get("objective_id") or "").strip() != normalized_objective_id
        }

        last_run_summary = self._manifest.get("last_run_summary", {})
        if str(last_run_summary.get("objective_id") or "").strip() == normalized_objective_id:
            self._manifest["last_run_summary"] = {}

        self._save_manifest()
        return {
            "objective_id": normalized_objective_id,
            "removed_paths_count": removed_count + report_removed_count,
            "removed_report_count": report_removed_count,
            "removed_queue_items_count": queue_removed_count,
            "removed_paths": removed_paths,
        }

    def reprocess_existing_sources(
        self,
        *,
        only_missing: bool = True,
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        """Re-run analysis on already-ingested sources to backfill entities/concepts.

        - When ``only_missing`` is True, skip sources whose manifest entry already
          lists at least one entity_ref or concept_ref.
        - ``progress_callback(index, total, source_id, title, status, error)`` is
          invoked after each source so callers can surface progress to users.
        """
        sources = self._manifest.get("sources", {})
        items = [
            (source_id, record)
            for source_id, record in sources.items()
            if isinstance(record, dict) and str(record.get("status") or "") == "ingested"
        ]
        if only_missing:
            items = [
                (source_id, record)
                for source_id, record in items
                if not (record.get("entity_refs") or record.get("concept_refs"))
            ]

        total = len(items)
        processed = 0
        updated = 0
        skipped_no_raw = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        if progress_callback is not None:
            progress_callback(0, total, "", "", "started", None)

        for source_id, record in items:
            processed += 1
            title = str(record.get("title") or record.get("url") or source_id)
            raw_path_str = str(record.get("raw_path") or "").strip()
            if not raw_path_str:
                skipped_no_raw += 1
                if progress_callback is not None:
                    progress_callback(processed, total, source_id, title, "skipped_no_raw", None)
                continue
            raw_path = Path(raw_path_str)
            try:
                raw_text = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
            except Exception as exc:
                failed += 1
                errors.append({"source_id": source_id, "reason": f"read_error:{exc}"})
                if progress_callback is not None:
                    progress_callback(processed, total, source_id, title, "failed", str(exc))
                continue

            if not raw_text.strip():
                skipped_no_raw += 1
                if progress_callback is not None:
                    progress_callback(processed, total, source_id, title, "skipped_no_raw", None)
                continue

            raw_text = raw_text[: self.max_content_chars]
            topic_tags = [str(item).strip() for item in record.get("topic_tags", []) if str(item).strip()]
            topic_hint = topic_tags[0].replace("-", " ") if topic_tags else title
            try:
                analysis = self._analyze_source(
                    title=title,
                    url=str(record.get("url") or ""),
                    topic=topic_hint,
                    raw_text=raw_text,
                    topic_tags=topic_tags,
                    concept_refs=[],
                    entity_refs=[],
                    target_synthesis_refs=[],
                )
            except Exception as exc:
                failed += 1
                errors.append({"source_id": source_id, "reason": f"analysis_error:{exc}"})
                if progress_callback is not None:
                    progress_callback(processed, total, source_id, title, "failed", str(exc))
                continue

            entity_refs = [str(item).strip() for item in analysis.get("entities", []) if str(item).strip()]
            concept_refs = [str(item).strip() for item in analysis.get("concepts", []) if str(item).strip()]

            if not entity_refs and not concept_refs:
                if progress_callback is not None:
                    progress_callback(processed, total, source_id, title, "no_refs", None)
                continue

            for entity_ref in entity_refs:
                self._update_reference_page(
                    path=self._compiled_entity_path(entity_ref),
                    title=entity_ref.replace("-", " ").title(),
                    kind="entity",
                    source_id=source_id,
                    source_title=title,
                    topic_tags=topic_tags,
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

            record["entity_refs"] = sorted(set(record.get("entity_refs", []) + entity_refs))
            record["concept_refs"] = sorted(set(record.get("concept_refs", []) + concept_refs))
            record["last_reviewed_at"] = _utcnow_iso()
            sources[source_id] = record
            updated += 1

            if updated % 25 == 0:
                self._save_manifest()

            if progress_callback is not None:
                progress_callback(processed, total, source_id, title, "updated", None)

        self._manifest["last_run_summary"] = {
            "step": "reprocess",
            "updated_at": _utcnow_iso(),
            "processed": processed,
            "updated": updated,
            "skipped_no_raw": skipped_no_raw,
            "failed": failed,
        }
        self._save_manifest()

        return {
            "total": total,
            "processed": processed,
            "updated": updated,
            "skipped_no_raw": skipped_no_raw,
            "failed": failed,
            "errors": errors[:50],
        }
