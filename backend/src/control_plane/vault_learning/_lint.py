from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.control_plane.vault_text_utils import (
    parse_frontmatter as _parse_frontmatter,
)
from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)
from src.models.factory import create_chat_model

logger = logging.getLogger(__name__)


class LintMixin:
    # ------------------------------------------------------------------
    # User-and-vault context for the LLM judge.
    # ------------------------------------------------------------------
    @staticmethod
    def _memory_summary(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("summary") or "").strip()
        if isinstance(value, str):
            return value.strip()
        return ""

    def _collect_user_context_for_judge(self) -> dict[str, Any]:
        try:
            from src.agents.memory.updater import get_memory_data

            memory = get_memory_data()
        except Exception:
            logger.debug("vault_lint_memory_unavailable", exc_info=True)
            return {}
        user = memory.get("user") or {}
        history = memory.get("history") or {}
        kept_facts: list[str] = []
        for fact in memory.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            category = str(fact.get("category") or "")
            content = str(fact.get("content") or "").strip()
            try:
                confidence = float(fact.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if (
                content
                and confidence >= 0.6
                and category in {"preference", "goal", "knowledge", "context", "interest"}
            ):
                kept_facts.append(content)
            if len(kept_facts) >= 8:
                break
        return {
            "work_context": self._memory_summary(user.get("workContext")),
            "personal_context": self._memory_summary(user.get("personalContext")),
            "top_of_mind": self._memory_summary(user.get("topOfMind")),
            "recent_months": self._memory_summary(history.get("recentMonths")),
            "facts": kept_facts,
        }

    def _collect_vault_domain_context(self) -> dict[str, Any]:
        sources = self._manifest.get("sources", {}) or {}
        tag_counts: dict[str, int] = {}
        sample_titles: list[str] = []
        for record in sources.values():
            if not isinstance(record, dict):
                continue
            for tag in record.get("topic_tags") or []:
                label = str(tag).strip()
                if label:
                    tag_counts[label] = tag_counts.get(label, 0) + 1
            title = str(record.get("title") or "").strip()
            if title and len(sample_titles) < 12:
                sample_titles.append(title[:80])
        top_tags = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
        return {
            "top_topic_tags": [tag for tag, _ in top_tags],
            "sample_source_titles": sample_titles,
            "source_count": len(sources),
        }

    def _build_judge_prompt(
        self,
        batch: list[dict[str, Any]],
        user_context: dict[str, Any],
        vault_context: dict[str, Any],
    ) -> str:
        lines: list[str] = []
        lines.append(
            "You are reviewing pages in a personal knowledge vault. The user searches "
            "and references these pages later — your job is to decide which pages "
            "are worth keeping for their future search/lookup needs."
        )
        lines.append("")
        lines.append("USER CONTEXT (from their stored memory):")
        if user_context.get("work_context"):
            lines.append(f"- Work focus: {user_context['work_context'][:400]}")
        if user_context.get("personal_context"):
            lines.append(f"- Personal: {user_context['personal_context'][:300]}")
        if user_context.get("top_of_mind"):
            lines.append(f"- Top of mind: {user_context['top_of_mind'][:400]}")
        if user_context.get("recent_months"):
            lines.append(f"- Recent interests: {user_context['recent_months'][:400]}")
        if user_context.get("facts"):
            lines.append("- Notable facts/preferences:")
            for fact in user_context["facts"]:
                lines.append(f"  • {fact[:200]}")
        if not any(user_context.get(k) for k in ("work_context", "personal_context", "top_of_mind", "recent_months", "facts")):
            lines.append("- (memory file empty — judge primarily from vault domain signals)")
        lines.append("")
        lines.append("VAULT DOMAIN:")
        tags = vault_context.get("top_topic_tags") or []
        if tags:
            lines.append(f"- Common topics across {vault_context.get('source_count', 0)} sources: {', '.join(tags)}")
        sample_titles = vault_context.get("sample_source_titles") or []
        if sample_titles:
            lines.append("- Sample source titles:")
            for title in sample_titles:
                lines.append(f"  • {title}")
        lines.append("")
        lines.append("DECISION RULES:")
        lines.append("- KEEP if the page label is something the user might plausibly")
        lines.append("  search for, browse to, or want to re-encounter — OR if the")
        lines.append("  entity/concept organizes knowledge meaningfully in their domain.")
        lines.append("- REMOVE only when you are confident the page provides zero")
        lines.append("  lookup value: news outlet/site names ('Times Now News',")
        lines.append("  'Facebook'), generic stopwords mistakenly extracted as")
        lines.append("  entities, malformed labels, or pages so broad they could")
        lines.append("  never help search (e.g. 'website', 'page').")
        lines.append("")
        lines.append("CRITICAL: bias strongly toward KEEP. False removals destroy")
        lines.append("real knowledge. When uncertain, choose 'keep'.")
        lines.append("")
        lines.append(
            "Respond with a JSON array, one object per item: "
            '[{"slug": "...", "verdict": "keep" | "remove", "reason": "short reason"}]. '
            "Cover every item exactly once. No prose before or after the array."
        )
        lines.append("")
        lines.append(f"ITEMS ({len(batch)}):")
        for idx, item in enumerate(batch, start=1):
            body_excerpt = (item.get("body_excerpt") or "").replace("\n", " ").strip()[:160]
            source_titles = item.get("source_titles") or []
            titles_str = "; ".join(t[:60] for t in source_titles[:4])
            lines.append(
                f"{idx}. slug={item['slug']} | kind={item['kind']} | "
                f"label={item['label']!r} | sources={item['live_source_count']} | "
                f"titles=[{titles_str}] | body={body_excerpt!r}"
            )
        return "\n".join(lines)

    def _parse_judge_response(self, raw: str) -> dict[str, dict[str, str]]:
        verdicts: dict[str, dict[str, str]] = {}
        try:
            data = self._extract_json_payload(raw)
        except Exception:
            return verdicts
        items: Any = data
        if isinstance(data, dict):
            for key in ("items", "verdicts", "results"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        if not isinstance(items, list):
            return verdicts
        for entry in items:
            if not isinstance(entry, dict):
                continue
            slug = str(entry.get("slug") or "").strip()
            verdict = str(entry.get("verdict") or "").strip().lower()
            if not slug or verdict not in {"keep", "remove"}:
                continue
            verdicts[slug] = {
                "verdict": verdict,
                "reason": str(entry.get("reason") or "").strip()[:200],
            }
        return verdicts

    def _judge_pages_with_llm(
        self,
        pages: list[dict[str, Any]],
        *,
        user_context: dict[str, Any],
        vault_context: dict[str, Any],
        batch_size: int = 20,
    ) -> dict[str, dict[str, str]]:
        verdicts: dict[str, dict[str, str]] = {}
        if not pages:
            return verdicts
        try:
            model = create_chat_model(thinking_enabled=False)
        except Exception:
            logger.exception("vault_lint_llm_init_failed")
            return verdicts
        for start in range(0, len(pages), batch_size):
            batch = pages[start:start + batch_size]
            prompt = self._build_judge_prompt(batch, user_context, vault_context)
            try:
                response = model.invoke(prompt)
                raw = (
                    response.content
                    if isinstance(response.content, str)
                    else str(response.content)
                )
            except Exception:
                logger.exception(
                    "vault_lint_llm_invoke_failed batch_start=%d",
                    start,
                )
                continue
            parsed = self._parse_judge_response(raw)
            verdicts.update(parsed)
            logger.info(
                "vault_lint_llm_batch start=%d size=%d parsed=%d",
                start,
                len(batch),
                len(parsed),
            )
        return verdicts

    # ------------------------------------------------------------------
    # Lint-and-prune: removes low-quality entity/concept pages.
    #
    # Two modes:
    #
    # 1. Heuristic (use_llm=False):
    #    - orphan         — source_refs empty OR none referenced still in manifest.
    #    - dismissed      — entities only: in entity_dismissals but file lingers.
    #    - singleton_stub — exactly one live source AND body is the boilerplate.
    #
    # 2. LLM-judged (use_llm=True):
    #    - orphan + dismissed remain deterministic auto-flags.
    #    - Every other page is scored by an LLM judge given the user's
    #      memory.json context + the vault's domain context. The judge
    #      returns keep/remove per page with a brief reason. Bias toward
    #      KEEP: false removals destroy real knowledge.
    #
    # When `entity_slugs_to_prune` / `concept_slugs_to_prune` are supplied,
    # evaluation is skipped entirely and only those slugs are pruned.
    # This lets the UI preview-then-commit without re-invoking the LLM.
    #
    # Side effects when actually pruning:
    #   - entity files routed through dismiss_entity() so future ingests
    #     won't re-create the same junk.
    #   - concept files: file deleted + slug stripped from sources'
    #     concept_refs in the manifest.
    #   - manifest["last_lint_at"] updated; report written to 03_ops/reports/.
    # ------------------------------------------------------------------
    @staticmethod
    def _is_stub_page_body(body: str, kind: str) -> bool:
        boilerplate_phrase = f"Maintained {kind} page derived from ingested sources"
        evidence_bullet_re = re.compile(r"^\s*-\s*Supports source\s+`?[^`]+`?\s*$")
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if boilerplate_phrase in line:
                continue
            if evidence_bullet_re.match(line):
                continue
            return False
        return True

    def lint_and_prune_pages(
        self,
        *,
        dry_run: bool = True,
        use_llm: bool = False,
        entity_slugs_to_prune: list[str] | None = None,
        concept_slugs_to_prune: list[str] | None = None,
    ) -> dict[str, Any]:
        sources = self._manifest.get("sources", {}) or {}
        live_source_ids = {str(sid) for sid in sources.keys() if str(sid).strip()}
        dismissed_entity_slugs = set((self._manifest.get("entity_dismissals", {}) or {}).keys())

        # Map slug -> human label and source-title list by walking sources once.
        entity_labels: dict[str, str] = {}
        concept_labels: dict[str, str] = {}
        entity_source_titles: dict[str, list[str]] = {}
        concept_source_titles: dict[str, list[str]] = {}
        for source_id, record in sources.items():
            if not isinstance(record, dict):
                continue
            source_title = str(record.get("title") or source_id).strip() or str(source_id)
            for raw in record.get("entity_refs") or []:
                label = str(raw).strip()
                if not label:
                    continue
                slug = _slugify(label)
                if not slug:
                    continue
                if len(label) > len(entity_labels.get(slug, "")):
                    entity_labels[slug] = label
                entity_source_titles.setdefault(slug, []).append(source_title)
            for raw in record.get("concept_refs") or []:
                label = str(raw).strip()
                if not label:
                    continue
                slug = _slugify(label)
                if not slug:
                    continue
                if len(label) > len(concept_labels.get(slug, "")):
                    concept_labels[slug] = label
                concept_source_titles.setdefault(slug, []).append(source_title)

        explicit_commit = (
            entity_slugs_to_prune is not None or concept_slugs_to_prune is not None
        )

        def _evaluate(
            directory: Path,
            kind: str,
            label_map: dict[str, str],
            source_title_map: dict[str, list[str]],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            """Returns (auto_flagged_findings, llm_candidates).

            auto_flagged_findings: orphan + dismissed (no LLM needed).
            llm_candidates: pages eligible for either the singleton_stub
                heuristic or the LLM judge, with page metadata attached.
            """
            auto: list[dict[str, Any]] = []
            candidates: list[dict[str, Any]] = []
            if not directory.exists():
                return auto, candidates
            for path in sorted(directory.glob("*.md")):
                if path.name == "index.md":
                    continue
                slug = path.stem
                try:
                    frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                source_refs_raw = frontmatter.get("source_refs") or []
                source_refs = [str(ref) for ref in source_refs_raw if str(ref).strip()]
                live_refs = [ref for ref in source_refs if ref in live_source_ids]
                is_stub = self._is_stub_page_body(body, kind)

                base = {
                    "slug": slug,
                    "label": label_map.get(slug, slug.replace("-", " ").title()),
                    "source_refs": source_refs,
                    "live_source_refs": live_refs,
                }
                if kind == "entity" and slug in dismissed_entity_slugs:
                    auto.append({**base, "reasons": ["dismissed"]})
                    continue
                if not live_refs:
                    auto.append({**base, "reasons": ["orphan"]})
                    continue
                candidates.append(
                    {
                        **base,
                        "kind": kind,
                        "live_source_count": len(live_refs),
                        "is_stub": is_stub,
                        "body_excerpt": body.strip()[:200],
                        "source_titles": list(
                            dict.fromkeys(source_title_map.get(slug, []))
                        )[:8],
                    }
                )
            return auto, candidates

        entity_findings: list[dict[str, Any]]
        concept_findings: list[dict[str, Any]]

        if explicit_commit:
            # Commit path: prune exactly the slugs supplied by the caller.
            # Skip evaluation entirely — the preview already decided.
            entity_findings = [
                {
                    "slug": slug,
                    "label": entity_labels.get(slug, slug.replace("-", " ").title()),
                    "reasons": ["committed"],
                    "source_refs": [],
                    "live_source_refs": [],
                }
                for slug in (entity_slugs_to_prune or [])
            ]
            concept_findings = [
                {
                    "slug": slug,
                    "label": concept_labels.get(slug, slug.replace("-", " ").title()),
                    "reasons": ["committed"],
                    "source_refs": [],
                    "live_source_refs": [],
                }
                for slug in (concept_slugs_to_prune or [])
            ]
        else:
            entity_auto, entity_candidates = _evaluate(
                self.compiled_entities_dir, "entity", entity_labels, entity_source_titles,
            )
            concept_auto, concept_candidates = _evaluate(
                self.compiled_concepts_dir, "concept", concept_labels, concept_source_titles,
            )

            entity_findings = list(entity_auto)
            concept_findings = list(concept_auto)

            all_candidates = entity_candidates + concept_candidates

            if use_llm and all_candidates:
                user_context = self._collect_user_context_for_judge()
                vault_context = self._collect_vault_domain_context()
                verdicts = self._judge_pages_with_llm(
                    all_candidates,
                    user_context=user_context,
                    vault_context=vault_context,
                )
                for candidate in all_candidates:
                    verdict = verdicts.get(candidate["slug"])
                    if verdict and verdict["verdict"] == "remove":
                        finding = {
                            "slug": candidate["slug"],
                            "label": candidate["label"],
                            "reasons": [f"llm:{verdict['reason'] or 'noise'}"],
                            "source_refs": candidate.get("source_refs", []),
                            "live_source_refs": candidate.get("live_source_refs", []),
                        }
                        if candidate["kind"] == "entity":
                            entity_findings.append(finding)
                        else:
                            concept_findings.append(finding)
            else:
                # Heuristic-only path: singleton_stub on the candidates.
                for candidate in all_candidates:
                    if candidate["live_source_count"] == 1 and candidate["is_stub"]:
                        finding = {
                            "slug": candidate["slug"],
                            "label": candidate["label"],
                            "reasons": ["singleton_stub"],
                            "source_refs": candidate.get("source_refs", []),
                            "live_source_refs": candidate.get("live_source_refs", []),
                        }
                        if candidate["kind"] == "entity":
                            entity_findings.append(finding)
                        else:
                            concept_findings.append(finding)

        removed_entities = 0
        removed_concepts = 0
        if not dry_run:
            # Entities: route through dismiss_entity so future ingests skip them.
            for finding in entity_findings:
                slug = finding["slug"]
                primary_reason = finding["reasons"][0]
                try:
                    self.dismiss_entity(slug=slug, reason=f"linted_{primary_reason}")
                    removed_entities += 1
                except Exception:
                    logger.exception("vault_lint_dismiss_entity_failed slug=%s", slug)

            # Concepts: delete file + strip slug from sources' concept_refs.
            concept_slugs_to_remove = {finding["slug"] for finding in concept_findings}
            if concept_slugs_to_remove:
                for source_record in sources.values():
                    if not isinstance(source_record, dict):
                        continue
                    refs = source_record.get("concept_refs") or []
                    if not isinstance(refs, list):
                        continue
                    filtered = [
                        ref for ref in refs
                        if str(ref).strip() and _slugify(str(ref)) not in concept_slugs_to_remove
                    ]
                    if len(filtered) != len(refs):
                        source_record["concept_refs"] = filtered

                for slug in concept_slugs_to_remove:
                    compiled_path = self.compiled_concepts_dir / f"{slug}.md"
                    if compiled_path.exists():
                        try:
                            compiled_path.unlink()
                            removed_concepts += 1
                        except OSError:
                            logger.exception("vault_lint_unlink_concept_failed slug=%s", slug)

            # Rewrite concept index so it reflects the post-prune state.
            if removed_concepts:
                index_path = self.compiled_concepts_dir / "index.md"
                index_path.write_text(
                    self._render_index_for_dir("Concepts", self.compiled_concepts_dir),
                    encoding="utf-8",
                )

            self._manifest["last_lint_at"] = _utcnow_iso()
            self._save_manifest()

        entities_total = sum(
            1 for path in self.compiled_entities_dir.glob("*.md") if path.name != "index.md"
        ) if self.compiled_entities_dir.exists() else 0
        concepts_total = sum(
            1 for path in self.compiled_concepts_dir.glob("*.md") if path.name != "index.md"
        ) if self.compiled_concepts_dir.exists() else 0

        report = {
            "generated_at": _utcnow_iso(),
            "dry_run": bool(dry_run),
            "entities": {
                "total_before": entities_total,
                "flagged": entity_findings,
                "removed": removed_entities,
            },
            "concepts": {
                "total_before": concepts_total,
                "flagged": concept_findings,
                "removed": removed_concepts,
            },
        }
        if not dry_run:
            report_path = (
                self.lint_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-prune.json"
            )
            try:
                self.lint_reports_dir.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            except OSError:
                logger.exception("vault_lint_report_write_failed")
        return report

    def lint_vault(self, *, freshness_window_days: int = 30) -> dict[str, Any]:
        expired_queries = self.expire_queries()
        stale_syntheses: list[str] = []
        orphan_pages: list[str] = []
        missing_backlinks: list[str] = []
        contradictions: list[str] = []
        open_questions: list[str] = []

        now = _utcnow()
        for path in sorted(self.compiled_syntheses_dir.glob("*.md")):
            if path.name == "index.md":
                continue
            frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            reviewed_at_raw = frontmatter.get("last_reviewed_at")
            reviewed_at = (
                datetime.fromisoformat(str(reviewed_at_raw)).replace(tzinfo=UTC)
                if reviewed_at_raw
                else now - timedelta(days=freshness_window_days + 1)
            )
            freshness = int(frontmatter.get("freshness_window_days") or freshness_window_days)
            if reviewed_at < now - timedelta(days=freshness):
                stale_syntheses.append(path.name)
            if not frontmatter.get("source_refs"):
                orphan_pages.append(path.name)
            if not frontmatter.get("open_questions"):
                missing_backlinks.append(path.name)
            if "contradiction" in body.lower():
                contradictions.append(path.name)
            for question in frontmatter.get("open_questions", []):
                open_questions.append(f"{path.name}: {question}")

        for directory in (self.compiled_concepts_dir, self.compiled_entities_dir):
            for path in sorted(directory.glob("*.md")):
                if path.name == "index.md":
                    continue
                frontmatter, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
                if not frontmatter.get("source_refs"):
                    orphan_pages.append(path.name)

        report = {
            "generated_at": _utcnow_iso(),
            "stale_syntheses_count": len(stale_syntheses),
            "orphan_pages_count": len(orphan_pages),
            "missing_backlinks_count": len(missing_backlinks),
            "contradictions_count": len(contradictions),
            "open_questions_count": len(open_questions),
            "expired_queries_count": expired_queries["expired_count"],
            "stale_syntheses": stale_syntheses,
            "orphan_pages": orphan_pages,
            "missing_backlinks": missing_backlinks,
            "contradictions": contradictions,
            "open_questions": open_questions,
            "queue_backlog_count": len([item for item in self._load_queue() if str(item.get("status") or "") == "queued"]),
        }
        report_path = self.lint_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-lint.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if report["open_questions_count"] or report["stale_syntheses_count"] or report["orphan_pages_count"]:
            task_path = self.task_review_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-vault-lint.md"
            task_path.write_text(
                "# Vault Lint Review\n\n"
                + "\n".join(f"- {item}" for item in open_questions[:20] or stale_syntheses[:20] or orphan_pages[:20]),
                encoding="utf-8",
            )
        self._manifest["last_lint_at"] = _utcnow_iso()
        self._manifest["last_run_summary"] = {"step": "lint", "updated_at": _utcnow_iso(), **report}
        self._save_manifest()
        return report

    def _collect_lint_snapshot(self, *, freshness_window_days: int = 30) -> dict[str, Any]:
        stale_syntheses = 0
        contradictions = 0
        open_questions = 0
        now = _utcnow()
        for path in sorted(self.compiled_syntheses_dir.glob("*.md")):
            if path.name == "index.md":
                continue
            frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            reviewed_at_raw = frontmatter.get("last_reviewed_at")
            reviewed_at = (
                datetime.fromisoformat(str(reviewed_at_raw)).replace(tzinfo=UTC)
                if reviewed_at_raw
                else now - timedelta(days=freshness_window_days + 1)
            )
            freshness = int(frontmatter.get("freshness_window_days") or freshness_window_days)
            if reviewed_at < now - timedelta(days=freshness):
                stale_syntheses += 1
            if "contradiction" in body.lower():
                contradictions += 1
            if isinstance(frontmatter.get("open_questions"), list):
                open_questions += len(frontmatter.get("open_questions", []))
        queue_backlog = len([item for item in self._load_queue() if str(item.get("status") or "") == "queued"])
        return {
            "stale_syntheses_count": stale_syntheses,
            "contradictions_count": contradictions,
            "open_questions_count": open_questions,
            "queue_backlog_count": queue_backlog,
        }
