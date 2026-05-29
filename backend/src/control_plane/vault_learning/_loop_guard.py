from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from src.config.loop_detection_config import get_loop_detection_config
from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)
from src.control_plane.vault_text_utils import (
    utcnow as _utcnow,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class LoopGuardMixin:
    def _fingerprint_attempt(self, *, objective_id: str, query_text: str, key_entities: list[str] | None = None, source_hash: str | None = None) -> str:
        entities = sorted(_slugify(item) for item in (key_entities or []) if str(item).strip())
        raw = f"{objective_id.strip().lower()}|{query_text.strip().lower()}|{'|'.join(entities)}|{str(source_hash or '').strip().lower()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _ensure_objective(self, *, objective_id: str, topic: str) -> dict[str, Any]:
        objective = self._manifest["objectives"].get(objective_id)
        if isinstance(objective, dict):
            return objective
        now = _utcnow_iso()
        objective = {
            "objective_id": objective_id,
            "topic": topic,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "last_action_at": now,
            "attempts_total": 0,
            "blocked_attempts": 0,
            "completed_attempts": 0,
        }
        self._manifest["objectives"][objective_id] = objective
        return objective

    def _append_action_history(self, payload: dict[str, Any]) -> None:
        event = {
            "event_id": f"evt-{uuid4().hex[:12]}",
            "created_at": _utcnow_iso(),
            **payload,
        }
        events = self._manifest.get("action_history", [])
        events.append(event)
        self._manifest["action_history"] = events[-2000:]

    def check_loop_guard(
        self,
        *,
        objective_id: str,
        topic: str,
        query_text: str,
        key_entities: list[str] | None = None,
        source_hash: str | None = None,
        cooldown_hours: int | None = None,
        retry_budget: int | None = None,
    ) -> dict[str, Any]:
        if not get_loop_detection_config().enabled:
            return {
                "allowed": True,
                "reason": "disabled",
                "fingerprint": "",
                "cooldown_hours": 0,
                "retry_budget": 0,
            }

        objective = self._ensure_objective(objective_id=objective_id, topic=topic)
        loop_guard = self._manifest.get("loop_guard", {})
        eff_cooldown = max(1, int(cooldown_hours or loop_guard.get("cooldown_hours") or 24))
        eff_retry_budget = max(1, int(retry_budget or loop_guard.get("retry_budget") or 3))
        fingerprint = self._fingerprint_attempt(
            objective_id=objective_id,
            query_text=query_text,
            key_entities=key_entities,
            source_hash=source_hash,
        )
        now = _utcnow()
        record = self._manifest["attempt_fingerprints"].get(fingerprint, {})
        last_attempt_at_raw = record.get("last_attempt_at")
        last_attempt_at = None
        if last_attempt_at_raw:
            try:
                last_attempt_at = datetime.fromisoformat(str(last_attempt_at_raw)).replace(tzinfo=UTC)
            except Exception:
                last_attempt_at = None
        attempts = int(record.get("attempts") or 0)

        blocked_reason = ""
        if attempts >= eff_retry_budget:
            blocked_reason = "retry_budget_exhausted"
        elif last_attempt_at and last_attempt_at >= (now - timedelta(hours=eff_cooldown)):
            blocked_reason = "cooldown_active"

        allowed = not bool(blocked_reason)
        self._append_action_history(
            {
                "objective_id": objective_id,
                "topic": topic,
                "phase": "loop_guard",
                "status": "allowed" if allowed else "blocked",
                "reason": blocked_reason or "passed",
                "fingerprint": fingerprint,
                "query_text": query_text,
            }
        )
        objective["updated_at"] = _utcnow_iso()
        objective["last_action_at"] = objective["updated_at"]
        objective["attempts_total"] = int(objective.get("attempts_total") or 0) + 1
        if not allowed:
            objective["blocked_attempts"] = int(objective.get("blocked_attempts") or 0) + 1

        if allowed:
            self._manifest["attempt_fingerprints"][fingerprint] = {
                "objective_id": objective_id,
                "topic": topic,
                "last_attempt_at": _utcnow_iso(),
                "attempts": attempts + 1,
                "status": "allowed",
            }
        self._save_manifest()
        return {
            "allowed": allowed,
            "reason": blocked_reason,
            "fingerprint": fingerprint,
            "cooldown_hours": eff_cooldown,
            "retry_budget": eff_retry_budget,
        }

    def _raw_memory_bytes(self) -> int:
        total = 0
        if not self.raw_dir.exists():
            return 0
        for path in self.raw_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    @staticmethod
    def _human_bytes(num_bytes: int) -> str:
        size = float(max(0, num_bytes))
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        while size >= 1024.0 and idx < len(units) - 1:
            size /= 1024.0
            idx += 1
        return f"{size:.2f} {units[idx]}"
