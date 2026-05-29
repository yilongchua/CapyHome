from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class SynthesisMixin:
    def synthesize_knowledge_graph(
        self,
        *,
        objective_id: str,
        topic: str = "",
    ) -> dict[str, Any]:
        objective = self._ensure_objective(objective_id=objective_id, topic=topic)
        lint = self._collect_lint_snapshot()
        findings: list[str] = []
        gaps: list[str] = []
        contradictions: list[str] = []
        next_actions: list[str] = []

        queued = lint.get("queue_backlog_count", 0)
        if isinstance(queued, int) and queued > 0:
            gaps.append(f"{queued} queued search results pending ingestion.")
            next_actions.append("Run vault_ingest with queue items for this objective.")

        stale = lint.get("stale_syntheses", [])
        if isinstance(stale, list) and stale:
            gaps.append(f"{len(stale)} stale synthesis pages require review.")
            next_actions.append("Refresh stale synthesis pages with current evidence.")

        open_questions = lint.get("open_questions", [])
        if isinstance(open_questions, list) and open_questions:
            gaps.append(f"{len(open_questions)} open questions remain unresolved.")
            next_actions.append("Address top-priority open questions in synthesis pages.")

        lint_contradictions = lint.get("contradictions", [])
        if isinstance(lint_contradictions, list):
            contradictions.extend(str(item) for item in lint_contradictions[:20])

        if not findings:
            findings.append("Vault evidence compiled.")
        if not gaps:
            next_actions.append("Maintain periodic lint and freshness checks.")

        report = {
            "generated_at": _utcnow_iso(),
            "objective_id": objective_id,
            "topic": topic or objective.get("topic", ""),
            "findings": findings,
            "gaps": gaps,
            "contradictions": contradictions,
            "next_actions": next_actions,
            "lint_snapshot": {
                "stale_syntheses_count": lint.get("stale_syntheses_count", 0),
                "open_questions_count": lint.get("open_questions_count", 0),
                "contradictions_count": lint.get("contradictions_count", 0),
                "queue_backlog_count": lint.get("queue_backlog_count", 0),
            },
        }
        report_path = self.synthesis_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-synthesis.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self._append_action_history(
            {
                "objective_id": objective_id,
                "topic": report["topic"],
                "phase": "synthesize_knowledge_graph",
                "status": "completed",
                "report_path": str(report_path),
            }
        )
        objective["updated_at"] = _utcnow_iso()
        objective["last_action_at"] = objective["updated_at"]
        self._manifest["last_run_summary"] = {
            "step": "synthesize_knowledge_graph",
            "updated_at": _utcnow_iso(),
            "objective_id": objective_id,
            "findings_count": len(findings),
            "gaps_count": len(gaps),
            "contradictions_count": len(contradictions),
        }
        self._save_manifest()
        return report

    def _coverage_progress(self, *, objective_id: str = "") -> dict[str, Any]:
        sources_total = len(self._manifest.get("sources", {}))
        syntheses_total = len(self._manifest.get("topic_syntheses", {}))
        lint = self._collect_lint_snapshot()
        stale = int(lint.get("stale_syntheses_count") or 0)
        contradictions = int(lint.get("contradictions_count") or 0)
        open_questions = int(lint.get("open_questions_count") or 0)

        breadth = min(1.0, sources_total / 40.0)
        synthesis_depth = min(1.0, syntheses_total / 20.0)
        freshness = max(0.0, 1.0 - (stale / max(1, syntheses_total or 1)))
        contradiction_resolution = max(0.0, 1.0 - (contradictions / max(1, syntheses_total or 1)))
        question_closure = max(0.0, 1.0 - (open_questions / max(1, syntheses_total or 1)))

        weighted = (
            0.25 * breadth
            + 0.25 * synthesis_depth
            + 0.2 * freshness
            + 0.15 * contradiction_resolution
            + 0.15 * question_closure
        )
        percent = round(max(0.0, min(100.0, weighted * 100.0)), 2)
        return {
            "objective_id": objective_id,
            "percent": percent,
            "breakdown": {
                "source_breadth": round(breadth * 100.0, 2),
                "synthesis_depth": round(synthesis_depth * 100.0, 2),
                "freshness": round(freshness * 100.0, 2),
                "contradiction_resolution": round(contradiction_resolution * 100.0, 2),
                "open_question_closure": round(question_closure * 100.0, 2),
            },
            "last_updated_at": _utcnow_iso(),
        }

    def get_coverage_progress(self, *, objective_id: str = "") -> dict[str, Any]:
        return self._coverage_progress(objective_id=objective_id)

    def evaluate_sufficiency(self, *, objective_id: str, topic: str = "", min_score: float = 78.0) -> dict[str, Any]:
        objective = self._ensure_objective(objective_id=objective_id, topic=topic)
        progress = self._coverage_progress(objective_id=objective_id)
        lint = self._collect_lint_snapshot()
        blockers: list[str] = []
        if int(lint.get("contradictions_count") or 0) > 0:
            blockers.append("unresolved_contradictions")
        if int(lint.get("open_questions_count") or 0) > 0:
            blockers.append("open_questions")
        if int(lint.get("stale_syntheses_count") or 0) > 0:
            blockers.append("stale_syntheses")
        score = float(progress.get("percent") or 0.0)
        decision = "insufficient"
        if score >= min_score and not blockers:
            decision = "sufficient"
        elif score >= min_score * 0.85:
            decision = "near_sufficient"

        state = self._manifest.get("sufficiency_state", {}).get(objective_id, {})
        streak = int(state.get("sufficient_streak") or 0)
        if decision == "sufficient" and not blockers:
            streak += 1
        else:
            streak = 0
        auto_pause_recommended = streak >= 2 and not blockers

        report = {
            "generated_at": _utcnow_iso(),
            "objective_id": objective_id,
            "topic": topic or objective.get("topic", ""),
            "score": round(score, 2),
            "decision": decision,
            "blocking_checks": blockers,
            "reasons": [
                "weighted_coverage_progress" if score >= min_score else "coverage_below_threshold",
                *([f"blocker:{item}" for item in blockers] or ["no_blockers_detected"]),
            ],
            "recommended_actions": [
                "Prioritize contradiction resolution." if "unresolved_contradictions" in blockers else "",
                "Resolve high-priority open questions." if "open_questions" in blockers else "",
                "Refresh stale syntheses." if "stale_syntheses" in blockers else "",
                "Continue periodic monitoring." if not blockers else "",
            ],
            "min_score": min_score,
            "auto_pause_recommended": auto_pause_recommended,
            "sufficient_streak": streak,
            "progress": progress,
        }
        report["recommended_actions"] = [item for item in report["recommended_actions"] if item]
        report_path = self.sufficiency_reports_dir / f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-sufficiency.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        suff_state = self._manifest.setdefault("sufficiency_state", {})
        suff_state[objective_id] = {
            "updated_at": _utcnow_iso(),
            "score": report["score"],
            "decision": decision,
            "blocking_checks": blockers,
            "sufficient_streak": streak,
            "auto_pause_recommended": auto_pause_recommended,
            "report_path": str(report_path),
        }
        objective["updated_at"] = _utcnow_iso()
        objective["last_action_at"] = objective["updated_at"]
        self._append_action_history(
            {
                "objective_id": objective_id,
                "topic": report["topic"],
                "phase": "vault_sufficiency_evaluate",
                "status": decision,
                "score": report["score"],
                "report_path": str(report_path),
            }
        )
        self._manifest["last_run_summary"] = {
            "step": "vault_sufficiency_evaluate",
            "updated_at": _utcnow_iso(),
            "objective_id": objective_id,
            "score": report["score"],
            "decision": decision,
            "auto_pause_recommended": auto_pause_recommended,
        }
        self._save_manifest()
        return report

    def get_action_items(self, *, limit: int = 100) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        queue = self._load_queue()
        queued = [item for item in queue if str(item.get("status") or "") == "queued"]
        for queued_item in queued[: max(1, min(limit, 20))]:
            items.append(
                {
                    "kind": "queue",
                    "priority": "medium",
                    "title": str(queued_item.get("title") or queued_item.get("url") or "Queued source"),
                    "detail": str(queued_item.get("reason") or "queued_search_result"),
                    "created_at": str(queued_item.get("queued_at") or _utcnow_iso()),
                    "status": "pending",
                }
            )
        for directory, kind, priority in (
            (self.task_backlog_dir, "task_backlog", "high"),
            (self.task_review_dir, "task_review", "high"),
        ):
            for path in sorted(directory.glob("*.md"), reverse=True)[: max(1, min(limit, 30))]:
                items.append(
                    {
                        "kind": kind,
                        "priority": priority,
                        "title": path.stem.replace("-", " ").title(),
                        "detail": str(path),
                        "created_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                        "status": "pending",
                    }
                )
        for objective in self._manifest.get("objectives", {}).values():
            if not isinstance(objective, dict):
                continue
            if str(objective.get("status") or "active") != "active":
                continue
            items.append(
                {
                    "kind": "objective",
                    "priority": "medium",
                    "title": f"Objective: {objective.get('topic') or objective.get('objective_id')}",
                    "detail": f"attempts={objective.get('attempts_total', 0)} blocked={objective.get('blocked_attempts', 0)}",
                    "created_at": str(objective.get("updated_at") or _utcnow_iso()),
                    "status": "active",
                    "objective_id": str(objective.get("objective_id") or ""),
                }
            )

        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        sliced = items[: max(1, limit)]
        counts = {
            "total": len(sliced),
            "queue": len([item for item in sliced if item["kind"] == "queue"]),
            "task_backlog": len([item for item in sliced if item["kind"] == "task_backlog"]),
            "task_review": len([item for item in sliced if item["kind"] == "task_review"]),
            "objective": len([item for item in sliced if item["kind"] == "objective"]),
        }
        return {"generated_at": _utcnow_iso(), "counts": counts, "items": sliced}
