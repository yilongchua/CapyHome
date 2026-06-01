from __future__ import annotations

from pathlib import Path
from typing import Any

from src.control_plane.vault_text_utils import (
    frontmatter_dump as _frontmatter_dump,
)
from src.control_plane.vault_text_utils import (
    parse_frontmatter as _parse_frontmatter,
)
from src.control_plane.vault_text_utils import (
    slugify as _slugify,
)
from src.control_plane.vault_text_utils import (
    utcnow_iso as _utcnow_iso,
)


class PagesMixin:
    def _write_page(
        self,
        *,
        path: Path,
        frontmatter: dict[str, Any],
        title: str,
        sections: list[str],
    ) -> None:
        body = "\n\n".join([f"# {title}", *sections]).strip() + "\n"
        path.write_text(f"{_frontmatter_dump(frontmatter)}\n\n{body}", encoding="utf-8")

    def _index_document(
        self,
        *,
        doc_id: str,
        kind: str,
        title: str,
        path: Path,
        text: str,
        tags: list[str] | None = None,
    ) -> None:
        self._manifest["search_index"][doc_id] = {
            "id": doc_id,
            "kind": kind,
            "title": title,
            "path": str(path),
            "snippet": text[:500],
            "text": text[:4000],
            "tags": tags or [],
            "updated_at": _utcnow_iso(),
        }
        # Index lives in a throttled sidecar, not the hot manifest save.
        self._search_index_dirty = True

    def _update_reference_page(
        self,
        *,
        path: Path,
        title: str,
        kind: str,
        source_id: str,
        source_title: str,
        topic_tags: list[str],
        extra_frontmatter: dict[str, Any] | None = None,
        open_questions: list[str] | None = None,
    ) -> None:
        frontmatter: dict[str, Any]
        body: str
        if path.exists():
            frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        else:
            frontmatter, body = {}, ""
        source_refs = {str(item) for item in frontmatter.get("source_refs", []) if str(item).strip()}
        source_refs.add(source_id)
        frontmatter.update(
            {
                "id": path.stem,
                "kind": kind,
                "last_supported_by": source_id,
                "last_reviewed_at": _utcnow_iso(),
                "freshness_window_days": int(frontmatter.get("freshness_window_days") or 30),
                "source_refs": sorted(source_refs),
                "topic_tags": sorted(set(topic_tags) | set(frontmatter.get("topic_tags", []))),
                "open_questions": open_questions or frontmatter.get("open_questions", []),
            }
        )
        if extra_frontmatter:
            frontmatter.update(extra_frontmatter)

        sections = [
            "## Evidence\n\n" + "\n".join(f"- Supports source `{ref}`" for ref in frontmatter["source_refs"]),
        ]
        if body.strip():
            sections.insert(0, body.strip())
        else:
            sections.insert(0, f"## Overview\n\nMaintained {kind} page derived from ingested sources.")
        self._write_page(path=path, frontmatter=frontmatter, title=title, sections=sections)
        self._index_document(
            doc_id=path.stem,
            kind=kind,
            title=title,
            path=path,
            text=f"{title}\n\n{sections[0]}\n\n{source_title}",
            tags=frontmatter.get("topic_tags", []),
        )

    def _update_synthesis_page(
        self,
        *,
        topic: str,
        source_id: str,
        source_title: str,
        topic_tags: list[str],
        concept_refs: list[str],
        entity_refs: list[str],
        source_excerpt: str,
        target_synthesis_refs: list[str] | None = None,
    ) -> list[str]:
        synthesis_refs = list(target_synthesis_refs or [])
        if not synthesis_refs:
            synthesis_refs.append(self._topic_slug(topic or source_title))

        for synthesis_ref in synthesis_refs:
            path = self._compiled_synthesis_path(_slugify(synthesis_ref))
            open_questions = []
            if not path.exists():
                open_questions = [f"What new evidence is still missing for {synthesis_ref}?"]
            self._update_reference_page(
                path=path,
                title=synthesis_ref.replace("-", " ").title(),
                kind="synthesis",
                source_id=source_id,
                source_title=source_title,
                topic_tags=topic_tags,
                extra_frontmatter={
                    "concept_refs": concept_refs,
                    "entity_refs": entity_refs,
                },
                open_questions=open_questions,
            )
            frontmatter, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if "## Latest Supporting Evidence" not in body:
                body = f"{body.rstrip()}\n\n## Latest Supporting Evidence\n\n"
            evidence_line = f"- `{_utcnow_iso()}` {source_title}: {source_excerpt[:280]}"
            if evidence_line not in body:
                body = body.rstrip() + "\n" + evidence_line + "\n"
            path.write_text(f"{_frontmatter_dump(frontmatter)}\n\n{body.lstrip()}", encoding="utf-8")
            self._index_document(
                doc_id=path.stem,
                kind="synthesis",
                title=frontmatter.get("id", path.stem).replace("-", " ").title(),
                path=path,
                text=body,
                tags=frontmatter.get("topic_tags", []),
            )
            self._manifest["topic_syntheses"][_slugify(synthesis_ref)] = {
                "path": str(path),
                "last_updated_at": _utcnow_iso(),
                "topic_tags": topic_tags,
            }

        return [_slugify(item) for item in synthesis_refs]
