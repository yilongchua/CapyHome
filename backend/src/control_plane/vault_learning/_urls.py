from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)


class UrlsMixin:
    def _domain_allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        if not self.allowed_domains:
            return True
        return any(host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains)

    def _is_web_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _normalize_urls(self, urls: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in urls:
            url = str(item).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append(url)
        return normalized

    def _source_id_for_url(self, url: str) -> str:
        host = urlparse(url).hostname or "source"
        return f"{_slugify(host)}-{hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]}"

    def _query_id_for_text(self, query_text: str) -> str:
        return f"query-{hashlib.sha1(query_text.strip().lower().encode('utf-8')).hexdigest()[:12]}"

    def _topic_slug(self, topic: str, fallback: str = "general-research") -> str:
        return _slugify(topic) if topic.strip() else fallback

    def _topic_tags(self, topic: str, metadata: dict[str, Any] | None = None) -> list[str]:
        tags = []
        if isinstance(metadata, dict):
            raw_tags = metadata.get("topic_tags")
            if isinstance(raw_tags, list):
                tags.extend(str(item).strip() for item in raw_tags if str(item).strip())
        if topic.strip():
            tags.append(self._topic_slug(topic))
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in tags:
            normalized = _slugify(tag)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped

    def _raw_package_dir(self, source_id: str, fetched_at: datetime) -> Path:
        return self.raw_sources_dir / fetched_at.strftime("%Y") / fetched_at.strftime("%m") / source_id

    def _compiled_source_path(self, source_id: str) -> Path:
        return self.compiled_sources_dir / f"{source_id}.md"

    def _compiled_entity_path(self, entity_id: str) -> Path:
        return self.compiled_entities_dir / f"{_slugify(entity_id)}.md"

    def _compiled_concept_path(self, concept_id: str) -> Path:
        return self.compiled_concepts_dir / f"{_slugify(concept_id)}.md"

    def _compiled_synthesis_path(self, topic_slug: str) -> Path:
        return self.compiled_syntheses_dir / f"{topic_slug}.md"

    def _compiled_query_path(self, query_id: str) -> Path:
        return self.compiled_queries_dir / f"{query_id}.md"
