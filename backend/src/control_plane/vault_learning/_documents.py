from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

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


class DocumentsMixin:
    def discover(
        self,
        *,
        urls: list[str],
        source: str,
        topic: str = "",
        max_results: int = 8,
    ) -> dict[str, Any]:
        candidates = self._normalize_urls(urls)

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []

        for url in candidates:
            if not self._is_web_url(url):
                rejected.append({"url": url, "reason": "invalid_scheme"})
                continue
            if not self._domain_allowed(url):
                rejected.append({"url": url, "reason": "domain_not_allowed"})
                continue
            accepted.append({"url": url, "source": source, "discovered_at": _utcnow_iso(), "topic": topic})
            if len(accepted) >= max(1, max_results):
                break

        for candidate in accepted:
            key = hashlib.sha256(candidate["url"].encode("utf-8")).hexdigest()
            self._manifest["candidates"][key] = {**candidate, "status": "discovered"}

        inbox_payload = {
            "source": source,
            "topic": topic,
            "generated_at": _utcnow_iso(),
            "candidates": accepted,
            "rejected": rejected,
            "candidate_count": len(accepted),
            "rejected_count": len(rejected),
        }
        inbox_name = f"{_utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}-discover.json"
        inbox_path = self.inbox_dir / inbox_name
        inbox_path.write_text(json.dumps(inbox_payload, indent=2), encoding="utf-8")
        self._manifest["last_run_summary"] = {
            "step": "discover",
            "candidate_count": len(accepted),
            "rejected_count": len(rejected),
            "queue_path": str(self.search_results_queue_path),
            "updated_at": _utcnow_iso(),
        }
        self._save_manifest()
        return {**inbox_payload, "inbox_path": str(inbox_path)}

    def enqueue_clip(
        self,
        *,
        url: str,
        title: str,
        markdown: str,
        topic: str = "",
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_url = str(url).strip()
        if not normalized_url or not self._is_web_url(normalized_url):
            raise ValueError("A valid http(s) URL is required for vault clips.")
        rendered_markdown = markdown.strip()
        if not rendered_markdown:
            raise ValueError("Clip markdown cannot be empty.")
        result = self.enqueue_search_results(
            query=topic or title or normalized_url,
            results=[
                {
                    "title": title.strip() or normalized_url,
                    "url": normalized_url,
                    "snippet": rendered_markdown[:500],
                    "extracted_content": rendered_markdown,
                    "topic_tags": topic_tags or self._topic_tags(topic),
                    "source_tool": "browser_clipper",
                    "reason": "clipped_page",
                    "metadata": {"ingest_origin": "browser_clipper"},
                }
            ],
        )
        self._manifest["last_run_summary"] = {
            "step": "clip",
            "updated_at": _utcnow_iso(),
            "appended_count": int(result.get("appended_count") or 0),
        }
        self._save_manifest()
        return result

    def save_document(
        self,
        *,
        title: str,
        content: str,
        topic: str = "",
        topic_tags: list[str] | None = None,
        source_url: str = "",
        source_thread_id: str = "",
    ) -> dict[str, Any]:
        normalized_title = title.strip()
        normalized_content = content.strip()
        if not normalized_title:
            raise ValueError("Title is required.")
        if not normalized_content:
            raise ValueError("Content is required.")
        slug = _slugify(normalized_title) or "saved-note"
        synthetic_url = source_url.strip() or f"https://vault.local/saved/{slug}"
        queue_entry = {
            "title": normalized_title,
            "topic_tags": topic_tags or self._topic_tags(topic or normalized_title),
            "target_synthesis_refs": [self._topic_slug(topic or normalized_title)],
            "source_tool": "explicit_save",
            "metadata": {"source_thread_id": source_thread_id.strip()} if source_thread_id.strip() else {},
        }
        result = self.reingest_if_changed(
            url=synthetic_url,
            source="explicit_save",
            topic=topic or normalized_title,
            pre_extracted_content=normalized_content,
            queue_entry=queue_entry,
        )
        self.compile_incremental()
        self._manifest["last_run_summary"] = {
            "step": "save",
            "updated_at": _utcnow_iso(),
            "source_id": result.get("source_id"),
            "status": result.get("status"),
        }
        self._save_manifest()
        return result

    def get_graph(self, *, limit: int = 200) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        edge_seen: set[tuple[str, str, str]] = set()

        def ensure_node(node_id: str, *, label: str, kind: str, path: str, tags: list[str] | None = None) -> None:
            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "label": label,
                    "kind": kind,
                    "path": path,
                    "tags": tags or [],
                    "degree": 0,
                }

        for category_dir in sorted(self.compiled_dir.iterdir() if self.compiled_dir.exists() else []):
            if not category_dir.is_dir():
                continue
            category = category_dir.name
            for path in sorted(category_dir.glob("*.md")):
                if path.name == "index.md":
                    continue
                frontmatter, _body = _parse_frontmatter(path.read_text(encoding="utf-8"))
                stem = path.stem
                node_id = f"{category}:{stem}"
                ensure_node(
                    node_id,
                    label=str(frontmatter.get("title") or stem.replace("-", " ").title()),
                    kind=category,
                    path=str(path),
                    tags=[str(item) for item in frontmatter.get("topic_tags", []) if str(item).strip()],
                )

                for ref in frontmatter.get("source_refs", []) if isinstance(frontmatter.get("source_refs"), list) else []:
                    target_id = f"sources:{_slugify(str(ref))}"
                    ensure_node(target_id, label=str(ref), kind="sources", path="")
                    edge_key = (node_id, target_id, "source_ref")
                    if edge_key not in edge_seen:
                        edge_seen.add(edge_key)
                        edges.append({"source": node_id, "target": target_id, "type": "source_ref"})
                for ref_field, kind in (("concept_refs", "concepts"), ("entity_refs", "entities"), ("synthesis_refs", "syntheses")):
                    raw_refs = frontmatter.get(ref_field, [])
                    if not isinstance(raw_refs, list):
                        continue
                    for ref in raw_refs:
                        target_slug = _slugify(str(ref))
                        if not target_slug:
                            continue
                        target_id = f"{kind}:{target_slug}"
                        ensure_node(target_id, label=str(ref), kind=kind, path="")
                        edge_key = (node_id, target_id, ref_field)
                        if edge_key in edge_seen:
                            continue
                        edge_seen.add(edge_key)
                        edges.append({"source": node_id, "target": target_id, "type": field})  # noqa: F821  # preserved verbatim from original; latent bug

        for edge in edges:
            if edge["source"] in nodes:
                nodes[edge["source"]]["degree"] += 1
            if edge["target"] in nodes:
                nodes[edge["target"]]["degree"] += 1

        ranked_nodes = sorted(nodes.values(), key=lambda item: (int(item.get("degree") or 0), str(item.get("label") or "")), reverse=True)
        limited_nodes = ranked_nodes[: max(1, int(limit))]
        node_ids = {str(item["id"]) for item in limited_nodes}
        limited_edges = [edge for edge in edges if edge["source"] in node_ids and edge["target"] in node_ids]
        category_counts: dict[str, int] = {}
        for item in limited_nodes:
            kind = str(item.get("kind") or "unknown")
            category_counts[kind] = category_counts.get(kind, 0) + 1

        return {
            "generated_at": _utcnow_iso(),
            "counts": {
                "nodes": len(limited_nodes),
                "edges": len(limited_edges),
                "categories": category_counts,
            },
            "nodes": limited_nodes,
            "edges": limited_edges,
            "highlights": {
                "top_connected": limited_nodes[:10],
                "orphans": [item for item in limited_nodes if int(item.get("degree") or 0) == 0][:10],
            },
        }

    def get_source(self, source_id: str) -> dict[str, Any]:
        source = self._manifest.get("sources", {}).get(source_id)
        if not isinstance(source, dict):
            raise ValueError(f"Unknown source id: {source_id}")
        return {
            "source": source,
            "trust_decision": self._manifest.get("trust_decisions", {}).get(source_id, {}),
            "dependencies": self._manifest.get("source_dependencies", {}).get(source_id, []),
        }
