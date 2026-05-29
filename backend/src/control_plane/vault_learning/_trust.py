from __future__ import annotations

from urllib.parse import urlparse

from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class TrustMixin:
    def _record_trust_decision(
        self,
        *,
        source_id: str,
        url: str,
        score: float,
        reasons: list[str],
        decision: str,
    ) -> None:
        self._manifest["trust_decisions"][source_id] = {
            "source_id": source_id,
            "url": url,
            "score": round(score, 4),
            "reasons": reasons,
            "decision": decision,
            "decided_at": _utcnow_iso(),
        }

    def _trust_score(self, *, url: str, text: str) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.35
        host = (urlparse(url).hostname or "").lower()
        if host:
            score += 0.1
        if len(text) >= 300:
            score += 0.25
        else:
            reasons.append("content_too_short")
        if "http" in text.lower():
            score += 0.1
        if any(token in host for token in ("gov", "edu", "org")):
            score += 0.15
        if not reasons:
            reasons.append("basic_quality_checks_passed")
        return min(1.0, score), reasons
